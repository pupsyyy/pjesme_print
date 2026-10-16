#!/usr/bin/env python3
"""
Oznake (tagovi) pjesama: zadani popis i automatski prijedlog.

Prijedlog se slaže iz tri izvora, od najpouzdanijeg:
  1. naziv pjesmarice/PDF-a iz kojeg je pjesma došla
     (npr. "Papa band pjesmarica_Klanjanje.pdf" -> Klanjanje)
  2. tekst nepromjenjivih dijelova mise i naslovi (Psalam, Aleluja,
     Gospodine smiluj se, Slava Bogu na visini, Svet, Jaganjče Božji)
  3. ključne riječi u tekstu (klanjanje, pričest, korizma, Marija, prinos,
     ulazna/završna) — samo prijedlog koji korisnik potvrđuje

Ulazna, Završna i Kroz godinu su liturgijski izbor, a ne sadržaj teksta,
pa se predlažu samo uz jasne riječi ili prema nazivu pjesmarice.
"""

import re

# (naziv, opis) — redoslijed kao u misi
DEFAULT_TAGS = [
    ("Ulazna", "ulazna pjesma"),
    ("Gospodine", "nepromjenjivi dijelovi mise: Gospodine smiluj se, Slava, Svet, Jaganjče Božji"),
    ("Psalam", "otpjevni psalam"),
    ("Aleluja", "pjesma prije evanđelja"),
    ("Prinos", "pjesma za prinos darova"),
    ("Pričest", "pjesma za pričest"),
    ("Klanjanje", "klanjanje pred Presvetim"),
    ("Završna", "završna pjesma"),
    ("Marijanska", "pjesme Gospi"),
    ("Korizma", "korizmeno vrijeme"),
    ("Kroz godinu", "vrijeme kroz godinu"),
]

_TRANS = str.maketrans({"đ": "d", "Đ": "d", "č": "c", "Č": "c", "ć": "c", "Ć": "c",
                        "š": "s", "Š": "s", "ž": "z", "Ž": "z"})


def _n(text):
    """Mala slova bez dijakritika i interpunkcije, jedan razmak."""
    s = (text or "").translate(_TRANS).casefold()
    s = re.sub(r"[^\w\s]|_", " ", s)
    return " " + " ".join(s.split()) + " "


# 1. naziv izvora (PDF / pjesmarica)
SOURCE_RULES = [
    (r"klanjanj|klajanj|klanjanje|adoraci", "Klanjanje"),
    (r"pricest", "Pričest"),
    (r"kroz ?godin", "Kroz godinu"),
    (r"korizm", "Korizma"),
    (r"psal", "Psalam"),
    (r"ulazn", "Ulazna"),
    (r"zavrsn", "Završna"),
    (r"prinos", "Prinos"),
    (r"marijansk|gospin", "Marijanska"),
    (r"\baleluj", "Aleluja"),
]

# 2. sigurna pravila (naslov + tekst)
def _ordinary(t):
    """Nepromjenjivi dijelovi mise prema liturgijskom tekstu."""
    return (
        (" gospodine smiluj " in t and " kriste smiluj " in t)            # Kyrie
        or " slava bogu na visini " in t                                  # Gloria
        or (" svet svet svet " in t and " hosana " in t)                  # Sanctus
        or " bog sabaot " in t
        or (" jaganjce bozji " in t and (" smiluj " in t or " daruj nam mir " in t))
        or " vjerujem u jednoga boga " in t                               # Credo
    )


# 3. ključne riječi u tekstu: (oznaka, [(uzorak, težina)], prag)
KEYWORD_RULES = [
    ("Klanjanje", [(r" klanja", 2), (r" pokloni", 2), (r" poklanja", 2), (r" hostij", 2),
                   (r" oltar", 1), (r" euharist", 2), (r" pred tobom ", 1),
                   (r" sakrament", 2), (r" presvet", 1)], 3),
    ("Pričest", [(r" tijelo i krv", 3), (r" sveto tijelo", 3), (r" sveta krv", 3),
                 (r" tijelo tvoje", 2), (r" krv tvoj", 2), (r" kruh ", 1),
                 (r" kruh zivot", 3), (r" krus[e]? zivot", 3), (r" kruh nebesk", 3),
                 (r" pricest", 3), (r" evo jaganjc", 3), (r" gozb", 2), (r" kalez", 2),
                 (r" hostij", 1), (r" gladan", 1), (r" zedan", 1), (r" hrani", 1),
                 (r" stol ", 1)], 5),
    ("Korizma", [(r" kriz", 2), (r" golgot", 3), (r" postaj", 2), (r" rane ", 1),
                 (r" ranama ", 1), (r" trnj", 2), (r" koplj", 2), (r" razapet", 3),
                 (r" muk", 1), (r" obrati", 2), (r" pokaj", 2), (r" korizm", 3),
                 (r" umro ", 1), (r" umire", 1), (r" grob", 1), (r" cjeliva", 3)], 4),
    ("Marijanska", [(r" marij", 3), (r" gospa", 3), (r" majko bozja", 3),
                    (r" majka bozja", 3), (r" kraljic", 2), (r" zdravo marijo", 3),
                    (r" bezgresn", 3), (r" bogorodic", 3), (r" majko ", 1)], 3),
    ("Prinos", [(r" prinos", 3), (r" darove ", 2), (r" primi ove", 3),
                (r" kruh i vino", 3), (r" vino i kruh", 3), (r" prinosim", 3)], 3),
    ("Ulazna", [(r" dodite ", 2), (r" sabrani", 2), (r" okuplj", 2), (r" udite", 2),
                (r" u dom tvoj", 2), (r" u tvoj dom", 2), (r" dvorima", 2),
                (r" evo nas ", 1)], 4),
    ("Završna", [(r" podite ", 2), (r" idite ", 2), (r" naviješt", 2), (r" navijest", 2),
                 (r" u svijet ", 1), (r" posalji nas", 3), (r" salji nas", 3),
                 (r" blagoslovi nas", 2)], 4),
]


def suggest(song, source_names=()):
    """Prijedlog oznaka za pjesmu -> sortirani popis naziva."""
    tags = set()
    for name in source_names or ():
        s = _n(name)
        for pat, tag in SOURCE_RULES:
            if re.search(pat, s):
                tags.add(tag)

    title = _n(song.get("title"))
    body = _n(" ".join(ln.get("text") or "" for sec in song.get("sections") or []
                       for ln in sec.get("lines") or []))
    both = title + body

    if re.search(r" psal(am|m|mi) ", title) or re.search(r" psalam \d", title):
        tags.add("Psalam")
    if title.startswith(" aleluja") or title.startswith(" alleluia"):
        tags.add("Aleluja")
    if _ordinary(both):
        tags.add("Gospodine")

    for tag, pats, threshold in KEYWORD_RULES:
        score = 0
        for pat, w in pats:
            if re.search(pat, title):
                score += 2 * w            # u naslovu vrijedi dvostruko
            if re.search(pat, body):
                score += w
        if score >= threshold:
            tags.add(tag)

    order = {name: i for i, (name, _d) in enumerate(DEFAULT_TAGS)}
    return sorted(tags, key=lambda t: (order.get(t, 99), t))
