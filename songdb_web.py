#!/usr/bin/env python3
"""
Web dio baze pjesama (Flask Blueprint):

  /baza                 stranica: uvoz PDF-ova, slične pjesme (odluka),
                        popis i pretraga, pregled s akordima, preuzimanja
  /db/...               JSON za stranicu (pisanje: lozinka DB_PASSWORD ako je zadana)
  /api/v1/...           samo čitanje, za druge aplikacije (token API_TOKEN ako je
                        zadan; CORS dopušten)
"""

import hmac
import io
import json
import os
import re
import unicodedata
from pathlib import Path

from flask import (Blueprint, Response, g, jsonify, make_response, request,
                   send_file)

import backup
import chord_extract
import chord_render
import importjobs
import musicxml
import omr
import setlists
import songdb
import songedit
import songimport
import songlinks
import songnotes
import songtags
import transpose

bp = Blueprint("songdb", __name__)


def _conn():
    """Jedna veza prema bazi po zahtjevu (zatvara se na kraju zahtjeva)."""
    if "songdb_conn" not in g:
        g.songdb_conn = songdb.connect()
        if g.songdb_conn.db_file:
            omr.resume(g.songdb_conn.db_file)      # poslovi prekinuti restartom
            importjobs.resume(g.songdb_conn.db_file)
            backup.ensure_daily(g.songdb_conn.db_file)
    return g.songdb_conn


@bp.teardown_app_request
def _close_conn(_exc):
    conn = g.pop("songdb_conn", None)
    if conn is not None:
        conn.close()


def _secret_ok(given, expected):
    return hmac.compare_digest((given or "").encode(), expected.encode())


def _require_write():
    """Pisanje u bazu: ako je postavljena DB_PASSWORD, traži je u zaglavlju."""
    pw = os.environ.get("DB_PASSWORD")
    if pw and not _secret_ok(request.headers.get("X-DB-Password"), pw):
        return jsonify(error="Potrebna je lozinka baze.", need_password=True), 401
    return None


def _ascii_name(name):
    """Naziv datoteke bez dijakritika (Bože svet si -> Boze_svet_si)."""
    s = unicodedata.normalize("NFKD", (name or "").translate({ord("đ"): "d", ord("Đ"): "D"}))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60] or "pjesma"


def _pdf_response(data, name, inline=False):
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     as_attachment=not inline, download_name=name + ".pdf")


# ── Uvoz ──────────────────────────────────────────────────────────────────────

@bp.post("/db/import")
def db_import():
    denied = _require_write()
    if denied:
        return denied
    files = request.files.getlist("pdf") + request.files.getlist("file")
    if not files:
        return jsonify(error="Nije poslan nijedan PDF ni ZIP."), 400
    conn = _conn()
    tags = [songdb.tag_name(conn, t)
            for t in songtags.parse_tag_input(request.form.get("tags", ""))]
    reports, jobs = [], []
    for f in files:
        name = os.path.basename(f.filename or "dokument.pdf")
        data = f.read()
        if name.lower().endswith(".zip") and conn.db_file and request.form.get("wait") != "1":
            jobs.append(importjobs.enqueue(conn, name, data, tags))     # ZIP: u pozadini
        else:
            reports.extend(songimport.import_upload(conn, name, data, tags))
    return jsonify(reports=reports, jobs=jobs, tags=tags, stats=songdb.stats(conn))


@bp.get("/db/import-jobs")
def db_import_jobs():
    return jsonify(jobs=importjobs.active(_conn()))


@bp.get("/db/import-jobs/<int:job_id>")
def db_import_job(job_id):
    job = importjobs.get(_conn(), job_id)
    return jsonify(job=job) if job else (jsonify(error="Uvoz ne postoji."), 404)


# ── Čitanje (stranica) ────────────────────────────────────────────────────────

