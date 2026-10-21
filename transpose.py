#!/usr/bin/env python3
"""
Transpozicija akorda — za prikaz, PDF, ChordPro i popis za misu.

Razumije zapise iz naših pjesmarica:
  * hrvatski/njemački: H = h-dur, B = b (B♭), Fis/Cis/Es/As, malo slovo = mol
    (a, fis, h), i miješano s # (f#, c#) — kao u pjesmaricama iz Worda;
  * engleski: B = h, Bb, F#m, Am7, D/F#;
  * spojene akorde bez razmaka ("AHE", "FGa", "eDCH"), zagrade ("(D", "E)"),
    sufikse (sus4, add9, maj7, (add4)), bas (/F#, /FIS*).
Mijenja se samo osnovni ton i bas; ostatak oznake ostaje kako je napisan.
Povisilice ili snizilice biraju se prema ciljnom tonalitetu (F, B, Es… -> b).
"""

import copy
import re

ROOTS = "CDEFGABH"
_NAT = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "H": 11}
_SUFFIX = re.compile(r"(?:maj|min|mol|dim|aug|sus|add|m(?!aj)|M|\+|°|-|\d)+|\([^)]*\)")
SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
FLAT_MAJOR = {5, 10, 3, 8, 1}          # F, B♭, E♭, A♭, D♭
FLAT_MINOR = {2, 7, 0, 5, 10, 3}       # d, g, c, f, b♭, e♭


class _Root:
    """Jedan ton u oznaci akorda: gdje je, koji je i kako je napisan."""

    __slots__ = ("start", "end", "pc", "lower", "upper_acc", "style")

    def __init__(self, start, end, pc, lower, upper_acc, style):
        self.start, self.end, self.pc = start, end, pc
        self.lower, self.upper_acc, self.style = lower, upper_acc, style


def _parse_root(s, i, german):
    """Ton na mjestu i -> (_Root, kraj) ili None."""
    ch = s[i]
    if ch.upper() not in ROOTS:
        return None
    letter = ch.upper()
    if letter == "B":
        pc = 10 if german else 11
    else:
        pc = _NAT[letter]
    j = i + 1
    rest = s[j:]
    style = None
    low = rest.lower()
    if low.startswith("is"):
        pc, j, style = pc + 1, j + 2, "words"
    elif low.startswith("es") and letter not in "AE":
        pc, j, style = pc - 1, j + 2, "words"
    elif low.startswith("s") and letter in "AE" and not low.startswith("sus"):
        pc, j, style = pc - 1, j + 1, "words"          # As, Es
    elif rest.startswith("#"):
        pc, j, style = pc + 1, j + 1, "symbols"
    elif rest.startswith("b"):
        # "Bb", "Eb7", "Abm": b odmah iza tona je snizilica (i u hrvatskom
        # zapisu "Bb" znači B♭, kao "B")
        pc = 10 if letter == "B" else pc - 1
        j, style = j + 1, "symbols"
    upper_acc = s[i + 1:j].isupper() and j > i + 1 and style == "words"
    return _Root(i, j, pc % 12, ch.islower(), upper_acc, style), j


def roots(chord, german):
    """Svi tonovi (osnovni i bas) u oznaci, s lijeva na desno."""
    out, i, n = [], 0, len(chord)
    expect = True                  # očekuje se novi ton (početak, iza razmaka, "(", "/")
    while i < n:
        c = chord[i]
        if c in " (/,|":
            expect = True
            i += 1
            continue
        m = _SUFFIX.match(chord, i) if not expect else None
        if m and m.end() > i:
            i = m.end()
            continue
        r = _parse_root(chord, i, german)
        if r:
            out.append(r[0])
            i = r[1]
            expect = False
            continue
        i += 1                     # nepoznat znak (*, ) …): preskoči
    return out


def _name(pc, flats, style, lower, upper_acc, german):
    if german:
        if style == "words":
            names = (["C", "Des", "D", "Es", "E", "F", "Ges", "G", "As", "A", "B", "H"] if flats
                     else ["C", "Cis", "D", "Dis", "E", "F", "Fis", "G", "Gis", "A", "B", "H"])
        else:
            names = (["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "B", "H"] if flats
                     else ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "B", "H"])
    else:
        names = FLAT if flats else SHARP
        if pc == 10:
            names = FLAT                                   # B♭ (ne A#)
    name = names[pc]
    if upper_acc:
        name = name.upper()
    if lower:
        name = name[0].lower() + name[1:]
    return name


