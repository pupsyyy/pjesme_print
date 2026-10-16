#!/usr/bin/env python3
"""
Čitanje pjesama S AKORDIMA iz PDF-a aplikacije za pjesmarice (dart_pdf izvoz,
Open Sans: naslov Bold 21.6, "Key: X" Regular 13.8 desno, labele Bold 13.8,
akordi Bold 12 iznad teksta Regular 12).

Za svaki redak bilježi se TOČNO mjesto akorda — indeks znaka u tekstu iznad
kojeg akord počinje (kao [G] u ChordPro formatu):

    {"text": "Bože svet si aleluja,", "chords": [[5, "G"], [16, "C"]]}

Pravila rasporeda izvorne aplikacije (izmjerena na izvornim PDF-ovima, vidi
docs/BAZA_PJESAMA_NAPREDAK.md):
  * akord x = lijevi rub + širina(tekst[:indeks])
  * ako bi akord udario u prethodni, pomakne se na kraj prethodnog + razmak;
    novija verzija ("v2") tada usred riječi ubaci crticu u tekst ("alelu-ja")
    — takva crtica se ovdje uklanja jer nije dio pjesme
  * redak samih akorda bez teksta iscrtava se u "monospace" stupcima (7.2 pt)

Uz semantiku (tekst + indeksi) čuvaju se i podaci potrebni da se pjesma
iscrta identično izvorniku (`fmt`, `layout`) — vidi chord_render.py.
"""

import io
import re
from collections import defaultdict

import pdfplumber

import opensans

# ── Profili rasporeda dviju verzija aplikacije ────────────────────────────────

PROFILES = {
    # novija verzija (2026): prirodna visina retka fonta. Ako akord nema mjesta
    # (min. razmak do prethodnog), u tekst se ubacuju crtice: usred riječi
    # vidljive ("alelu-ja"), drugdje nevidljive (samo pomaknu tekst/akord).
    # Razmak je izmjeren između 2.754 i 4.254 pt -> uzet je jedan razmak.
    "v2": {"pair": 13.342, "line": 16.342, "chord_gap": 532 / 2048 * 12,
           "hyphenate": True},
    # starija verzija (2025): fiksna visina retka 16 pt, brojevi stranica;
    # akord se samo pomakne (točno 8 pt iza prethodnog), tekst se ne mijenja
    "v1": {"pair": 13.0, "line": 16.0, "chord_gap": 8.0, "hyphenate": False},
}
HYPHEN_W = 659 / 2048          # širina crtice (em) — "nevidljiva crtica" v2
DEFAULT_PROFILE = "v2"

LEFT = 28.3465            # lijeva i gornja margina (1 cm)

# Labele sekcija (Verse 1, Chorus, Refren, Kitica 2…)
SECTION_RE = re.compile(
    r"^(verse|chorus|pre-?chorus|bridge|intro|outro|tag|interlude|instrumental|"
    r"ending|coda|vamp|solo|refren|pripjev|kitica|strofa|uvod|završetak|"
    r"međuigra|[VCB]\d*)\b.*$", re.IGNORECASE)

# Labele blokova koji su metapodaci, a ne dio pjesme
META_LABELS = {"pjesmarica", "pjesmarice", "capo", "tempo", "time", "autor",
               "author", "copyright", "ccli", "album", "izvođač", "izvodac"}

# Akord (i hrvatska notacija: h, fis, a = a-mol, B…)
CHORD_RE = re.compile(
    r"^\(?(?:N\.?C\.?|"
    r"[A-H](?:is|es|s|#|b)?"
    r"(?:m(?!aj)|mol|min|-)?[#b]?"
    r"(?:maj|dim|aug|sus|add|°|\+|M)?\d*"
    r"(?:(?:sus|add|maj|b|#)\d+)*"
    r"(?:\([^)]*\))?"
    r"(?:/[A-H](?:is|es|s|#|b)?)?"
    r"\*?)\)?$", re.IGNORECASE)
NEUTRAL_TOKENS = {"|", "||", "-", "/", "x2", "2x", "x3", "3x", "x4", "4x", "%"}


def is_chord(tok):
    return bool(CHORD_RE.match(tok))


def all_chords(tokens):
    real = [t for t in tokens if t not in NEUTRAL_TOKENS]
    return bool(real) and all(is_chord(t) for t in real)


