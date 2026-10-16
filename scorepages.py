#!/usr/bin/env python3
"""
Prepoznavanje stranica s NOTAMA (notnim zapisom) u PDF-u.

Stranica je notna ako na njoj postoje crtovlja (5 jednako razmaknutih
vodoravnih crta; 6 za tabulature) ili puno znakova iz notnog fonta
(Sibelius/MuseScore/Finale/Dorico/LilyPond...). Crtovlja se traže:
  * u vektorskom crtežu (crte, tanki pravokutnici, putanje) — izvoz iz
    programa za note,
  * na slici stranice (sken, fotografija, slika nota umetnuta u Word) —
    vodoravna projekcija tamnih točaka po uskim prugama stranice.

Vrste stranica:
  "score" — note (tekst na stranici je samo tekst ispod nota, naslov i sl.)
  "mixed" — note + običan tekst pjesme (npr. melodija gore, kitice ispod)
  None    — obična stranica s tekstom

Ništa se ne „čita" iz nota — za to je omr.py (Audiveris).
"""

import re
import statistics
import unicodedata

from PIL import Image

MUSIC_FONT_RE = re.compile(
    r"opus(?!text|chords|lyrics)|bravura|leland|petaluma|emmentaler|feta|parmesan|"
    r"gonville|mscore|musejazz(?!text)|maestro|petrucci|engraver|broadway|"
    r"finale ?(?:ash|jazz|legacy|maestro|broadway|engraver|petrucci)|kousaku|"
    r"helsinki|inkpen|reprise|norfolk|november|sonata|lassus|goldenage|"
    r"capella|nwc2|toccata|sebastian|fiddle|musicalsymbols|lilyjazz(?!text)",
    re.IGNORECASE)
MIN_MUSIC_CHARS = 40       # toliko notnih znakova = note i bez crtovlja
MIN_LINE = 50.0            # crta crtovlja je duga barem toliko (pt)
SPACING = (2.0, 12.0)      # razmak crta crtovlja (pt)
RASTER_DPI = 150
RASTER_STRIPS = 8
RASTER_MIN_IMAGE = 0.05    # slike pokrivaju barem toliki dio stranice -> traži i na slici
OUTSIDE_ROWS = 3           # redaka teksta izvan nota -> stranica je "mixed"


def is_music_font(fontname):
    name = (fontname or "").split("+", 1)[-1].replace(" ", "")
    return bool(MUSIC_FONT_RE.search(name))


# ── Crtovlja u vektorskom crtežu ─────────────────────────────────────────────

def _segments(page):
    """Vodoravne crte na stranici: [(x0, x1, y)]."""
    segs = []
    for ln in page.lines:
        if abs(ln["top"] - ln["bottom"]) < 0.8:
            segs.append((ln["x0"], ln["x1"], (ln["top"] + ln["bottom"]) / 2))
    for r in page.rects:
        if r["height"] <= 1.5 and r["width"] >= 1:
            segs.append((r["x0"], r["x1"], (r["top"] + r["bottom"]) / 2))
    h = page.height
    for c in page.curves:
        if c["bottom"] - c["top"] < 0.8:
            segs.append((c["x0"], c["x1"], (c["top"] + c["bottom"]) / 2))
            continue
        # više crta u jednoj putanji: točke s istom visinom
        by_y = {}
        for x, y in c.get("pts") or []:
            by_y.setdefault(round(y, 1), []).append(x)
        for y, xs in by_y.items():
            if len(xs) >= 2:
                # pdfplumber daje pts u PDF koordinatama (y odozdo) ili odozgo
                top = y if c["top"] - 1 <= y <= c["bottom"] + 1 else h - y
                segs.append((min(xs), max(xs), top))
    return _merge_collinear(segs)


def _merge_collinear(segs):
    """Spoji komade iste crte (neki programi crtaju crtovlje po taktovima)."""
    out = []
    for x0, x1, y in sorted(segs, key=lambda s: (s[2], s[0])):
        merged = False
        for k in range(len(out) - 1, -1, -1):
            px0, px1, py = out[k]
            if y - py >= 0.3:
                break
            if x0 <= px1 + 2.5 and x1 >= px0 - 2.5:
                out[k] = (min(px0, x0), max(px1, x1), py)
                merged = True
                break
        if not merged:
            out.append((x0, x1, y))
    return [s for s in out if s[1] - s[0] >= MIN_LINE]


def _runs(ys, lo, hi, tol=0.15, gaps=0):
    """Crtovlja među visinama crta: 5 (ili 6, tabulatura) jednako razmaknutih
    vrijednosti; druge crte između njih (crescendo, produžne crte teksta) ne
    smetaju. Vrati [(prva, zadnja, razmak)]."""
    ys = sorted(set(round(y, 1) for y in ys))

    def near(target, d):
        best = None
        for y in ys:
            if abs(y - target) <= max(tol * d, 0.6) and (
                    best is None or abs(y - target) < abs(best - target)):
                best = y
        return best

    found, used = [], set()
    for i, y0 in enumerate(ys):
        if y0 in used:
            continue
        for y1 in ys[i + 1:]:
            d = y1 - y0
            if d < lo:
                continue
            if d > hi:
                break
            if near(y0 - d, d) is not None:
                continue                      # nastavak niza odozgo (crte bilježnice)
            run, slots, missing = [y0, y1], 2, 0
            while slots < 8:
                y = near(run[-1] + (slots - len(run) + 1) * d, d)
                if y is None or y in run:
                    if missing < gaps and slots < 5:
                        missing += 1          # crta prekrivena notom (sken)
                        slots += 1
                        continue
                    break
                run.append(y)
                slots += 1
            if slots in (5, 6) and len(run) >= slots - missing:
                found.append((run[0], run[-1], (run[-1] - run[0]) / (slots - 1)))
                used.update(run)
                break
    return found


