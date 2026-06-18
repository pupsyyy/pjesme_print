#!/usr/bin/env python3
"""
Konvertira PDF s tekstovima pjesama u Word i/ili PDF dokument.

Format izlaza:
  - A4 landscape, dvije kolone
  - Nema naslova ni sekcija labela
  - Verse = normalni tekst
  - Chorus/Bridge = uvučen, bold+italic
  - Između pjesama = linija razdjelnika (______...)
  - Times New Roman 10pt

Korištenje:
    python pdf_to_word.py ulaz.pdf             # generira .docx i .pdf
    python pdf_to_word.py ulaz.pdf izlaz.docx  # samo .docx
    python pdf_to_word.py ulaz.pdf izlaz.pdf   # samo .pdf
"""

import sys
import re
from pathlib import Path

import pdfplumber
from docx import Document
from docx.shared import Pt, Cm, Twips, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm, mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    BalancedColumns,
)
from reportlab.platypus.flowables import Flowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Registracija TTF fontova (svi podržavaju hrvatska slova) ──────────────────
_FONTS = {
    "Liberation Serif": {
        "dir": "/usr/share/fonts/truetype/liberation/",
        "files": {
            "normal":     "LiberationSerif-Regular.ttf",
            "bold":       "LiberationSerif-Bold.ttf",
            "italic":     "LiberationSerif-Italic.ttf",
            "boldItalic": "LiberationSerif-BoldItalic.ttf",
        },
        "word_name": "Liberation Serif",
    },
    "Liberation Sans": {
        "dir": "/usr/share/fonts/truetype/liberation/",
        "files": {
            "normal":     "LiberationSans-Regular.ttf",
            "bold":       "LiberationSans-Bold.ttf",
            "italic":     "LiberationSans-Italic.ttf",
            "boldItalic": "LiberationSans-BoldItalic.ttf",
        },
        "word_name": "Liberation Sans",
    },
    "FreeSerif": {
        "dir": "/usr/share/fonts/truetype/freefont/",
        "files": {
            "normal":     "FreeSerif.ttf",
            "bold":       "FreeSerifBold.ttf",
            "italic":     "FreeSerifItalic.ttf",
            "boldItalic": "FreeSerifBoldItalic.ttf",
        },
        "word_name": "FreeSerif",
    },
    "FreeSans": {
        "dir": "/usr/share/fonts/truetype/freefont/",
        "files": {
            "normal":     "FreeSans.ttf",
            "bold":       "FreeSansBold.ttf",
            "italic":     "FreeSansOblique.ttf",
            "boldItalic": "FreeSansBoldOblique.ttf",
        },
        "word_name": "FreeSans",
    },
}
FONT_CHOICES = list(_FONTS.keys())

def _register_font(name):
    info = _FONTS[name]
    d = info["dir"]
    f = info["files"]
    pdfmetrics.registerFont(TTFont(name,               d + f["normal"]))
    pdfmetrics.registerFont(TTFont(name + "-Bold",     d + f["bold"]))
    pdfmetrics.registerFont(TTFont(name + "-Italic",   d + f["italic"]))
    pdfmetrics.registerFont(TTFont(name + "-BoldItalic", d + f["boldItalic"]))
    pdfmetrics.registerFontFamily(
        name,
        normal=name,
        bold=name + "-Bold",
        italic=name + "-Italic",
        boldItalic=name + "-BoldItalic",
    )

_registered = set()
def ensure_font(name):
    if name not in _registered:
        _register_font(name)
        _registered.add(name)

# Registriraj default font odmah
ensure_font("Liberation Serif")


# Regex za jedan akord (hrvatska/njemačka notacija)
# Primjeri: D, A, Fis, H, Cis, Es, Dm, Am7, G/B, Dsus4, Hm, Bb
_CHORD_TOKEN = re.compile(
    r"^[CDEFGAHB]"          # osnovna nota
    r"(IS|ES|is|es|#|b)?"   # povisilica/snizilica
    r"(m|mol|maj|min|dim|aug|sus|add)?"  # kvaliteta
    r"\d*"                  # broj (7, 9, 11...)
    r"(sus\d*|add\d*)?"     # sus/add sufiks
    r"(/[CDEFGAHB](IS|ES|is|es|#|b)?)?$",  # slash akord
    re.IGNORECASE,
)

