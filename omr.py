#!/usr/bin/env python3
"""
Prepoznavanje nota (OMR) — Audiveris: PDF ili slika nota -> MusicXML (.mxl).

NEOBAVEZNO: radi samo ako je Audiveris instaliran (Docker slika izgrađena s
--build-arg WITH_OMR=1, vidi DEPLOY.md). Bez njega aplikacija radi normalno,
samo nema gumba „Prepoznaj note".

Prepoznavanje traje (≈15–60 s po stranici) i treba 1–2 GB RAM-a, zato ide u
pozadini: zahtjev napravi posao (tablica omr_jobs), a pozadinska nit ga
izvrši — u cijelom kontejneru samo jedan Audiveris u isto vrijeme (zaključana
datoteka pored baze). Rezultat je nova nota (.mxl, origin "omr") uz istu
pjesmu; točnost je dobra za čiste note iz programa (Sibelius, MuseScore...),
slabija za skenove i fotografije — ispravci se rade u MuseScoreu.
"""

import fcntl
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

AUDIVERIS = os.environ.get("OMR_AUDIVERIS") or "/opt/audiveris/bin/Audiveris"
TIMEOUT = int(os.environ.get("OMR_TIMEOUT") or 1200)          # sekundi po poslu
LANGS = os.environ.get("OMR_LANGS") or "hrv+eng"               # jezik teksta ispod nota
JAVA_OPTS = os.environ.get("OMR_JAVA_OPTS") or "-Xmx1536m"
SOURCE_KINDS = ("pdf", "image")

SCHEMA = """
CREATE TABLE IF NOT EXISTS omr_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id         INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'queued',
    message         TEXT NOT NULL DEFAULT '',
    result_note_id  INTEGER,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS omr_jobs_note ON omr_jobs(note_id);
"""
# status: queued | running | done | error

_proc_lock = threading.Lock()
_running = set()                      # baze za koje u ovom procesu radi nit


class OmrError(ValueError):
    """Poruka za korisnika."""


def available():
    return os.path.isfile(AUDIVERIS) and os.access(AUDIVERIS, os.X_OK)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _job_row(r):
    return {"id": r["id"], "note_id": r["note_id"], "status": r["status"],
            "message": r["message"], "result_note_id": r["result_note_id"],
            "created_at": r["created_at"], "finished_at": r["finished_at"]}


def get_job(conn, job_id):
    r = conn.execute("SELECT * FROM omr_jobs WHERE id=?", (job_id,)).fetchone()
    return _job_row(r) if r else None


def jobs_for_song(conn, song_id):
    """Zadnji posao za svaku notu pjesme."""
    rows = conn.execute(
        "SELECT j.* FROM omr_jobs j JOIN attachments a ON a.id = j.note_id"
        " WHERE a.song_id=? AND j.id IN (SELECT MAX(id) FROM omr_jobs GROUP BY note_id)"
        " ORDER BY j.id", (song_id,)).fetchall()
    return [_job_row(r) for r in rows]


def enqueue(conn, note_id):
    """Novi posao (ili postojeći koji još čeka/radi) za note. Digne OmrError."""
    if not available():
        raise OmrError("Prepoznavanje nota nije instalirano na serveru.")
    r = conn.execute("SELECT kind FROM attachments WHERE id=?", (note_id,)).fetchone()
    if r is None:
        raise OmrError("Note ne postoje.")
    if r["kind"] not in SOURCE_KINDS:
        raise OmrError("Prepoznati se mogu samo note u PDF-u ili na slici.")
    if not conn.db_file:
        raise OmrError("Nema baze na disku.")
    cur = conn.execute("SELECT * FROM omr_jobs WHERE note_id=? AND status IN ('queued','running')",
                       (note_id,)).fetchone()
    if cur is None:
        jid = conn.execute("INSERT INTO omr_jobs (note_id, created_at) VALUES (?,?)",
                           (note_id, _now())).lastrowid
        cur = conn.execute("SELECT * FROM omr_jobs WHERE id=?", (jid,)).fetchone()
    kick(conn.db_file)
    return _job_row(cur)


def kick(db_file):
    """Pokreni pozadinsku nit (ako već ne radi u ovom procesu)."""
    key = str(db_file)
    with _proc_lock:
        if key in _running:
            return
        _running.add(key)
    threading.Thread(target=_worker, args=(key,), daemon=True, name="omr").start()


def _worker(db_file):
    import songdb  # noqa: PLC0415 — izbjegava kružni import

    try:
        lock_path = Path(db_file).parent / "omr.lock"
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)       # jedan Audiveris u cijelom kontejneru
            conn = songdb.connect(db_file)
            try:
                while True:
                    job = _claim(conn)
                    if job is None:
                        break
                    _run(conn, job)
            finally:
                conn.close()
    finally:
        with _proc_lock:
            _running.discard(db_file)