# ── Glifovi i retci ───────────────────────────────────────────────────────────

class Row:
    """Glifovi na istoj osnovnoj liniji, istog reza i veličine."""

    def __init__(self, glyphs, baseline, size, bold):
        self.glyphs = sorted(glyphs, key=lambda g: g["x"])
        self.baseline = baseline
        self.size = size
        self.bold = bold

    @property
    def x(self):
        return self.glyphs[0]["x"]

    @property
    def top(self):
        return self.baseline - opensans.ASCENT * self.size

    @property
    def bottom(self):
        return self.baseline + opensans.DESCENT * self.size

    def runs(self, sw=None):
        """Neprekinuti nizovi glifova (riječi) -> [(tekst, x0, x1)]."""
        sw = sw or opensans.SPACE * self.size
        out = []
        for g in self.glyphs:
            if out and g["x"] - out[-1][2] < 0.35 * sw:
                t, x0, _ = out[-1]
                out[-1] = (t + g["text"], x0, g["x1"])
            else:
                out.append((g["text"], g["x"], g["x1"]))
        return out

    def __repr__(self):
        return f"Row({self.baseline:.2f} {self.size} {'B' if self.bold else 'R'} {self.plain()!r})"

    def plain(self):
        return " ".join(r[0] for r in self.runs())


def _glyphs(page):
    """Znakovi stranice s ishodištem (x, osnovna linija od vrha)."""
    H = float(page.height)
    out = []
    for c in page.chars:
        t = c.get("text") or ""
        if not t.strip():
            continue
        m = c.get("matrix") or (1, 0, 0, 1, c["x0"], H - c["bottom"])
        font = (c.get("fontname") or "").split("+")[-1]
        out.append({
            "text": t, "x": float(m[4]), "x1": float(c["x1"]),
            "baseline": H - float(m[5]), "size": round(float(c["size"]), 1),
            "bold": "bold" in font.lower() or "black" in font.lower(),
            "font": font,
        })
    _exact_positions(out)
    return out


def _exact_positions(glyphs):
    """Open Sans: x znakova unutar riječi izračunaj iz točne metrike fonta.

    Aplikacija svaku riječ crta od točnog x; PDF čitač ostale znakove riječi
    smješta prema zaokruženoj tablici širina (greška do ~0.05 pt). Za točno
    sidrenje akorda (tolerancija 0.05 pt) ovdje se to ispravlja.
    """
    by_line = defaultdict(list)
    for g in glyphs:
        if g["font"].startswith("OpenSans"):
            by_line[(round(g["baseline"], 1), g["size"], g["bold"])].append(g)
    for (_bl, size, bold), gs in by_line.items():
        gs.sort(key=lambda g: g["x"])
        sw = opensans.SPACE * size
        start, word = None, ""
        for g in gs:
            if start is not None and g["x"] - (start["x"] + opensans.width(word, bold, size)) < 0.35 * sw:
                g["x"] = start["x"] + opensans.width(word, bold, size)
                word += g["text"]
            else:
                start, word = g, g["text"]
            g["x1"] = g["x"] + opensans.width(g["text"], bold, size)
            g["exact"] = True


def _rows(glyphs):
    groups = defaultdict(list)
    for g in glyphs:
        groups[(round(g["baseline"] * 2) / 2, g["size"], g["bold"])].append(g)
    rows = []
    for (bl, size, bold), gs in groups.items():
        rows.append(Row(gs, sum(g["baseline"] for g in gs) / len(gs), size, bold))
    rows.sort(key=lambda r: (r.baseline, r.x))
    return rows


# ── Rekonstrukcija teksta retka ───────────────────────────────────────────────

def _split_gap(gap, sw, hw, first, spacers):
    """Razmak između riječi -> (broj razmaka, broj nevidljivih crtica).

    Prvo se pokušaju samo razmaci; nevidljive crtice (v2, ispred akorda koji
    nema mjesta) dolaze u obzir tek ako razmaci ne objašnjavaju razmak.
    """
    if gap <= 0.35 * sw:
        return 0, 0
    for m in range(0, 41 if spacers else 1):
        rest = gap - m * hw
        if rest < -0.03:
            break
        n = round(rest / sw)
        if n < 0 or (n == 0 and m == 0):
            continue
        if abs(rest - n * sw) < 0.03:
            return n, m
    n = round(gap / sw)
    return (max(1, n) if not first else n), 0


