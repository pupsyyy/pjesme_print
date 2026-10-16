#!/usr/bin/env python3
"""
Čitanje pjesmarica napravljenih u Wordu (npr. "TABOR PJESMARICA 2017"):
glava i podnožje na svakoj stranici, centriran podcrtan naslov, akordi u
posebnom retku iznad teksta — obojani (crveno) ili samo akordi — razmaknuti
pravim razmacima, tekst i akordi centrirani svaki za sebe.

Budući da su redci centrirani neovisno, akord se veže VIZUALNO: na slovo
iznad kojeg stoji njegov početak. U bazu ide sadržaj (tekst + indeksi
akorda), a ispis iz baze je u standardnom izgledu (chord_render).

Kurziv/podebljanje redaka se čuva (fmt), ali se iz njega NE zaključuje da je
sekcija refren: u Tabor pjesmarici je kurzivom ponekad refren, a ponekad
kitica — pogrešna oznaka u bazi bila bi gora od nikakve.
"""

import re
import statistics
from collections import defaultdict

from chord_extract import all_chords

TOL_BASELINE = 1.0


def _is_red(c):
    col = c.get("non_stroking_color")
    return bool(col) and len(col) == 3 and col[0] > 0.5 and col[1] < 0.35 and col[2] < 0.35


def _style(font):
    f = font.lower()
    return ("bold" if ("bold" in f or "demi" in f or "black" in f or "heavy" in f) else "") + \
           ("italic" if ("italic" in f or "oblique" in f) else "")


class WRow:
    def __init__(self, chars):
        self.chars = sorted(chars, key=lambda c: c["x"])
        vis = [c for c in self.chars if c["text"].strip()]
        self.vis = vis
        self.baseline = statistics.median(c["baseline"] for c in vis) if vis else None
        self.size = statistics.median(c["size"] for c in vis) if vis else 0
        self.red = bool(vis) and sum(_is_red(c) for c in vis) >= len(vis) / 2
        styles = [c["style"] for c in vis]
        self.style = max(set(styles), key=styles.count) if styles else ""
        self.bold = "bold" in self.style
        self.italic = "italic" in self.style

    def words(self):
        """Riječi (vidljivi znakovi spojeni dok nema razmaka) -> [(tekst, x0, x1)]."""
        out = []
        prev = None
        for c in self.chars:
            if not c["text"].strip():
                prev = None
                continue
            gap_ok = prev is not None and c["x"] - prev["x1"] < 0.25 * c["size"]
            if out and gap_ok:
                t, x0, _ = out[-1]
                out[-1] = (t + c["text"], x0, c["x1"])
            else:
                out.append((c["text"], c["x"], c["x1"]))
            prev = c
        return out

    def text_positions(self):
        """Tekst retka (razmaci sažeti u jedan, bez rubnih) + x početka svakog znaka."""
        chars, xs = [], []
        pending_space = None
        for t, x0, x1 in self.words():
            if chars:
                chars.append(" ")
                xs.append(pending_space)
            chars.extend(t)
            xs.extend(self._letter_xs(x0, x1, t))
            pending_space = x1
        end = pending_space if pending_space is not None else 0.0
        return "".join(chars), xs, end

    def _letter_xs(self, x0, x1, t):
        inside = [c for c in self.vis if x0 - 0.01 <= c["x"] <= x1 + 0.01]
        inside.sort(key=lambda c: c["x"])
        xs = []
        for c in inside:
            for _ in c["text"]:
                xs.append(c["x"])
        if len(xs) != len(t):          # ligature / neobična kodiranja: ravnomjerno
            w = (x1 - x0) / max(len(t), 1)
            xs = [x0 + k * w for k in range(len(t))]
        return xs

    def plain(self):
        return self.text_positions()[0]


def _chars(page):
    H = float(page.height)
    out = []
    for c in page.chars:
        m = c.get("matrix") or (1, 0, 0, 1, c["x0"], H - c["bottom"])
        style = _style((c.get("fontname") or "").split("+")[-1])
        # Word "lažni" kurziv: obični font s nagibom u matrici teksta
        if abs(float(m[2])) > 0.1 * abs(float(m[3]) or 1) and "italic" not in style:
            style += "italic"
        out.append({"text": c.get("text") or "", "x": float(c["x0"]), "x1": float(c["x1"]),
                    "baseline": H - float(m[5]), "size": round(float(c["size"]), 1),
                    "style": style, "non_stroking_color": c.get("non_stroking_color")})
    return out


def _content_band(page):
    """Između crta glave i podnožja (ako postoje), inače cijela stranica."""
    H, W = float(page.height), float(page.width)
    top, bottom = 0.0, H
    for r in list(page.rects) + list(page.lines):
        width = abs(float(r["x1"]) - float(r["x0"]))
        if width < W * 0.4 or abs(float(r["bottom"]) - float(r["top"])) > 4:
            continue
        y = float(r["top"])
        if y < H * 0.12:
            top = max(top, y + 2)
        elif y > H * 0.88:
            bottom = min(bottom, y - 2)
    return top, bottom


def _underlines(page):
    return [(float(r["top"]), float(r["x0"]), float(r["x1"])) for r in page.rects
            if abs(float(r["bottom"]) - float(r["top"])) <= 4]


