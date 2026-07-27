#!/usr/bin/env python3
"""
PDF izvoz pjesmarice (reportlab), dva izgleda:
  - kompaktni: A4 ležeće, 2-3 balansirana stupca, bez naslova, refren uvučen
    bold+kurziv, crta između pjesama (original "Pjesmarica konverter")
  - klasični: A4 uspravno, naslov + tonalitet desno, boldane labele sekcija,
    stranica po pjesmi
"""

import html
import os
from io import BytesIO

from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BalancedColumns, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from pdf_to_word import CHORUS_TYPES, DIVIDER, FONT_CHOICES
from watermark import faded_logo_png, normalize_level


def _watermark_drawer(style, page_w, page_h, width_frac):
    """Vrati onPage funkciju koja crta logo centrirano iza teksta, ili None."""
    if not style.get("watermark"):
        return None
    png = faded_logo_png(normalize_level(style.get("watermark_level")))
    reader = ImageReader(BytesIO(png))
    iw, ih = reader.getSize()
    w = page_w * width_frac
    h = w * ih / iw
    x = (page_w - w) / 2
    y = (page_h - h) / 2

    def draw(canvas, _doc):
        canvas.saveState()
        canvas.drawImage(reader, x, y, w, h, mask="auto",
                         preserveAspectRatio=True)
        canvas.restoreState()

    return draw

# Moguće lokacije fontova (Linux distribucije se razlikuju)
_FONT_SEARCH_DIRS = [
    "/usr/share/fonts/truetype/liberation/",
    "/usr/share/fonts/liberation/",
    "/usr/share/fonts/truetype/freefont/",
    "/usr/share/fonts/freefont/",
    "/usr/share/fonts/truetype/dejavu/",
    "/usr/share/fonts/truetype/",
    "/usr/share/fonts/",
]

# name -> datoteke za normal/bold/italic/boldItalic
_FONT_FILES = {
    "Liberation Serif": ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf",
                         "LiberationSerif-Italic.ttf", "LiberationSerif-BoldItalic.ttf"),
    "Liberation Sans": ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf",
                        "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
    "FreeSerif": ("FreeSerif.ttf", "FreeSerifBold.ttf",
                  "FreeSerifItalic.ttf", "FreeSerifBoldItalic.ttf"),
    "FreeSans": ("FreeSans.ttf", "FreeSansBold.ttf",
                 "FreeSansOblique.ttf", "FreeSansBoldOblique.ttf"),
    # rezervni izbor ako Liberation/Free nisu instalirani
    "DejaVu Serif": ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf",
                     "DejaVuSerif-Italic.ttf", "DejaVuSerif-BoldItalic.ttf"),
    "DejaVu Sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf",
                    "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
}

_registered = {}


def _find_font_file(filename):
    for d in _FONT_SEARCH_DIRS:
        path = os.path.join(d, filename)
        if os.path.exists(path):
            return path
    return None


def _register(name):
    """Registrira obitelj fontova; vraća bazno ime ili None ako nema datoteka."""
    files = _FONT_FILES[name]
    paths = [_find_font_file(f) for f in files]
    if not all(paths):
        return None
    pdfmetrics.registerFont(TTFont(name, paths[0]))
    pdfmetrics.registerFont(TTFont(name + "-Bold", paths[1]))
    pdfmetrics.registerFont(TTFont(name + "-Italic", paths[2]))
    pdfmetrics.registerFont(TTFont(name + "-BoldItalic", paths[3]))
    pdfmetrics.registerFontFamily(name, normal=name, bold=name + "-Bold",
                                  italic=name + "-Italic",
                                  boldItalic=name + "-BoldItalic")
    return name


def ensure_font(name):
    """Vrati (normal, bold, boldItalic) imena fontova, s fallbackom."""
    if name in _registered:
        return _registered[name]
    result = None
    if name in _FONT_FILES:
        base = _register(name)
        if base:
            result = (base, base + "-Bold", base + "-BoldItalic")
    if result is None:
        # fallback: DejaVu iste "obitelji", pa standardni PDF fontovi
        fallback = "DejaVu Serif" if "Serif" in name else "DejaVu Sans"
        if fallback != name and _register(fallback):
            result = (fallback, fallback + "-Bold", fallback + "-BoldItalic")
        else:
            std = ("Times-Roman", "Times-Bold", "Times-BoldItalic") \
                if "Serif" in name else \
                ("Helvetica", "Helvetica-Bold", "Helvetica-BoldOblique")
            result = std
    _registered[name] = result
    return result