def _int_arg(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


@bp.get("/db/stats")
def db_stats():
    return jsonify(songdb.stats(_conn()))


@bp.get("/db/songs")
def db_songs():
    q = request.args.get("q", "")
    exclude = [x for v in request.args.getlist("exclude") for x in v.split(",")]
    return jsonify(songdb.list_songs(_conn(), q, _int_arg("limit", 200, 1, 1000),
                                     _int_arg("offset", 0, 0, 10**9),
                                     tag=request.args.get("tag") or None, exclude=exclude))


@bp.get("/db/tags")
def db_tags():
    return jsonify(songdb.list_tags(_conn()))


@bp.post("/db/tags/suggest")
def db_tags_suggest():
    denied = _require_write()
    if denied:
        return denied
    songs, tags = songdb.suggest_all(_conn())
    return jsonify(ok=True, songs=songs, tags=tags)


@bp.post("/db/tags/bulk")
def db_tags_bulk():
    denied = _require_write()
    if denied:
        return denied
    d = request.get_json(silent=True) or {}
    action = d.get("action", "add")
    if action not in ("add", "remove"):
        return jsonify(error="action mora biti add ili remove"), 400
    try:
        n = songdb.bulk_tag(_conn(), [int(i) for i in (d.get("ids") or [])[:2000]],
                            str(d.get("name") or ""), action)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, changed=n)


@bp.post("/db/songs/<int:song_id>/tags")
def db_song_tag_add(song_id):
    denied = _require_write()
    if denied:
        return denied
    try:
        ok = songdb.set_tag(_conn(), song_id, str((request.get_json(silent=True) or {}).get("name") or ""))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not ok:
        return jsonify(error="Pjesma ne postoji."), 404
    return jsonify(tags=songdb.song_tags(_conn(), song_id))


@bp.delete("/db/songs/<int:song_id>/tags/<path:name>")
def db_song_tag_remove(song_id, name):
    denied = _require_write()
    if denied:
        return denied
    songdb.remove_tag(_conn(), song_id, name)
    return jsonify(tags=songdb.song_tags(_conn(), song_id))


def _tr():
    """?transpose=n (polutonovi) na pjesmi, PDF-u, ChordPro i API-ju."""
    return transpose.normalize(request.args.get("transpose"))


@bp.get("/db/songs/<int:song_id>")
def db_song(song_id):
    s = songdb.get_song(_conn(), song_id, _tr())
    return jsonify(s) if s else (jsonify(error="Pjesma ne postoji."), 404)


# ── Popis za misu ─────────────────────────────────────────────────────────────

def _json_body():
    return request.get_json(silent=True) or {}


@bp.get("/db/setlists")
def db_setlists():
    return jsonify(setlists=setlists.list_setlists(_conn()), parts=setlists.PARTS)


@bp.post("/db/setlists")
def db_setlist_create():
    denied = _require_write()
    if denied:
        return denied
    d = _json_body()
    conn = _conn()
    sid = setlists.create(conn, str(d.get("title") or ""), str(d.get("date") or ""))
    return jsonify(setlist=setlists.get(conn, sid))


@bp.get("/db/setlists/<int:setlist_id>")
def db_setlist(setlist_id):
    sl = setlists.get(_conn(), setlist_id)
    return jsonify(setlist=sl, parts=setlists.PARTS) if sl else (jsonify(error="Popis ne postoji."), 404)


@bp.patch("/db/setlists/<int:setlist_id>")
def db_setlist_update(setlist_id):
    denied = _require_write()
    if denied:
        return denied
    d = _json_body()
    ok = setlists.update(_conn(), setlist_id, d.get("title"), d.get("date"), d.get("note"))
    return jsonify(ok=True) if ok else (jsonify(error="Popis ne postoji."), 404)


@bp.delete("/db/setlists/<int:setlist_id>")
def db_setlist_delete(setlist_id):
    denied = _require_write()
    if denied:
        return denied
    ok = setlists.delete(_conn(), setlist_id)
    return jsonify(ok=True) if ok else (jsonify(error="Popis ne postoji."), 404)


