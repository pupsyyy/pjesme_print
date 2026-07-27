#!/usr/bin/env python3
"""
Vodeni žig (logo u pozadini) za PDF i Word izvoz.

Logo se čuva kao crna linijska grafika s prozirnom pozadinom
(assets/logo.png). Ovdje se pretvara u blijedu sivu verziju zadane jačine,
koja se onda crta centrirano iza teksta.
"""

import io
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image

LOGO_PATH = Path(__file__).with_name("assets") / "logo.png"

# razina jačine -> (siva boja RGB, faktor prozirnosti linija)
LEVELS = {
    "vrlo_blijedo": ((120, 120, 120), 0.10),
    "srednje": ((90, 90, 90), 0.20),
}
DEFAULT_LEVEL = "vrlo_blijedo"


def normalize_level(level):
    return level if level in LEVELS else DEFAULT_LEVEL


def _fade(alpha, level):
    """Od alpha kanala napravi blijedu sivu RGBA sliku zadane jačine (PNG bytes)."""
    gray, factor = LEVELS[normalize_level(level)]
    faded = alpha.point(lambda v: int(v * factor))
    out = Image.new("RGBA", alpha.size, gray + (0,))
    out.putalpha(faded)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


@lru_cache(maxsize=4)
def faded_logo_png(level: str) -> bytes:
    """Blijedi žig iz ugrađenog (prozirnog) logotipa — koristi njegov alpha."""
    im = Image.open(LOGO_PATH).convert("RGBA")
    return _fade(im.getchannel("A"), level)


# Maksimalna dimenzija korisničkog logotipa (zaštita)
MAX_LOGO_PX = 4000


def faded_png_from_bytes(image_bytes: bytes, level: str) -> bytes:
    """Blijedi žig iz proizvoljne slike; bijela pozadina postaje prozirna.

    Alpha se računa iz svjetline (crno = puno, bijelo = ništa), pa se linijska
    grafika na bijeloj podlozi automatski očisti od pozadine, s mekim rubovima.
    Ako slika već ima prozirnost, ona se poštuje (množi s dobivenom alfom).
    """
    im = Image.open(io.BytesIO(image_bytes))
    im.thumbnail((MAX_LOGO_PX, MAX_LOGO_PX))
    im = im.convert("RGBA")
    lum = im.convert("L")                       # svjetlina
    from_white = lum.point(lambda v: 255 - v)   # bijelo->0, crno->255
    orig_a = im.getchannel("A")
    # kombiniraj s postojećom prozirnošću (min)
    combined = Image.new("L", im.size)
    combined.putdata([min(a, b) for a, b in zip(from_white.getdata(),
                                                orig_a.getdata())])
    return _fade(combined, level)


@lru_cache(maxsize=1)
def logo_size():
    """(širina, visina) originalnog logotipa u pikselima."""
    with Image.open(LOGO_PATH) as im:
        return im.size


def add_word_watermark(doc, png, width_frac):
    """Dodaj gotovi (blijedi) PNG kao centrirani vodeni žig iza teksta.

    Isti mehanizam koji Word koristi za slikovni watermark (VML u zaglavlju) —
    pojavljuje se iza teksta na svakoj stranici.
    """
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    with Image.open(BytesIO(png)) as _im:
        iw, ih = _im.size

    section = doc.sections[0]
    w_pt = section.page_width.pt * width_frac
    h_pt = w_pt * ih / iw

    header = section.header
    header.is_linked_to_previous = False
    image_part = doc.part.package.image_parts.get_or_add_image_part(BytesIO(png))
    rId = header.part.relate_to(image_part, RT.IMAGE)

    shape = (
        '<w:p %s>'
        '<w:r><w:pict>'
        '<v:shape xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'id="PapaBandWatermark" o:spid="_x0000_s2049" type="#_x0000_t75" '
        'style="position:absolute;margin-left:0;margin-top:0;'
        'width:%.1fpt;height:%.1fpt;z-index:-251658752;'
        'mso-position-horizontal:center;mso-position-horizontal-relative:margin;'
        'mso-position-vertical:center;mso-position-vertical-relative:margin" '
        'o:allowincell="f">'
        '<v:imagedata r:id="%s" o:title="PapaBand"/>'
        '</v:shape>'
        '</w:pict></w:r></w:p>'
    ) % (nsdecls("w", "r"), w_pt, h_pt, rId)
    header._element.append(parse_xml(shape))