def proportional_text(row, left, spacers=False):
    """Tekst retka s razmacima + x početka svakog znaka (i kraja teksta).

    Aplikacija ne crta razmake; broj razmaka = razmak / širina razmaka.
    Vodeći razmaci (redak ne počinje na lijevom rubu) se čuvaju.
    spacers=True (v2): razmak može sadržavati i nevidljive crtice ispred
    akorda — one nisu dio teksta, samo pomiču x.
    """
    sw = opensans.SPACE * row.size
    hw = HYPHEN_W * row.size
    chars, xs = [], []
    cur = left
    for g in row.glyphs:
        gap = g["x"] - cur
        n, m = _split_gap(gap, sw, hw, not chars, spacers and bool(chars))
        cur += m * hw
        for k in range(n):
            chars.append(" ")
            xs.append(cur + k * sw)
        chars.append(g["text"])
        xs.append(g["x"])
        cur = g["x1"]
    return "".join(chars), xs, cur


def mono_text(row, left):
    """Tekst "monospace" retka: svaka riječ počinje u stupcu x = left + st·7.2."""
    step = opensans.MONO * row.size
    s = ""
    for t, x0, _x1 in row.runs():
        col = max(round((x0 - left) / step), len(s) + (1 if s else 0))
        s += " " * (col - len(s)) + t
    return s


def is_mono_row(row, left):
    step = opensans.MONO * row.size
    return all(abs((x0 - left) / step - round((x0 - left) / step)) * step < 0.3
               for _t, x0, _x1 in row.runs())


def tokens_with_cols(s):
    return [[m.start(), m.group()] for m in re.finditer(r"\S+", s)]


# ── Sidrenje akorda na tekst (par: redak akorda + redak teksta) ──────────────

def _chord_tokens(row, lyric_pos, prof):
    """Riječi retka akorda -> akordi; akord s razmakom unutra ostaje jedan."""
    sw = opensans.SPACE * row.size
    toks = []
    for t, x0, x1 in row.runs():
        if toks:
            gap = x0 - toks[-1][2]
            n = round(gap / sw)
            anchored = any(abs(x0 - p) < 0.06 for p in lyric_pos)
            if (not anchored and n >= 1 and abs(gap - n * sw) < 0.06
                    and abs(gap - prof["chord_gap"]) > 0.06):
                pt, px0, _ = toks[-1]
                toks[-1] = (pt + " " * n + t, px0, x1)
                continue
        toks.append((t, x0, x1))
    return toks


def _boundary(text, i):
    return i == 0 or i >= len(text) or text[i - 1] == " " or text[i] == " "


def anchor_pair(chord_row, lyric_row, left, prof):
    """Vrati redak {"text", "chords"} s indeksima akorda."""
    text, xs, x_end = proportional_text(lyric_row, left, spacers=prof["hyphenate"])
    pos = xs + [x_end]
    toks = _chord_tokens(chord_row, pos, prof)

    tol = 0.05 if all(g.get("exact") for g in lyric_row.glyphs + chord_row.glyphs) else 0.5
    anchors = []
    lo = 0
    for t, x0, _x1 in toks:
        cand = [i for i in range(lo, len(pos)) if abs(pos[i] - x0) < tol]
        if cand:
            i = cand[0]
        elif x0 > pos[-1] + tol and lo <= len(text):
            # iza kraja teksta: razmaci na kraju retka (nevidljivi) ili
            # pomak (v1: iza prethodnog akorda; v2: nevidljive crtice)
            sw = opensans.SPACE * lyric_row.size
            hw = HYPHEN_W * lyric_row.size
            pushed = bool(anchors) and not prof["hyphenate"] and abs(
                x0 - (toks[len(anchors) - 1][1]
                      + opensans.width(toks[len(anchors) - 1][0], True, chord_row.size)
                      + prof["chord_gap"])) < tol
            n_sp = 0
            if not pushed:
                for m in range(0, 4 if prof["hyphenate"] else 1):
                    n = round((x0 - pos[-1] - m * hw) / sw)
                    if n >= 0 and abs(pos[-1] + n * sw + m * hw - x0) < max(tol, 0.06):
                        n_sp = n
                        break
            if n_sp:
                base = pos[-1]
                text += " " * n_sp
                pos = pos[:-1] + [base + k * sw for k in range(n_sp)] + [base + n_sp * sw]
            i = len(text)
        else:
            # akord je bio pomaknut udesno: sidro je znak ispod njegova početka;
            # v2 usred riječi ubacuje crticu, pa je ondje sidro na granici riječi
            ok = [i for i in range(lo, len(pos)) if pos[i] <= x0 + tol] or [lo]
            if prof["hyphenate"]:
                ok = [i for i in ok if _boundary(text, i)] or ok
            i = ok[-1]
        anchors.append(i)
        lo = i

    # crtice koje je ubacila aplikacija (v2) — ukloni
    if prof["hyphenate"]:
        for k in range(1, len(anchors)):
            a = anchors[k]
            j = a
            while j > 0 and text[j - 1] == "-":
                j -= 1
            if j == a or j == 0 or a >= len(text):
                continue
            if not (text[j - 1].isalnum() and text[a].isalnum()):
                continue
            pt, px0, _ = toks[k - 1]
            prev_end = px0 + opensans.width(pt, True, chord_row.size)
            if pos[j] < prev_end + prof["chord_gap"] - 0.05:
                n = a - j
                text = text[:j] + text[a:]
                pos = pos[:j] + pos[a:]
                anchors = [x - n if x >= a else x for x in anchors]

    return {"text": text, "chords": [[i, t[0]] for i, t in zip(anchors, toks)]}