def vector_staves(page):
    """Crtovlja iz vektorskog crteža: [{"top","bottom","spacing","x0","x1"}]."""
    segs = _segments(page)
    groups = []
    for s in segs:
        for g in groups:
            overlap = min(g["x1"], s[1]) - max(g["x0"], s[0])
            if overlap >= 0.8 * min(g["x1"] - g["x0"], s[1] - s[0]):
                g["ys"].append(s[2])
                g["x0"], g["x1"] = min(g["x0"], s[0]), max(g["x1"], s[1])
                break
        else:
            groups.append({"x0": s[0], "x1": s[1], "ys": [s[2]]})
    staves = []
    for g in groups:
        for top, bottom, sp in _runs(g["ys"], *SPACING):
            staves.append({"top": top, "bottom": bottom, "spacing": sp,
                           "x0": g["x0"], "x1": g["x1"]})
    return sorted(staves, key=lambda s: s["top"])


# ── Crtovlja na slici (sken, fotografija, slika u Wordu) ─────────────────────

def _image_cover(page):
    area = page.width * page.height or 1
    cov = 0.0
    for im in page.images:
        w = max(0.0, min(im["x1"], page.width) - max(im["x0"], 0))
        h = max(0.0, min(im["bottom"], page.height) - max(im["top"], 0))
        cov += w * h
    return cov / area


def _row_centers(col, thr, max_band):
    """Visine (px) tamnih vodoravnih crta u jednoj pruzi."""
    centers, start = [], None
    for y, v in enumerate(col + [0]):
        if v >= thr:
            if start is None:
                start = y
        elif start is not None:
            if y - start <= max_band:
                centers.append((start + y - 1) / 2)
            start = None
    return centers


def raster_staves(page, dpi=RASTER_DPI):
    """Crtovlja nađena na slici stranice (u pt, kao vector_staves)."""
    img = page.to_image(resolution=dpi).original.convert("L")
    w, h = img.size
    bw = img.point(lambda p: 255 if p < 140 else 0)
    scale = 72.0 / dpi
    lo, hi = SPACING[0] / scale, SPACING[1] / scale
    per_strip = []
    sw = w // RASTER_STRIPS
    for k in range(RASTER_STRIPS):
        strip = bw.crop((k * sw, 0, (k + 1) * sw, h)).resize((1, h), Image.BOX)
        col = list(strip.get_flattened_data() if hasattr(strip, "get_flattened_data")
                   else strip.getdata())
        # crta na skenu često pada između dva retka točaka: zbroji sa susjednim
        col = [min(255, v + max(col[y - 1] if y else 0, col[y + 1] if y + 1 < h else 0))
               for y, v in enumerate(col)]
        centers = _row_centers(col, 0.5 * 255, max_band=max(5, int(1.5 / scale)))
        per_strip.append((k, _runs(centers, lo, hi, tol=0.2, gaps=1)))
    # crtovlje mora biti nađeno u barem 2 pruge
    staves = []
    for k, runs in per_strip:
        for top, bottom, sp in runs:
            others = [r for kk, rr in per_strip if kk != k for r in rr
                      if abs(r[0] - top) <= 1.5 * sp and abs(r[2] - sp) <= 0.2 * sp]
            if not others:
                continue
            st = {"top": top * scale, "bottom": bottom * scale, "spacing": sp * scale,
                  "x0": k * sw * scale, "x1": (k + 2) * sw * scale}
            same = [s for s in staves if abs(s["top"] - st["top"]) <= 1.5 * st["spacing"]]
            if same:
                same[0]["x0"] = min(same[0]["x0"], st["x0"])
                same[0]["x1"] = max(same[0]["x1"], st["x1"])
            else:
                staves.append(st)
    return sorted(staves, key=lambda s: s["top"])


# ── Razvrstavanje stranice ────────────────────────────────────────────────────

def _zones(staves):
    """Pojasevi nota: crtovlje + akordi iznad + tekst (kitice) ispod."""
    return [(s["top"] - 6 * s["spacing"], s["bottom"] + 11 * s["spacing"]) for s in staves]


def _in_zones(y, zones):
    return any(a <= y <= b for a, b in zones)


def _text_rows(chars):
    rows = []
    for c in sorted(chars, key=lambda c: c["bottom"]):
        if rows and abs(rows[-1][-1]["bottom"] - c["bottom"]) <= 2.0:
            rows[-1].append(c)
        else:
            rows.append([c])
    return rows


