#!/usr/bin/env python3
"""
Izvoz pjesmarice u PDF (reportlab), istim rasporedom kao Word izvoz:
naslov lijevo + tonalitet desno, boldane labele sekcija, stranica po pjesmi.
"""

import html
from io import BytesIO
from pathlib import Path

from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

# Kandidati za TTF fontove (Docker slika instalira liberation + dejavu).
# Ugrađeni TTF je nužan za ispravne hrvatske dijakritike u PDF-u.
_FONT_CANDIDATES = {
    "sans": [
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ],
    "serif": [
        ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
    ],
}

# Fallback na standardne PDF fontove (bez punih dijakritika) ako TTF nema
_FALLBACK = {"sans": ("Helvetica", "Helvetica-Bold"),
             "serif": ("Times-Roman", "Times-Bold")}

# Word font (iz opcija) -> obitelj za PDF
_FAMILY_BY_FONT = {"Calibri": "sans", "Arial": "sans", "Verdana": "sans",
                   "Cambria": "serif", "Times New Roman": "serif",
                   "Georgia": "serif"}

_registered = {}


def _family_fonts(family):
    """Vrati (regular, bold) ime registriranog fonta za obitelj."""
    if family in _registered:
        return _registered[family]
    result = _FALLBACK[family]
    for reg_path, bold_path in _FONT_CANDIDATES[family]:
        if Path(reg_path).exists() and Path(bold_path).exists():
            reg_name = f"Pjesme-{family}"
            bold_name = f"Pjesme-{family}-Bold"
            pdfmetrics.registerFont(TTFont(reg_name, reg_path))
            pdfmetrics.registerFont(TTFont(bold_name, bold_path))
            result = (reg_name, bold_name)
            break
    _registered[family] = result
    return result


def _esc(text):
    return html.escape(text)


def build_pdf(songs, style):
    """Složi PDF iz liste pjesama; vraća bytes."""
    family = _FAMILY_BY_FONT.get(style["font"], "sans")
    font_reg, font_bold = _family_fonts(family)

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
        title="Pjesmarica", author="Pjesme Print")

    def blank():
        return Spacer(1, line_h)

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

    doc.build(story)
    return buf.getvalue()