def anchor_visual(chord_runs, lyric_row, left):
    """Akordi utipkani kao tekst iznad teksta: sidro = najbliži početak znaka."""
    text, xs, x_end = proportional_text(lyric_row, left)
    pos = xs + [x_end]
    out, lo = [], 0
    for t, x0, _x1 in chord_runs:
        i = min(range(lo, len(pos)), key=lambda k: abs(pos[k] - x0)) \
            if lo < len(pos) else len(text)
        out.append([i, t])
        lo = i
    return text, out


# ── Profil ────────────────────────────────────────────────────────────────────

def detect_profile(rows):
    votes = defaultdict(int)
    for a, b in zip(rows, rows[1:]):
        d = b.baseline - a.baseline
        if a.size == 12 and b.size == 12:
            for name, p in PROFILES.items():
                if abs(d - p["pair"]) < 0.08 or abs(d - p["line"]) < 0.08:
                    votes[name] += 1
    return max(votes, key=votes.get) if votes else DEFAULT_PROFILE


# ── Stranica -> zaglavlje + blokovi ───────────────────────────────────────────

def _crosses(rows, bound):
    """Ima li redak čiji se tekst neprekinuto proteže preko granice stupca?"""
    for r in rows:
        gs = r.glyphs
        for a, b in zip(gs, gs[1:]):
            if a["x"] < bound - 1 <= b["x"] and b["x"] - a["x1"] < 12:
                return True
    return False


def _split_columns(rows, page_w):
    """Stranice u više stupaca (starija verzija): podijeli retke po stupcima.

    Stupac počinje tamo gdje počinje labela ili barem dva retka teksta, a
    nijedan redak ne prelazi tu granicu bez razmaka (inače bi dugi redak
    s puno vodećih razmaka prerezao tekst).
    """
    starts = sorted(r.x for r in rows if (r.size > 13 and r.bold) or not r.bold)
    label_x = [r.x for r in rows if r.size > 13 and r.bold]
    lefts = []
    for x in starts:
        if not lefts or x > lefts[-1] + 120:
            lefts.append(x)
    lefts = lefts[:1] + [
        x for x in lefts[1:]
        if (any(abs(x - lx) < 1 for lx in label_x)
            or sum(1 for sx in starts if abs(sx - x) < 1) >= 2)
        and not _crosses(rows, x)]
    if len(lefts) <= 1:
        return [(min(starts) if starts else LEFT, rows)]
    bounds = lefts[1:] + [page_w + 1]
    cols = [[] for _ in lefts]
    for r in rows:
        parts = defaultdict(list)
        for g in r.glyphs:
            ci = next(i for i, b in enumerate(bounds) if g["x"] < b - 1)
            parts[ci].append(g)
        for ci, gs in parts.items():
            cols[ci].append(Row(gs, r.baseline, r.size, r.bold))
    return [(lefts[i], sorted(c, key=lambda r: r.baseline)) for i, c in enumerate(cols)]


