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
import omr
import songlinks
import songnotes
import songtags

SIMILARITY_THRESHOLD = 0.80   # tekst ≥ 80 % sličan (drukčiji naslov) -> pitaj
SAME_RATIO = 0.95             # tekst ≥ 95 % isti -> već imamo
SAME_TITLE = 0.80             # sličan naslov ...
SAME_CONTAIN = 0.90           # ... i kraća verzija ≥ 90 % sadržana u duljoj -> već imamo
SAME_MIN_WORDS = 6            # (za sadržanost kraća verzija mora imati barem toliko riječi)
SAME_OVERLAP = 0.50           # ... a tekst barem upola isti (inače pitaj)
LONGER = 1.15                 # nova verzija ima > 15 % više teksta -> ipak pitaj
DEFAULT_DB = Path(__file__).with_name("data") / "pjesme.db"
# pjesma koja (zasad) ima samo note — bez ijednog retka teksta i akorda
NOTES_ONLY_SQL = "(lyrics = '' AND data LIKE '%\"sections\": []%')"

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
    sort        INTEGER NOT NULL DEFAULT 100,
    grp         TEXT NOT NULL DEFAULT ''
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
    key = str(path.resolve()) if str(path) != ":memory:" else None
    if key is None or key not in _READY:
        # shema i nadogradnja jednom po procesu (ne na svaki zahtjev), pod
        # pisaćom bravom da dva gunicorn workera ne nadograđuju istodobno
        conn.execute("BEGIN IMMEDIATE")
        try:
            for script in (SCHEMA, songnotes.SCHEMA, songlinks.SCHEMA, omr.SCHEMA):
                for stmt in script.split(";"):
                    if stmt.strip():
                        conn.execute(stmt)
            _migrate(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if key:
            _READY.add(key)
    conn.db_file = None if str(path) == ":memory:" else path
    return conn


_READY = set()                # baze kojima je u ovom procesu shema već provjerena


def _migrate(conn):
    """Nadogradnja postojeće baze (stupci dodani u kasnijim verzijama)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pending)")}
    if "reason" not in cols:
        conn.execute("ALTER TABLE pending ADD COLUMN reason TEXT NOT NULL DEFAULT 'similar'")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(songs)")}
    if "tagged" not in cols:
        # 1 = oznake su već predložene/uređene (ponovni prijedlog ih ne dira)
        conn.execute("ALTER TABLE songs ADD COLUMN tagged INTEGER NOT NULL DEFAULT 0")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tags)")}
    if "grp" not in cols:
        # "" = liturgijska oznaka, "zbirka" = izvor/repertoar (može se sakriti)
        conn.execute("ALTER TABLE tags ADD COLUMN grp TEXT NOT NULL DEFAULT ''")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(attachments)")}
    for col, decl in (("sha256", "TEXT NOT NULL DEFAULT ''"),
                      ("origin", "TEXT NOT NULL DEFAULT 'upload'"),
                      ("source", "TEXT NOT NULL DEFAULT '{}'")):
        if col not in cols:
            conn.execute(f"ALTER TABLE attachments ADD COLUMN {col} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS attachments_sha ON attachments(sha256)")
    defaults = [(n, d, "") for n, d in songtags.DEFAULT_TAGS] + \
        [(n, d, songtags.COLLECTION) for n, d in songtags.COLLECTION_TAGS]
    for i, (name, desc, grp) in enumerate(defaults):
        conn.execute("INSERT OR IGNORE INTO tags (name, description, sort, grp) VALUES (?,?,?,?)",
                     (name, desc, i, grp))


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
            # kraća verzija sadržana u duljoj: tiho preskoči ako je naslov
            # (gotovo) isti ili tekst većim dijelom isti ("Jona": 2 od 3 retka);
            # inače ("Isus" / "Isus je Gospodin", malo zajedničkog) -> pitaj
            same = (note_title_similarity(song["title"], row["title"]) >= SAME_TITLE
                    or ratio >= SAME_OVERLAP)
            kind = "same" if same else "similar"
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
    pid = _notes_only_song(conn, song)
    if pid:
        # pjesma je dosad imala samo note (bez teksta) — sad dobiva tekst
        _replace(conn, pid, song, source)
        return {"status": "added", "title": song["title"], "id": pid, "filled": True}
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


def import_songs(conn, songs, filename="", sha256="", pages=0, tags=(), transaction=True):
    """Uvoz pjesama iz jednog PDF-a. Vrati izvještaj po statusima.

    tags: oznake (zbirke) za NOVE pjesme iz ovog uvoza; pjesme koje već
    imamo ih ne dobivaju (a slične ih dobiju ako se zadrže kao nove)."""
    report = {"file": filename, "added": [], "duplicates": [], "conflicts": [], "order": []}
    if transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for s in songs:
            src = {"file": filename, "pages": (s.get("source") or {}).get("pages") or [],
                   "imported_at": _now()}
            if tags:
                src["tags"] = list(tags)
            res = add_song(conn, s, src)
            if res["status"] == "added":
                for t in tags:
                    set_tag(conn, res["id"], t, group=songtags.COLLECTION)
            status = res.pop("status")
            key = {"added": "added", "duplicate": "duplicates", "conflict": "conflicts"}[status]
            report[key].append(res)
            # (id pjesme u bazi, naslov) redom kao ulaz — za note iz istog PDF-a;
            # slična pjesma koja čeka odluku -> postojeća
            sid = res["id"] if status == "added" else res.get("existing_id")
            title = res["title"] if status == "added" else (
                res.get("existing_title") or _song_title(conn, sid))
            report["order"].append((sid, title))
        conn.execute(
            "INSERT INTO imports (filename, sha256, pages, added, duplicates, conflicts,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (filename, sha256, pages, len(report["added"]), len(report["duplicates"]),
             len(report["conflicts"]), _now()))
        if transaction:
            conn.execute("COMMIT")
    except Exception:
        if transaction:
            conn.execute("ROLLBACK")
        raise
    return report


def _has_text(row):
    return bool(row["lyrics"]) or '"sections": []' not in row["data"]


def _song_title(conn, song_id):
    row = conn.execute("SELECT title FROM songs WHERE id=?", (song_id,)).fetchone()
    return row["title"] if row else ""


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
    out = _row_to_song(row, tags=song_tags(conn, song_id), notes=list_notes(conn, song_id))
    out["links"] = list_links(conn, song_id)
    out["has_text"] = _has_text(row)
    return out


def song_data(conn, song_id):
    """Pjesma u obliku za chord_render (s izvornim profilom izgleda)."""
    row = conn.execute("SELECT data FROM songs WHERE id=?", (song_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def list_songs(conn, q="", limit=50, offset=0, tag=None, exclude=()):
    """Popis (sažetak) s pretragom po naslovu, podnaslovu, broju i tekstu.

    tag: samo pjesme s tom oznakom; "-" = pjesme bez ijedne oznake.
    exclude: sakrij pjesme s bilo kojom od tih oznaka (npr. Veliki zbor)."""
    where, args = [], []
    for w in norm(q).split():
        where.append("search LIKE ?")
        args.append(f"%{w}%")
    if tag == "-":
        where.append("id NOT IN (SELECT song_id FROM song_tags)")
    elif tag:
        where.append("id IN (SELECT st.song_id FROM song_tags st JOIN tags t ON t.id = st.tag_id"
                     " WHERE t.name = ? COLLATE NOCASE)")
        args.append(tag_name(conn, tag))
    exclude = [tag_name(conn, e) for e in exclude or () if e and e.strip()]
    if exclude:
        where.append("id NOT IN (SELECT st.song_id FROM song_tags st JOIN tags t ON t.id = st.tag_id"
                     " WHERE t.name COLLATE NOCASE IN (" + ",".join("?" * len(exclude)) + "))")
        args.extend(exclude)
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM songs{sql_where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM songs{sql_where} ORDER BY title COLLATE NOCASE, id"
        " LIMIT ? OFFSET ?", args + [int(limit), int(offset)]).fetchall()
    ids = [r["id"] for r in rows]
    tags = _tags_for(conn, ids)
    counts = _note_counts(conn, ids)
    links = _link_counts(conn, ids)
    items = []
    for r in rows:
        item = _row_to_song(r, full=False, tags=tags.get(r["id"], []))
        item["notes_count"] = counts.get(r["id"], 0)
        item["links_count"] = links.get(r["id"], 0)
        item["has_text"] = _has_text(r)
        items.append(item)
    return {"total": total, "items": items}


def export_all(conn):
    rows = conn.execute("SELECT * FROM songs ORDER BY id").fetchall()
    tags = _tags_for(conn, [r["id"] for r in rows])
    notes, links = {}, {}
    for r in conn.execute("SELECT * FROM attachments ORDER BY id"):     # sve odjednom
        notes.setdefault(r["song_id"], []).append(_note_row(r))
    for r in conn.execute("SELECT * FROM song_links ORDER BY id"):
        links.setdefault(r["song_id"], []).append(_link_row(r))
    out = []
    for r in rows:
        song = _row_to_song(r, tags=tags.get(r["id"], []), notes=notes.get(r["id"], []))
        song["links"] = sorted(links.get(r["id"], []),
                               key=lambda x: (songlinks.voice_order(x["voice"]), x["id"]))
        song["has_text"] = _has_text(r)
        out.append(song)
    return out


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
        "SELECT t.name, t.description, t.grp, COUNT(st.song_id) AS n,"
        " SUM(CASE WHEN st.source = 'auto' THEN 1 ELSE 0 END) AS n_auto"
        " FROM tags t LEFT JOIN song_tags st ON st.tag_id = t.id"
        " GROUP BY t.id ORDER BY t.sort, t.name").fetchall()
    untagged = conn.execute(
        "SELECT COUNT(*) FROM songs WHERE id NOT IN (SELECT song_id FROM song_tags)").fetchone()[0]
    return {"tags": [{"name": r["name"], "description": r["description"], "count": r["n"],
                      "auto": r["n_auto"] or 0, "group": r["grp"]} for r in rows],
            "untagged": untagged}


def _tag_id(conn, name, group=""):
    name = " ".join((name or "").lstrip("#").split())[:40]
    if not name:
        raise ValueError("Oznaka ne smije biti prazna.")
    row = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    key = songtags.tag_key(name)
    for r in conn.execute("SELECT id, name FROM tags"):
        if songtags.tag_key(r["name"]) == key:       # "#velikizbor" -> "Veliki zbor"
            return r["id"]
    return conn.execute("INSERT INTO tags (name, grp) VALUES (?,?)", (name, group)).lastrowid


def tag_name(conn, name):
    """Postojeći naziv oznake za upisani (npr. "#pučkepjesme" -> "Pučke pjesme")."""
    key = songtags.tag_key(name)
    for r in conn.execute("SELECT name FROM tags"):
        if songtags.tag_key(r["name"]) == key:
            return r["name"]
    return " ".join((name or "").lstrip("#").split())[:40]


def set_tag(conn, song_id, name, source="user", group=""):
    """Dodaj oznaku pjesmi (ili potvrdi automatski prijedlog). False = nema pjesme."""
    if conn.execute("SELECT 1 FROM songs WHERE id=?", (song_id,)).fetchone() is None:
        return False
    tid = _tag_id(conn, name, group)
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
        " (SELECT id FROM tags WHERE name = ? COLLATE NOCASE)",
        (song_id, tag_name(conn, name))).rowcount > 0


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
            "origin": r["origin"], "inline": r["kind"] in songnotes.INLINE_KINDS}


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


def add_note(conn, song_id, filename, data, name=None, sha=None, origin="upload",
             source=None, unique="song"):
    """Spremi datoteku nota uz pjesmu. Vrati opis ili None ako pjesme nema.

    sha: ključ za prepoznavanje istih nota (zadano: SHA-256 sadržaja).
    unique: "song" = iste note uz istu pjesmu se ne dodaju ponovo,
            "all"  = ni uz bilo koju drugu pjesmu (uvoz).
    Ako takve note već postoje, vraća postojeće s "duplicate": True.
    Digne songnotes.NoteError za nedopuštenu/neispravnu datoteku."""
    if conn.execute("SELECT 1 FROM songs WHERE id=?", (song_id,)).fetchone() is None:
        return None
    if not conn.db_file:
        raise songnotes.NoteError("Note se ne mogu spremiti u privremenu bazu.")
    mime, kind, ext = songnotes.check_file(filename, data)
    sha = sha or hashlib.sha256(data).hexdigest()
    if unique == "all":
        dup = conn.execute("SELECT * FROM attachments WHERE sha256=? ORDER BY id", (sha,)).fetchone()
    else:
        dup = conn.execute("SELECT * FROM attachments WHERE sha256=? AND song_id=?",
                           (sha, song_id)).fetchone()
    if dup is not None:
        return _note_row(dup) | {"song_id": dup["song_id"], "duplicate": True}
    shown_file = songnotes.clean_name(filename, "note" + ext)
    shown_name = songnotes.clean_name(name, os.path.splitext(shown_file)[0])
    stored = songnotes.new_stored_name(ext)
    songnotes.write_file(songnotes.notes_dir(conn.db_file), stored, data)
    try:
        nid = conn.execute(
            "INSERT INTO attachments (song_id, name, filename, stored, mime, kind, size,"
            " created_at, sha256, origin, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (song_id, shown_name, shown_file, stored, mime, kind, len(data), _now(), sha,
             origin, json.dumps(source or {}, ensure_ascii=False))).lastrowid
    except Exception:
        songnotes.remove_file(songnotes.notes_dir(conn.db_file), stored)
        raise
    return _note_row(conn.execute("SELECT * FROM attachments WHERE id=?", (nid,)).fetchone()) | {
        "song_id": song_id}


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


def move_note(conn, note_id, song_id):
    """Premjesti note na drugu pjesmu. Pjesma koja je imala SAMO te note (bez
    teksta) i sad je prazna se briše. Vrati False ako nota ili pjesme nema."""
    r = conn.execute("SELECT song_id FROM attachments WHERE id=?", (note_id,)).fetchone()
    if r is None or conn.execute("SELECT 1 FROM songs WHERE id=?", (song_id,)).fetchone() is None:
        return False
    old = r["song_id"]
    conn.execute("UPDATE attachments SET song_id=? WHERE id=?", (song_id, note_id))
    if old != song_id and _is_empty_song(conn, old):
        conn.execute("DELETE FROM songs WHERE id=?", (old,))
    return True


def _is_empty_song(conn, song_id):
    """Pjesma bez teksta, nota i snimki (ostatak nakon premještanja nota)."""
    row = conn.execute("SELECT lyrics, data FROM songs WHERE id=?", (song_id,)).fetchone()
    return (row is not None and not _has_text(row)
            and not conn.execute("SELECT 1 FROM attachments WHERE song_id=?", (song_id,)).fetchone()
            and not conn.execute("SELECT 1 FROM song_links WHERE song_id=?", (song_id,)).fetchone())


def delete_note(conn, note_id):
    r = conn.execute("SELECT stored FROM attachments WHERE id=?", (note_id,)).fetchone()
    if r is None:
        return False
    conn.execute("DELETE FROM attachments WHERE id=?", (note_id,))
    if conn.db_file:
        songnotes.remove_file(songnotes.notes_dir(conn.db_file), r["stored"])
    return True


# ── Pjesme samo s notama, traženje pjesme za note ─────────────────────────────

NOTE_TITLE = 0.90             # naslov nota ≈ naslov pjesme (bez "sadržan u")
NOTE_WORDS = 0.60             # riječi teksta ispod nota sadržane u pjesmi
NOTE_MIN_WORDS = 8


def note_title_similarity(a, b):
    """Strože od title_similarity: "Aleluja" NIJE "Aleluja (Isus Krist je Bog)"."""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _word_overlap(words, lyrics_norm):
    """Koliki dio (različitih) riječi iz nota postoji u tekstu pjesme."""
    ws = {w for w in words if len(w) > 2}
    if len(ws) < NOTE_MIN_WORDS:
        return 0.0
    have = set(lyrics_norm.split())
    return len(ws & have) / len(ws)


def find_song_for_notes(conn, title="", words=()):
    """Pjesma kojoj pripadaju note: po naslovu (i tekstu ispod nota kad je više
    pjesama istog naslova), a bez naslova po tekstu. Vrati (id, naslov) ili None."""
    words = [w for w in norm(" ".join(words)).split()]
    by_title, by_words = [], []
    for row in conn.execute("SELECT id, title, subtitle, norm_lyrics FROM songs"):
        t = max(note_title_similarity(title, row["title"]),
                note_title_similarity(title, f'{row["title"]} {row["subtitle"]}'))
        w = _word_overlap(words, row["norm_lyrics"]) if words else 0.0
        if title and t >= NOTE_TITLE:
            by_title.append((t, w, row["id"], row["title"]))
        elif w >= NOTE_WORDS:
            by_words.append((w, row["id"], row["title"]))
    if by_title:
        best = max(by_title, key=lambda x: (x[1], x[0], -x[2]))
        return best[2], best[3]
    if by_words and not title:
        best = max(by_words)
        return best[1], best[2]
    if by_words:
        # naslov nota se razlikuje, ali tekst ispod nota je jasno ta pjesma
        best = max(by_words)
        if best[0] >= 0.8:
            return best[1], best[2]
    return None


def _notes_only_song(conn, song):
    """Postojeća pjesma istog naslova koja ima samo note (bez teksta)."""
    if not song.get("sections"):
        return None
    for row in conn.execute(f"SELECT id, title FROM songs WHERE {NOTES_ONLY_SQL}"):
        if note_title_similarity(song["title"], row["title"]) >= NOTE_TITLE:
            return row["id"]
    return None


def create_notes_song(conn, title, source=None):
    """Nova pjesma koja zasad ima samo note (tekst se popuni kad stigne PDF s
    tekstom istog naslova)."""
    song = clean_song({"title": title or "(bez naslova)", "key": "", "subtitle": "",
                       "meta": [], "sections": [], "layout": {"profile": "v2"}})
    return _insert(conn, song, source)


# ── Snimke (YouTube, Google disk, audio) ──────────────────────────────────────

def _link_row(r):
    _url, kind, embed = songlinks.parse(r["url"])
    return {"id": r["id"], "voice": r["voice"], "title": r["title"], "url": r["url"],
            "kind": r["kind"], "embed": embed, "created_at": r["created_at"]}


def _link_counts(conn, song_ids):
    if not song_ids:
        return {}
    marks = ",".join("?" * len(song_ids))
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT song_id, COUNT(*) FROM song_links WHERE song_id IN ({marks}) GROUP BY song_id",
        song_ids)}


def list_links(conn, song_id):
    rows = [_link_row(r) for r in conn.execute(
        "SELECT * FROM song_links WHERE song_id=? ORDER BY id", (song_id,))]
    return sorted(rows, key=lambda x: (songlinks.voice_order(x["voice"]), x["id"]))


def add_link(conn, song_id, url, voice="", title=""):
    """Dodaj snimku. Vrati opis, None ako pjesme nema; digne songlinks.LinkError."""
    if conn.execute("SELECT 1 FROM songs WHERE id=?", (song_id,)).fetchone() is None:
        return None
    url, kind, _embed = songlinks.parse(url)
    voice, title = songlinks.clean_text(voice, 30), songlinks.clean_text(title)
    dup = conn.execute("SELECT * FROM song_links WHERE song_id=? AND url=? AND voice=?",
                       (song_id, url, voice)).fetchone()
    if dup:
        return _link_row(dup) | {"duplicate": True}
    lid = conn.execute(
        "INSERT INTO song_links (song_id, voice, title, url, kind, created_at) VALUES (?,?,?,?,?,?)",
        (song_id, voice, title, url, kind, _now())).lastrowid
    return _link_row(conn.execute("SELECT * FROM song_links WHERE id=?", (lid,)).fetchone())


def update_link(conn, link_id, voice=None, title=None):
    r = conn.execute("SELECT * FROM song_links WHERE id=?", (link_id,)).fetchone()
    if r is None:
        return False
    conn.execute("UPDATE song_links SET voice=?, title=? WHERE id=?",
                 (songlinks.clean_text(voice, 30) if voice is not None else r["voice"],
                  songlinks.clean_text(title) if title is not None else r["title"], link_id))
    return True


def delete_link(conn, link_id):
    return conn.execute("DELETE FROM song_links WHERE id=?", (link_id,)).rowcount > 0


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
            for t in (source or {}).get("tags") or []:
                set_tag(conn, result, t, group=songtags.COLLECTION)
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
            "imports": one("SELECT COUNT(*) FROM imports"),
            "notes": one("SELECT COUNT(*) FROM attachments"),
            "links": one("SELECT COUNT(*) FROM song_links"),
            "notes_only": one(f"SELECT COUNT(*) FROM songs WHERE {NOTES_ONLY_SQL}")}