def _rows(chars):
    """Retci po osnovnoj liniji; mali superskripti (npr. "rd") pripoje se retku ispod."""
    vis = sorted((c for c in chars if c["text"].strip()), key=lambda c: c["baseline"])
    groups = []
    for c in vis:
        if groups and abs(c["baseline"] - groups[-1][0]) <= TOL_BASELINE:
            groups[-1][1].append(c)
        else:
            groups.append([c["baseline"], [c]])
    # razmaci (nevidljivi) idu u redak čija im je osnovna linija najbliža
    for c in chars:
        if c["text"].strip() or not groups:
            continue
        g = min(groups, key=lambda g: abs(g[0] - c["baseline"]))
        if abs(g[0] - c["baseline"]) <= TOL_BASELINE:
            g[1].append(c)
    rows = [WRow(cs) for _bl, cs in groups]
    # superskripti (npr. "rd" u "capo 3rd fret") pripoji retku odmah ispod
    merged = rows
    out = []
    for i, r in enumerate(merged):
        nxt = merged[i + 1] if i + 1 < len(merged) else None
        if (nxt is not None and r.size < 0.65 * nxt.size
                and 0 < nxt.baseline - r.baseline < 0.6 * nxt.size):
            nxt.chars = sorted(nxt.chars + r.chars, key=lambda c: c["x"])
            nxt.vis = sorted(nxt.vis + r.vis, key=lambda c: c["x"])
            continue
        out.append(r)
    return out


def anchor_visual(chord_words, lyric_row):
    """Akordi (riječi s x) -> [[indeks, akord]] prema slovu ispod početka akorda."""
    text, xs, end = lyric_row.text_positions()
    # Kandidati: početak svakog SLOVA (akord nikad ne stoji nad zarezom ili
    # uskličnikom) i kraj teksta; najbliži početku akorda, kod izjednačenja lijevi.
    starts = [(xs[k], k) for k in range(len(text)) if text[k].isalnum()] + [(end, len(text))]
    out = []
    lo = 0
    for t, x0, _x1 in chord_words:
        cand = [s for s in starts if s[1] >= lo] or [(end, len(text))]
        i = min(cand, key=lambda s: (abs(s[0] - x0), s[0]))[1] if text else 0
        out.append([i, t])
        lo = i
    return text, out


def _chord_only(chord_words, size):
    """Redak samih akorda -> stupci (relativni razmak kao u izvoru)."""
    if not chord_words:
        return []
    step = 0.5 * size
    x_min = chord_words[0][1]
    out, cur = [], 0
    for t, x0, _x1 in chord_words:
        col = max(round((x0 - x_min) / step), cur)
        out.append([col, t])
        cur = col + len(t) + 1
    return out


_CAPO = re.compile(r"^\s*capo\b\s*(.*)$", re.IGNORECASE)


def parse_page(page):
    """Stranica Word pjesmarice -> dict kao chord_extract.parse_page (format "word")."""
    top, bottom = _content_band(page)
    chars = [c for c in _chars(page) if top < c["baseline"] < bottom]
    rows = [r for r in _rows(chars) if r.vis]
    if not rows:
        return None
    unders = _underlines(page)

    # naslov: prvi podebljani (ne-akordni) redak ≥ 14 pt na vrhu, ili podcrtani
    title, subtitle, meta = None, [], []
    i = 0
    if rows and not rows[0].red and (rows[0].bold or any(
            0 <= u[0] - rows[0].baseline <= 6 for u in unders)) and rows[0].size >= 13:
        title = rows[0].plain().strip()
        i = 1
        # podnaslov / capo: manji redci odmah ispod naslova
        while i < len(rows) and not rows[i].red and rows[i].size <= 14.5 \
                and rows[i].baseline - rows[i - 1].baseline < 2.2 * rows[i].size + 6:
            t = rows[i].plain().strip()
            m = _CAPO.match(t)
            if m:
                meta.append({"label": "Capo", "values": [m.group(1).strip() or t]})
            elif t:
                subtitle.append(t.strip("()").strip() if t.startswith("(") and t.endswith(")") else t)
            i += 1
    body = rows[i:]

    # razmak retka (medijan) -> prazan odlomak = nova sekcija
    gaps = [b.baseline - a.baseline for a, b in zip(body, body[1:])]
    pitch = statistics.median(gaps) if gaps else 19.0

    sections, lines = [], []
    k = 0
    prev_bl = None
    while k < len(body):
        r = body[k]
        if prev_bl is not None and r.baseline - prev_bl > 1.55 * pitch and lines:
            sections.append({"label": None, "lines": lines})
            lines = []
        nxt = body[k + 1] if k + 1 < len(body) else None
        words = r.words()
        is_chords = r.red or all_chords([w[0] for w in words])
        if is_chords:
            if nxt is not None and not nxt.red and not all_chords([w[0] for w in nxt.words()]) \
                    and nxt.baseline - r.baseline < 1.3 * pitch:
                text, chords = anchor_visual(words, nxt)
                ln = {"text": text, "chords": chords}
                if nxt.bold:
                    ln["fmt"] = {"text": "bold"}
                elif nxt.italic:
                    ln["fmt"] = {"text": "italic"}
                lines.append(ln)
                prev_bl = nxt.baseline
                k += 2
                continue
            lines.append({"text": "", "chords": _chord_only(words, r.size)})
        else:
            ln = {"text": r.plain(), "chords": []}
            if r.bold:
                ln["fmt"] = {"text": "bold"}
            elif r.italic:
                ln["fmt"] = {"text": "italic"}
            lines.append(ln)
        prev_bl = r.baseline
        k += 1
    if lines:
        sections.append({"label": None, "lines": lines})

    return {"title": title, "key": "", "subtitle": subtitle, "meta": meta,
            "profile": "v2", "format": "word", "columns": 1, "page_numbers": False,
            "blocks": [dict(s) for s in sections], "warnings": [],
            "red_tokens": sum(1 for r in rows if r.red for _w in r.words())}


def looks_like_word_page(page):
    """Stranica koja NIJE izvoz aplikacije (nema Open Sans fonta)."""
    return not any((c.get("fontname") or "").split("+")[-1].startswith("OpenSans")
                   for c in page.chars)