@bp.post("/db/setlists/<int:setlist_id>/token")
def db_setlist_token(setlist_id):
    denied = _require_write()
    if denied:
        return denied
    tok = setlists.new_token(_conn(), setlist_id)
    return jsonify(token=tok) if tok else (jsonify(error="Popis ne postoji."), 404)


@bp.post("/db/setlists/<int:setlist_id>/items")
def db_setlist_add(setlist_id):
    denied = _require_write()
    if denied:
        return denied
    d = _json_body()
    try:
        iid = setlists.add_item(_conn(), setlist_id, int(d.get("song_id") or 0),
                                str(d.get("part") or ""), d.get("transpose") or 0)
    except (setlists.SetlistError, ValueError) as exc:
        return jsonify(error=str(exc) or "Neispravna pjesma."), 400
    if iid is None:
        return jsonify(error="Popis ne postoji."), 404
    return jsonify(item_id=iid, setlist=setlists.get(_conn(), setlist_id))


@bp.patch("/db/setlist-items/<int:item_id>")
def db_setlist_item_update(item_id):
    denied = _require_write()
    if denied:
        return denied
    d = _json_body()
    ok = setlists.update_item(_conn(), item_id, d.get("part"), d.get("transpose"))
    return jsonify(ok=True) if ok else (jsonify(error="Stavka ne postoji."), 404)


@bp.post("/db/setlist-items/<int:item_id>/move")
def db_setlist_item_move(item_id):
    denied = _require_write()
    if denied:
        return denied
    ok = setlists.move_item(_conn(), item_id, int(_json_body().get("step") or 1))
    return jsonify(ok=True) if ok else (jsonify(error="Stavka ne postoji."), 404)


@bp.delete("/db/setlist-items/<int:item_id>")
def db_setlist_item_delete(item_id):
    denied = _require_write()
    if denied:
        return denied
    ok = setlists.remove_item(_conn(), item_id)
    return jsonify(ok=True) if ok else (jsonify(error="Stavka ne postoji."), 404)


@bp.get("/db/setlists/suggest")
def db_setlist_suggest():
    return jsonify(songs=setlists.suggestions(_conn(), request.args.get("part", "")))


def _setlist_pdf(sl):
    name = _ascii_name(sl["title"] + ("_" + sl["date"] if sl["date"] else ""))
    return _pdf_response(setlists.pdf(_conn(), sl), name, inline=request.args.get("inline") == "1")


@bp.get("/db/setlists/<int:setlist_id>/pdf")
def db_setlist_pdf(setlist_id):
    sl = setlists.get(_conn(), setlist_id)
    return _setlist_pdf(sl) if sl else (jsonify(error="Popis ne postoji."), 404)


@bp.get("/db/setlists/<int:setlist_id>/text")
def db_setlist_text(setlist_id):
    sl = setlists.get(_conn(), setlist_id)
    if sl is None:
        return jsonify(error="Popis ne postoji."), 404
    return jsonify(text=setlists.as_text(sl))


@bp.get("/db/setlists/<int:setlist_id>/editor")
def db_setlist_editor(setlist_id):
    conn = _conn()
    sl = setlists.get(conn, setlist_id)
    if sl is None:
        return jsonify(error="Popis ne postoji."), 404
    return jsonify(title=sl["title"], songs=[song_to_editor(d) for _it, d in setlists.songs(conn, sl)])


# javni link za zbor: samo čitanje, bez lozinke (u Caddyju izuzeti /p/*)
def _public(token):
    sl = setlists.get(_conn(), token=token) if token else None
    return sl