def is_chord_line(line: str) -> bool:
    """Vraća True ako linija sadrži samo akorde (bez normalnih riječi)."""
    tokens = line.split()
    if not tokens:
        return False
    # Linija mora imati barem jedan token i svi moraju biti akordi
    # Dodatna provjera: ako je token dulji od 7 znakova, vjerojatno nije akord
    return all(
        bool(_CHORD_TOKEN.match(t)) and len(t) <= 7
        for t in tokens
    )


DEFAULT_FONT = "Liberation Serif"
DEFAULT_FONT_SIZE = 9
DEFAULT_N_COLS = 3

CHORUS_INDENT = Twips(720)

DIVIDER = "_" * 35

# Sekcije koje tretiramo kao CHORUS (uvučene, bold+italic)
CHORUS_TYPES = re.compile(
    r"^(Chorus\s*\d*|C\d*|Bridge\s*\d*|B\d*|BRIDGE|Pre-?Chorus|Tag|Outro)\s*(\(.*\))?$",
    re.IGNORECASE,
)
# Sekcije koje tretiramo kao VERSE (normalne)
VERSE_TYPES = re.compile(
    r"^(Verse\s*\d*|V\d+\s*(\(.*\))?|Intro|verse)\s*$",
    re.IGNORECASE,
)
# Bilo koja sekcija labela (da je prepoznamo i preskočimo)
ANY_SECTION = re.compile(
    r"^(Chorus\s*\d*|Verse\s*\d*|Bridge\s*\d*|Intro|Outro|Pre-?Chorus|Tag|"
    r"V\d+\s*(\(.*\))?|C\d*|B\d*|BRIDGE)\s*(\(.*\))?$",
    re.IGNORECASE,
)


# ── Pomocne XML funkcije ───────────────────────────────────────────────────────

def set_columns(doc, n_cols=DEFAULT_N_COLS):
    """Postavi kolone na A4 landscape."""
    section = doc.sections[0]
    section.page_width = Cm(29.7)
    section.page_height = Cm(21.0)
    section.left_margin = Cm(0.5)
    section.right_margin = Cm(0.5)
    section.top_margin = Cm(0.5)
    section.bottom_margin = Cm(0.5)

    sectPr = section._sectPr
    cols = OxmlElement("w:cols")
    cols.set(qn("w:num"), str(n_cols))
    cols.set(qn("w:space"), "720")
    cols.set(qn("w:equalWidth"), "1")
    sectPr.append(cols)


def set_para_spacing(para, before=0, after=0):
    pPr = para._p.get_or_add_pPr()
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:before"), str(before))
    spacing.set(qn("w:after"), str(after))
    pPr.append(spacing)


def add_run(para, text, bold=False, italic=False, size=DEFAULT_FONT_SIZE, font=DEFAULT_FONT):
    run = para.add_run(text)
    run.font.name = _FONTS[font]["word_name"]
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = RGBColor(0, 0, 0)
    return run


def verse_para(doc, text, font_size=DEFAULT_FONT_SIZE, font=DEFAULT_FONT):
    para = doc.add_paragraph()
    set_para_spacing(para, before=0, after=0)
    para.paragraph_format.left_indent = None
    para.paragraph_format.first_line_indent = None
    add_run(para, text, bold=False, italic=False, size=font_size, font=font)
    return para


def chorus_para(doc, text, font_size=DEFAULT_FONT_SIZE, font=DEFAULT_FONT):
    para = doc.add_paragraph()
    set_para_spacing(para, before=0, after=0)
    para.paragraph_format.left_indent = CHORUS_INDENT
    add_run(para, text, bold=True, italic=True, size=font_size, font=font)
    return para


def divider_para(doc, font_size=DEFAULT_FONT_SIZE, font=DEFAULT_FONT):
    para = doc.add_paragraph()
    set_para_spacing(para, before=60, after=60)
    add_run(para, DIVIDER, bold=False, italic=False, size=font_size, font=font)
    return para


