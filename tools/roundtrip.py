#!/usr/bin/env python3
"""
Dokaz točnosti baze: PDF -> pjesme (parser) -> JSON -> ponovno iscrtani PDF
-> usporedba s izvornim, glif po glif (+ opcionalno piksel po piksel).

    python3 tools/roundtrip.py izvor.pdf [izvor2.pdf ...] [--png DIR]

--png DIR: uz PyMuPDF spremi slike izvornika, rekreacije i razlike.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import chord_compare  # noqa: E402
import chord_extract  # noqa: E402
import chord_render  # noqa: E402


def pixel_diff(src, out, png_dir, stem, dpi=110):
    import pymupdf
    a, b = pymupdf.open(stream=src), pymupdf.open(stream=out)
    res = []
    for i in range(min(len(a), len(b))):
        pa = a[i].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        pb = b[i].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        sa, sb = pa.samples, pb.samples
        n = min(len(sa), len(sb))
        diff = sum(1 for k in range(n) if abs(sa[k] - sb[k]) > 64)
        ink = sum(1 for k in range(n) if sa[k] < 128) or 1
        res.append((i + 1, diff, ink))
        if png_dir:
            os.makedirs(png_dir, exist_ok=True)
            pa.save(os.path.join(png_dir, f"{stem}_p{i+1}_izvor.png"))
            pb.save(os.path.join(png_dir, f"{stem}_p{i+1}_baza.png"))
    return res


def main(argv):
    png_dir = None
    if "--png" in argv:
        k = argv.index("--png")
        png_dir = argv[k + 1]
        argv = argv[:k] + argv[k + 2:]
    import pymupdf
    total = ok = 0
    for path in argv:
        with open(path, "rb") as fh:
            src = fh.read()
        res = chord_extract.extract(src, os.path.basename(path))
        # kroz JSON, točno kako bi bilo spremljeno u bazu
        songs = json.loads(json.dumps(res["songs"], ensure_ascii=False))
        stem = os.path.splitext(os.path.basename(path))[0]
        print(f"== {os.path.basename(path)}: {len(songs)} pjesama")
        for si, song in enumerate(songs, 1):
            d = pymupdf.open(stream=src)
            d.select([p - 1 for p in song["source"]["pages"]])
            one = d.tobytes()
            out = chord_render.render_pdf([song], first_page=song["source"]["pages"][0])
            rep = chord_compare.compare(one, out)
            total += 1
            ok += rep["identical"]
            note = ""
            if (song.get("layout") or {}).get("columns", 1) > 1:
                note = "  [izvornik u više stupaca]"
            print(f"  {si:2}. {song['title'][:40]:40} " +
                  chord_compare.summary(rep).replace("\n", "\n      ") + note)
            try:
                for pno, diff, ink in pixel_diff(one, out, png_dir, f"{stem}_{si}"):
                    print(f"      piksel str. {pno}: različitih {diff} ({100*diff/ink:.2f} % tinte)")
            except ImportError:
                pass
    print(f"UKUPNO: {ok}/{total} pjesama identično")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