def _blocks(rows):
    """Redci jednog stupca -> blokovi (razmak između blokova ~18 pt)."""
    blocks = []
    for r in rows:
        if blocks:
            prev = blocks[-1][-1]
            gap = r.top - prev.bottom
            if gap < 9:
                blocks[-1].append(r)
                continue
            r.gap_before = gap
        else:
            r.gap_before = None
        blocks.append([r])
    return blocks


def _lines(rows, left, prof):
    """Redci bloka (bez labele) -> lista redaka pjesme."""
    out = []
    i = 0
    n = len(rows)
    while i < n:
        r = rows[i]
        nxt = rows[i + 1] if i + 1 < n else None
        d = (nxt.baseline - r.baseline) if nxt else None
        pair = nxt is not None and r.bold and not nxt.bold and d < 1.2 * r.size

        if pair:                                         # akordi iznad teksta
            out.append(anchor_pair(r, nxt, left, prof))
            i += 2
            continue

        if r.bold and r.size <= 12.5 and is_mono_row(r, left):
            s = mono_text(r, left)
            toks = tokens_with_cols(s)
            if all_chords([t for _, t in toks]):
                # redak samih akorda; ako odmah ispod slijedi bold mono tekst,
                # to je par "akordi iznad teksta" utipkan u stupcima
                if (nxt is not None and nxt.bold and nxt.size == r.size
                        and is_mono_row(nxt, left) and d < 1.6 * r.size):
                    s2 = mono_text(nxt, left)
                    if not all_chords([t for _, t in tokens_with_cols(s2)]):
                        out.append({"text": s2, "chords": toks,
                                    "fmt": {"text": "mono"}})
                        i += 2
                        continue
                out.append({"text": "", "chords": toks})
            else:
                out.append({"text": s, "chords": [], "fmt": {"text": "mono"}})
            i += 1
            continue

        text, _xs, _end = proportional_text(r, left)
        if r.bold:
            out.append({"text": text, "chords": [], "fmt": {"text": "bold"}})
            i += 1
            continue

        toks = tokens_with_cols(text)
        if all_chords([t for _, t in toks]):
            # akordi utipkani kao običan tekst; tekst ispod = njihov redak
            if (nxt is not None and not nxt.bold and nxt.size == r.size
                    and d < 1.6 * r.size):
                t2, _x, _e = proportional_text(nxt, left)
                if not all_chords([t for _, t in tokens_with_cols(t2)]):
                    lyric, chords = anchor_visual(r.runs(), nxt, left)
                    out.append({"text": lyric, "chords": chords,
                                "fmt": {"chord_text": text}})
                    i += 2
                    continue
            out.append({"text": "", "chords": toks, "fmt": {"chord_text": text}})
            i += 1
            continue

        out.append({"text": text, "chords": []})
        i += 1
    return out


def _is_label(row):
    return row.bold and 13 <= row.size <= 15


