#!/usr/bin/env python3
"""
Uvoz u bazu pjesama: PDF-ovi, datoteke nota i ZIP s više njih odjednom.

Pjesma u bazi može imati: samo tekst, tekst + akorde, tekst + note,
tekst + akorde + note (note su zasebne datoteke, vidi songnotes.py).

PDF:
  * stranice s tekstom (s akordima ili bez) -> pjesme (s provjerom duplikata),
  * stranice s notama (scorepages.py) -> note uz pjesmu kojoj pripadaju:
      1. ista pjesma u istom PDF-u (PDF s jednom pjesmom; "mixed" stranica;
         note bez naslova odmah iza/ispred teksta pjesme),
      2. pjesma u bazi istog naslova / s istim tekstom ispod nota,
      3. jedina pjesma iz iste mape ZIP-a,
      4. inače nova pjesma koja zasad ima samo note (tekst se dopuni kad
         stigne PDF s tekstom istog naslova).
ZIP: svaka mapa je cjelina — prvo PDF-ovi, pa datoteke nota (MusicXML,
MuseScore, MIDI, slike) koje idu uz pjesmu iz iste mape ili istog naslova.
Note koje već imamo (isti sadržaj) se ne dodaju ponovo.
"""

import hashlib
import io
import logging
import os
import posixpath
import zipfile

import chord_extract
import scorepages
import songdb
import songnotes

log = logging.getLogger("songimport")
MAX_ZIP_FILES = 2000
MAX_ZIP_TOTAL = 1024 * 1024 * 1024        # raspakirano ukupno (zaštita od "zip bombe")
MAX_ZIP_ENTRY = 200 * 1024 * 1024


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _is_password_error(exc):
    msg = (str(exc) + " " + type(exc).__name__).lower()
    return "password" in msg or "encrypt" in msg


def _pages_label(pages):
    if len(pages) == 1:
        return f"str. {pages[0]}"
    if pages == list(range(pages[0], pages[-1] + 1)):
        return f"str. {pages[0]}–{pages[-1]}"
    return "str. " + ", ".join(map(str, pages))


class _Folder:
    """Pjesme iz iste mape ZIP-a (ili jednog poslanog PDF-a)."""

    def __init__(self, path=""):
        self.path = path
        self.songs = {}                      # id -> naslov

    def single(self):
        return next(iter(self.songs.items())) if len(self.songs) == 1 else None


# ── Ulaz ──────────────────────────────────────────────────────────────────────

def import_upload(conn, filename, data, tags=()):
    """Jedna poslana datoteka (PDF, ZIP ili note) -> lista izvještaja."""
    name = os.path.basename(filename or "dokument")
    low = name.lower()
    if low.endswith(".zip"):
        return import_zip(conn, name, data, tags)
    if data.startswith(b"%PDF"):
        return [import_pdf(conn, name, data, tags, _Folder())]
    if low.endswith(".pdf"):
        return [{"file": name, "error": "Nije PDF datoteka."}]
    if os.path.splitext(low)[1] in songnotes.TYPES:
        return [import_note_file(conn, name, data, tags, _Folder())]
    return [{"file": name, "error": "Nije PDF, ZIP ni datoteka nota."}]


def import_zip(conn, name, data, tags=()):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return [{"file": name, "error": "ZIP se ne može otvoriti."}]
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ZIP_FILES:
        return [{"file": name, "error": f"ZIP ima previše datoteka (najviše {MAX_ZIP_FILES})."}]
    skipped = []
    folders = {}
    total = 0
    for info in sorted(infos, key=lambda i: i.filename):
        path = info.filename.replace("\\", "/")
        base = posixpath.basename(path)
        if path.startswith("__MACOSX/") or base.startswith(".") or not base:
            continue
        ext = os.path.splitext(base.lower())[1]
        if ext != ".pdf" and ext not in songnotes.TYPES:
            skipped.append(path)
            continue
        if info.flag_bits & 0x1:
            return [{"file": name, "error": "ZIP je zaštićen lozinkom."}]
        if info.file_size > MAX_ZIP_ENTRY:
            skipped.append(path)
            continue
        total += info.file_size
        if total > MAX_ZIP_TOTAL:
            return [{"file": name, "error": "ZIP je prevelik kad se raspakira."}]
        folders.setdefault(posixpath.dirname(path), []).append(info)
    if not folders:
        return [{"file": name, "error": "U ZIP-u nema PDF-ova ni datoteka nota."}]
    reports = []
    for dirname in sorted(folders):
        folder = _Folder(dirname)
        entries = folders[dirname]
        pdfs = [i for i in entries if i.filename.lower().endswith(".pdf")]
        others = [i for i in entries if i not in pdfs]
        for info in pdfs + others:          # prvo tekst (PDF), pa note iz iste mape
            shown = f"{name}/{info.filename}"
            try:
                content = _read_limited(zf, info)
            except (zipfile.BadZipFile, ValueError, RuntimeError, OSError):
                reports.append({"file": shown, "error": "Datoteka u ZIP-u je oštećena."})
                continue
            if info in pdfs:
                if not content.startswith(b"%PDF"):
                    reports.append({"file": shown, "error": "Nije PDF datoteka."})
                    continue
                reports.append(import_pdf(conn, shown, content, tags, folder))
            else:
                reports.append(import_note_file(conn, shown, content, tags, folder))
    if skipped:
        reports.append({"file": name, "warnings": [
            "Preskočeno (nije PDF ni note): " + ", ".join(skipped[:20])
            + (" …" if len(skipped) > 20 else "")], "added": [], "duplicates": [],
            "conflicts": [], "notes": []})
    return reports