@bp.get("/p/<token>")
def public_setlist(token):
    sl = _public(token)
    if sl is None:
        return Response("Popis ne postoji (ili je link promijenjen).", status=404,
                        mimetype="text/plain")
    base = f"{request.script_root}/p/{token}"
    resp = make_response(setlists.public_html(_conn(), sl, base))
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@bp.get("/p/<token>/pdf")
def public_setlist_pdf(token):
    sl = _public(token)
    return _setlist_pdf(sl) if sl else (jsonify(error="Popis ne postoji."), 404)


@bp.get("/p/<token>/notes/<int:note_id>")
def public_setlist_note(token, note_id):
    sl = _public(token)
    found = songdb.get_note(_conn(), note_id) if sl else None
    if found is None or found[0]["song_id"] not in {it["song_id"] for it in sl["items"]}:
        return jsonify(error="Note ne postoje."), 404
    return _note_file(note_id)


# ── Backup ────────────────────────────────────────────────────────────────────

@bp.get("/db/backups")
def db_backups():
    conn = _conn()
    if not conn.db_file:
        return jsonify(backups=[], keep=backup.keep())
    return jsonify(backups=backup.list_backups(conn.db_file), keep=backup.keep(),
                   disabled=backup.disabled())


@bp.post("/db/backups")
def db_backup_now():
    denied = _require_write()
    if denied:
        return denied
    conn = _conn()
    if not conn.db_file:
        return jsonify(error="Nema baze na disku."), 400
    return jsonify(backup=backup.make_backup(conn.db_file, "ručno"))


@bp.get("/db/backups/<name>.zip")
def db_backup_download(name):
    denied = _require_write()            # cijela baza: samo uz lozinku
    if denied:
        return denied
    conn = _conn()
    path = backup.zip_path(conn.db_file, name) if conn.db_file else None
    if path is None:
        return jsonify(error="Backup ne postoji."), 404
    resp = send_file(path, mimetype="application/zip", as_attachment=True,
                     download_name=f"pjesme-backup-{name}.zip", max_age=0)
    resp.call_on_close(lambda: os.path.exists(path) and os.remove(path))
    return resp


# ── Uređivanje pjesme ─────────────────────────────────────────────────────────

@bp.get("/db/songs/<int:song_id>/edit")
def db_song_edit_form(song_id):
    data = songdb.song_data(_conn(), song_id)
    if data is None:
        return jsonify(error="Pjesma ne postoji."), 404
    body, meta = songedit.to_text(data)
    return jsonify(title=data.get("title") or "", subtitle=data.get("subtitle") or "",
                   key=data.get("key") or "", body=body, meta=meta)


@bp.post("/db/songs/<int:song_id>/preview")
def db_song_edit_preview(song_id):
    d = request.get_json(silent=True) or {}
    try:
        return jsonify(sections=songedit.parse_text(str(d.get("body") or "")))
    except songedit.EditError as exc:
        return jsonify(error=str(exc)), 400


@bp.put("/db/songs/<int:song_id>")
def db_song_update(song_id):
    denied = _require_write()
    if denied:
        return denied
    d = request.get_json(silent=True) or {}
    conn = _conn()
    old = songdb.song_data(conn, song_id)
    if old is None:
        return jsonify(error="Pjesma ne postoji."), 404
    try:
        new = songedit.apply(old, d.get("title"), d.get("subtitle"), d.get("key"),
                             str(d.get("body") or ""), str(d.get("meta") or ""))
    except songedit.EditError as exc:
        return jsonify(error=str(exc)), 400
    changed = songdb.update_song(conn, song_id, new)
    return jsonify(ok=True, changed=bool(changed), song=songdb.get_song(conn, song_id))


@bp.post("/db/songs/<int:song_id>/merge")
def db_song_merge(song_id):
    """Spoji drugu pjesmu (duplikat) u ovu."""
    denied = _require_write()
    if denied:
        return denied
    d = request.get_json(silent=True) or {}
    text_from = d.get("text_from") or "auto"
    if text_from not in ("auto", "keep", "other"):
        return jsonify(error="text_from mora biti auto, keep ili other"), 400
    try:
        ok = songdb.merge_songs(_conn(), song_id, int(d.get("other_id") or 0), text_from)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not ok:
        return jsonify(error="Pjesma ne postoji."), 404
    return jsonify(ok=True, song=songdb.get_song(_conn(), song_id))