def _claim(conn):
    """Uzmi sljedeći posao (i onaj "running" koji je zapeo, npr. nakon restarta)."""
    stale = (datetime.now(timezone.utc) - timedelta(seconds=TIMEOUT + 120)).replace(
        microsecond=0).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute(
            "SELECT * FROM omr_jobs WHERE status='queued' OR (status='running' AND started_at < ?)"
            " ORDER BY id LIMIT 1", (stale,)).fetchone()
        if r:
            conn.execute("UPDATE omr_jobs SET status='running', started_at=? WHERE id=?",
                         (_now(), r["id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return r


def _finish(conn, job_id, status, message="", result=None):
    conn.execute("UPDATE omr_jobs SET status=?, message=?, result_note_id=?, finished_at=?"
                 " WHERE id=?", (status, message[:500], result, _now(), job_id))


def _run(conn, job):
    import songdb  # noqa: PLC0415

    found = songdb.get_note(conn, job["note_id"])
    if found is None or not found[1].exists():
        _finish(conn, job["id"], "error", "Note više ne postoje.")
        return
    note, path = found
    try:
        outputs = run_audiveris(path, Path(note["filename"]).suffix or ".pdf")
    except OmrError as exc:
        _finish(conn, job["id"], "error", str(exc))
        return
    first = None
    many = len(outputs) > 1
    for k, data in enumerate(outputs, start=1):
        suffix = f" — dio {k}" if many else ""
        added = songdb.add_note(
            conn, note["song_id"], f"{Path(note['filename']).stem}{suffix}.mxl", data,
            name=f"{note['name']}{suffix} (prepoznato)", origin="omr",
            source={"from_note": note["id"]})
        first = first or (added or {}).get("id")
    _finish(conn, job["id"], "done", f"Prepoznato: {len(outputs)} MusicXML", first)


MAX_PIXELS = 18_000_000            # Audiveris odbija stranice > 20 milijuna točaka
PDF_DPI = 300


def pdf_dpi(path):
    """Rezolucija za PDF: 300 dpi, ali manje za stranice velikog formata."""
    import pypdfium2 as pdfium  # noqa: PLC0415

    doc = pdfium.PdfDocument(str(path))
    try:
        area = max((w / 72) * (h / 72) for w, h in (doc.get_page_size(i) for i in range(len(doc))))
    finally:
        doc.close()
    return max(72, min(PDF_DPI, int((MAX_PIXELS / max(area, 1)) ** 0.5)))


def _shrink_image(src, dst, convert=False):
    """Velike fotografije (mobitel) smanji ispod granice Audiverisa; WebP u PNG."""
    from PIL import Image  # noqa: PLC0415

    with Image.open(src) as im:
        if im.width * im.height <= MAX_PIXELS and not convert:
            shutil.copyfile(src, dst)
            return
        f = min(1.0, (MAX_PIXELS / (im.width * im.height)) ** 0.5)
        im.convert("L").resize((int(im.width * f), int(im.height * f)), Image.LANCZOS).save(dst)


def run_audiveris(src, suffix=".pdf"):
    """Pokreni Audiveris na datoteci -> lista MusicXML (.mxl) sadržaja. Digne OmrError."""
    with tempfile.TemporaryDirectory(prefix="omr-") as tmp:
        tmp = Path(tmp)
        suffix = suffix.lower()
        constants = [f"org.audiveris.omr.text.Language.defaultSpecification={LANGS}"]
        if suffix == ".pdf":
            inp = tmp / "note.pdf"
            shutil.copyfile(src, inp)
            try:
                constants.append(f"org.audiveris.omr.image.ImageLoading.pdfResolution={pdf_dpi(inp)}")
            except Exception:  # noqa: BLE001 — neispravan PDF: neka Audiveris javi
                pass
        else:
            inp = tmp / ("note" + (".png" if suffix == ".webp" else suffix))
            try:
                _shrink_image(src, inp, convert=suffix == ".webp")
            except Exception as exc:  # noqa: BLE001
                raise OmrError("Slika nota se ne može otvoriti.") from exc
        out = tmp / "out"
        out.mkdir()
        # GDK_SCALE: Audiveris inače učitava GTK (za HiDPI zaslon), kojeg na serveru nema
        env = dict(os.environ, HOME=str(tmp), GDK_SCALE=os.environ.get("GDK_SCALE") or "1",
                   JAVA_TOOL_OPTIONS=(os.environ.get("JAVA_TOOL_OPTIONS", "") + " "
                                      + JAVA_OPTS + " -Djava.awt.headless=true").strip())
        if os.environ.get("OMR_TESSDATA"):
            env["TESSDATA_PREFIX"] = os.environ["OMR_TESSDATA"]
        cmd = [AUDIVERIS, "-batch"]
        for c in constants:
            cmd += ["-constant", c]
        cmd += ["-export", "-output", str(out), "--", str(inp)]
        try:
            proc = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True, text=True,
                                  timeout=TIMEOUT, check=False)
        except subprocess.TimeoutExpired as exc:
            raise OmrError(f"Prepoznavanje je trajalo dulje od {TIMEOUT // 60} min.") from exc
        except OSError as exc:
            raise OmrError("Audiveris se ne može pokrenuti.") from exc
        files = sorted(out.rglob("*.mxl"))
        if not files:
            tail = " ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
            raise OmrError("Audiveris nije prepoznao note (sken loše kvalitete ili nije "
                           "notni zapis?)." + (f" [{tail[-200:]}]" if tail else ""))
        return [f.read_bytes() for f in files]
