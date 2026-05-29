#!/usr/bin/env python3
"""
Konvertira PDF s tekstovima pjesama u Word dokument.

Format izlaza:
  - A4 landscape, dvije kolone
  - Nema naslova ni sekcija labela
  - Verse = normalni tekst
  - Chorus/Bridge = uvučen, bold+italic
  - Između pjesama = linija razdjelnika (______...)
  - Times New Roman 10pt

Korištenje:
    python pdf_to_word.py ulaz.pdf izlaz.docx
    python pdf_to_word.py ulaz.pdf
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


FONT_NAME = "Times New Roman"
FONT_SIZE = 8

CHORUS_INDENT = Twips(720)   # ~1.27 cm uvlaka za refren

DIVIDER = "_" * 60

# Sekcije koje tretiramo kao CHORUS (uvučene, bold+italic)
CHORUS_TYPES = re.compile(
    r"^(Chorus|C\d*|Bridge|B\d*|BRIDGE|Pre-?Chorus|Tag|Outro)\s*(\(.*\))?$",
    re.IGNORECASE,
)
# Sekcije koje tretiramo kao VERSE (normalne)
VERSE_TYPES = re.compile(
    r"^(Verse\s*\d*|V\d+\s*(\(.*\))?|Intro|verse)\s*$",
    re.IGNORECASE,
)
# Bilo koja sekcija labela (da je prepoznamo i preskočimo)
ANY_SECTION = re.compile(
    r"^(Chorus|Verse\s*\d*|Bridge|Intro|Outro|Pre-?Chorus|Tag|"
    r"V\d+\s*(\(.*\))?|C\d*|B\d*|BRIDGE)\s*$",
    re.IGNORECASE,
)


# ── Pomocne XML funkcije ───────────────────────────────────────────────────────

def set_two_columns(doc):
    """Postavi dvije kolone na A4 landscape."""
    section = doc.sections[0]
    # A4 landscape
    section.page_width = Cm(29.7)
    section.page_height = Cm(21.0)
    section.left_margin = Cm(1.5)
    section.right_margin = Cm(1.5)
    section.top_margin = Cm(1.5)
    section.bottom_margin = Cm(1.5)

    # Dvije kolone s razmakom
    sectPr = section._sectPr
    cols = OxmlElement("w:cols")
    cols.set(qn("w:num"), "2")
    cols.set(qn("w:space"), "720")  # ~1.27 cm razmak između kolona
    cols.set(qn("w:equalWidth"), "1")
    sectPr.append(cols)


def set_para_spacing(para, before=0, after=0):
    pPr = para._p.get_or_add_pPr()
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:before"), str(before))
    spacing.set(qn("w:after"), str(after))
    pPr.append(spacing)


def add_run(para, text, bold=False, italic=False, size=FONT_SIZE):
    run = para.add_run(text)
    run.font.name = FONT_NAME
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = RGBColor(0, 0, 0)
    return run


def verse_para(doc, text):
    """Normalni redak stiha."""
    para = doc.add_paragraph()
    set_para_spacing(para, before=0, after=0)
    para.paragraph_format.left_indent = None
    para.paragraph_format.first_line_indent = None
    add_run(para, text, bold=False, italic=False)
    return para


def chorus_para(doc, text):
    """Uvučeni refren – bold + italic."""
    para = doc.add_paragraph()
    set_para_spacing(para, before=0, after=0)
    para.paragraph_format.left_indent = CHORUS_INDENT
    add_run(para, text, bold=True, italic=True)
    return para


def divider_para(doc):
    """Linija razdjelnika između pjesama."""
    para = doc.add_paragraph()
    set_para_spacing(para, before=60, after=60)
    add_run(para, DIVIDER, bold=False, italic=False)
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

def write_song(doc, blocks, first=False):
    if not first:
        divider_para(doc)

    for block_idx, (btype, lines) in enumerate(blocks):
        for line in lines:
            if line == "":
                pass  # preskoči prazne retke unutar bloka
            elif btype == "chorus":
                chorus_para(doc, line)
            else:
                verse_para(doc, line)


# ── Postavljanje dokumenta ────────────────────────────────────────────────────

def setup_document():
    doc = Document()

    # Ukloni zadani razmak stila Normal
    style = doc.styles["Normal"]
    style.font.name = FONT_NAME
    style.font.size = Pt(FONT_SIZE)
    style.paragraph_format.space_before = Pt(0)
    style.paragraph_format.space_after = Pt(0)

    set_two_columns(doc)
    return doc


# ── Glavni program ─────────────────────────────────────────────────────────────

def convert_pdf_to_docx(pdf_path: str, docx_path: str):
    print(f"Učitavam: {pdf_path}")
    songs = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            blocks = parse_page(page)
            if blocks:
                songs.append(blocks)
                print(f"  Stranica {page_num}: OK ({len(blocks)} blok(ova))")
            else:
                print(f"  Stranica {page_num}: (preskočena)")

    if not songs:
        print("Nije pronađena nijedna pjesma!")
        return

    doc = setup_document()

    for idx, blocks in enumerate(songs):
        write_song(doc, blocks, first=(idx == 0))

    doc.save(docx_path)
    print(f"\nSpremi kao: {docx_path}")
    print(f"Ukupno pjesama: {len(songs)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    pdf_in = sys.argv[1]
    docx_out = sys.argv[2] if len(sys.argv) >= 3 else str(Path(pdf_in).with_suffix(".docx"))

    convert_pdf_to_docx(pdf_in, docx_out)