def parse_page(page, prof_name=None):
    """Jedna stranica -> dict(header, blocks, profile, …) ili None (prazna)."""
    glyphs = _glyphs(page)
    if not glyphs:
        return None
    W, H = float(page.width), float(page.height)
    # brojevi stranica u donjoj margini (starija verzija) — preskoči
    page_num = [g for g in glyphs if g["baseline"] > H - LEFT - 15]
    page_numbers = bool(page_num) and all(g["text"].isdigit() for g in page_num)
    if page_numbers:
        glyphs = [g for g in glyphs if g not in page_num]
    rows = _rows(glyphs)
    if not rows:
        return None
    prof_name = prof_name or detect_profile(rows)
    prof = PROFILES[prof_name]

    title_rows = [r for r in rows if r.size >= 18 and r.bold]
    title, key, subtitle = None, None, []
    used = set()
    if title_rows and title_rows[0].baseline < 120:
        trs = [title_rows[0]]
        for r in title_rows[1:]:
            if r.baseline - trs[-1].baseline < 1.6 * r.size:
                trs.append(r)
        title = " ".join(proportional_text(r, LEFT)[0].strip() for r in trs)
        used.update(id(r) for r in trs)
        last = trs[-1]
        for r in rows:
            if (not r.bold and 13 <= r.size <= 15 and r.x > W * 0.5
                    and r.baseline < trs[0].baseline + 10):
                k = proportional_text(r, r.x)[0].strip()
                key = re.sub(r"^key\s*:\s*", "", k, flags=re.I)
                used.add(id(r))
        for r in rows:
            if id(r) in used or r.baseline <= last.baseline:
                continue
            if not r.bold and r.size <= 12.5 and r.top - last.bottom < 3:
                subtitle.append(proportional_text(r, LEFT)[0].strip())
                used.add(id(r))
                last = r
            else:
                break

    body = [r for r in rows if id(r) not in used]
    header_bottom = last.bottom if title else None
    result = {"title": title, "key": key, "subtitle": subtitle,
              "profile": prof_name, "columns": 0, "blocks": [], "warnings": [],
              "page_numbers": page_numbers}

    columns = _split_columns(body, W)
    result["columns"] = len(columns)
    result["col_lefts"] = [round(c[0], 3) for c in columns]
    for ci, (left, crows) in enumerate(columns):
        for bi, block in enumerate(_blocks(crows)):
            label = None
            rest = block
            if _is_label(block[0]):
                label = proportional_text(block[0], left)[0].strip()
                rest = block[1:]
            b = {"label": label, "lines": _lines(rest, left, prof)}
            if len(columns) > 1:
                b["col"] = ci
            if ci == 0 and bi == 0 and header_bottom is not None:
                gap = block[0].top - header_bottom
            else:
                gap = block[0].gap_before
            if gap is not None and abs(gap - 18) > 1.5:
                b["space"] = round(gap, 2)
            result["blocks"].append(b)
    return result


# ── Dokument -> pjesme ────────────────────────────────────────────────────────

def _split_meta(block):
    """Blok metapodatka: vrijednosti su početni obični redci bez akorda.

    Vrati (meta, ostatak_kao_sekcija|None)."""
    vals, rest = [], []
    for ln in block["lines"]:
        if not rest and not ln["chords"] and "fmt" not in ln and ln["text"].strip():
            vals.append(ln["text"].strip())
        else:
            rest.append(ln)
    meta = {"label": block["label"], "values": vals}
    extra = {"label": None, "lines": rest, "attached": True} if rest else None
    return meta, extra


def songs_from_pages(pages, filename=""):
    """Rezultati parse_page -> lista pjesama (pjesma može preći na iduću stranicu)."""
    songs, warnings = [], []
    for pno, pg in pages:
        if pg is None:
            continue
        has_chords = any(ln.get("chords") for b in pg["blocks"] for ln in b["lines"])
        has_lines = any(b["lines"] for b in pg["blocks"])
        if pg.get("format") == "word" and (
                not has_lines
                or (not pg["title"] and not has_chords and (not songs or pno == 1))):
            # naslovnica, sadržaj i sl. u pjesmarici iz Worda — nije pjesma
            warnings.append(f"Stranica {pno} preskočena (nije pjesma).")
            continue
        if pg["title"] or not songs:
            if not pg["title"]:
                warnings.append(f"Stranica {pno}: nema naslova pjesme.")
            layout = {"profile": pg["profile"]}
            if pg.get("format"):
                layout["source_format"] = pg["format"]
            song = {"title": pg["title"] or "(bez naslova)", "key": pg["key"] or "",
                    "subtitle": "\n".join(pg["subtitle"]), "meta": list(pg.get("meta") or []),
                    "sections": [], "layout": layout,
                    "source": {"file": filename, "pages": [pno]}}
            songs.append(song)
            in_header = True
        else:
            song = songs[-1]
            song["source"]["pages"].append(pno)
            in_header = False
        if pg["columns"] > 1:
            song["layout"]["columns"] = pg["columns"]
            song["layout"]["col_lefts"] = pg["col_lefts"]
        if pg.get("page_numbers"):
            song["layout"]["page_numbers"] = True
        for b in pg["blocks"]:
            lab = (b["label"] or "").strip()
            if in_header and lab and lab.casefold() in META_LABELS:
                meta, extra = _split_meta(b)
                if "col" in b:
                    meta["col"] = b["col"]
                    if extra:
                        extra["col"] = b["col"]
                song["meta"].append(meta)
                if extra:
                    song["sections"].append(extra)
                    in_header = False
                continue
            in_header = False
            sec = {"label": b["label"], "lines": b["lines"]}
            for k in ("space", "col"):
                if k in b:
                    sec[k] = b[k]
            song["sections"].append(sec)
    return songs, warnings