def _read_limited(zf, info):
    """Pročitaj datoteku iz ZIP-a, ali ne više od prijavljene veličine."""
    with zf.open(info) as f:
        data = f.read(min(info.file_size, MAX_ZIP_ENTRY) + 1)
    if len(data) > MAX_ZIP_ENTRY or len(data) != info.file_size:
        raise ValueError("veličina se ne slaže")
    return data


# ── PDF ───────────────────────────────────────────────────────────────────────

def import_pdf(conn, name, data, tags=(), folder=None):
    folder = folder or _Folder()
    try:
        res = chord_extract.extract(data, os.path.basename(name))
    except Exception as exc:  # noqa: BLE001 — poruka korisniku, ne 500
        msg = ("PDF je zaštićen lozinkom." if _is_password_error(exc)
               else "PDF se ne može pročitati.")
        return {"file": name, "error": msg}
    groups = res["scores"]
    if not res["songs"] and not groups:
        if len(res["skipped"]) == res["pages"]:
            return {"file": name, "error": "U PDF-u nema teksta pjesama ni nota (sken lošije "
                    "kvalitete?).", "warnings": res["warnings"]}
        return {"file": name, "error": "U PDF-u nisu prepoznate pjesme ni note (nema naslova). "
                "Baza prima PDF-ove iz aplikacije za pjesmarice, pjesmarice iz Worda — "
                "pjesma s naslovom na vrhu stranice, akordi u retku iznad teksta — "
                "i note (iz programa za note ili skenirane)."}
    if res["songs"] and not res["titled"]:
        if not groups:
            return {"file": name, "error": "U PDF-u nisu prepoznate pjesme (nema naslova). "
                    "Baza prima PDF-ove iz aplikacije za pjesmarice i pjesmarice iz Worda — "
                    "pjesma s naslovom na vrhu stranice, akordi u retku iznad teksta."}
        # note s tekstom bez prepoznatog naslova (zasluge, list s riječima...):
        # uvoze se samo note, tekst bez naslova ne postaje "(bez naslova)" pjesma
        res["warnings"].append("Tekst bez naslova nije uvezen kao pjesma — uvezene su samo note.")
        res["songs"] = []
    sha = _sha(data)
    rep = songdb.import_songs(conn, res["songs"], name, sha, res["pages"], tags=tags)
    order = rep.pop("order")
    page_song = {}                           # stranica -> (id, naslov) pjesme s tekstom
    for song, (sid, title) in zip(res["songs"], order):
        if sid is None:
            continue
        folder.songs[sid] = title
        for p in (song.get("source") or {}).get("pages") or []:
            page_song[p] = (sid, title)
    rep["notes"] = []
    for g in groups:
        try:
            rep["notes"].append(_place_group(conn, name, data, sha, g, res, page_song,
                                             folder, tags))
        except Exception:  # noqa: BLE001 — jedan komad nota ne smije srušiti cijeli uvoz
            log.exception("note iz %s, str. %s", name, g["pages"])
            rep["notes"].append({"title": g["title"], "pages": g["pages"], "kind": g["kind"],
                                 "status": "error",
                                 "error": "Stranice s notama se ne mogu izdvojiti iz PDF-a."})
    rep["warnings"] = res["warnings"]
    rep["pages"] = res["pages"]
    return rep


def _target(conn, g, res, page_song, folder, fallback_title):
    """Pjesma za komad nota -> ((id, naslov), "new" ako je treba napraviti s naslovom)."""
    pages = g["pages"]
    if g["kind"] == "mixed" and pages[0] in page_song:
        return page_song[pages[0]], None
    file_songs = {v for v in page_song.values()}
    if len(file_songs) == 1:
        return next(iter(file_songs)), None          # PDF s jednom pjesmom
    if not g["title"]:
        for p in (pages[0] - 1, pages[-1] + 1):      # note uz tekst pjesme
            if p in page_song:
                return page_song[p], None
    found = songdb.find_song_for_notes(conn, g["title"], g["words"])
    if found:
        return found, None
    if folder.single():
        return folder.single(), None
    title = g["title"] or (fallback_title if not res["songs"] else "")
    if not g["title"] and title:
        found = songdb.find_song_for_notes(conn, title, g["words"])
        if found:
            return found, None
    return None, title or fallback_title or "(note bez naslova)"