def _esc(text):
    return html.escape(text)


# ── Kompaktni izgled (original konverter) ─────────────────────────────────────

def build_pdf_compact(songs, style):
    """A4 ležeće, balansirani stupci, bez naslova; vraća bytes."""
    from pdf_to_word import song_compact_blocks

    font_reg, font_bold, font_bi = ensure_font(style["font"])
    size = style["body_size"]
    margin = style["margin_cm"] * cm
    col_gap = 1.0 * cm

    st_verse = ParagraphStyle("verse", fontName=font_reg, fontSize=size,
                              leading=size * 1.2)
    st_chorus = ParagraphStyle("chorus", fontName=font_bi, fontSize=size,
                               leading=size * 1.2, leftIndent=18)
    st_divider = ParagraphStyle("divider", fontName=font_reg, fontSize=size,
                                leading=size * 1.4, spaceBefore=2, spaceAfter=2)

    story = []
    first = True
    for song in songs:
        if not first:
            story.append(Paragraph(DIVIDER, st_divider))
        first = False
        for chorus, line in song_compact_blocks(song):
            story.append(Paragraph(_esc(line), st_chorus if chorus else st_verse))

    buf = BytesIO()
    page_w, page_h = landscape(A4)
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin,
        title="Pjesmarica", author="Pjesmarica konverter")
    cols = BalancedColumns(
        story, nCols=style["n_cols"], needed=1 * cm,
        spaceBefore=0, spaceAfter=0, leftPadding=0, rightPadding=col_gap / 2)
    wm = _watermark_drawer(style, page_w, page_h, width_frac=0.42)
    doc.build([cols], onFirstPage=wm, onLaterPages=wm) if wm else doc.build([cols])
    return buf.getvalue()


# ── Klasični izgled (naslov + tonalitet, stranica po pjesmi) ──────────────────

def build_pdf(songs, style):
    """A4 uspravno s naslovima; vraća bytes."""
    font_reg, font_bold, _bi = ensure_font(style["font"])

    title_size = style["title_size"]
    body_size = style["body_size"]
    margin = style["margin_cm"] * cm
    line_h = body_size * 1.25

    st_title = ParagraphStyle("title", fontName=font_bold, fontSize=title_size,
                              leading=title_size * 1.12)
    st_key = ParagraphStyle("key", fontName=font_reg, fontSize=body_size,
                            leading=line_h, alignment=TA_RIGHT)
    st_body = ParagraphStyle("body", fontName=font_reg, fontSize=body_size,
                             leading=line_h)
    st_label = ParagraphStyle("label", fontName=font_bold, fontSize=body_size,
                              leading=line_h)

    page_w = A4[0]
    avail_w = page_w - 2 * margin

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin,
        title="Pjesmarica", author="Pjesmarica konverter")

    def blank():
        return Spacer(1, line_h)

    wm = _watermark_drawer(style, A4[0], A4[1], width_frac=0.58)

    story = []
    for idx, song in enumerate(songs):
        if idx > 0:
            if style["page_break"]:
                story.append(PageBreak())
            else:
                story.append(Spacer(1, line_h * 2))

        # Naslov + tonalitet (desno)
        title_par = Paragraph(_esc(song["title"]), st_title)
        key_text = (song.get("key") or "").strip()
        if key_text:
            key_par = Paragraph(_esc(key_text).replace("\n", "<br/>"), st_key)
            row = Table([[title_par, key_par]],
                        colWidths=[avail_w * 0.70, avail_w * 0.30])
            row.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(row)
        else:
            story.append(title_par)
            story.append(Spacer(1, 4))

        if song.get("subtitle"):
            story.append(Paragraph(_esc(song["subtitle"]), st_body))

        if song.get("pjesmarica"):
            story.append(blank())
            story.append(Paragraph("Pjesmarica", st_label))
            for val in song["pjesmarica"]:
                story.append(Paragraph(_esc(val), st_body))

        if song.get("notes"):
            story.append(blank())
            for note in song["notes"]:
                if note == "":
                    story.append(blank())
                else:
                    story.append(Paragraph(_esc(note), st_body))

        for label, lines in song.get("sections", []):
            story.append(blank())
            if label:
                story.append(Paragraph(_esc(label), st_label))
            for line in lines:
                if line == "":
                    story.append(blank())
                else:
                    story.append(Paragraph(_esc(line), st_body))

    doc.build(story, onFirstPage=wm, onLaterPages=wm) if wm else doc.build(story)
    return buf.getvalue()