def extract(data, filename=""):
    """PDF (bytes) -> {"songs": [...], "pages": n, "skipped": [...], "warnings": [...]}"""
    import songbook_word  # noqa: PLC0415 — izbjegava kružni import

    pages, skipped = [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        n = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            if songbook_word.looks_like_word_page(page):
                pg = songbook_word.parse_page(page)      # pjesmarica iz Worda
            else:
                pg = parse_page(page)                    # izvoz aplikacije
            if pg is None:
                skipped.append(i)
            pages.append((i, pg))
    songs, warnings = songs_from_pages(pages, filename)
    titled = sum(1 for _i, pg in pages if pg and pg["title"])
    if skipped:
        warnings.append("Bez teksta (sken?) — preskočene stranice: "
                        + ", ".join(map(str, skipped)) + ".")
    return {"songs": songs, "pages": n, "skipped": skipped, "warnings": warnings,
            "titled": titled}


# ── Pomoćno: tekst pjesme, ChordPro, "akordi iznad teksta" ────────────────────

def numbers(song):
    """Brojevi iz bloka "Pjesmarica"."""
    for m in song.get("meta") or []:
        if (m.get("label") or "").casefold().startswith("pjesmaric"):
            return list(m.get("values") or [])
    return []


def lyrics_text(song):
    """Čisti tekst pjesme (bez akorda), sekcije odvojene praznim retkom."""
    parts = []
    for sec in song.get("sections") or []:
        lines = [ln["text"].strip() for ln in sec["lines"] if ln["text"].strip()]
        if lines:
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def line_to_chordpro(ln):
    text = ln["text"]
    chords = sorted(ln.get("chords") or [], key=lambda c: c[0])
    if not text.strip():
        # samo akordi: razmaci između kao u izvorniku (stupci)
        s, cur = "", 0
        for col, ch in chords:
            s += " " * max(col - cur, 1 if s else 0) + f"[{ch}]"
            cur = col + len(ch)
        return s
    out, prev = [], 0
    for i, ch in chords:
        i = min(i, len(text))
        out.append(text[prev:i])
        out.append(f"[{ch}]")
        prev = i
    out.append(text[prev:])
    return "".join(out).rstrip()


def to_chordpro(song):
    lines = [f"{{title: {song.get('title') or ''}}}"]
    if song.get("subtitle"):
        for s in song["subtitle"].splitlines():
            lines.append(f"{{subtitle: {s}}}")
    if song.get("key"):
        lines.append(f"{{key: {song['key']}}}")
    for m in song.get("meta") or []:
        for v in m.get("values") or []:
            lines.append(f"{{meta: {m.get('label')} {v}}}")
    for sec in song.get("sections") or []:
        lines.append("")
        label = sec.get("label")
        env = None
        if label:
            low = label.casefold()
            if low.startswith(("chorus", "refren", "pripjev")):
                env = "chorus"
            elif low.startswith(("verse", "kitica", "strofa")):
                env = "verse"
            elif low.startswith("bridge"):
                env = "bridge"
            if env:
                lines.append(f"{{start_of_{env}: {label}}}")
            else:
                lines.append(f"{{comment: {label}}}")
        for ln in sec["lines"]:
            lines.append(line_to_chordpro(ln))
        if env:
            lines.append(f"{{end_of_{env}}}")
    return "\n".join(lines).rstrip() + "\n"


def line_to_text_rows(ln):
    """Redak -> ["akordi", "tekst"] za obični tekst (stupci = indeksi znakova)."""
    chords = sorted(ln.get("chords") or [], key=lambda c: c[0])
    rows = []
    if chords:
        s = ""
        for i, ch in chords:
            if len(s) > i:
                i = len(s) + 1 if s else i
            s += " " * (i - len(s)) + ch
        rows.append(s.rstrip())
    if ln["text"].strip():
        rows.append(ln["text"].rstrip())
    return rows


if __name__ == "__main__":
    import json
    import sys
    for path in sys.argv[1:]:
        with open(path, "rb") as fh:
            res = extract(fh.read(), path.rsplit("/", 1)[-1])
        for s in res["songs"]:
            print(to_chordpro(s))
        if res["warnings"]:
            print("UPOZORENJA:", json.dumps(res["warnings"], ensure_ascii=False))
