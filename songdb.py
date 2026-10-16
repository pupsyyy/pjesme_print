#!/usr/bin/env python3
"""
Baza pjesama (SQLite): tekst, metapodaci i akordi s točnim mjestom.

Pjesma se čuva kao JSON (vidi chord_extract.py) + izvedena polja za
pretragu i usporedbu. Duplikati:
  * identična pjesma (isti naslov, tonalitet, metapodaci, tekst i akordi)
    -> preskače se
  * tekst ≥ 80 % sličan postojećoj pjesmi -> čeka odluku korisnika
    (zadrži postojeću / zamijeni novom / zadrži obje)
"""

import difflib
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import chord_extract
import songnotes
import songtags

SIMILARITY_THRESHOLD = 0.80   # tekst ≥ 80 % sličan (drukčiji naslov) -> pitaj
SAME_RATIO = 0.95             # tekst ≥ 95 % isti -> već imamo
SAME_TITLE = 0.80             # sličan naslov ...
SAME_CONTAIN = 0.90           # ... i kraća verzija ≥ 90 % sadržana u duljoj -> već imamo
SAME_MIN_WORDS = 6            # (za sadržanost kraća verzija mora imati barem toliko riječi)
LONGER = 1.15                 # nova verzija ima > 15 % više teksta -> ipak pitaj
DEFAULT_DB = Path(__file__).with_name("data") / "pjesme.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    subtitle    TEXT NOT NULL DEFAULT '',
    song_key    TEXT NOT NULL DEFAULT '',
    numbers     TEXT NOT NULL DEFAULT '[]',
    data        TEXT NOT NULL,
    lyrics      TEXT NOT NULL DEFAULT '',
    norm_lyrics TEXT NOT NULL DEFAULT '',
    search      TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL,
    sources     TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS songs_fingerprint ON songs(fingerprint);
