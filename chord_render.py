#!/usr/bin/env python3
"""
Iscrtavanje pjesama iz baze u IZVORNOM izgledu aplikacije za pjesmarice
(A4, Open Sans 1.10, akordi iznad teksta) — služi za ispis i za dokaz da su
akordi u bazi zapisani točno: rekreirani PDF mora se poklopiti s izvornim.

Pravila rasporeda (v2 = novija verzija, v1 = starija) opisana su u
chord_extract.py i docs/BAZA_PJESAMA_NAPREDAK.md.
"""

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rl_canvas

import opensans
from chord_extract import LEFT, PROFILES

TOP = LEFT
TITLE_SIZE, KEY_SIZE, LABEL_SIZE, BODY_SIZE = 21.6, 13.8, 13.8, 12.0
BLOCK_GAP = 18.0

# Vertikalni model po profilu: (visina retka, pomak osnovne linije od vrha)
def _row_metrics(profile, kind, size):
    if profile == "v1":
        if kind == "label":
            return 19.0, 14.335
        if kind == "subtitle":
            return 16.757, opensans.ASCENT * size
        if kind == "body":
            return 16.0, 12.411
    return opensans.LINE * size, opensans.ASCENT * size


def _mid_word(text, i):
    return 0 < i < len(text) and text[i - 1].isalnum() and text[i].isalnum()


def layout_pair(text, chords, size, prof, left=LEFT):
    """Položaji akorda iznad teksta (+ crtice koje v2 ubacuje).

    Vrati (tekst_za_ispis, pomaci, [(x, akord), ...]); pomaci[i] = dodatni
    pomak (nevidljive crtice) ispred znaka i u tekstu za ispis.
    """
    text = text or ""
    chords = sorted(chords, key=lambda c: c[0])
    idx = [min(max(int(i), 0), len(text)) for i, _ in chords]
    extra = {}
    hw = opensans.width("-", False, size)

    def X(i):
        return left + opensans.width(text[:i], False, size) + \
            sum(w for j, w in extra.items() if j <= i)

    out = []
    prev_end = None
    for k, (_i, ch) in enumerate(chords):
        a = idx[k]
        x = X(a)
        if prev_end is not None:
            need = prev_end + prof["chord_gap"]
            if not prof["hyphenate"]:
                x = max(x, need)                        # v1: samo pomakni akord
            else:
                visible = _mid_word(text, a)
                n = 0
                while x < need - 1e-6 and n < 30:
                    if visible:                           # "alelu-ja"
                        text = text[:a] + "-" + text[a:]
                        extra = {(j + 1 if j >= a else j): w for j, w in extra.items()}
                        idx = [j + 1 if (j > a or (j == a and m > k)) else j
                               for m, j in enumerate(idx)]
                        a += 1
                        idx[k] = a
                    else:                                 # nevidljiva crtica
                        extra[a] = extra.get(a, 0.0) + hw
                    n += 1
                    x = X(a)
        out.append((x, ch))
        prev_end = x + opensans.width(ch, True, size)
    return text, extra, out