def _place_group(conn, name, data, sha, g, res, page_song, folder, tags):
    base = os.path.basename(name)
    stem = os.path.splitext(base)[0]
    fallback = scorepages.title_from_filename(base) or \
        scorepages.title_from_filename(posixpath.basename(folder.path))
    target, new_title = _target(conn, g, res, page_song, folder, fallback)
    whole = len(g["pages"]) == res["pages"]
    # ključ za "već imamo": sadržaj stranica, ne bajtovi PDF-a (ponovni izvoz
    # istih nota ima drugi datum/ID, ali iste note)
    key = _sha(("score:" + g["fp"]).encode())
    out = {"title": g["title"], "pages": g["pages"], "kind": g["kind"]}
    existing = conn.execute("SELECT a.song_id, s.title FROM attachments a JOIN songs s"
                            " ON s.id = a.song_id WHERE a.sha256=?", (key,)).fetchone()
    if existing:
        return out | {"status": "duplicate", "song_id": existing["song_id"],
                      "song_title": existing["title"]}
    if whole:
        content, fname = data, base
    else:
        content = scorepages.extract_pages(data, g["pages"])
        fname = f"{stem} ({_pages_label(g['pages'])}).pdf"
    try:
        songnotes.check_file(fname, content)     # prije nove pjesme: da ne ostane prazna
    except songnotes.NoteError as exc:
        return out | {"status": "error", "error": str(exc)}
    status = "attached"
    if target is None:
        sid = songdb.create_notes_song(conn, new_title, {"file": name, "pages": g["pages"],
                                                         "imported_at": songdb._now()})
        for t in tags:
            songdb.set_tag(conn, sid, t, group=songdb.songtags.COLLECTION)
        target, status = (sid, new_title), "new_song"
        folder.songs.setdefault(sid, new_title)
    note_name = (g["title"] or target[1]) if whole else \
        f"{g['title'] or target[1]} ({_pages_label(g['pages'])})"
    try:
        songdb.add_note(conn, target[0], fname, content, name=note_name, sha=key,
                        origin="import", source={"file": name, "pages": g["pages"]},
                        unique="all")
    except songnotes.NoteError as exc:
        return out | {"status": "error", "error": str(exc)}
    return out | {"status": status, "song_id": target[0], "song_title": target[1]}


# ── Datoteka nota (MusicXML, MuseScore, MIDI, slika) ─────────────────────────

def import_note_file(conn, name, data, tags=(), folder=None):
    folder = folder or _Folder()
    base = os.path.basename(name)
    rep = {"file": name, "added": [], "duplicates": [], "conflicts": [], "notes": []}
    try:
        songnotes.check_file(base, data)
    except songnotes.NoteError as exc:
        return {"file": name, "error": str(exc)}
    title = scorepages.title_from_filename(base)
    target = None
    for sid, t in folder.songs.items():           # pjesma iz iste mape istog naslova
        if songdb.note_title_similarity(title, t) >= songdb.NOTE_TITLE:
            target = (sid, t)
    target = target or folder.single() or songdb.find_song_for_notes(conn, title)
    if target is None:
        dir_title = scorepages.title_from_filename(posixpath.basename(folder.path))
        if dir_title:
            target = songdb.find_song_for_notes(conn, dir_title)
            title = title or dir_title
    sha = _sha(data)
    existing = conn.execute("SELECT a.song_id, s.title FROM attachments a JOIN songs s"
                            " ON s.id = a.song_id WHERE a.sha256=?", (sha,)).fetchone()
    entry = {"title": title, "pages": [], "kind": "file"}
    if existing:
        rep["notes"].append(entry | {"status": "duplicate", "song_id": existing["song_id"],
                                     "song_title": existing["title"]})
        return rep
    status = "attached"
    if target is None:
        sid = songdb.create_notes_song(conn, title or "(note bez naslova)",
                                       {"file": name, "imported_at": songdb._now()})
        for t in tags:
            songdb.set_tag(conn, sid, t, group=songdb.songtags.COLLECTION)
        target, status = (sid, title or "(note bez naslova)"), "new_song"
        folder.songs.setdefault(sid, target[1])
    songdb.add_note(conn, target[0], base, data, sha=sha, origin="import",
                    source={"file": name}, unique="all")
    rep["notes"].append(entry | {"status": status, "song_id": target[0],
                                 "song_title": target[1]})
    return rep
