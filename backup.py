#!/usr/bin/env python3
"""
Automatski backup baze pjesama i nota.

Jednom dnevno (pri prvom zahtjevu tog dana, u pozadini) nastaje mapa
data/backup/<datum_vrijeme>/ s:
  * pjesme.db — dosljedna kopija SQLite baze (sqlite backup API, i dok aplikacija radi)
  * note/     — datoteke nota; tvrde poveznice (hard link) na iste datoteke, pa
                ne zauzimaju dodatni prostor (note se nikad ne mijenjaju, samo brišu)
  * info.json — broj pjesama/nota, veličina
Čuva se zadnjih BACKUP_KEEP (zadano 14) kopija. Backup je na istom disku —
štiti od grešaka (brisanje, krivi uvoz); za zaštitu od kvara diska preuzmi
ZIP (gumb 💾 u bazi) ili kopiraj mapu data/backup na drugo računalo.
"""

import fcntl
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("backup")
CHECK_EVERY = 600             # s — koliko često zahtjev provjerava treba li backup
_last_check = {}
_lock = threading.Lock()


def keep():
    try:
        return max(1, int(os.environ.get("BACKUP_KEEP") or 14))
    except ValueError:
        return 14


def disabled():
    return os.environ.get("BACKUP_DISABLE") == "1"


def backup_dir(db_file):
    return Path(db_file).parent / "backup"


def _info(path):
    try:
        return json.loads((path / "info.json").read_text())
    except (OSError, ValueError):
        return {}


def list_backups(db_file):
    root = backup_dir(db_file)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir(), reverse=True):
        if p.is_dir() and (p / "pjesme.db").is_file():
            out.append({"name": p.name, **_info(p)})
    return out


def make_backup(db_file, reason="dnevni"):
    """Napravi backup odmah. Vrati opis (dict)."""
    db_file = Path(db_file)
    root = backup_dir(db_file)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    name = now.strftime("%Y-%m-%d_%H%M%S")
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=root))
    try:
        src = sqlite3.connect(str(db_file), timeout=30)
        dst = sqlite3.connect(str(tmp / "pjesme.db"))
        try:
            src.backup(dst)
            songs = dst.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        finally:
            dst.close()
            src.close()
        notes, size = 0, (tmp / "pjesme.db").stat().st_size
        note_src = db_file.parent / "note"
        if note_src.is_dir():
            (tmp / "note").mkdir()
            for f in note_src.iterdir():
                if not f.is_file() or f.name.endswith(".tmp"):
                    continue
                try:
                    os.link(f, tmp / "note" / f.name)          # bez dodatnog prostora
                except OSError:
                    shutil.copy2(f, tmp / "note" / f.name)
                notes += 1
                size += f.stat().st_size
        info = {"created": now.replace(microsecond=0).isoformat(), "reason": reason,
                "songs": songs, "notes": notes, "size": size}
        (tmp / "info.json").write_text(json.dumps(info))
        final = root / name
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    prune(db_file)
    return {"name": name, **info}


def prune(db_file):
    for old in list_backups(db_file)[keep():]:
        shutil.rmtree(backup_dir(db_file) / old["name"], ignore_errors=True)
    for p in backup_dir(db_file).glob(".tmp-*"):            # ostaci prekinutog backupa
        if time.time() - p.stat().st_mtime > 3600:
            shutil.rmtree(p, ignore_errors=True)


def ensure_daily(db_file):
    """Pozovi na zahtjevu: ako današnjeg backupa nema, napravi ga u pozadini."""
    if disabled() or not db_file:
        return
    key = str(db_file)
    now = time.time()
    with _lock:
        if now - _last_check.get(key, 0) < CHECK_EVERY:
            return
        _last_check[key] = now
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if any(b["name"].startswith(today) for b in list_backups(db_file)):
        return
    threading.Thread(target=_daily, args=(key, today), daemon=True, name="backup").start()


def _daily(db_file, today):
    try:
        with open(backup_dir(db_file).parent / "backup.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # drugi worker već radi
            except OSError:
                return
            if any(b["name"].startswith(today) for b in list_backups(db_file)):
                return
            make_backup(db_file)
    except Exception:  # noqa: BLE001
        log.exception("backup nije uspio")


def zip_path(db_file, name):
    """ZIP backupa za preuzimanje -> (putanja privremene datoteke) ili None."""
    if not name or "/" in name or name.startswith("."):
        return None
    path = backup_dir(db_file) / name
    if not (path / "pjesme.db").is_file():
        return None
    fd, out = tempfile.mkstemp(prefix="backup-", suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(path.rglob("*")):
            if f.is_file():
                z.write(f, f"pjesme-backup-{name}/{f.relative_to(path)}")
    return out