class _Writer:
    def __init__(self, profile, page_numbers, first_page=1):
        self.prof_name = profile if profile in PROFILES else "v2"
        self.prof = PROFILES[self.prof_name]
        self.page_numbers = page_numbers
        self.buf = BytesIO()
        self.W, self.H = A4
        self.c = rl_canvas.Canvas(self.buf, pagesize=A4, pageCompression=1)
        self.c.setTitle("Pjesmarica")
        self.c.setAuthor("Pjesmarica konverter")
        self.reg, self.bold = opensans.register()
        self.page_no = first_page - 1
        self.y = TOP
        self.left = LEFT
        self.started = False

    # ── stranice ──
    def new_page(self):
        if self.started:
            self._finish_page()
        self.started = True
        self.page_no += 1
        self.y = TOP

    def _finish_page(self):
        if self.page_numbers:
            s = str(self.page_no)
            # starija verzija: broj stranice dolje desno (Helvetica 12)
            self.c.setFont("Helvetica", 12)
            self.c.drawRightString(self.W - LEFT, self.H - 816.513, s)
        self.c.showPage()

    def ensure(self, height):
        if self.y + height > self.H - LEFT + 0.01:
            self.new_page()

    # ── crtanje ──
    def runs(self, text, x0, baseline, bold, size, extra=None):
        """Svaka riječ od točnog x (kao izvorna aplikacija)."""
        font = self.bold if bold else self.reg
        self.c.setFont(font, size)
        i = 0
        n = len(text)
        while i < n:
            if text[i] == " ":
                i += 1
                continue
            j = i
            while j < n and text[j] != " " and not (extra and j > i and j in extra):
                j += 1
            x = x0 + opensans.width(text[:i], bold, size)
            if extra:
                x += sum(w for k, w in extra.items() if k <= i)
            self.c.drawString(x, self.H - baseline, text[i:j])
            i = j

    def mono(self, tokens, baseline, bold, size, left=None):
        left = self.left if left is None else left
        font = self.bold if bold else self.reg
        self.c.setFont(font, size)
        step = opensans.MONO * size
        for col, tok in tokens:
            parts = tok.split(" ")
            x = left + col * step
            for k, p in enumerate(parts):
                if p:
                    self.c.drawString(x, self.H - baseline, p)
                x += opensans.width(p + " ", bold, size)

    def row(self, kind, size, draw):
        h, asc = _row_metrics(self.prof_name, kind, size)
        self.ensure(h)
        draw(self.y + asc)
        self.y += h

    # ── pjesma ──
    def song(self, song):
        self.new_page()
        W = self.W
        key = (song.get("key") or "").strip()
        title = song.get("title") or ""
        if key:
            ktxt = "Key: " + key
            kw = opensans.width(ktxt, False, KEY_SIZE)
            self.runs(ktxt, W - LEFT - kw, TOP + opensans.ASCENT * KEY_SIZE,
                      False, KEY_SIZE)
        for tl in _wrap(title, True, TITLE_SIZE,
                        W - 2 * LEFT - (kw + 12 if key else 0)):
            self.row("title", TITLE_SIZE,
                     lambda bl, tl=tl: self.runs(tl, LEFT, bl, True, TITLE_SIZE))
        for st in (song.get("subtitle") or "").splitlines():
            self.row("subtitle", BODY_SIZE,
                     lambda bl, st=st: self.runs(st, LEFT, bl, False, BODY_SIZE))

        blocks = [({"label": m.get("label"), "col": m.get("col", 0),
                    "lines": [{"text": v, "chords": []} for v in m.get("values") or []]})
                  for m in song.get("meta") or []]
        blocks += list(song.get("sections") or [])
        lefts = (song.get("layout") or {}).get("col_lefts") or [LEFT]
        y0 = self.y
        page0 = self.page_no
        for ci, left in enumerate(lefts):
            # izvornik u više stupaca (starija verzija): svaki stupac od vrha
            if ci:
                self.y = y0
            self.left = left
            for b in blocks:
                if b.get("col", 0) != ci and len(lefts) > 1:
                    continue
                if not b.get("attached"):
                    self.y += b.get("space", BLOCK_GAP)
                if b.get("label"):
                    self.row("label", LABEL_SIZE,
                             lambda bl, t=b["label"]: self.runs(t, self.left, bl, True, LABEL_SIZE))
                for ln in b.get("lines") or []:
                    self.line(ln)
        self.left = LEFT
        del page0

    def line(self, ln):
        size = BODY_SIZE
        text = ln.get("text") or ""
        chords = ln.get("chords") or []
        fmt = ln.get("fmt") or {}
        line_h, asc = _row_metrics(self.prof_name, "body", size)

        if "chord_text" in fmt:                       # akordi utipkani kao tekst
            self.row("body", size, lambda bl: self.runs(fmt["chord_text"], self.left, bl, False, size))
            if text:
                self.row("body", size, lambda bl: self.runs(text, self.left, bl, False, size))
            return
        if fmt.get("text") == "mono":                 # akordi + tekst u stupcima
            if chords:
                cols = fmt.get("chord_cols") or chords
                self.row("body", size, lambda bl: self.mono(cols, bl, True, size))
            if text:
                toks = [[i, t] for i, t in _tokens(text)]
                self.row("body", size, lambda bl: self.mono(toks, bl, True, size))
            return
        if fmt.get("text") == "bold":
            self.row("body", size, lambda bl: self.runs(text, self.left, bl, True, size))
            return
        if chords and not text.strip():               # samo akordi (stupci)
            self.row("body", size, lambda bl: self.mono(chords, bl, True, size))
            return
        if chords:                                    # akordi iznad teksta
            pair = self.prof["pair"]
            self.ensure(pair + line_h)
            bl = self.y + asc
            shown, extra, placed = layout_pair(text, chords, size, self.prof, self.left)
            for x, ch in placed:
                self.runs(ch, x, bl, True, size)
            self.runs(shown, self.left, bl + pair, False, size, extra)
            self.y += pair + line_h
            return
        self.row("body", size, lambda bl: self.runs(text, self.left, bl, False, size))

    def finish(self):
        if self.started:
            self._finish_page()
        self.c.save()
        return self.buf.getvalue()


def _tokens(text):
    import re
    return [(m.start(), m.group()) for m in re.finditer(r"\S+", text)]


def _wrap(text, bold, size, avail):
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w) if cur else w
        if cur and opensans.width(cand, bold, size) > avail:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    lines.append(cur)
    return lines


def render_pdf(songs, profile=None, first_page=1):
    """Pjesme -> PDF (bytes) u izvornom izgledu; svaka pjesma na novoj stranici.

    profile: "v2"/"v1" ili None = profil zapisan uz pjesmu (izvor).
    first_page: broj prve stranice (starija verzija ispisuje brojeve).
    """
    songs = list(songs)
    first = songs[0] if songs else {}
    lay = first.get("layout") or {}
    prof = profile or lay.get("profile") or "v2"
    w = _Writer(prof, bool(lay.get("page_numbers")) and prof == "v1", first_page)
    for s in songs:
        w.song(s)
    if not songs:
        w.new_page()
    return w.finish()