def empty_para(doc):
    para = doc.add_paragraph()
    set_para_spacing(para, before=0, after=0)
    add_run(para, "")
    return para


# ── Parsiranje PDF stranice ────────────────────────────────────────────────────

def parse_page(page):
    """
    Vraća listu blokova:
      [("verse", ["linija", ...]), ("chorus", ["linija", ...]), ...]
    Preskačemo naslov, key, pjesmarica meta-info.
    """
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    if not words:
        return None

    page_width = page.width
    right_threshold = page_width * 0.55

    # Grupiraj u retke
    lines_by_y = {}
    for w in words:
        y = round(w["top"] / 3) * 3
        lines_by_y.setdefault(y, []).append(w)

    sorted_ys = sorted(lines_by_y.keys())
    raw_lines = []
    for y in sorted_ys:
        row_words = sorted(lines_by_y[y], key=lambda w: w["x0"])
        left_words = [w for w in row_words if w["x0"] < right_threshold]
        text_left = " ".join(w["text"] for w in left_words).strip()
        raw_lines.append(text_left)

    if not raw_lines:
        return None

    # Preskoci naslov (prvi neprazni red) i opcionalni podnaslov
    idx = 0
    while idx < len(raw_lines) and not raw_lines[idx]:
        idx += 1
    if idx >= len(raw_lines):
        return None
    idx += 1  # preskoči naslov

    # Preskoči podnaslov ako ne počinje s "Pjesmarica" i nije sekcija
    if idx < len(raw_lines):
        line = raw_lines[idx]
        if line and not line.startswith("Pjesmarica") and not ANY_SECTION.match(line):
            idx += 1

    # Preskoči Pjesmarica blok
    if idx < len(raw_lines) and raw_lines[idx].startswith("Pjesmarica"):
        idx += 1
        while idx < len(raw_lines):
            line = raw_lines[idx]
            if not line or ANY_SECTION.match(line):
                break
            if re.match(r"^[\d\s\-/]+$", line) or re.match(r"^P\d", line):
                idx += 1
            else:
                break

    # Preskoči napomene (tekst prije prve sekcije)
    # – ako se pojave redovi koji izgledaju kao upute a ne sekcija labels
    first_section_found = False
    notes_end = idx
    for j in range(idx, len(raw_lines)):
        if ANY_SECTION.match(raw_lines[j] or ""):
            first_section_found = True
            notes_end = j
            break
    if not first_section_found:
        notes_end = idx  # nema sekcije – počinjemo odmah

    idx = notes_end

    # Parsiraj sekcije
    blocks = []  # list of (type, lines)
    current_type = "verse"
    current_lines = []

    while idx < len(raw_lines):
        line = raw_lines[idx]
        idx += 1

        if not line:
            if current_lines:
                current_lines.append("")
            continue

        if is_chord_line(line):
            continue  # preskoči redak s akordima

        if ANY_SECTION.match(line):
            # Spremi prethodni blok
            if current_lines:
                while current_lines and current_lines[-1] == "":
                    current_lines.pop()
                if current_lines:
                    blocks.append((current_type, current_lines))
                current_lines = []
            # Odredi novi tip
            if CHORUS_TYPES.match(line):
                current_type = "chorus"
            else:
                current_type = "verse"
        else:
            current_lines.append(line)

    if current_lines:
        while current_lines and current_lines[-1] == "":
            current_lines.pop()
        if current_lines:
            blocks.append((current_type, current_lines))

    return blocks


# ── Pisanje pjesme u dokument ──────────────────────────────────────────────────

def write_song(doc, blocks, first=False, font_size=DEFAULT_FONT_SIZE, font=DEFAULT_FONT):
    if not first:
        divider_para(doc, font_size, font)

    for btype, lines in blocks:
        for line in lines:
            if line == "":
                pass
            elif btype == "chorus":
                chorus_para(doc, line, font_size, font)
            else:
                verse_para(doc, line, font_size, font)


# ── Postavljanje dokumenta ────────────────────────────────────────────────────

def setup_document(font_size=DEFAULT_FONT_SIZE, n_cols=DEFAULT_N_COLS, font=DEFAULT_FONT):
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = _FONTS[font]["word_name"]
    style.font.size = Pt(font_size)
    style.paragraph_format.space_before = Pt(0)
    style.paragraph_format.space_after = Pt(0)

    set_columns(doc, n_cols)
    return doc