@bp.get("/db/songs/<int:song_id>/versions")
def db_song_versions(song_id):
    return jsonify(versions=songdb.list_versions(_conn(), song_id))


@bp.post("/db/songs/<int:song_id>/versions/<int:version_id>/restore")
def db_song_restore(song_id, version_id):
    denied = _require_write()
    if denied:
        return denied
    ok = songdb.restore_version(_conn(), song_id, version_id)
    return jsonify(ok=True) if ok else (jsonify(error="Verzija ne postoji."), 404)


@bp.delete("/db/songs/<int:song_id>")
def db_delete(song_id):
    denied = _require_write()
    if denied:
        return denied
    ok = songdb.delete_song(_conn(), song_id)
    return jsonify(ok=ok) if ok else (jsonify(error="Pjesma ne postoji."), 404)


# ── Note (notni zapis) uz pjesmu ──────────────────────────────────────────────

@bp.get("/db/songs/<int:song_id>/notes")
def db_notes(song_id):
    return jsonify(notes=songdb.list_notes(_conn(), song_id))


@bp.post("/db/songs/<int:song_id>/notes")
def db_note_add(song_id):
    denied = _require_write()
    if denied:
        return denied
    files = request.files.getlist("file")
    if not files:
        return jsonify(error="Nije poslana nijedna datoteka."), 400
    conn = _conn()
    added, errors = [], []
    for f in files:
        try:
            n = songdb.add_note(conn, song_id, f.filename or "", f.read(),
                                request.form.get("name") if len(files) == 1 else None)
        except songnotes.NoteError as exc:
            errors.append(str(exc))
            continue
        if n is None:
            return jsonify(error="Pjesma ne postoji."), 404
        added.append(n)
    if not added:
        return jsonify(error=" ".join(errors)), 400
    return jsonify(added=added, errors=errors, notes=songdb.list_notes(conn, song_id))


def _note_file(note_id):
    found = songdb.get_note(_conn(), note_id)
    if found is None or not found[1].exists():
        return jsonify(error="Note ne postoje."), 404
    note, path = found
    inline = note["inline"] and request.args.get("download") != "1"
    resp = send_file(path, mimetype=note["mime"], as_attachment=not inline,
                     download_name=note["filename"], max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.get("/db/notes/<int:note_id>")
def db_note_file(note_id):
    return _note_file(note_id)


def _musicxml_note(note_id):
    """(opis, bajtovi) MusicXML note ili odgovor s greškom."""
    found = songdb.get_note(_conn(), note_id)
    if found is None or not found[1].exists():
        return None, (jsonify(error="Note ne postoje."), 404)
    note, path = found
    if note["kind"] != "musicxml":
        return None, (jsonify(error="Ovo radi samo s MusicXML notama (.musicxml/.mxl)."), 400)
    return (note, path.read_bytes()), None


def _vocab(conn, song_id):
    """Poznate riječi za spajanje slogova: iz svih pjesama u bazi i iz teksta
    ispod nota u PDF-ovima iste pjesme (ondje su slogovi točno označeni)."""
    import collections

    import pdfplumber
    import scorepages
    vocab = collections.Counter()
    for (lyr,) in conn.execute("SELECT lyrics FROM songs"):
        for w in lyr.split():
            vocab[musicxml._norm_word(w)] += 1
    for n in songdb.list_notes(conn, song_id):
        if n["kind"] != "pdf":
            continue
        found = songdb.get_note(conn, n["id"])
        try:
            with pdfplumber.open(str(found[1])) as doc:
                for page in doc.pages[:20]:
                    info = scorepages.classify(page)
                    for w in scorepages.lyric_words(page, info) if info else []:
                        vocab[musicxml._norm_word(w)] += 3       # isti izvor vrijedi više
        except Exception:  # noqa: BLE001 — loš PDF: samo bez tih riječi
            continue
    vocab.pop("", None)
    return vocab


@bp.get("/db/notes/<int:note_id>/lyrics")
def db_note_lyrics(note_id):
    """Prijedlog teksta (i akorda) pjesme iz MusicXML nota — za urednik."""
    got, err = _musicxml_note(note_id)
    if err:
        return err
    note, data = got
    try:
        res = musicxml.lyrics_text(data, _vocab(_conn(), note["song_id"]))
    except musicxml.MusicXmlError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(body=res["body"], verses=res["verses"], chords=res["chords"], title=res["title"])


@bp.get("/db/notes/<int:note_id>/events")
def db_note_events(note_id):
    """Note po dionicama za vježbanje glasova (zvuk u pregledniku)."""
    got, err = _musicxml_note(note_id)
    if err:
        return err
    try:
        return jsonify(musicxml.events(got[1]))
    except musicxml.MusicXmlError as exc:
        return jsonify(error=str(exc)), 400


@bp.patch("/db/notes/<int:note_id>")
def db_note_rename(note_id):
    denied = _require_write()
    if denied:
        return denied
    try:
        ok = songdb.rename_note(_conn(), note_id, (request.get_json(silent=True) or {}).get("name"))
    except songnotes.NoteError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True) if ok else (jsonify(error="Note ne postoje."), 404)


