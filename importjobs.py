#!/usr/bin/env python3
"""
Uvoz ZIP-a u pozadini, s napretkom.

Veliki ZIP (puno PDF-ova) se obrađuje minutama; umjesto da preglednik čeka
jedan dugi zahtjev, ZIP se spremi u data/uploads/, nastane posao (tablica
import_jobs) i pozadinska nit ga obradi datoteku po datoteku, upisujući
napredak (npr. 12/80, trenutna datoteka). Stranica prati napredak i na kraju
prikaže isti izvještaj kao i kod običnog uvoza. Prekinut posao (restart
kontejnera) nastavlja se sam — ponovni uvoz istih datoteka ne stvara duplikate.
Radi jedan uvoz u isto vrijeme u cijelom kontejneru (zaključana datoteka).
"""

import fcntl
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("importjobs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT NOT NULL,
    stored       TEXT NOT NULL,
    tags         TEXT NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'queued',
    total        INTEGER NOT NULL DEFAULT 0,
    done         INTEGER NOT NULL DEFAULT 0,
    current      TEXT NOT NULL DEFAULT '',
    reports      TEXT NOT NULL DEFAULT '[]',
    message      TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT
);
"""
# status: queued | running | done | error

_lock = threading.Lock()
_running = set()
_resumed = set()


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def uploads_dir(db_file):
    return Path(db_file).parent / "uploads"


def _row(r):
    return {"id": r["id"], "filename": r["filename"], "status": r["status"], "total": r["total"],
            "done": r["done"], "current": r["current"], "message": r["message"],
            "created_at": r["created_at"], "finished_at": r["finished_at"],
            "reports": json.loads(r["reports"]) if r["status"] in ("done", "error") else []}


def get(conn, job_id):
    r = conn.execute("SELECT * FROM import_jobs WHERE id=?", (job_id,)).fetchone()
    return _row(r) if r else None


def active(conn):
    """Poslovi koji čekaju ili rade (za traku na stranici)."""
    return [_row(r) for r in conn.execute(
        "SELECT * FROM import_jobs WHERE status IN ('queued','running') ORDER BY id")]


def enqueue(conn, filename, data, tags=()):
    """Spremi ZIP i dodaj posao. Vrati opis posla."""
    if not conn.db_file:
        raise ValueError("Nema baze na disku.")
    d = uploads_dir(conn.db_file)
    d.mkdir(parents=True, exist_ok=True)
    stored = secrets.token_hex(12) + ".zip"
    tmp = d / (stored + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, d / stored)
    jid = conn.execute("INSERT INTO import_jobs (filename, stored, tags, created_at) VALUES (?,?,?,?)",
                       (filename, stored, json.dumps(list(tags), ensure_ascii=False),
                        _now())).lastrowid
    kick(conn.db_file)
    return get(conn, jid)


def resume(db_file):
    """Prvi zahtjev u procesu: nastavi poslove prekinute restartom."""
    key = str(db_file)
    if key in _resumed:
        return
    _resumed.add(key)
    if _pending(key, include_running=True):
        kick(key)


def _pending(db_file, include_running=False):
    import sqlite3  # noqa: PLC0415

    try:
        with sqlite3.connect(db_file, timeout=10) as c:
            states = "('queued','running')" if include_running else "('queued')"
            return c.execute(f"SELECT 1 FROM import_jobs WHERE status IN {states} LIMIT 1").fetchone()
    except sqlite3.Error:
        return None


def kick(db_file):
    key = str(db_file)
    with _lock:
        if key in _running:
            return
        _running.add(key)
    threading.Thread(target=_worker, args=(key,), daemon=True, name="import").start()


def _worker(db_file):
    import songdb  # noqa: PLC0415

    while True:
        try:
            with open(Path(db_file).parent / "import.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                conn = songdb.connect(db_file)
                try:
                    # imamo bravu: "running" je ostao od prekinutog procesa -> ponovo
                    conn.execute("UPDATE import_jobs SET status='queued' WHERE status='running'")
                    while True:
                        r = conn.execute("SELECT * FROM import_jobs WHERE status='queued'"
                                         " ORDER BY id LIMIT 1").fetchone()
                        if r is None:
                            break
                        conn.execute("UPDATE import_jobs SET status='running', started_at=? WHERE id=?",
                                     (_now(), r["id"]))
                        _run(conn, r)
                finally:
                    conn.close()
        except Exception:  # noqa: BLE001
            log.exception("uvoz u pozadini")
            time.sleep(5)
        with _lock:
            if not _pending(db_file):
                _running.discard(db_file)
                return


def _run(conn, r):
    import songimport  # noqa: PLC0415

    path = uploads_dir(conn.db_file) / r["stored"]
    last = [0.0]

    def progress(done, total, current):
        now = time.time()
        if now - last[0] >= 0.5 or done == total:
            last[0] = now
            conn.execute("UPDATE import_jobs SET done=?, total=?, current=? WHERE id=?",
                         (done, total, current[:300], r["id"]))

    try:
        data = path.read_bytes()
        reports = songimport.import_zip(conn, r["filename"], data, json.loads(r["tags"]),
                                        progress=progress)
        conn.execute("UPDATE import_jobs SET status='done', reports=?, current='', finished_at=?,"
                     " done=total WHERE id=?",
                     (json.dumps(reports, ensure_ascii=False), _now(), r["id"]))
    except Exception as exc:  # noqa: BLE001
        log.exception("uvoz %s", r["filename"])
        conn.execute("UPDATE import_jobs SET status='error', message=?, finished_at=? WHERE id=?",
                     (f"Uvoz nije uspio: {type(exc).__name__}", _now(), r["id"]))
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