def style_of(chords):
    """Zapis pjesme: (njemački/hrvatski?, "words"|"symbols")."""
    german = False
    words = symbols = 0
    for ch in chords:
        for i, c in enumerate(ch):
            if c in "Hh" and (i == 0 or not ch[i - 1].isalpha()):
                german = True
        # malo slovo = mol (hrvatski zapis): na početku ili iza razmaka/zagrade/
        # kose crte ili iza drugog akorda ("FGa"), ali ne snizilica ("Bb", "Eb")
        if re.search(r"(?:^|[\s(/])[a-h](?:is|es|#)?(?![a-z])", ch) \
                or re.search(r"[A-H](?:is|es)?\d*[ac-h](?![a-z])", ch) \
                or re.search(r"[A-Ha-h](?:is|es)(?![a-z])", ch):
            german = True
        words += len(re.findall(r"[A-Ha-h](?:is|es)", ch))
        symbols += len(re.findall(r"[A-H][#b]|[a-h]#", ch))
    return german, ("words" if words > symbols else "symbols")


def _key_pc(key, german):
    """Tonalitet (npr. "G", "Am", "fis", "Ab (D)") -> (ton, mol?) ili None."""
    rs = roots(key or "", german)
    if not rs:
        return None
    r = rs[0]
    minor = r.lower or bool(re.match(r"^[A-H](?:is|es|s|#|b)?m(?!aj)", (key or "").strip()))
    return r.pc, minor


def use_flats(key_pc):
    if key_pc is None:
        return None
    pc, minor = key_pc
    return pc in (FLAT_MINOR if minor else FLAT_MAJOR)


def transpose_text(text, n, german, flats, style):
    """Transponiraj sve tonove u tekstu oznake (akord, tonalitet, redak akorda)."""
    if not n or not text:
        return text
    out, last = [], 0
    for r in roots(text, german):
        pc = (r.pc + n) % 12
        out.append(text[last:r.start])
        out.append(_name(pc, flats, r.style or style, r.lower, r.upper_acc, german))
        last = r.end
    out.append(text[last:])
    return "".join(out)


def _song_chords(song):
    return [c[1] for sec in song.get("sections") or [] for ln in sec.get("lines") or []
            for c in ln.get("chords") or []]


def context(song, n):
    """Zapis i povisilice/snizilice za transpoziciju pjesme za n polutonova."""
    chords = _song_chords(song)
    german, style = style_of(chords + [song.get("key") or ""])
    kp = _key_pc(song.get("key"), german)
    if kp is None and chords:
        kp = _key_pc(chords[0], german)
    target = ((kp[0] + n) % 12, kp[1]) if kp else None
    flats = use_flats(target)
    if flats is None:
        flats = any(re.search(r"[A-H]b|[A-Ha-h]es|\bAs\b|\bEs\b", c) for c in chords)
    return german, flats, style


def key_after(song, n):
    """Tonalitet nakon transpozicije (za izbornik), ili "" ako se ne zna."""
    german, flats, style = context(song, n)
    key = (song.get("key") or "").strip()
    if not key:
        chords = _song_chords(song)
        if not chords:
            return ""
        first = roots(chords[0], german)
        if not first:
            return ""
        key = chords[0][:first[0].end] + ("m" if not first[0].lower and re.match(
            r"^[A-H](?:is|es|s|#|b)?m(?!aj)", chords[0]) else "")
    return transpose_text(key, n, german, flats, style)


def _shift_row(row, n, german, flats, style):
    """Redak samo s akordima (fmt.chord_text): akordi ostaju na svojim stupcima."""
    out = ""
    for m in re.finditer(r"\S+", row):
        new = transpose_text(m.group(), n, german, flats, style)
        pos = max(m.start(), len(out) + (1 if out else 0))
        out += " " * (pos - len(out)) + new
    return out


def normalize(n):
    """Polutonovi -> -5..6 (12 = isto; -7 = +5)."""
    try:
        n = int(n or 0) % 12
    except (TypeError, ValueError):
        return 0
    return n - 12 if n > 6 else n


def transpose_song(song, n):
    """Kopija pjesme transponirana za n polutonova. n=0 -> ista kopija."""
    song = copy.deepcopy(song)
    n = normalize(n)
    if not n:
        return song
    german, flats, style = context(song, n)
    for sec in song.get("sections") or []:
        for ln in sec.get("lines") or []:
            ln["chords"] = [[i, transpose_text(ch, n, german, flats, style)]
                            for i, ch in ln.get("chords") or []]
            fmt = ln.get("fmt") or {}
            if fmt.get("chord_text"):
                fmt["chord_text"] = _shift_row(fmt["chord_text"], n, german, flats, style)
    if song.get("key"):
        song["key"] = transpose_text(song["key"], n, german, flats, style)
    return song


def options(song, span=6):
    """[{n, key}] za izbornik transpozicije (-span..+span)."""
    return [{"n": k, "key": key_after(song, k)} for k in range(-span + 1, span + 1)]
