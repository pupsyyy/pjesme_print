#!/usr/bin/env python3
"""
Uređivanje pjesme iz baze kao običan tekst — akordi u retku IZNAD teksta,
kao u uredniku pjesmarice (stupac akorda = slovo ispod njega):

    [Verse 1]
         G          C
    Bože svet si aleluja,

    [Chorus]
    C     a
    ...

  * [Oznaka] u svom retku = nova sekcija (Verse 1, Refren, Kitica 2...);
    prazan redak = nova sekcija bez oznake
  * redak samo s akordima iznad retka teksta = akordi tog retka
  * redak samo s akordima bez teksta ispod = redak akorda (intro, međuigra)
Metapodaci su posebno polje: "Pjesmarica: 203" po retku.

Retci koji se nisu promijenili ostaju TOČNO kakvi su bili (oblikovanje,
izvorni raspored), pa se ispis u izvornom izgledu ne mijenja bez potrebe.
"""

import copy
import re

import chord_extract
from pdf_to_word import SECTION_LABELS

_LABEL = re.compile(r"^\s*\[([^\[\]]{1,60})\]\s*$")
MAX_LINES = 1500


class EditError(ValueError):
    """Poruka za korisnika."""


_ONE = r"(?:[A-H](?:is|es|#|b)?|[a-h](?:is|es|#)?)(?:maj|min|sus|add|dim|aug|m(?!aj)|\+|\d)*"
_COMPOUND = re.compile(rf"(?:{_ONE})+(?:/[A-Ha-h](?:is|es|#|b)?)?\*?")


def _chordish(tok):
    """Akord ili više akorda spojenih bez razmaka ("AHE", "FGa", "DG"), "7"."""
    t = tok.strip("()")
    if not t:
        return False
    if chord_extract.is_chord(tok) or chord_extract.is_chord(t) or re.fullmatch(r"\d{1,2}", t):
        return True
    # spojeni akordi: barem jedno veliko slovo (inače bi i riječ "da" bila akord)
    return bool(_COMPOUND.fullmatch(t)) and any(c.isupper() for c in t)


def _is_chord_row(line):
    toks = line.split()
    real = [t for t in toks if t not in chord_extract.NEUTRAL_TOKENS]
    return bool(real) and all(_chordish(t) for t in real) \
        and any(not re.fullmatch(r"\(?\d{1,2}\)?", t) for t in real) or (
            bool(real) and all(re.fullmatch(r"\d{1,2}", t) for t in real) and len(line) - len(line.lstrip()) > 0)


def to_text(song):
    """Pjesma -> (tijelo, metapodaci) za urednik."""
    out = []
    for sec in song.get("sections") or []:
        if out:
            out.append("")
        if sec.get("label"):
            out.append(f"[{sec['label']}]")
        for ln in sec.get("lines") or []:
            rows = chord_extract.line_to_text_rows(ln)
            out.extend(rows if rows else [""])
    meta = []
    for m in song.get("meta") or []:
        meta.append(f"{m.get('label') or ''}: {', '.join(m.get('values') or [])}".strip())
    return "\n".join(out).rstrip() + "\n", "\n".join(meta)


def _chords_at(row):
    return [[m.start(), m.group()] for m in re.finditer(r"\S+", row)
            if m.group() not in chord_extract.NEUTRAL_TOKENS]


def parse_text(body):
    """Tekst urednika -> sekcije [{"label", "lines": [{"text", "chords"}]}]."""
    rows = (body or "").replace("\r\n", "\n").replace("\t", "    ").split("\n")
    if len(rows) > MAX_LINES:
        raise EditError(f"Pjesma je preduga (najviše {MAX_LINES} redaka).")
    sections, cur = [], None

    def new(label=None):
        nonlocal cur
        cur = {"label": label, "lines": []}
        sections.append(cur)

    i = 0
    while i < len(rows):
        row = rows[i].rstrip()
        if not row.strip():
            cur = None if cur is None or cur["lines"] else cur
            if cur is not None and cur["lines"]:
                cur = None
            i += 1
            continue
        m = _LABEL.match(row)
        if m or (SECTION_LABELS.match(row.strip()) and not _is_chord_row(row)):
            new((m.group(1) if m else row.strip().rstrip(":")).strip())
            i += 1
            continue
        if cur is None:
            new()
        if _is_chord_row(row):
            nxt = rows[i + 1].rstrip() if i + 1 < len(rows) else ""
            if nxt.strip() and not _is_chord_row(nxt) and not _LABEL.match(nxt):
                cur["lines"].append({"text": nxt, "chords": _chords_at(row)})
                i += 2
                continue
            cur["lines"].append({"text": "", "chords": _chords_at(row)})
            i += 1
            continue
        cur["lines"].append({"text": row, "chords": []})
        i += 1
    return [s for s in sections if s["lines"] or s["label"]]


def parse_meta(text):
    meta = []
    for row in (text or "").splitlines():
        if not row.strip():
            continue
        label, _, vals = row.partition(":")
        values = [v.strip() for v in vals.split(",") if v.strip()] if _ else []
        meta.append({"label": label.strip()[:60], "values": values})
    return meta


def apply(old, title, subtitle, key, body, meta_text):
    """Nova verzija pjesme iz polja urednika; nepromijenjeni retci se čuvaju."""
    title = " ".join((title or "").split())[:200]
    if not title:
        raise EditError("Naslov ne smije biti prazan.")
    sections = parse_text(body)
    old_rows = {}
    for si, sec in enumerate(old.get("sections") or []):
        for li, ln in enumerate(sec.get("lines") or []):
            key_rows = tuple(chord_extract.line_to_text_rows(ln))
            old_rows.setdefault((sec.get("label"), key_rows), []).append(ln)
    old_secs = {(s.get("label"), i): s for i, s in enumerate(old.get("sections") or [])}
    for si, sec in enumerate(sections):
        for li, ln in enumerate(sec["lines"]):
            rows = tuple(chord_extract.line_to_text_rows(ln))
            same = old_rows.get((sec["label"], rows))
            if same:
                sec["lines"][li] = copy.deepcopy(same.pop(0))   # redak nepromijenjen
        prev = old_secs.get((sec["label"], si))
        if prev:
            for k in ("space", "col", "attached"):
                if k in prev:
                    sec[k] = prev[k]
    song = copy.deepcopy(old)
    _body, old_meta_text = to_text(old)
    meta = (copy.deepcopy(old.get("meta") or []) if (meta_text or "").strip() == old_meta_text.strip()
            else parse_meta(meta_text))                  # nepromijenjeno -> točno kao prije
    song.update({"title": title, "subtitle": (subtitle or "").strip()[:300],
                 "key": " ".join((key or "").split())[:20], "sections": sections,
                 "meta": meta})
    return song
