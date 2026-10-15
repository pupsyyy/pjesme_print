#!/usr/bin/env python3
"""
Open Sans 1.10 (assets/fonts) — font kojim aplikacija za pjesmarice iscrtava
PDF-ove. Ovdje su točne metrike (2048 jedinica/em) za računanje položaja
akorda i za vjerno ponovno iscrtavanje.
"""

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = Path(__file__).with_name("assets") / "fonts"
REGULAR = "OpenSans110"
BOLD = "OpenSans110-Bold"

# Vertikalne metrike fonta (hhea): ascent 2189, descent 600, unitsPerEm 2048
ASCENT = 2189 / 2048
DESCENT = 600 / 2048
LINE = ASCENT + DESCENT          # prirodna visina retka (1.36182 em)
SPACE = 532 / 2048               # širina razmaka (isto za regular i bold)
MONO = 0.6                       # korak stupca za "monospace" retke akorda

_registered = False


def register():
    """Registriraj oba reza u reportlabu (jednom po procesu)."""
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont(REGULAR, str(FONT_DIR / "OpenSans-Regular.ttf")))
        pdfmetrics.registerFont(TTFont(BOLD, str(FONT_DIR / "OpenSans-Bold.ttf")))
        _registered = True
    return REGULAR, BOLD


def width(text, bold, size):
    """Točna širina teksta u pt (bez kerninga, kao izvorna aplikacija)."""
    register()
    return pdfmetrics.stringWidth(text, BOLD if bold else REGULAR, size)