@bp.delete("/db/notes/<int:note_id>")
def db_note_delete(note_id):
    denied = _require_write()
    if denied:
        return denied
    ok = songdb.delete_note(_conn(), note_id)
    return jsonify(ok=True) if ok else (jsonify(error="Note ne postoje."), 404)


@bp.post("/db/notes/<int:note_id>/move")
def db_note_move(note_id):
    """Premjesti note na drugu pjesmu (pogrešno pridružene pri uvozu)."""
    denied = _require_write()
    if denied:
        return denied
    try:
        target = int((request.get_json(silent=True) or {}).get("song_id"))
    except (TypeError, ValueError):
        return jsonify(error="Odaberi pjesmu."), 400
    ok = songdb.move_note(_conn(), note_id, target)
    return jsonify(ok=True) if ok else (jsonify(error="Note ili pjesma ne postoje."), 404)


# ── Prepoznavanje nota (OMR, neobavezno) ─────────────────────────────────────

@bp.post("/db/notes/<int:note_id>/omr")
def db_note_omr(note_id):
    denied = _require_write()
    if denied:
        return denied
    try:
        job = omr.enqueue(_conn(), note_id)
    except omr.OmrError as exc:
        return jsonify(error=str(exc)), (503 if not omr.available() else 400)
    return jsonify(job=job)


@bp.get("/db/omr/<int:job_id>")
def db_omr_job(job_id):
    job = omr.get_job(_conn(), job_id)
    return jsonify(job=job) if job else (jsonify(error="Posao ne postoji."), 404)


@bp.get("/db/songs/<int:song_id>/omr")
def db_song_omr(song_id):
    return jsonify(available=omr.available(), jobs=omr.jobs_for_song(_conn(), song_id))


# ── Snimke (YouTube po glasovima, Google disk, audio) ─────────────────────────

@bp.post("/db/songs/<int:song_id>/links")
def db_link_add(song_id):
    denied = _require_write()
    if denied:
        return denied
    d = request.get_json(silent=True) or {}
    conn = _conn()
    try:
        link = songdb.add_link(conn, song_id, str(d.get("url") or ""), str(d.get("voice") or ""),
                               str(d.get("title") or ""))
    except songlinks.LinkError as exc:
        return jsonify(error=str(exc)), 400
    if link is None:
        return jsonify(error="Pjesma ne postoji."), 404
    return jsonify(link=link, links=songdb.list_links(conn, song_id))


