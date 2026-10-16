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


def db_path():
    return Path(os.environ.get("DB_PATH") or DEFAULT_DB)


def connect(path=None):
    """Otvori (i po potrebi napravi) bazu."""
    path = Path(path or db_path())
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    """Nadogradnja postojeće baze (stupci dodani u kasnijim verzijama)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pending)")}
    if "reason" not in cols:
        conn.execute("ALTER TABLE pending ADD COLUMN reason TEXT NOT NULL DEFAULT 'similar'")


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


def _row_to_song(row, full=True):
    data = json.loads(row["data"])
    out = {
        "id": row["id"], "title": row["title"], "subtitle": row["subtitle"],
        "key": row["song_key"], "numbers": json.loads(row["numbers"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
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
    return _row_to_song(row) if row else None


def song_data(conn, song_id):
    """Pjesma u obliku za chord_render (s izvornim profilom izgleda)."""
    row = conn.execute("SELECT data FROM songs WHERE id=?", (song_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def list_songs(conn, q="", limit=50, offset=0):
    """Popis (sažetak) s pretragom po naslovu, podnaslovu, broju i tekstu."""
    where, args = [], []
    for w in norm(q).split():
        where.append("search LIKE ?")
        args.append(f"%{w}%")
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM songs{sql_where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM songs{sql_where} ORDER BY title COLLATE NOCASE, id"
        " LIMIT ? OFFSET ?", args + [int(limit), int(offset)]).fetchall()
    return {"total": total, "items": [_row_to_song(r, full=False) for r in rows]}


def export_all(conn):
    return [_row_to_song(r) for r in conn.execute("SELECT * FROM songs ORDER BY id")]


def delete_song(conn, song_id):
    return conn.execute("DELETE FROM songs WHERE id=?", (song_id,)).rowcount > 0


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
