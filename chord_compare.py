#!/usr/bin/env python3
"""
Usporedba izvornog i rekreiranog PDF-a pjesme, glif po glif:
isti znak, isti rez (bold/regular), ista veličina, isti položaj (x i osnovna
linija) unutar tolerancije. Ako se sve poklapa, PDF-ovi su sadržajno
identični — tekst i akordi stoje na istim mjestima.
"""

import io

import pdfplumber

from chord_extract import _glyphs


def _kind(g):
    f = g["font"]
    if f.startswith("OpenSans"):
        return "B" if g["bold"] else "R"
    return f.split("-")[0]


def page_glyphs(data):
    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            out.append([(g["text"], _kind(g), g["size"], g["x"], g["baseline"])
                        for g in _glyphs(page)])
    return out


def compare(src, out, tol=0.02):
    """Vrati izvještaj: po stranicama broj poklopljenih/nedostajućih/viška glifova."""
    a_pages, b_pages = page_glyphs(src), page_glyphs(out)
    report = {"pages": [], "identical": len(a_pages) == len(b_pages),
              "max_dx": 0.0, "max_dy": 0.0, "glyphs": 0, "matched": 0}
    for pi in range(max(len(a_pages), len(b_pages))):
        a = a_pages[pi] if pi < len(a_pages) else []
        b = list(b_pages[pi]) if pi < len(b_pages) else []
        used = [False] * len(b)
        missing = []
        matched = 0
        for g in a:
            best, bd = None, None
            for j, h in enumerate(b):
                if used[j] or h[0] != g[0] or h[1] != g[1] or abs(h[2] - g[2]) > 0.05:
                    continue
                d = max(abs(h[3] - g[3]), abs(h[4] - g[4]))
                if d <= tol and (bd is None or d < bd):
                    best, bd = j, d
            if best is None:
                missing.append(g)
            else:
                used[best] = True
                matched += 1
                h = b[best]
                report["max_dx"] = max(report["max_dx"], abs(h[3] - g[3]))
                report["max_dy"] = max(report["max_dy"], abs(h[4] - g[4]))
        extra = [h for j, h in enumerate(b) if not used[j]]
        report["pages"].append({"page": pi + 1, "glyphs": len(a), "matched": matched,
                                "missing": missing[:20], "extra": extra[:20],
                                "n_missing": len(missing), "n_extra": len(extra)})
        report["glyphs"] += len(a)
        report["matched"] += matched
        if missing or extra:
            report["identical"] = False
    return report


def summary(report):
    lines = [f"{'IDENTIČNO' if report['identical'] else 'RAZLIKE'}: "
             f"{report['matched']}/{report['glyphs']} glifova, "
             f"max odstupanje x {report['max_dx']:.4f} pt, y {report['max_dy']:.4f} pt"]
    for p in report["pages"]:
        if p["n_missing"] or p["n_extra"]:
            lines.append(f"  str. {p['page']}: nedostaje {p['n_missing']}, višak {p['n_extra']}")
            for g in p["missing"][:6]:
                lines.append(f"    - {g[0]!r} {g[1]} {g[2]} x={g[3]:.3f} y={g[4]:.3f}")
            for g in p["extra"][:6]:
                lines.append(f"    + {g[0]!r} {g[1]} {g[2]} x={g[3]:.3f} y={g[4]:.3f}")
    return "\n".join(lines)