def classify(page):
    """Vrati None (tekst) ili {"kind": "score"|"mixed", "staves", "zones", "title",
    "music_chars"}."""
    chars = page.chars
    music = sum(1 for c in chars if is_music_font(c["fontname"]))
    staves = vector_staves(page)
    if not staves and _image_cover(page) >= RASTER_MIN_IMAGE:
        try:
            staves = raster_staves(page)
        except Exception:  # noqa: BLE001 — loša slika: stranica je onda tekst
            staves = []
    if not staves and music < MIN_MUSIC_CHARS:
        return None
    if len(staves) < 2 and music < 10 and not (staves and _image_cover(page) >= RASTER_MIN_IMAGE):
        return None             # jedna slučajna "crtovlja" bez notnih znakova
    zones = _zones(staves)
    text = [c for c in chars if c["text"].strip() and not is_music_font(c["fontname"])]
    first = staves[0]["top"] if staves else page.height
    outside = [r for r in _text_rows(text)
               if r[0]["top"] > first and not _in_zones(r[0]["bottom"], zones)
               and sum(ch["text"].isalpha() for ch in r) >= 3]
    kind = "mixed" if staves and len(outside) >= OUTSIDE_ROWS else "score"
    return {"kind": kind, "staves": len(staves), "zones": zones, "music_chars": music,
            "title": header_title(page, staves) if kind == "score" else ""}


def text_only(page, info):
    """Stranica bez nota (za čitanje teksta pjesme na "mixed" stranici)."""
    zones = info["zones"]

    def keep(obj):
        if obj.get("object_type") == "char":
            if is_music_font(obj.get("fontname")):
                return False
            return not _in_zones(obj.get("bottom", 0), zones)
        return True
    return page.filter(keep)


_CID = re.compile(r"\(cid:\d+\)")


def header_title(page, staves):
    """Naslov iznad prvog crtovlja: redak najvećim slovima (ili "")."""
    limit = (staves[0]["top"] - 4 * staves[0]["spacing"]) if staves else page.height * 0.3
    chars = [c for c in page.chars if not is_music_font(c["fontname"])]
    visible = [c for c in chars if c["text"].strip()]
    if not visible or limit <= 0:
        return ""
    body = statistics.median(c["size"] for c in visible)
    best = None
    for row in _text_rows([c for c in chars if c["bottom"] <= limit]):
        row.sort(key=lambda c: c["x0"])
        size = statistics.median(c["size"] for c in row)
        text, prev = "", None
        for c in row:
            if prev is not None and c["x0"] - prev["x1"] > 0.15 * c["size"]:
                text += " "
            text += c["text"]
            prev = c
        text = " ".join(_CID.sub("", text).split())
        if sum(ch.isalpha() for ch in text) < 3:
            continue
        if best is None or size > best[0] + 0.5:
            best = (size, text)
    if best and best[0] >= 1.2 * body:
        return best[1]
    return ""


# ── Naslov iz imena datoteke ──────────────────────────────────────────────────

_NOISE = re.compile(
    r"\b(full ?score|score|partitura|parts?|note|notni zapis|sheet ?music|satb|ssa|ttbb|"
    r"zbor|choir|klavir|piano|orgulje|organ|final|v\d+)\b", re.IGNORECASE)


def title_from_filename(name):
    """'Isus_Najljepše_ime_-_Full_Score.pdf' -> 'Isus Najljepše ime'."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name or "")
    stem = re.sub(r"^[0-9a-f]{8}-", "", stem)              # prefiks preglednika
    stem = unicodedata.normalize("NFC", stem.replace("_", " "))
    stem = _NOISE.sub(" ", stem)
    stem = re.sub(r"[\s\-–—.,()]+$", "", re.sub(r"^[\s\-–—.,()\d]+", "", stem))
    stem = re.sub(r"\s+-\s*$", "", stem)
    return " ".join(stem.split())


# ── Cijeli PDF ────────────────────────────────────────────────────────────────

def group_pages(found):
    """{broj stranice: classify()} -> komadi nota [{"pages": [..], "title", "kind"}].

    Uzastopne notne stranice su jedan komad; novi komad počinje notnom
    stranicom koja ima svoj naslov. "mixed" stranica je uvijek svoj komad."""
    groups = []
    for pno in sorted(found):
        info = found[pno]
        prev = groups[-1] if groups else None
        if (info["kind"] == "score" and prev and prev["kind"] == "score"
                and prev["pages"][-1] == pno - 1 and not info["title"]):
            prev["pages"].append(pno)
            continue
        groups.append({"pages": [pno], "title": info["title"], "kind": info["kind"]})
    return groups


def extract_pages(data, pages):
    """Novi PDF samo s danim stranicama (1-based)."""
    import io

    import pypdfium2 as pdfium

    src = pdfium.PdfDocument(data)
    try:
        dst = pdfium.PdfDocument.new()
        try:
            dst.import_pages(src, [p - 1 for p in pages])
            buf = io.BytesIO()
            dst.save(buf)
            return buf.getvalue()
        finally:
            dst.close()
    finally:
        src.close()