# ── PDF generiranje (reportlab) ────────────────────────────────────────────────

def make_pdf_styles(font_size=DEFAULT_FONT_SIZE, font=DEFAULT_FONT):
    ensure_font(font)
    verse = ParagraphStyle(
        "verse",
        fontName=font,
        fontSize=font_size,
        leading=font_size * 1.2,
        leftIndent=0,
        spaceAfter=0,
        spaceBefore=0,
    )
    chorus = ParagraphStyle(
        "chorus",
        fontName=font + "-BoldItalic",
        fontSize=font_size,
        leading=font_size * 1.2,
        leftIndent=18,
        spaceAfter=0,
        spaceBefore=0,
    )
    divider = ParagraphStyle(
        "divider",
        fontName=font,
        fontSize=font_size,
        leading=font_size * 1.4,
        spaceAfter=2,
        spaceBefore=2,
    )
    return verse, chorus, divider


def songs_to_flowables(songs, font_size=DEFAULT_FONT_SIZE, font=DEFAULT_FONT):
    style_verse, style_chorus, style_divider = make_pdf_styles(font_size, font)
    story = []
    for song_idx, blocks in enumerate(songs):
        if song_idx > 0:
            story.append(Paragraph(DIVIDER, style_divider))

        for btype, lines in blocks:
            style = style_chorus if btype == "chorus" else style_verse
            for line in lines:
                if line and line != "":
                    safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    story.append(Paragraph(safe, style))

    return story


def convert_pdf_to_pdf(songs, pdf_out_path: str, font_size=DEFAULT_FONT_SIZE, n_cols=DEFAULT_N_COLS, font=DEFAULT_FONT):
    """Generiraj PDF u A4 landscape s kolonama."""
    margin = 0.5 * cm
    col_gap = 1.0 * cm

    doc = SimpleDocTemplate(
        pdf_out_path,
        pagesize=landscape(A4),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
    )

    story_inner = songs_to_flowables(songs, font_size, font)

    cols = BalancedColumns(
        story_inner,
        nCols=n_cols,
        needed=1 * cm,
        spaceBefore=0,
        spaceAfter=0,
        leftPadding=0,
        rightPadding=col_gap / 2,
    )

    doc.build([cols])
    print(f"Spremi kao: {pdf_out_path}")


# ── Parsiranje PDF-a ───────────────────────────────────────────────────────────

def load_songs(pdf_path: str):
    songs = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            blocks = parse_page(page)
            if blocks:
                songs.append(blocks)
                print(f"  Stranica {page_num}: OK ({len(blocks)} blok(ova))")
            else:
                print(f"  Stranica {page_num}: (preskočena)")
    return songs


# ── Glavni program ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    pdf_in = sys.argv[1]
    out_arg = sys.argv[2] if len(sys.argv) >= 3 else None

    print(f"Učitavam: {pdf_in}")
    songs = load_songs(pdf_in)

    if not songs:
        print("Nije pronađena nijedna pjesma!")
        sys.exit(1)

    print(f"Ukupno pjesama: {len(songs)}\n")

    if out_arg:
        # Eksplicitni izlaz
        if out_arg.endswith(".pdf"):
            convert_pdf_to_pdf(songs, out_arg)
        else:
            # Word
            doc = setup_document()
            for idx, blocks in enumerate(songs):
                write_song(doc, blocks, first=(idx == 0))
            doc.save(out_arg)
            print(f"Spremi kao: {out_arg}")
    else:
        # Bez argumenta → generiraj i .docx i .pdf
        base = str(Path(pdf_in).with_suffix(""))
        docx_out = base + "_out.docx"
        pdf_out = base + "_out.pdf"

        doc = setup_document()
        for idx, blocks in enumerate(songs):
            write_song(doc, blocks, first=(idx == 0))
        doc.save(docx_out)
        print(f"Spremi kao: {docx_out}")

        convert_pdf_to_pdf(songs, pdf_out)


if __name__ == "__main__":
    main()
