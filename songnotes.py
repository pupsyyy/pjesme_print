#!/usr/bin/env python3
"""
Note (notni zapis) uz pjesme — datoteke koje se spremaju ODVOJENO od teksta
i akorda: u bazi je samo popis (tablica attachments), a same datoteke su u
mapi "note" pored baze (data/note/ na serveru).

Dopuštene vrste provjeravaju se po SADRŽAJU (ne samo po nastavku imena), a
datoteka se sprema pod nasumičnim imenom — originalno ime služi samo za
prikaz i preuzimanje.
"""

import os
import re
import secrets
import zipfile
from io import BytesIO
from pathlib import Path

# nastavak -> (MIME, vrsta, provjera sadržaja)
def _zip_with(names):
    def check(data):
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                listed = z.namelist()
        except zipfile.BadZipFile:
            return False
        return any(any(n.lower().endswith(x) for x in names) for n in listed)
    return check


def _xml_with(*roots):
    def check(data):
        head = data[:4096].decode("utf-8", "ignore").lower()
        return any(r.lower() in head for r in roots)
    return check


TYPES = {
    ".pdf": ("application/pdf", "pdf", lambda d: d.startswith(b"%PDF")),
    ".png": ("image/png", "image", lambda d: d.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", "image", lambda d: d.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", "image", lambda d: d.startswith(b"\xff\xd8\xff")),
    ".webp": ("image/webp", "image", lambda d: d[:4] == b"RIFF" and d[8:12] == b"WEBP"),
    ".musicxml": ("application/vnd.recordare.musicxml+xml", "musicxml",
                  _xml_with("<score-partwise", "<score-timewise")),
    ".xml": ("application/vnd.recordare.musicxml+xml", "musicxml",
             _xml_with("<score-partwise", "<score-timewise")),
    ".mxl": ("application/vnd.recordare.musicxml", "musicxml",
             _zip_with([".xml", ".musicxml"])),
    ".mscz": ("application/x-musescore", "musescore", _zip_with([".mscx"])),
    ".mscx": ("application/x-musescore+xml", "musescore", _xml_with("<museScore")),
    ".mid": ("audio/midi", "midi", lambda d: d.startswith(b"MThd")),
    ".midi": ("audio/midi", "midi", lambda d: d.startswith(b"MThd")),
}
ACCEPT = ",".join(TYPES)                       # za <input accept=...>
INLINE_KINDS = {"pdf", "image"}               # prikaz u pregledniku; ostalo se preuzima

SCHEMA = """
CREATE TABLE IF NOT EXISTS attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id     INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    stored      TEXT NOT NULL UNIQUE,
    mime        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attachments_song ON attachments(song_id);
"""


class NoteError(ValueError):
    """Neispravna datoteka nota (poruka je za korisnika)."""


def notes_dir(db_file):
    """Mapa s datotekama nota pored baze (data/note/)."""
    return Path(db_file).parent / "note"


def check_file(filename, data):
    """Vrati (mime, kind, nastavak) ili digni NoteError."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in TYPES:
        raise NoteError("Note mogu biti PDF, slika (PNG/JPG/WebP), MusicXML, "
                        "MuseScore ili MIDI datoteka.")
    mime, kind, ok = TYPES[ext]
    if not data or not ok(data):
        raise NoteError(f"Datoteka „{filename}\u201d nije ispravna {ext} datoteka.")
    return mime, kind, ext


def clean_name(name, fallback):
    """Ime za prikaz: bez upravljačkih znakova i putanje, najviše 80 znakova."""
    name = re.sub(r"[\x00-\x1f\x7f]", "", os.path.basename(name or "")).strip()
    return (name or fallback)[:80]


def new_stored_name(ext):
    return secrets.token_hex(16) + ext


def write_file(directory, stored, data):
    """Atomski zapis (privremena datoteka pa preimenovanje)."""
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (stored + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, directory / stored)


def remove_file(directory, stored):
    try:
        (directory / stored).unlink()
    except FileNotFoundError:
        pass