CREATE INDEX IF NOT EXISTS songs_title ON songs(title COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS pending (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    data        TEXT NOT NULL,
    existing_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    similarity  REAL NOT NULL,
    fingerprint TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    sort        INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS song_tags (
    song_id     INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source      TEXT NOT NULL DEFAULT 'user',
    PRIMARY KEY (song_id, tag_id)
);
CREATE INDEX IF NOT EXISTS song_tags_tag ON song_tags(tag_id);

CREATE TABLE IF NOT EXISTS imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    pages       INTEGER NOT NULL,
    added       INTEGER NOT NULL,
    duplicates  INTEGER NOT NULL,
    conflicts   INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
"""

# Polja pjesme koja se spremaju u JSON (izvor se vodi zasebno)
SONG_FIELDS = ("title", "key", "subtitle", "meta", "sections", "layout")


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class _Conn(sqlite3.Connection):
    """Veza koja zna gdje je baza (note se spremaju u mapu pored nje)."""
    db_file = None


def db_path():
    return Path(os.environ.get("DB_PATH") or DEFAULT_DB)


def connect(path=None):
    """Otvori (i po potrebi napravi) bazu."""
    path = Path(path or db_path())
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None, factory=_Conn)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.executescript(songnotes.SCHEMA)
    _migrate(conn)
    conn.db_file = None if str(path) == ":memory:" else path
    return conn


def _migrate(conn):
    """Nadogradnja postojeće baze (stupci dodani u kasnijim verzijama)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pending)")}
    if "reason" not in cols:
        conn.execute("ALTER TABLE pending ADD COLUMN reason TEXT NOT NULL DEFAULT 'similar'")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(songs)")}
    if "tagged" not in cols:
        # 1 = oznake su već predložene/uređene (ponovni prijedlog ih ne dira)
        conn.execute("ALTER TABLE songs ADD COLUMN tagged INTEGER NOT NULL DEFAULT 0")
    if conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0:
        for i, (name, desc) in enumerate(songtags.DEFAULT_TAGS):
            conn.execute("INSERT OR IGNORE INTO tags (name, description, sort) VALUES (?,?,?)",
                         (name, desc, i))


# ── Normalizacija i usporedba ─────────────────────────────────────────────────

_TRANS = str.maketrans({"đ": "d", "Đ": "d", "ß": "ss", "ø": "o", "ł": "l", "æ": "ae"})
_REPEAT = re.compile(r"\b(?:x\s*\d+|\d+\s*x)\b", re.IGNORECASE)


def norm(text):
    """Mala slova, bez dijakritika, interpunkcije i oznaka ponavljanja (2x)."""
    s = (text or "").translate(_TRANS).casefold()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _REPEAT.sub(" ", s)
    s = re.sub(r"[^\w\s]|_", " ", s)
    return " ".join(s.split())


def clean_song(song):
    """Samo polja pjesme koja idu u bazu (bez izvora)."""
    out = {k: song.get(k) for k in SONG_FIELDS}
    out["title"] = (out["title"] or "").strip() or "(bez naslova)"
    out["key"] = (out["key"] or "").strip()
    out["subtitle"] = (out["subtitle"] or "").strip()
    out["meta"] = out["meta"] or []
    out["sections"] = out["sections"] or []
    out["layout"] = out["layout"] or {"profile": chord_extract.DEFAULT_PROFILE}
    return out


def fingerprint(song):
    """Otisak sadržaja: naslov, podnaslov, tonalitet, metapodaci, tekst, akordi.

    Ne ovisi o izgledu (verzija aplikacije, stupci), pa je ista pjesma iz
    starijeg i novijeg PDF-a — ista.
    """
    canon = {
        "t": norm(song.get("title")), "s": (song.get("subtitle") or "").strip(),
        "k": (song.get("key") or "").strip(),
        "m": [[m.get("label"), m.get("values")] for m in song.get("meta") or []],
        "x": [[sec.get("label"),
               [[ln.get("text", "").rstrip(), ln.get("chords") or []]
                for ln in sec.get("lines") or []]]
              for sec in song.get("sections") or []],
    }
    raw = json.dumps(canon, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _chord_words(song):
    return [c[1] for sec in song.get("sections") or [] for ln in sec.get("lines") or []
            for c in ln.get("chords") or []]


def compare_words(song):
    """Riječi za usporedbu sličnosti (tekst; ako ga nema — akordi)."""
    words = norm(chord_extract.lyrics_text(song)).split()
    return words or ["#" + c for c in _chord_words(song)] or norm(song.get("title")).split()


def similarity(a, b, threshold=0.0):
    """Sličnost dvaju nizova riječi (0..1). Ispod praga vraća gornju ogradu."""
    if not a and not b:
        return 1.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for quick in (sm.real_quick_ratio, sm.quick_ratio):
        r = quick()
        if r < threshold:
            return r
    return sm.ratio()


# ── Zapis i čitanje ───────────────────────────────────────────────────────────

def _derived(song):
    lyrics = chord_extract.lyrics_text(song)
    numbers = chord_extract.numbers(song)
    search = norm(" ".join([song["title"], song.get("subtitle") or "",
                            " ".join(numbers), lyrics]))
    return {"lyrics": lyrics, "norm_lyrics": " ".join(compare_words(song)),
            "search": search, "numbers": json.dumps(numbers, ensure_ascii=False)}


def _insert(conn, song, source):
    sid = _insert_row(conn, song, source)
    auto_tag(conn, sid, song, [source.get("file", "")] if source else [])
    return sid


def _insert_row(conn, song, source):
    d = _derived(song)
    now = _now()
    cur = conn.execute(
        "INSERT INTO songs (title, subtitle, song_key, numbers, data, lyrics,"
        " norm_lyrics, search, fingerprint, sources, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (song["title"], song["subtitle"], song["key"], d["numbers"],
         json.dumps(song, ensure_ascii=False), d["lyrics"], d["norm_lyrics"],
         d["search"], fingerprint(song),
         json.dumps([source] if source else [], ensure_ascii=False), now, now))
    return cur.lastrowid


def _replace(conn, song_id, song, source):
    row = conn.execute("SELECT sources FROM songs WHERE id=?", (song_id,)).fetchone()
    sources = json.loads(row["sources"]) if row else []
    if source:
        sources.append(source)
    d = _derived(song)
    conn.execute(
        "UPDATE songs SET title=?, subtitle=?, song_key=?, numbers=?, data=?,"
        " lyrics=?, norm_lyrics=?, search=?, fingerprint=?, sources=?, updated_at=?"
        " WHERE id=?",
        (song["title"], song["subtitle"], song["key"], d["numbers"],
         json.dumps(song, ensure_ascii=False), d["lyrics"], d["norm_lyrics"],
         d["search"], fingerprint(song), json.dumps(sources, ensure_ascii=False),
         _now(), song_id))


def _add_source(conn, song_id, source):
    if not source:
        return
    row = conn.execute("SELECT sources FROM songs WHERE id=?", (song_id,)).fetchone()
    if row is None:
        return
    sources = json.loads(row["sources"])
    if source not in sources:
        sources.append(source)
        conn.execute("UPDATE songs SET sources=? WHERE id=?",
                     (json.dumps(sources, ensure_ascii=False), song_id))


def title_similarity(a, b):
    """Sličnost naslova (0..1); naslov sadržan u drugom ("Nešto više od Jone") = 1."""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b or f" {a} " in f" {b} " or f" {b} " in f" {a} ":
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def containment(a, b):
    """Koliki dio kraće verzije (riječi, redom) postoji u duljoj (0..1)."""
    if not a or not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(m.size for m in sm.get_matching_blocks()) / min(len(a), len(b))


def find_match(conn, song, threshold=SIMILARITY_THRESHOLD):
    """Postojeća pjesma koja je ista ili slična novoj.

    Vrati dict {id, title, similarity, kind, longer} ili None:
      kind "same"    — ista pjesma (moguće druga verzija/duljina)
      kind "similar" — tekst ≥ threshold sličan, drukčiji naslov
      longer         — nova verzija ima osjetno više teksta od postojeće
    """
    words = compare_words(song)
    best = None
    for row in conn.execute("SELECT id, title, norm_lyrics FROM songs"):
        other = row["norm_lyrics"].split()
        ratio = similarity(words, other, threshold)
        kind = None
        if ratio >= SAME_RATIO:
            kind = "same"
        elif (title_similarity(song["title"], row["title"]) >= SAME_TITLE
              and min(len(words), len(other)) >= SAME_MIN_WORDS
              and containment(words, other) >= SAME_CONTAIN):
            kind = "same"
        elif ratio >= threshold:
            kind = "similar"
        if kind is None:
            continue
        score = (kind == "same", ratio)
        if best is None or score > best["_score"]:
            best = {"id": row["id"], "title": row["title"], "similarity": ratio,
                    "kind": kind, "longer": len(words) > LONGER * len(other),
                    "_score": score}
    if best:
        best.pop("_score")
    return best


def find_similar(conn, song, threshold=SIMILARITY_THRESHOLD):
    """Najsličnija postojeća pjesma -> (id, naslov, sličnost) ili None."""
    m = find_match(conn, song, threshold)
    return (m["id"], m["title"], m["similarity"]) if m else None


def add_song(conn, song, source=None, threshold=SIMILARITY_THRESHOLD):
    """Dodaj jednu pjesmu uz provjeru duplikata (unutar transakcije pozivatelja).

    Vrati dict: status = added | duplicate | conflict.
    """
    song = clean_song(song)
    fp = fingerprint(song)
    row = conn.execute("SELECT id, title FROM songs WHERE fingerprint=?", (fp,)).fetchone()
    if row:
        _add_source(conn, row["id"], source)
        return {"status": "duplicate", "title": song["title"], "existing_id": row["id"]}
    row = conn.execute("SELECT id, existing_id FROM pending WHERE fingerprint=?", (fp,)).fetchone()
    if row:
        return {"status": "duplicate", "title": song["title"],
                "existing_id": row["existing_id"], "pending_id": row["id"]}
    m = find_match(conn, song, threshold)
    if m and m["kind"] == "same" and not m["longer"]:
        # već imamo tu pjesmu (druga verzija istog teksta) — ne dodaje se
        _add_source(conn, m["id"], source)
        return {"status": "duplicate", "title": song["title"], "existing_id": m["id"],
                "existing_title": m["title"], "similarity": round(m["similarity"], 4),
                "reason": "same_song"}
    if m:
        reason = "longer" if m["kind"] == "same" else "similar"
        cur = conn.execute(
            "INSERT INTO pending (data, existing_id, similarity, fingerprint, source,"
            " created_at, reason) VALUES (?,?,?,?,?,?,?)",
            (json.dumps(song, ensure_ascii=False), m["id"], m["similarity"], fp,
             json.dumps(source or {}, ensure_ascii=False), _now(), reason))
        return {"status": "conflict", "title": song["title"], "pending_id": cur.lastrowid,
                "existing_id": m["id"], "existing_title": m["title"],
                "similarity": round(m["similarity"], 4), "reason": reason}
    sid = _insert(conn, song, source)
    return {"status": "added", "title": song["title"], "id": sid}


def import_songs(conn, songs, filename="", sha256="", pages=0):
    """Uvoz pjesama iz jednog PDF-a. Vrati izvještaj po statusima."""
    report = {"file": filename, "added": [], "duplicates": [], "conflicts": []}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for s in songs:
            src = {"file": filename, "pages": (s.get("source") or {}).get("pages") or [],
                   "imported_at": _now()}
            res = add_song(conn, s, src)
            key = {"added": "added", "duplicate": "duplicates",
                   "conflict": "conflicts"}[res.pop("status")]
            report[key].append(res)
        conn.execute(
            "INSERT INTO imports (filename, sha256, pages, added, duplicates, conflicts,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (filename, sha256, pages, len(report["added"]), len(report["duplicates"]),
             len(report["conflicts"]), _now()))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return report