@bp.patch("/db/links/<int:link_id>")
def db_link_update(link_id):
    denied = _require_write()
    if denied:
        return denied
    d = request.get_json(silent=True) or {}
    ok = songdb.update_link(_conn(), link_id, d.get("voice"), d.get("title"))
    return jsonify(ok=True) if ok else (jsonify(error="Snimka ne postoji."), 404)


@bp.delete("/db/links/<int:link_id>")
def db_link_delete(link_id):
    denied = _require_write()
    if denied:
        return denied
    ok = songdb.delete_link(_conn(), link_id)
    return jsonify(ok=True) if ok else (jsonify(error="Snimka ne postoji."), 404)


@bp.get("/db/pending")
def db_pending():
    return jsonify(items=songdb.list_pending(_conn()))


@bp.post("/db/pending/all")
def db_resolve_all():
    denied = _require_write()
    if denied:
        return denied
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in songdb.RESOLVE_ACTIONS:
        return jsonify(error="Odluka mora biti: " + ", ".join(songdb.RESOLVE_ACTIONS)), 400
    return jsonify(ok=True, resolved=songdb.resolve_all(_conn(), action))


@bp.post("/db/pending/<int:pending_id>")
def db_resolve(pending_id):
    denied = _require_write()
    if denied:
        return denied
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in songdb.RESOLVE_ACTIONS:
        return jsonify(error="Odluka mora biti: " + ", ".join(songdb.RESOLVE_ACTIONS)), 400
    sid = songdb.resolve(_conn(), pending_id, action)
    if sid is None:
        return jsonify(error="Ta odluka je već riješena."), 404
    return jsonify(ok=True, song_id=sid)


def _song_pdf(song_id, inline):
    conn = _conn()
    data = songdb.song_data(conn, song_id, _tr())
    if data is None:
        return jsonify(error="Pjesma ne postoji."), 404
    name = _ascii_name(data["title"]) + (f"_{data['key']}" if _tr() and data.get("key") else "")
    return _pdf_response(chord_render.render_pdf([data]), _ascii_name(name), inline)


@bp.get("/db/songs/<int:song_id>/pdf")
def db_song_pdf(song_id):
    return _song_pdf(song_id, request.args.get("inline") == "1")


def _chordpro_response(song_id):
    data = songdb.song_data(_conn(), song_id, _tr())
    if data is None:
        return jsonify(error="Pjesma ne postoji."), 404
    resp = make_response(chord_extract.to_chordpro(data))
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = \
        f'attachment; filename="{_ascii_name(data["title"])}.cho"'
    return resp


@bp.get("/db/songs/<int:song_id>/chordpro")
def db_song_chordpro(song_id):
    return _chordpro_response(song_id)


@bp.post("/db/pdf")
def db_many_pdf():
    """Više pjesama u jednom PDF-u (izvorni izgled, redom kako su poslane)."""
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    tr = body.get("transpose") or {}             # {"id": n} po pjesmi (neobavezno)
    conn = _conn()
    songs = [s for s in (songdb.song_data(conn, int(i), transpose.normalize(tr.get(str(i))))
                         for i in ids[:500]) if s]
    if not songs:
        return jsonify(error="Nijedna pjesma nije odabrana."), 400
    return _pdf_response(chord_render.render_pdf(songs), "pjesme_iz_baze")


def song_to_editor(song):
    """Pjesma iz baze -> polja urednika pjesmarice (akordi kao redak iznad teksta)."""
    lines = []
    for sec in song.get("sections") or []:
        if lines and lines[-1] != "":
            lines.append("")
        if sec.get("label"):
            lines.append(sec["label"])
        for ln in sec.get("lines") or []:
            lines.extend(chord_extract.line_to_text_rows(ln))
    key = (song.get("key") or "").strip()
    return {"title": song.get("title") or "", "key": ("Key: " + key) if key else "",
            "subtitle": song.get("subtitle") or "",
            "pjesmarica": "\n".join(chord_extract.numbers(song)),
            "body": "\n".join(lines)}


