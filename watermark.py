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


@lru_cache(maxsize=4)
def faded_logo_png(level: str) -> bytes:
    """Vrati PNG (bytes) blijede sive verzije logotipa za zadanu razinu."""
    gray, factor = LEVELS[normalize_level(level)]
    im = Image.open(LOGO_PATH).convert("RGBA")
    alpha = im.getchannel("A").point(lambda v: int(v * factor))
    out = Image.new("RGBA", im.size, gray + (0,))
    out.putalpha(alpha)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


@lru_cache(maxsize=1)
def logo_size():
    """(širina, visina) originalnog logotipa u pikselima."""
    with Image.open(LOGO_PATH) as im:
        return im.size


def add_word_watermark(doc, level, width_frac):
    """Dodaj logo kao centrirani vodeni žig iza teksta (VML u zaglavlju).

    Isti mehanizam koji Word koristi za slikovni watermark — pojavljuje se
    iza teksta na svakoj stranici.
    """
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    png = faded_logo_png(normalize_level(level))
    iw, ih = logo_size()

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