def _row_to_song(row, full=True, tags=None, notes=None):
    data = json.loads(row["data"])
    out = {
        "id": row["id"], "title": row["title"], "subtitle": row["subtitle"],
        "key": row["song_key"], "numbers": json.loads(row["numbers"]),
        "tags": tags if tags is not None else [],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if notes is not None:
        out["notes"] = notes
    if full:
        out.update({
            "meta": data.get("meta") or [], "sections": data.get("sections") or [],
            "layout": data.get("layout") or {}, "lyrics": row["lyrics"],
            "chordpro": chord_extract.to_chordpro(data),
            "sources": json.loads(row["sources"]),
        })
    return out


def get_song(conn, song_id):
    row = conn.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
    if row is None:
        return None
    return _row_to_song(row, tags=song_tags(conn, song_id), notes=list_notes(conn, song_id))


def song_data(conn, song_id):
    """Pjesma u obliku za chord_render (s izvornim profilom izgleda)."""
    row = conn.execute("SELECT data FROM songs WHERE id=?", (song_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def list_songs(conn, q="", limit=50, offset=0, tag=None):
    """Popis (sažetak) s pretragom po naslovu, podnaslovu, broju i tekstu.

    tag: samo pjesme s tom oznakom; "-" = pjesme bez ijedne oznake."""
    where, args = [], []
    for w in norm(q).split():
        where.append("search LIKE ?")
        args.append(f"%{w}%")
    if tag == "-":
        where.append("id NOT IN (SELECT song_id FROM song_tags)")
    elif tag:
        where.append("id IN (SELECT st.song_id FROM song_tags st JOIN tags t ON t.id = st.tag_id"
                     " WHERE t.name = ? COLLATE NOCASE)")
        args.append(tag)
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM songs{sql_where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM songs{sql_where} ORDER BY title COLLATE NOCASE, id"
        " LIMIT ? OFFSET ?", args + [int(limit), int(offset)]).fetchall()
    ids = [r["id"] for r in rows]
    tags = _tags_for(conn, ids)
    counts = _note_counts(conn, ids)
    items = []
    for r in rows:
        item = _row_to_song(r, full=False, tags=tags.get(r["id"], []))
        item["notes_count"] = counts.get(r["id"], 0)
        items.append(item)
    return {"total": total, "items": items}


def export_all(conn):
    rows = conn.execute("SELECT * FROM songs ORDER BY id").fetchall()
    tags = _tags_for(conn, [r["id"] for r in rows])
    return [_row_to_song(r, tags=tags.get(r["id"], []), notes=list_notes(conn, r["id"]))
            for r in rows]


# ── Oznake ────────────────────────────────────────────────────────────────────

def _tags_for(conn, song_ids):
    """{song_id: [{"name", "auto"}]} za više pjesama odjednom."""
    out = {}
    if not song_ids:
        return out
    marks = ",".join("?" * len(song_ids))
    for r in conn.execute(
            f"SELECT st.song_id, t.name, st.source FROM song_tags st JOIN tags t ON t.id = st.tag_id"
            f" WHERE st.song_id IN ({marks}) ORDER BY t.sort, t.name", song_ids):
        out.setdefault(r["song_id"], []).append({"name": r["name"], "auto": r["source"] == "auto"})
    return out


def song_tags(conn, song_id):
    return _tags_for(conn, [song_id]).get(song_id, [])


def list_tags(conn):
    rows = conn.execute(
        "SELECT t.name, t.description, COUNT(st.song_id) AS n,"
        " SUM(CASE WHEN st.source = 'auto' THEN 1 ELSE 0 END) AS n_auto"
        " FROM tags t LEFT JOIN song_tags st ON st.tag_id = t.id"
        " GROUP BY t.id ORDER BY t.sort, t.name").fetchall()
    untagged = conn.execute(
        "SELECT COUNT(*) FROM songs WHERE id NOT IN (SELECT song_id FROM song_tags)").fetchone()[0]
    return {"tags": [{"name": r["name"], "description": r["description"], "count": r["n"],
                      "auto": r["n_auto"] or 0} for r in rows],
            "untagged": untagged}


def _tag_id(conn, name):
    name = " ".join((name or "").split())[:40]
    if not name:
        raise ValueError("Oznaka ne smije biti prazna.")
    row = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO tags (name) VALUES (?)", (name,)).lastrowid


def set_tag(conn, song_id, name, source="user"):
    """Dodaj oznaku pjesmi (ili potvrdi automatski prijedlog). False = nema pjesme."""
    if conn.execute("SELECT 1 FROM songs WHERE id=?", (song_id,)).fetchone() is None:
        return False
    tid = _tag_id(conn, name)
    if source == "auto":
        conn.execute("INSERT OR IGNORE INTO song_tags (song_id, tag_id, source) VALUES (?,?,'auto')",
                     (song_id, tid))
    else:
        conn.execute("INSERT INTO song_tags (song_id, tag_id, source) VALUES (?,?,'user')"
                     " ON CONFLICT(song_id, tag_id) DO UPDATE SET source='user'", (song_id, tid))
        conn.execute("UPDATE songs SET tagged=1 WHERE id=?", (song_id,))
    return True


def remove_tag(conn, song_id, name):
    conn.execute("UPDATE songs SET tagged=1 WHERE id=?", (song_id,))
    return conn.execute(
        "DELETE FROM song_tags WHERE song_id=? AND tag_id IN"
        " (SELECT id FROM tags WHERE name = ? COLLATE NOCASE)", (song_id, name)).rowcount > 0


def auto_tag(conn, song_id, song=None, source_names=()):
    """Automatski prijedlog oznaka (spremaju se kao 'auto'). Vrati dodane."""
    if song is None:
        song = song_data(conn, song_id)
    if not source_names:
        row = conn.execute("SELECT sources FROM songs WHERE id=?", (song_id,)).fetchone()
        source_names = [x.get("file", "") for x in json.loads(row["sources"])] if row else []
    tags = songtags.suggest(song, source_names)
    for t in tags:
        set_tag(conn, song_id, t, source="auto")
    conn.execute("UPDATE songs SET tagged=1 WHERE id=?", (song_id,))
    return tags


def suggest_all(conn):
    """Prijedlog oznaka za pjesme koje ih još nisu dobile. Vrati (pjesama, oznaka)."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM songs WHERE tagged=0")]
    added = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for sid in ids:
            added += len(auto_tag(conn, sid))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(ids), added


def bulk_tag(conn, song_ids, name, action="add"):
    """Ista oznaka za više pjesama (add/remove). Vrati broj promijenjenih."""
    n = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for sid in song_ids:
            if action == "remove":
                n += remove_tag(conn, int(sid), name)
            else:
                n += set_tag(conn, int(sid), name)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return n


def delete_song(conn, song_id):
    stored = [r["stored"] for r in conn.execute(
        "SELECT stored FROM attachments WHERE song_id=?", (song_id,))]
    ok = conn.execute("DELETE FROM songs WHERE id=?", (song_id,)).rowcount > 0
    if ok and conn.db_file:
        for name in stored:                       # datoteke nota idu s pjesmom
            songnotes.remove_file(songnotes.notes_dir(conn.db_file), name)
    return ok


# ── Note (notni zapis) ────────────────────────────────────────────────────────

def _note_row(r):
    return {"id": r["id"], "name": r["name"], "filename": r["filename"], "mime": r["mime"],
            "kind": r["kind"], "size": r["size"], "created_at": r["created_at"],
            "inline": r["kind"] in songnotes.INLINE_KINDS}


def _note_counts(conn, song_ids):
    if not song_ids:
        return {}
    marks = ",".join("?" * len(song_ids))
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT song_id, COUNT(*) FROM attachments WHERE song_id IN ({marks}) GROUP BY song_id",
        song_ids)}


def list_notes(conn, song_id):
    return [_note_row(r) for r in conn.execute(
        "SELECT * FROM attachments WHERE song_id=? ORDER BY id", (song_id,))]


def add_note(conn, song_id, filename, data, name=None):
    """Spremi datoteku nota uz pjesmu. Vrati opis ili None ako pjesme nema.

    Digne songnotes.NoteError za nedopuštenu/neispravnu datoteku."""
    if conn.execute("SELECT 1 FROM songs WHERE id=?", (song_id,)).fetchone() is None:
        return None
    if not conn.db_file:
        raise songnotes.NoteError("Note se ne mogu spremiti u privremenu bazu.")
    mime, kind, ext = songnotes.check_file(filename, data)
    shown_file = songnotes.clean_name(filename, "note" + ext)
    shown_name = songnotes.clean_name(name, os.path.splitext(shown_file)[0])
    stored = songnotes.new_stored_name(ext)
    songnotes.write_file(songnotes.notes_dir(conn.db_file), stored, data)
    try:
        nid = conn.execute(
            "INSERT INTO attachments (song_id, name, filename, stored, mime, kind, size, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (song_id, shown_name, shown_file, stored, mime, kind, len(data), _now())).lastrowid
    except Exception:
        songnotes.remove_file(songnotes.notes_dir(conn.db_file), stored)
        raise
    return _note_row(conn.execute("SELECT * FROM attachments WHERE id=?", (nid,)).fetchone())


def get_note(conn, note_id):
    """(opis, putanja do datoteke) ili None."""
    r = conn.execute("SELECT * FROM attachments WHERE id=?", (note_id,)).fetchone()
    if r is None or not conn.db_file:
        return None
    return _note_row(r) | {"song_id": r["song_id"]}, songnotes.notes_dir(conn.db_file) / r["stored"]


def rename_note(conn, note_id, name):
    name = songnotes.clean_name(name, "")
    if not name:
        raise songnotes.NoteError("Naziv ne smije biti prazan.")
    return conn.execute("UPDATE attachments SET name=? WHERE id=?", (name, note_id)).rowcount > 0


def delete_note(conn, note_id):
    r = conn.execute("SELECT stored FROM attachments WHERE id=?", (note_id,)).fetchone()
    if r is None:
        return False
    conn.execute("DELETE FROM attachments WHERE id=?", (note_id,))
    if conn.db_file:
        songnotes.remove_file(songnotes.notes_dir(conn.db_file), r["stored"])
    return True


def list_pending(conn):
    out = []
    for p in conn.execute("SELECT * FROM pending ORDER BY id"):
        new = json.loads(p["data"])
        out.append({
            "id": p["id"], "similarity": p["similarity"], "created_at": p["created_at"],
            "reason": p["reason"],
            "source": json.loads(p["source"]),
            "new": dict(new, numbers=chord_extract.numbers(new),
                        chordpro=chord_extract.to_chordpro(new)),
            "existing": get_song(conn, p["existing_id"]),
        })
    return out


RESOLVE_ACTIONS = ("keep_existing", "replace", "keep_both")


def resolve(conn, pending_id, action):
    """Odluka o sličnoj pjesmi. Vrati id pjesme koja ostaje/nastaje."""
    if action not in RESOLVE_ACTIONS:
        raise ValueError("Nepoznata odluka: " + str(action))
    conn.execute("BEGIN IMMEDIATE")
    try:
        p = conn.execute("SELECT * FROM pending WHERE id=?", (pending_id,)).fetchone()
        if p is None:
            conn.execute("ROLLBACK")
            return None
        song = json.loads(p["data"])
        source = json.loads(p["source"]) or None
        if action == "keep_existing":
            _add_source(conn, p["existing_id"], source)
            result = p["existing_id"]
        elif action == "replace":
            _replace(conn, p["existing_id"], song, source)
            result = p["existing_id"]
        else:
            result = _insert(conn, song, source)
        conn.execute("DELETE FROM pending WHERE id=?", (pending_id,))
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise


def resolve_all(conn, action):
    """Ista odluka za sve slične pjesme koje čekaju. Vrati broj riješenih."""
    if action not in RESOLVE_ACTIONS:
        raise ValueError("Nepoznata odluka: " + str(action))
    ids = [r["id"] for r in conn.execute("SELECT id FROM pending ORDER BY id")]
    return sum(1 for pid in ids if resolve(conn, pid, action) is not None)


def stats(conn):
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {"songs": one("SELECT COUNT(*) FROM songs"),
            "pending": one("SELECT COUNT(*) FROM pending"),
            "imports": one("SELECT COUNT(*) FROM imports")}