@bp.post("/db/to-editor")
def db_to_editor():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    tr = body.get("transpose") or {}
    conn = _conn()
    out = [song_to_editor(s) for s in (songdb.song_data(conn, int(i), transpose.normalize(tr.get(str(i))))
                                       for i in ids[:500]) if s]
    return jsonify(songs=out)


# ── Javni API za druge aplikacije (samo čitanje) ──────────────────────────────

def _api_auth():
    token = os.environ.get("API_TOKEN")
    if not token:
        return None
    given = request.headers.get("Authorization", "")
    given = given[7:] if given.lower().startswith("bearer ") else request.args.get("token")
    if not _secret_ok(given, token):
        return jsonify(error="Neispravan ili nedostaje API token."), 401
    return None


@bp.before_request
def _api_guard():
    if request.path.startswith("/api/v1"):
        if request.method == "OPTIONS":
            return Response(status=204)
        return _api_auth()
    return None


@bp.after_request
def _api_cors(resp):
    if request.path.startswith("/api/v1"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return resp


@bp.get("/api/v1")
def api_index():
    return jsonify(name="Pjesmarica — baza pjesama", **songdb.stats(_conn()),
                   endpoints=["/api/v1/songs?q=&tag=&exclude=&limit=&offset=", "/api/v1/tags",
                              "/api/v1/songs/<id>", "/api/v1/notes/<id>",
                              "/api/v1/songs/<id>.cho", "/api/v1/songs/<id>.pdf",
                              "/api/v1/export"])


@bp.get("/api/v1/songs")
def api_songs():
    return db_songs()


@bp.get("/api/v1/tags")
def api_tags():
    return db_tags()


@bp.get("/api/v1/songs/<int:song_id>")
def api_song(song_id):
    return db_song(song_id)


@bp.get("/api/v1/notes/<int:note_id>")
def api_note_file(note_id):
    return _note_file(note_id)


@bp.get("/api/v1/songs/<int:song_id>.cho")
def api_song_chordpro(song_id):
    return _chordpro_response(song_id)


@bp.get("/api/v1/songs/<int:song_id>.pdf")
def api_song_pdf(song_id):
    return _song_pdf(song_id, inline=True)


@bp.get("/api/v1/export")
def api_export():
    resp = make_response(json.dumps({"songs": songdb.export_all(_conn())},
                                    ensure_ascii=False))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    return resp


# ── Stranica ──────────────────────────────────────────────────────────────────

VEROVIO_CDN = "https://cdn.jsdelivr.net/npm/verovio@6.3.0/dist/verovio-toolkit-wasm.js"
VEROVIO_LOCAL = "static/vendor/verovio-toolkit-wasm.js"


def _verovio_url():
    """Preglednik nota: lokalna kopija (Docker je preuzme pri izgradnji) ili CDN."""
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), VEROVIO_LOCAL)
    return VEROVIO_LOCAL if os.path.isfile(local) else VEROVIO_CDN


@bp.get("/baza")
def page():
    from app import APP_VERSION, MAX_UPLOAD_MB
    html = (DB_PAGE.replace("__VERSION__", APP_VERSION)
            .replace("__MAX_MB__", str(MAX_UPLOAD_MB))
            .replace("__NOTE_ACCEPT__", songnotes.ACCEPT)
            .replace("__OMR__", "true" if omr.available() else "false")
            .replace("__VEROVIO_URL__", _verovio_url())
            .replace("__VOICES__", json.dumps(songlinks.VOICES, ensure_ascii=False)))
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


DB_PAGE = (Path(__file__).with_name("templates") / "baza.html").read_text(encoding="utf-8")
