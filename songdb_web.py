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

from flask import (Blueprint, Response, g, jsonify, make_response, request,
                   send_file)

import chord_extract
import chord_render
import omr
import songdb
import songimport
import songlinks
import songnotes
import songtags

bp = Blueprint("songdb", __name__)


def _conn():
    """Jedna veza prema bazi po zahtjevu (zatvara se na kraju zahtjeva)."""
    if "songdb_conn" not in g:
        g.songdb_conn = songdb.connect()
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
    reports = []
    for f in files:
        reports.extend(songimport.import_upload(conn, f.filename or "dokument.pdf", f.read(), tags))
    return jsonify(reports=reports, tags=tags, stats=songdb.stats(conn))


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


@bp.get("/db/songs/<int:song_id>")
def db_song(song_id):
    s = songdb.get_song(_conn(), song_id)
    return jsonify(s) if s else (jsonify(error="Pjesma ne postoji."), 404)


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
    data = songdb.song_data(conn, song_id)
    if data is None:
        return jsonify(error="Pjesma ne postoji."), 404
    return _pdf_response(chord_render.render_pdf([data]), _ascii_name(data["title"]), inline)


@bp.get("/db/songs/<int:song_id>/pdf")
def db_song_pdf(song_id):
    return _song_pdf(song_id, request.args.get("inline") == "1")


def _chordpro_response(song_id):
    data = songdb.song_data(_conn(), song_id)
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
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    conn = _conn()
    songs = [s for s in (songdb.song_data(conn, int(i)) for i in ids[:500]) if s]
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
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    conn = _conn()
    out = [song_to_editor(s) for s in (songdb.song_data(conn, int(i)) for i in ids[:500]) if s]
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

@bp.get("/baza")
def page():
    from app import APP_VERSION, MAX_UPLOAD_MB
    html = (DB_PAGE.replace("__VERSION__", APP_VERSION)
            .replace("__MAX_MB__", str(MAX_UPLOAD_MB))
            .replace("__NOTE_ACCEPT__", songnotes.ACCEPT)
            .replace("__OMR__", "true" if omr.available() else "false")
            .replace("__VOICES__", json.dumps(songlinks.VOICES, ensure_ascii=False)))
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


DB_PAGE = r"""<!doctype html>
<html lang="hr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Baza pjesama</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700&family=Lora:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#d97757">
<link rel="icon" type="image/png" href="static/icon-192.png">
<style>
  :root {
    --bg: #141413; --panel: #1e1d1b; --panel2: #26241f; --border: #35332e;
    --text: #faf9f5; --muted: #b0aea5; --accent: #d97757; --accent2: #c56144;
    --ok: #788c5d; --err: #cf6a4c; --ring: rgba(217,119,87,.35);
    --add: rgba(120,140,93,.22); --del: rgba(207,106,76,.20);
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 12px 32px rgba(0,0,0,.28);
    --ui: "Poppins", Arial, sans-serif; --serif: "Lora", Georgia, serif;
    --sheet: "Open Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  body.light {
    --bg: #faf9f5; --panel: #ffffff; --panel2: #f3f1ea; --border: #e8e6dc;
    --text: #141413; --muted: #6b6862; --accent: #d97757; --accent2: #bf5b3b;
    --ok: #5f7346; --err: #b8492c; --ring: rgba(217,119,87,.28);
    --add: rgba(95,115,70,.16); --del: rgba(184,73,44,.13);
    --shadow: 0 1px 2px rgba(20,20,19,.05), 0 10px 30px rgba(20,20,19,.07);
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 var(--serif);
         display: flex; flex-direction: column; -webkit-font-smoothing: antialiased; }
  button, input, select { font-family: var(--ui); color: inherit; }
  h1, h2, h3 { font-family: var(--ui); letter-spacing: -.01em; }
  a { color: var(--accent); }
  .hidden { display: none !important; }

  header { display: flex; align-items: center; gap: .8rem; padding: .7rem 1.15rem;
           border-bottom: 1px solid var(--border); background: var(--panel); flex-wrap: wrap; }
  .logo { width: 2.1rem; height: 2.1rem; flex: none; border-radius: 10px; display: grid;
          place-items: center; font-size: 1.1rem; color: #fff;
          background: linear-gradient(135deg, var(--accent), #e0895f); box-shadow: 0 2px 8px var(--ring); }
  header h1 { margin: 0; font-size: 1.14rem; font-weight: 600; line-height: 1.1; }
  header .sub { display: block; color: var(--muted); font-size: .8rem; }
  .grow { flex: 1; }
  .chip { font-family: var(--ui); font-size: .78rem; color: var(--muted); border: 1px solid var(--border);
          border-radius: 999px; padding: .2rem .65rem; background: var(--panel2); white-space: nowrap; }
  .btn { border: 1px solid var(--border); background: var(--panel2); border-radius: 10px;
         padding: .45rem .85rem; cursor: pointer; font-weight: 500; font-size: .86rem; text-decoration: none;
         color: var(--text); display: inline-flex; align-items: center; gap: .35rem;
         transition: border-color .15s, background .15s; font-family: var(--ui); }
  .btn:hover { border-color: var(--accent); }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  .btn.primary:hover { background: var(--accent2); border-color: var(--accent2); }
  .btn.danger:hover { border-color: var(--err); color: var(--err); }
  .btn:disabled { opacity: .45; cursor: default; }

  .layout { flex: 1; display: grid; grid-template-columns: 22rem 1fr; min-height: 0; }
  aside { border-right: 1px solid var(--border); background: var(--panel); display: flex;
          flex-direction: column; min-height: 0; }
  #drop { margin: .8rem; border: 2px dashed var(--border); border-radius: 14px; padding: 1rem;
          text-align: center; cursor: pointer; background: var(--panel2); font-family: var(--ui);
          font-size: .9rem; transition: border-color .15s, background .15s; }
  #drop:hover, #drop.over { border-color: var(--accent); }
  #drop.over { background: var(--ring); }
  #drop small { display: block; color: var(--muted); font-family: var(--serif); font-size: .78rem; }
  .search { padding: 0 .8rem .6rem; }
  .search input { width: 100%; background: var(--panel2); border: 1px solid var(--border);
                  border-radius: 10px; padding: .5rem .7rem; font-size: .9rem; }
  .search input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--ring); }
  .tagbar { display: flex; flex-wrap: wrap; gap: .3rem; padding: 0 .8rem .6rem; flex: none;
            max-height: 7.5rem; overflow-y: auto; }
  .tag { font-family: var(--ui); font-size: .74rem; font-weight: 500; white-space: nowrap;
         border: 1px solid var(--border); background: var(--panel2); color: var(--text);
         border-radius: 999px; padding: .15rem .55rem; cursor: pointer; line-height: 1.5;
         display: inline-flex; align-items: center; gap: .3rem; }
  .tag:hover { border-color: var(--accent); }
  .tag.on { background: var(--accent); border-color: var(--accent); color: #fff; }
  .tag.auto { border-style: dashed; color: var(--muted); }
  .tag .x { opacity: .6; font-size: .8em; }
  .tag .x:hover { opacity: 1; color: var(--err); }
  .tag .n { opacity: .65; font-size: .9em; }
  #list .tags { display: flex; flex-wrap: wrap; gap: .2rem; margin-top: .15rem; }
  #list .tags .tag { font-size: .66rem; padding: 0 .4rem; cursor: default; }
  .songtags { display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; margin: -.3rem 0 1rem; }
  .songtags select, .selbar select { background: var(--panel2); border: 1px solid var(--border);
         border-radius: 999px; padding: .15rem .5rem; font-size: .76rem; font-family: var(--ui); }
  .notes { border: 1px solid var(--border); border-radius: 12px; padding: .6rem .8rem;
           margin: -.2rem 0 1.1rem; background: var(--panel2); }
  .notes .head { display: flex; align-items: center; gap: .6rem; font-family: var(--ui);
                 font-weight: 600; font-size: .9rem; }
  .notes .empty-n { color: var(--muted); font-size: .85rem; margin: .3rem 0 0; }
  .notelist { display: flex; flex-wrap: wrap; gap: .6rem; margin-top: .5rem; }
  .note { border: 1px solid var(--border); border-radius: 10px; background: var(--panel);
          padding: .5rem .6rem; width: 13.5rem; display: flex; flex-direction: column; gap: .35rem; }
  .note .thumb { height: 7.5rem; border-radius: 6px; background: #fff; display: grid; place-items: center;
                 overflow: hidden; font-size: 2.2rem; cursor: pointer; border: 1px solid var(--border); }
  .note .thumb img { max-width: 100%; max-height: 100%; object-fit: contain; }
  .note .nm { font-family: var(--ui); font-weight: 600; font-size: .82rem; word-break: break-word; }
  .note .sub { color: var(--muted); font-size: .72rem; word-break: break-word; }
  .note .acts { display: flex; gap: .3rem; flex-wrap: wrap; }
  .note .acts .btn { padding: .2rem .5rem; font-size: .75rem; }
  .tag.zb { border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); }
  .tag.hid { opacity: .55; text-decoration: line-through; }
  .tag .eye { font-size: .85em; opacity: .75; }
  .tag .eye:hover { opacity: 1; }
  .imptags { padding: 0 .8rem .6rem; }
  .imptags input { width: 100%; background: var(--panel2); border: 1px solid var(--border);
                   border-radius: 10px; padding: .4rem .7rem; font-size: .8rem; }
  .imptags input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--ring); }
  .badge { font-family: var(--ui); font-size: .66rem; border-radius: 999px; padding: 0 .4rem;
           border: 1px dashed var(--border); color: var(--muted); margin-left: .3rem; white-space: nowrap; }
  .note .st { font-family: var(--ui); font-size: .72rem; color: var(--accent); }
  .note .st.bad { color: var(--err); }
  .links { border: 1px solid var(--border); border-radius: 12px; padding: .6rem .8rem;
           margin: -.2rem 0 1.1rem; background: var(--panel2); }
  .links .head { display: flex; align-items: center; gap: .6rem; font-family: var(--ui);
                 font-weight: 600; font-size: .9rem; }
  .linkrow { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; padding: .35rem 0;
             border-bottom: 1px solid var(--border); font-family: var(--ui); font-size: .84rem; }
  .linkrow:last-of-type { border-bottom: 0; }
  .linkrow .voice { font-weight: 600; min-width: 5.5rem; }
  .linkrow .lt { color: var(--muted); word-break: break-all; flex: 1; min-width: 8rem; }
  .linkrow .btn { padding: .2rem .5rem; font-size: .75rem; }
  .player { width: 100%; margin: .2rem 0 .5rem; }
  .player iframe { width: 100%; max-width: 640px; border: 0; border-radius: 10px; background: #000; display: block; }
  .player iframe.yt { aspect-ratio: 16 / 9; }
  .player iframe.drive { height: 180px; background: var(--panel); }
  .player audio { width: 100%; max-width: 640px; }
  .addlink { display: flex; gap: .4rem; flex-wrap: wrap; margin-top: .5rem; }
  .addlink select, .addlink input { background: var(--panel); border: 1px solid var(--border);
         border-radius: 8px; padding: .3rem .5rem; font-size: .8rem; font-family: var(--ui); }
  .addlink input.url { flex: 1; min-width: 12rem; }
  .info { border-left: 3px solid var(--accent); padding: .4rem .7rem; margin: 0 0 1rem;
          background: var(--panel2); border-radius: 6px; font-size: .88rem; }
  .modal { position: fixed; inset: 0; background: rgba(20,20,19,.6); z-index: 25; display: flex;
           align-items: center; justify-content: center; padding: 1rem; }
  .modal .win { background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
                box-shadow: var(--shadow); width: min(1100px, 100%); max-height: 100%; display: flex;
                flex-direction: column; min-height: 0; }
  .modal .bar { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; padding: .6rem .8rem;
                border-bottom: 1px solid var(--border); font-family: var(--ui); font-size: .85rem; }
  .modal .bar select, .modal .bar input { background: var(--panel2); border: 1px solid var(--border);
                border-radius: 8px; padding: .25rem .5rem; font-family: var(--ui); }
  .modal .body { overflow: auto; padding: .8rem; min-height: 0; }
  .modal .score { background: #fff; border-radius: 8px; padding: .5rem; }
  .modal .score svg { max-width: 100%; height: auto; display: block; margin: 0 auto; }
  .picklist { list-style: none; margin: .5rem 0 0; padding: 0; max-height: 50vh; overflow-y: auto; }
  .picklist li { padding: .4rem .6rem; border-radius: 8px; cursor: pointer; font-family: var(--ui); font-size: .88rem; }
  .picklist li:hover { background: var(--ring); }
  #pendingBar { margin: 0 .8rem .6rem; padding: .55rem .7rem; border-radius: 10px; cursor: pointer;
                border: 1px solid var(--accent); background: color-mix(in srgb, var(--accent) 14%, var(--panel));
                font-family: var(--ui); font-size: .85rem; }
  #list { list-style: none; margin: 0; padding: .2rem .5rem; overflow-y: auto; flex: 1; }
  #list li { display: flex; gap: .5rem; align-items: flex-start; padding: .45rem .5rem; border-radius: 10px;
             cursor: pointer; border: 1px solid transparent; }
  #list li:hover { background: var(--panel2); }
  #list li.sel { background: var(--ring); border-color: var(--accent); }
  #list input { accent-color: var(--accent); margin-top: .25rem; width: 1rem; height: 1rem; flex: none; }
  #list .t { font-family: var(--ui); font-size: .88rem; font-weight: 500; line-height: 1.3; }
  #list .m { color: var(--muted); font-size: .76rem; }
  .selbar { border-top: 1px solid var(--border); padding: .6rem .8rem; display: flex; gap: .4rem;
            flex-wrap: wrap; align-items: center; font-family: var(--ui); font-size: .8rem; }

  #main { overflow-y: auto; padding: 1.3rem 1.6rem 3rem; min-width: 0; }
  .empty { color: var(--muted); text-align: center; margin-top: 18vh; font-size: 1rem; }
  .empty h2 { color: var(--text); font-weight: 600; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
          padding: 1rem 1.2rem; margin-bottom: 1rem; box-shadow: var(--shadow); }
  .card h3 { margin: 0 0 .4rem; font-size: 1rem; font-weight: 600; }
  .muted { color: var(--muted); }
  .rep li { margin: .15rem 0; }
  .ok { color: var(--ok); } .warn { color: var(--accent); } .bad { color: var(--err); }

  .song-head { display: flex; gap: 1rem; align-items: flex-start; flex-wrap: wrap; margin-bottom: .8rem; }
  .song-head h2 { margin: 0; font-size: 1.45rem; font-weight: 600; line-height: 1.2; }
  .song-head .sub { color: var(--muted); }
  .actions { display: flex; gap: .4rem; flex-wrap: wrap; margin: .3rem 0 1rem; }

  .sheet { font-family: var(--sheet); font-size: 15px; line-height: 1.35; }
  .sheet .lab { font-weight: 700; font-size: 1.05em; margin: 1.1em 0 .2em; font-family: var(--sheet); }
  .sheet .sec:first-child .lab { margin-top: .2em; }
  .sheet .sec { margin-bottom: .9em; }
  .sheet .ln { white-space: pre; min-height: 1.35em; border-radius: 4px; }
  .sheet .seg { display: inline-flex; flex-direction: column; align-items: flex-start;
                vertical-align: bottom; white-space: pre; }
  .sheet .ch { font-weight: 700; color: var(--accent); padding-right: .3em; min-height: 1.35em; }
  .sheet .ly { min-height: 1.35em; }
  .sheet .ln.it .ly, .sheet .ln.it:not(:has(.seg)) { font-style: italic; }
  .sheet .ln.bo .ly, .sheet .ln.bo:not(:has(.seg)) { font-weight: 700; }
  .sheet .ly.hy::after { content: "-"; }
  .sheet .conly { font-weight: 700; color: var(--accent); }
  .sheet .meta { color: var(--muted); }
  .sheet .diff { background: var(--add); }
  .scroll-x { overflow-x: auto; }

  .cmp { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: .8rem 0; }
  .cmp > div { border: 1px solid var(--border); border-radius: 12px; padding: .8rem 1rem; background: var(--panel2);
               min-width: 0; }
  .cmp h4 { margin: 0 0 .5rem; font-family: var(--ui); font-size: .9rem; }
  .simbadge { font-family: var(--ui); font-weight: 600; color: #fff; background: var(--accent);
              border-radius: 999px; padding: .1rem .55rem; font-size: .8rem; }

  #toast { position: fixed; left: 50%; bottom: 1.3rem; transform: translateX(-50%); background: var(--panel);
           border: 1px solid var(--border); border-left: 4px solid var(--ok); border-radius: 12px;
           padding: .7rem 1.1rem; max-width: 90%; box-shadow: var(--shadow); font-family: var(--ui);
           font-size: .9rem; opacity: 0; pointer-events: none; transition: opacity .2s; z-index: 30; }
  #toast.show { opacity: 1; } #toast.err { border-left-color: var(--err); }
  #busy { position: fixed; inset: 0; background: rgba(20,20,19,.5); display: flex; align-items: center;
          justify-content: center; z-index: 20; }
  #busy .box { background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
               padding: 1.1rem 1.7rem; display: flex; gap: .8rem; align-items: center; font-family: var(--ui); }
  .spin { width: 1.15rem; height: 1.15rem; border: 3px solid var(--border); border-top-color: var(--accent);
          border-radius: 50%; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #btnTheme { background: var(--panel2); border: 1px solid var(--border); border-radius: 10px;
              padding: .4rem .6rem; cursor: pointer; line-height: 1; }

  @media (max-width: 820px) {
    header .sub { display: none; }
    .layout { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
    aside { border-right: 0; border-bottom: 1px solid var(--border); max-height: 46vh; }
    #main { padding: 1rem; }
    .cmp { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<header>
  <div class="logo">♫</div>
  <div>
    <h1>Baza pjesama</h1>
    <span class="sub">Tekst, metapodaci i akordi točno na svom mjestu</span>
  </div>
  <span class="grow"></span>
  <span class="chip" id="stats"></span>
  <a class="btn" href="./" title="Natrag na pretvaranje i uređivanje pjesmarice">← Konverter</a>
  <button id="btnTheme" title="Svijetla/tamna tema">☀️</button>
</header>

<div class="layout">
  <aside>
    <div id="drop">
      <b>＋ Uvezi PDF-ove ili ZIP</b> (klikni ili dovuci)
      <small>Tekst, akordi i note (i skenirane) · ZIP s više datoteka · do __MAX_MB__ MB po datoteci</small>
      <input type="file" id="fileInput" accept=".pdf,.zip,application/pdf,application/zip,__NOTE_ACCEPT__" multiple hidden>
    </div>
    <div class="imptags"><input id="importTags" type="text" autocomplete="off"
      placeholder="Oznake za nove pjesme iz uvoza, npr. #pučkepjesme #velikizbor"
      title="Sve NOVE pjesme iz sljedećeg uvoza dobiju ove oznake (pjesme koje već imamo ne). Oznake zbirki možeš sakriti u popisu (👁)."></div>
    <div id="pendingBar" class="hidden"></div>
    <div class="search"><input id="q" type="search" placeholder="Traži naslov, broj, tekst…" autocomplete="off"></div>
    <div class="tagbar" id="tagbar"></div>
    <ul id="list"></ul>
    <div class="selbar">
      <button class="btn" id="btnSelAll" style="padding:.25rem .55rem" title="Odaberi sve prikazane pjesme">☑ Sve</button>
      <span id="selInfo" class="muted">Odaberi pjesme kvačicom</span>
      <select id="selTag" disabled title="Dodaj oznaku svim odabranim pjesmama"><option value="">+ oznaka…</option></select>
      <span class="grow"></span>
      <button class="btn" id="btnSelPdf" disabled title="Odabrane pjesme u izvornom izgledu (s akordima)">⬇ PDF</button>
      <button class="btn primary" id="btnSelEditor" disabled title="Otvori odabrane pjesme u uredniku pjesmarice (kompaktno / klasično, Word)">Napravi pjesmaricu</button>
    </div>
  </aside>
  <section id="main">
    <div class="empty" id="welcome">
      <h2>Ubaci PDF-ove pjesama</h2>
      <p>Iz svakog PDF-a izvlače se pjesme s tekstom, metapodacima i akordima<br>
         (svaki akord pamti točno slovo iznad kojeg stoji).</p>
      <p>Stranice s <b>notama</b> spremaju se kao note uz svoju pjesmu (po naslovu ili
         tekstu ispod nota). Može i <b>ZIP</b> s više PDF-ova i datoteka nota odjednom.</p>
      <p>Pjesme koje već imamo ne dodaju se (ni druga verzija iste pjesme).<br>
         Pita te samo kad nova verzija ima više teksta ili kad je tekst ≥ 80 %
         sličan pjesmi drugog naslova.</p>
      <p class="muted" style="font-size:.85rem">Prima PDF-ove iz aplikacije za pjesme
         i pjesmarice iz Worda (npr. Tabor: naslov na vrhu stranice, akordi iznad teksta).</p>
      <p class="muted" style="font-size:.85rem">Druge aplikacije čitaju bazu preko
         <a href="api/v1" target="_blank">api/v1</a> · v__VERSION__</p>
    </div>
    <div id="view" class="hidden"></div>
  </section>
</div>

<input type="file" id="noteInput" accept="__NOTE_ACCEPT__" multiple hidden>
<div id="busy" class="hidden"><div class="box"><div class="spin"></div><span id="busyText">Radim…</span></div></div>
<div id="toast"></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const state = { songs: [], total: 0, sel: new Set(), current: null, q: "", tag: "", tags: [], untagged: 0,
                hidden: new Set() };
const PW_KEY = "pjesme-dbpw", DRAFT_KEY = "pjesme-draft", HIDDEN_KEY = "pjesme-hidden-tags";
const OMR = __OMR__, VOICES = __VOICES__;
const VEROVIO_URL = "https://cdn.jsdelivr.net/npm/verovio@6.3.0/dist/verovio-toolkit-wasm.js";
try { state.hidden = new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]")); } catch (_e) {}

function applyTheme(t) {
  document.body.classList.toggle("light", t === "light");
  $("btnTheme").textContent = t === "light" ? "🌙" : "☀️";
}
applyTheme(localStorage.getItem("pjesme-theme") || "dark");
$("btnTheme").onclick = () => {
  const t = document.body.classList.contains("light") ? "dark" : "light";
  localStorage.setItem("pjesme-theme", t); applyTheme(t);
};
function toast(msg, isErr) {
  const el = $("toast"); el.textContent = msg; el.classList.toggle("err", !!isErr);
  el.classList.add("show"); clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), isErr ? 6000 : 3000);
}
function busy(text) {
  if (text) { $("busyText").textContent = text; $("busy").classList.remove("hidden"); }
  else $("busy").classList.add("hidden");
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

/* fetch s lozinkom baze (ako je server traži) */
async function api(url, opts = {}, retry = true) {
  opts.headers = Object.assign({}, opts.headers || {});
  const pw = localStorage.getItem(PW_KEY);
  if (pw) opts.headers["X-DB-Password"] = pw;
  const res = await fetch(url, opts);
  if (res.status === 401 && retry) {
    const p = prompt("Lozinka baze pjesama:");
    if (p) { localStorage.setItem(PW_KEY, p); return api(url, opts, false); }
  }
  return res;
}
async function json(res) {
  let data = {};
  try { data = await res.json(); } catch (_e) {}
  if (!res.ok) throw new Error(data.error || ("Greška " + res.status));
  return data;
}

/* ── Prikaz pjesme s akordima ── */
function renderLine(ln, mark) {
  const st = (ln.fmt || {}).text;
  const div = el("div", "ln" + (mark ? " diff" : "") + (st === "italic" ? " it" : st === "bold" ? " bo" : ""));
  const text = ln.text || "";
  const chords = (ln.chords || []).slice().sort((a, b) => a[0] - b[0]);
  if (!chords.length) { div.textContent = text || " "; return div; }
  if (!text.trim()) {
    div.classList.add("conly");
    let s = "";
    chords.forEach(([c, ch]) => { if (s.length && s.length >= c) s += " "; while (s.length < c) s += " "; s += ch; });
    div.textContent = s; return div;
  }
  const first = Math.min(chords[0][0], text.length);
  if (first > 0) {
    const seg = el("span", "seg"); seg.append(el("span", "ch", ""), el("span", "ly", text.slice(0, first)));
    div.append(seg);
  }
  chords.forEach(([i, ch], k) => {
    const start = Math.min(i, text.length);
    const end = k + 1 < chords.length ? Math.min(chords[k + 1][0], text.length) : text.length;
    const seg = el("span", "seg");
    seg.append(el("span", "ch", ch), el("span", "ly", text.slice(start, Math.max(start, end))));
    div.append(seg);
  });
  return div;
}
function lineKey(ln) { return (ln.text || "").trim() + "|" + JSON.stringify(ln.chords || []); }
function renderSheet(song, other) {
  const otherKeys = other ? new Set(other.sections.flatMap(s => s.lines.map(lineKey))) : null;
  const wrap = el("div", "sheet scroll-x");
  (song.meta || []).forEach(m => {
    const d = el("div", "sec meta");
    d.append(el("div", "lab", m.label || ""));
    (m.values || []).forEach(v => d.append(el("div", "ln", v)));
    wrap.append(d);
  });
  (song.sections || []).forEach(sec => {
    const d = el("div", "sec");
    if (sec.label) d.append(el("div", "lab", sec.label));
    sec.lines.forEach(ln => d.append(renderLine(ln, otherKeys && !otherKeys.has(lineKey(ln)))));
    wrap.append(d);
  });
  return wrap;
}
/* Kao izvorna aplikacija: kad je akord širi od sloga usred riječi, crtica ("alelu-ja") */
function fixHyphens(root) {
  root.querySelectorAll(".ln").forEach(ln => {
    const segs = [...ln.querySelectorAll(".seg")];
    segs.forEach((seg, k) => {
      const next = segs[k + 1];
      if (!next) return;
      const ch = seg.firstChild, ly = seg.lastChild, t = ly.textContent, nt = next.lastChild.textContent;
      if (t && nt && /[\p{L}\p{N}]$/u.test(t) && /^[\p{L}\p{N}]/u.test(nt) &&
          ch.offsetWidth > ly.offsetWidth + 1) ly.classList.add("hy");
    });
  });
}
function showView(node, quiet) {
  $("welcome").classList.add("hidden");
  const v = $("view"); v.innerHTML = ""; v.append(node); v.classList.remove("hidden");
  $("main").scrollTop = 0;
  fixHyphens(v);
  if (!quiet && window.matchMedia("(max-width: 820px)").matches) v.scrollIntoView({ behavior: "smooth" });
}

/* ── Popis ── */
async function loadList() {
  const exclude = [...state.hidden].filter(t => t !== state.tag).join(",");
  const data = await json(await fetch("db/songs?limit=1000&q=" + encodeURIComponent(state.q) +
                                      "&tag=" + encodeURIComponent(state.tag) +
                                      "&exclude=" + encodeURIComponent(exclude)));
  state.songs = data.items; state.total = data.total;
  renderList();
}
function tagChip(t, opts = {}) {
  const c = el("span", "tag" + (t.auto ? " auto" : "") + (opts.on ? " on" : ""));
  c.append(document.createTextNode(t.name));
  if (t.auto) c.title = "Automatski prijedlog — klikni za potvrdu";
  return c;
}
async function loadTags() {
  const d = await json(await fetch("db/tags"));
  state.tags = d.tags; state.untagged = d.untagged;
  const bar = $("tagbar"); bar.innerHTML = "";
  const all = el("span", "tag" + (state.tag === "" ? " on" : ""), "Sve");
  all.onclick = () => setTagFilter("");
  bar.append(all);
  d.tags.filter(t => t.count || state.tag === t.name).forEach(t => {
    const zb = t.group === "zbirka", hid = state.hidden.has(t.name);
    const c = el("span", "tag" + (state.tag === t.name ? " on" : "") + (zb ? " zb" : "") + (hid ? " hid" : ""));
    c.append(document.createTextNode(t.name + " "), el("span", "n", String(t.count)));
    c.title = (t.description || t.name) + (zb ? " — zbirka" : "");
    c.onclick = () => setTagFilter(state.tag === t.name ? "" : t.name);
    if (zb || hid) {
      const eye = el("span", "eye", hid ? "🚫" : "👁");
      eye.title = hid ? "Skriveno u popisu — klikni da se opet prikazuje"
                      : "Sakrij pjesme s ovom oznakom iz popisa (npr. koje nam inače ne trebaju)";
      eye.onclick = e => {
        e.stopPropagation();
        hid ? state.hidden.delete(t.name) : state.hidden.add(t.name);
        try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...state.hidden])); } catch (_e) {}
        loadTags(); loadList().catch(er => toast(er.message, true));
      };
      c.append(eye);
    }
    bar.append(c);
  });
  if (d.untagged) {
    const u = el("span", "tag" + (state.tag === "-" ? " on" : ""));
    u.append(document.createTextNode("bez oznake "), el("span", "n", String(d.untagged)));
    u.onclick = () => setTagFilter(state.tag === "-" ? "" : "-");
    bar.append(u);
  }
  const sug = el("span", "tag", "✨ Predloži oznake");
  sug.title = "Automatski predloži oznake pjesmama koje ih još nisu dobile";
  sug.onclick = async () => {
    try { const r = await json(await api("db/tags/suggest", { method: "POST" }));
          toast(r.songs ? ("Prijedlog za " + r.songs + " pjesama: " + r.tags + " oznaka (isprekidano = potvrdi)")
                        : "Sve pjesme već imaju predložene oznake.");
          await refreshAll(); }
    catch (e) { toast(e.message, true); }
  };
  bar.append(sug);
  const sel = $("selTag"); sel.innerHTML = '<option value="">+ oznaka…</option>';
  d.tags.forEach(t => sel.append(new Option(t.name, t.name)));
  sel.append(new Option("Nova oznaka…", "__new__"));
}
function setTagFilter(t) { state.tag = t; loadTags(); loadList().catch(e => toast(e.message, true)); }
async function refreshAll() {
  await Promise.all([loadList(), refreshStats(), loadTags()]);
  if (state.current) openSong(state.current, true);
}
function renderList() {
  const ul = $("list"); ul.innerHTML = "";
  state.songs.forEach(s => {
    const li = el("li", state.current === s.id ? "sel" : "");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = state.sel.has(s.id);
    cb.onclick = e => { e.stopPropagation(); cb.checked ? state.sel.add(s.id) : state.sel.delete(s.id); renderSel(); };
    const box = el("div");
    const t = el("div", "t", s.title + (s.notes_count ? "  🎼" : "") + (s.links_count ? " 🎧" : ""));
    if (!s.has_text) t.append(el("span", "badge", "samo note"));
    box.append(t);
    const meta = [s.subtitle, s.key && ("Key: " + s.key), (s.numbers || []).join(", ")].filter(Boolean).join(" · ");
    if (meta) box.append(el("div", "m", meta));
    if ((s.tags || []).length) {
      const tg = el("div", "tags");
      s.tags.forEach(t => tg.append(tagChip(t)));
      box.append(tg);
    }
    li.append(cb, box);
    li.onclick = () => openSong(s.id);
    ul.append(li);
  });
  if (!state.songs.length) ul.append(el("li", "muted", state.q ? "Nema rezultata." : "Baza je prazna."));
  if (state.total > state.songs.length)
    ul.append(el("li", "muted", "Prikazano " + state.songs.length + " od " + state.total + " — suzi pretragu."));
  const hidden = [...state.hidden].filter(t => t !== state.tag);
  if (hidden.length) ul.append(el("li", "muted", "Skriveno oznakama: " + hidden.join(", ") + " (👁 u oznakama)"));
  renderSel();
}
function renderSel() {
  const n = state.sel.size;
  $("selInfo").textContent = n ? ("Odabrano: " + n) : "Odaberi pjesme kvačicom";
  $("btnSelPdf").disabled = $("btnSelEditor").disabled = $("selTag").disabled = !n;
}
$("btnSelAll").onclick = () => {
  const all = state.songs.length && state.songs.every(s => state.sel.has(s.id));
  state.songs.forEach(s => all ? state.sel.delete(s.id) : state.sel.add(s.id));
  renderList();
};
$("selTag").onchange = async () => {
  let name = $("selTag").value;
  $("selTag").value = "";
  if (!name) return;
  if (name === "__new__") { name = (prompt("Naziv nove oznake:") || "").trim(); if (!name) return; }
  try {
    const r = await json(await api("db/tags/bulk", { method: "POST", headers: { "Content-Type": "application/json" },
                                                    body: JSON.stringify({ ids: [...state.sel], name, action: "add" }) }));
    toast("Oznaka „" + name + "” dodana na " + r.changed + " pjesama.");
    await refreshAll();
  } catch (e) { toast(e.message, true); }
};
async function refreshStats() {
  const s = await json(await fetch("db/stats"));
  $("stats").textContent = s.songs + " pjesama";
  const bar = $("pendingBar");
  bar.classList.toggle("hidden", !s.pending);
  bar.textContent = "⚠ " + s.pending + (s.pending === 1 ? " slična pjesma čeka" : " sličnih pjesama čeka") + " tvoju odluku →";
}
let qTimer = null;
$("q").addEventListener("input", () => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { state.q = $("q").value.trim(); loadList().catch(e => toast(e.message, true)); }, 220);
});

/* ── Pjesma ── */
function renderSongTags(s) {
  const box = el("div", "songtags");
  (s.tags || []).forEach(t => {
    const c = tagChip(t);
    if (t.auto) c.onclick = () => tagOp(s.id, "POST", t.name);
    const x = el("span", "x", "✕"); x.title = "Makni oznaku";
    x.onclick = e => { e.stopPropagation(); tagOp(s.id, "DELETE", t.name); };
    c.append(x);
    box.append(c);
  });
  const sel = el("select");
  sel.append(new Option("+ oznaka…", ""));
  state.tags.filter(t => !(s.tags || []).some(x => x.name === t.name))
    .forEach(t => sel.append(new Option(t.name, t.name)));
  sel.append(new Option("Nova oznaka…", "__new__"));
  sel.onchange = () => {
    let name = sel.value;
    if (name === "__new__") name = (prompt("Naziv nove oznake:") || "").trim();
    if (name) tagOp(s.id, "POST", name);
  };
  box.append(sel);
  if ((s.tags || []).some(t => t.auto)) box.append(el("span", "muted", "isprekidano = prijedlog, klik potvrđuje"));
  return box;
}
async function tagOp(id, method, name) {
  try {
    const url = "db/songs/" + id + "/tags" + (method === "DELETE" ? "/" + encodeURIComponent(name) : "");
    const opts = { method };
    if (method === "POST") { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify({ name }); }
    await json(await api(url, opts));
    await refreshAll();
  } catch (e) { toast(e.message, true); }
}
function fmtSize(n) {
  return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " kB";
}
const NOTE_ICON = { pdf: "📄", image: "🖼", musicxml: "🎼", musescore: "🎼", midi: "🎹" };
const NOTE_KIND = { pdf: "PDF", image: "slika", musicxml: "MusicXML", musescore: "MuseScore", midi: "MIDI" };
const NOTE_ORIGIN = { "import": "iz uvoza", omr: "prepoznato (Audiveris)" };
let omrTimer = null;
function renderNotes(s, jobs) {
  const box = el("div", "notes");
  const head = el("div", "head");
  head.append(el("span", null, "🎼 Note"), el("span", "grow"));
  const add = el("button", "btn", "＋ Dodaj note");
  add.title = "PDF, slika, MusicXML (.musicxml/.mxl), MuseScore (.mscz) ili MIDI — sprema se odvojeno od teksta";
  add.onclick = () => { $("noteInput").dataset.song = s.id; $("noteInput").click(); };
  head.append(add);
  box.append(head);
  const notes = s.notes || [];
  if (!notes.length) {
    box.append(el("p", "empty-n", "Nema nota. Dodaj PDF ili sliku nota, MusicXML ili MuseScore datoteku."));
    return box;
  }
  const byNote = {};
  (jobs || []).forEach(j => { byNote[j.note_id] = j; });
  const list = el("div", "notelist");
  notes.forEach(n => {
    const card = el("div", "note");
    const url = "db/notes/" + n.id;
    const thumb = el("div", "thumb");
    if (n.kind === "image") {
      const img = el("img"); img.src = url; img.alt = n.name; img.loading = "lazy";
      thumb.append(img);
    } else thumb.textContent = NOTE_ICON[n.kind] || "📎";
    const viewable = n.kind === "musicxml";
    thumb.title = viewable ? "Prikaži note" : n.inline ? "Otvori" : "Preuzmi";
    thumb.onclick = () => viewable ? showScore(n, s) : window.open(n.inline ? url : url + "?download=1", "_blank");
    card.append(thumb, el("div", "nm", n.name),
                el("div", "sub", [NOTE_KIND[n.kind] || n.kind, fmtSize(n.size), NOTE_ORIGIN[n.origin], n.filename]
                                   .filter(Boolean).join(" · ")));
    const job = byNote[n.id];
    if (job && (job.status === "queued" || job.status === "running"))
      card.append(el("div", "st", "⏳ Prepoznajem note… (≈ pola minute po stranici)"));
    else if (job && job.status === "error") card.append(el("div", "st bad", "✕ " + job.message));
    const acts = el("div", "acts");
    if (viewable) { const v = el("button", "btn", "▶ Prikaži"); v.title = "Note u pregledniku, s transpozicijom";
                    v.onclick = () => showScore(n, s); acts.append(v); }
    if (n.inline) { const o = el("a", "btn", "Otvori"); o.href = url; o.target = "_blank"; acts.append(o); }
    if (OMR && (n.kind === "pdf" || n.kind === "image") &&
        !(job && (job.status === "queued" || job.status === "running"))) {
      const o = el("button", "btn", "🔍 Prepoznaj");
      o.title = "Pročitaj note (Audiveris) i spremi ih kao MusicXML — za prikaz, transpoziciju i MuseScore";
      o.onclick = async () => {
        try { await json(await api(url + "/omr", { method: "POST" }));
              toast("Prepoznavanje je pokrenuto — rezultat se pojavi ovdje kad bude gotov.");
              openSong(s.id, true); }
        catch (e) { toast(e.message, true); }
      };
      acts.append(o);
    }
    const d = el("a", "btn", "⬇"); d.href = url + "?download=1"; d.title = "Preuzmi";
    const r = el("button", "btn", "✎"); r.title = "Preimenuj (npr. Sopran, Gitara, Klavir)";
    r.onclick = async () => {
      const name = (prompt("Naziv nota (npr. Sopran, Gitara, Klavir):", n.name) || "").trim();
      if (!name || name === n.name) return;
      try { await json(await api(url, { method: "PATCH", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ name }) }));
            openSong(s.id, true); }
      catch (e) { toast(e.message, true); }
    };
    const m = el("button", "btn", "⇄"); m.title = "Premjesti na drugu pjesmu (ako su pogrešno pridružene)";
    m.onclick = () => pickSong("Premjesti note „" + n.name + "” na pjesmu:", s.id, async target => {
      try { await json(await api(url + "/move", { method: "POST", headers: { "Content-Type": "application/json" },
                                                  body: JSON.stringify({ song_id: target.id }) }));
            toast("Note premještene na „" + target.title + "”.");
            await refreshAll(); openSong(target.id); }
      catch (e) { toast(e.message, true); }
    });
    const x = el("button", "btn danger", "✕"); x.title = "Obriši note";
    x.onclick = async () => {
      if (!confirm("Obrisati note „" + n.name + "”?")) return;
      try { await json(await api(url, { method: "DELETE" })); await refreshAll(); }
      catch (e) { toast(e.message, true); }
    };
    acts.append(d, r, m, x);
    card.append(acts);
    list.append(card);
  });
  box.append(list);
  return box;
}

/* ── Snimke: YouTube po glasovima, Google disk, audio ── */
const LINK_ICON = { youtube: "▶️", drive: "☁️", audio: "🎵", link: "🔗" };
function renderLinks(s) {
  const box = el("div", "links");
  const head = el("div", "head");
  head.append(el("span", null, "🎧 Snimke"), el("span", "grow"));
  box.append(head);
  const links = s.links || [];
  if (!links.length) box.append(el("p", "empty-n muted", "Nema snimki. Dodaj YouTube link (npr. za svaki glas) ili audio s Google diska."));
  links.forEach(l => {
    const row = el("div", "linkrow");
    row.append(el("span", "voice", (LINK_ICON[l.kind] || "🔗") + " " + (l.voice || "Snimka")));
    row.append(el("span", "lt", l.title || l.url));
    const player = el("div", "player hidden");
    if (l.embed) {
      const play = el("button", "btn", "▶ Slušaj");
      play.onclick = () => {
        if (!player.classList.contains("hidden")) { player.innerHTML = ""; player.classList.add("hidden");
                                                    play.textContent = "▶ Slušaj"; return; }
        player.innerHTML = "";
        if (l.kind === "audio") {
          const a = el("audio"); a.controls = true; a.src = l.embed; a.preload = "none"; player.append(a);
        } else {
          const f = el("iframe", l.kind === "youtube" ? "yt" : "drive");
          f.src = l.embed; f.loading = "lazy"; f.referrerPolicy = "strict-origin-when-cross-origin";
          f.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen"; f.allowFullscreen = true;
          player.append(f);
        }
        player.classList.remove("hidden"); play.textContent = "■ Zatvori";
      };
      row.append(play);
    }
    const open = el("a", "btn", "↗"); open.href = l.url; open.target = "_blank"; open.rel = "noopener noreferrer";
    open.title = "Otvori u novoj kartici";
    const ed = el("button", "btn", "✎"); ed.title = "Promijeni glas / naziv";
    ed.onclick = async () => {
      const voice = prompt("Glas (" + VOICES.join(", ") + " ili drugo):", l.voice);
      if (voice === null) return;
      const title = prompt("Naziv (neobavezno):", l.title);
      if (title === null) return;
      try { await json(await api("db/links/" + l.id, { method: "PATCH", headers: { "Content-Type": "application/json" },
                                                      body: JSON.stringify({ voice, title }) }));
            openSong(s.id, true); }
      catch (e) { toast(e.message, true); }
    };
    const x = el("button", "btn danger", "✕"); x.title = "Obriši snimku";
    x.onclick = async () => {
      if (!confirm("Obrisati snimku " + (l.voice || l.url) + "?")) return;
      try { await json(await api("db/links/" + l.id, { method: "DELETE" })); await refreshAll(); }
      catch (e) { toast(e.message, true); }
    };
    row.append(open, ed, x);
    box.append(row, player);
  });
  const form = el("div", "addlink");
  const voice = el("select");
  VOICES.forEach(v => voice.append(new Option(v, v)));
  voice.append(new Option("drugo…", "__other__"));
  const urlIn = el("input", "url"); urlIn.type = "url";
  urlIn.placeholder = "YouTube ili Google disk link (drive.google.com/file/d/…)";
  const titleIn = el("input"); titleIn.placeholder = "naziv (neobavezno)"; titleIn.style.width = "9rem";
  const add = el("button", "btn primary", "＋ Dodaj");
  const submit = async () => {
    let v = voice.value;
    if (v === "__other__") { v = (prompt("Naziv glasa / snimke:") || "").trim(); if (!v) return; }
    try {
      await json(await api("db/songs/" + s.id + "/links", { method: "POST", headers: { "Content-Type": "application/json" },
                                                           body: JSON.stringify({ url: urlIn.value, voice: v, title: titleIn.value }) }));
      toast("Snimka dodana."); await refreshAll();
    } catch (e) { toast(e.message, true); }
  };
  add.onclick = submit;
  urlIn.onkeydown = e => { if (e.key === "Enter") submit(); };
  form.append(voice, urlIn, titleIn, add);
  box.append(form);
  box.append(el("div", "muted", "Google disk: datoteka mora biti dijeljena s „Svi koji imaju link”."));
  box.lastChild.style.fontSize = ".75rem";
  return box;
}

/* ── Odabir pjesme (premještanje nota) ── */
function pickSong(title, excludeId, onPick) {
  const modal = el("div", "modal");
  const win = el("div", "win"); win.style.width = "min(560px, 100%)";
  const bar = el("div", "bar");
  bar.append(el("b", null, title), el("span", "grow"));
  const close = el("button", "btn", "✕"); close.onclick = () => modal.remove();
  bar.append(close);
  const body = el("div", "body");
  const q = el("input"); q.type = "search"; q.placeholder = "Traži naslov ili tekst…"; q.style.width = "100%";
  q.className = "search"; q.style.padding = ".45rem .7rem";
  const ul = el("ul", "picklist");
  let t = null;
  const search = async () => {
    const d = await json(await fetch("db/songs?limit=50&q=" + encodeURIComponent(q.value.trim())));
    ul.innerHTML = "";
    d.items.filter(x => x.id !== excludeId).forEach(x => {
      const li = el("li", null, x.title + (x.subtitle ? " — " + x.subtitle : ""));
      if (!x.has_text) li.append(el("span", "badge", "samo note"));
      li.onclick = () => { modal.remove(); onPick(x); };
      ul.append(li);
    });
    if (!ul.children.length) ul.append(el("li", "muted", "Nema rezultata."));
  };
  q.oninput = () => { clearTimeout(t); t = setTimeout(() => search().catch(e => toast(e.message, true)), 200); };
  body.append(q, ul);
  win.append(bar, body); modal.append(win);
  modal.onclick = e => { if (e.target === modal) modal.remove(); };
  document.body.append(modal);
  q.focus(); search().catch(e => toast(e.message, true));
}

/* ── Prikaz MusicXML nota (Verovio), transpozicija ── */
let verovioReady = null;
function loadVerovio() {
  if (!verovioReady) verovioReady = new Promise((resolve, reject) => {
    const sc = document.createElement("script");
    sc.src = VEROVIO_URL; sc.crossOrigin = "anonymous";
    sc.onload = () => {
      if (window.verovio && verovio.module) {
        if (verovio.module.calledRun) resolve(); else verovio.module.onRuntimeInitialized = () => resolve();
      } else reject(new Error("Preglednik nota se nije učitao."));
    };
    sc.onerror = () => { verovioReady = null; reject(new Error("Preglednik nota se ne može učitati (nema interneta?).")); };
    document.head.append(sc);
  });
  return verovioReady;
}
async function showScore(n, s) {
  busy("Učitavam note…");
  let tk, buf;
  try {
    const [, res] = await Promise.all([loadVerovio(), fetch("db/notes/" + n.id)]);
    if (!res.ok) await json(res);
    buf = await res.arrayBuffer();
    tk = new verovio.toolkit();
  } catch (e) { busy(null); toast(e.message, true); return; }
  busy(null);
  const modal = el("div", "modal");
  const win = el("div", "win");
  const bar = el("div", "bar");
  bar.append(el("b", null, s.title + " — " + n.name), el("span", "grow"));
  const tr = el("select"); tr.title = "Transponiraj (polutonovi)";
  for (let k = -6; k <= 6; k++) tr.append(new Option(k === 0 ? "izvorni ton" : (k > 0 ? "+" : "") + k + " polut.", String(k)));
  tr.value = "0";
  const zoom = el("select"); zoom.title = "Veličina";
  [["30", "sitno"], ["40", "normalno"], ["55", "veće"]].forEach(([v, t]) => zoom.append(new Option(t, v)));
  zoom.value = "40";
  const prev = el("button", "btn", "◀"), next = el("button", "btn", "▶"), info = el("span", "muted");
  const dl = el("a", "btn", "⬇ MusicXML"); dl.href = "db/notes/" + n.id + "?download=1";
  const close = el("button", "btn", "✕ Zatvori"); close.onclick = () => modal.remove();
  bar.append(el("span", null, "Transpozicija:"), tr, zoom, prev, info, next, dl, close);
  const body = el("div", "body"), score = el("div", "score");
  body.append(score);
  win.append(bar, body); modal.append(win);
  modal.onclick = e => { if (e.target === modal) modal.remove(); };
  document.body.append(modal);
  let page = 1, pages = 1;
  const isZip = new Uint8Array(buf.slice(0, 2))[0] === 0x50;
  const text = isZip ? null : new TextDecoder().decode(buf);
  const render = () => {
    tk.setOptions({ scale: Number(zoom.value), transpose: tr.value === "0" ? "" : tr.value,
                    pageWidth: Math.max(800, Math.round((body.clientWidth - 30) * 100 / Number(zoom.value))),
                    adjustPageHeight: true, footer: "none" });
    const ok = isZip ? tk.loadZipDataBuffer(buf) : tk.loadData(text);
    if (!ok) { score.textContent = "Note se ne mogu prikazati (neispravan MusicXML)."; return; }
    pages = tk.getPageCount(); page = Math.min(page, pages);
    score.innerHTML = tk.renderToSVG(page);
    info.textContent = page + " / " + pages;
    prev.disabled = page <= 1; next.disabled = page >= pages;
  };
  tr.onchange = zoom.onchange = render;
  prev.onclick = () => { page--; render(); };
  next.onclick = () => { page++; render(); };
  try { render(); } catch (e) { score.textContent = "Greška pri prikazu: " + e.message; }
}

$("noteInput").onchange = async () => {
  const input = $("noteInput"), id = input.dataset.song, files = [...input.files];
  input.value = "";
  if (!files.length || !id) return;
  busy("Spremam note…");
  try {
    const fd = new FormData();
    files.forEach(f => fd.append("file", f));
    const r = await json(await api("db/songs/" + id + "/notes", { method: "POST", body: fd }));
    toast("Dodano: " + r.added.length + (r.errors.length ? " · " + r.errors.join(" ") : ""), r.errors.length > 0);
    await refreshAll();
  } catch (e) { toast(e.message, true); } finally { busy(null); }
};
async function openSong(id, keepScroll) {
  try {
    const s = await json(await fetch("db/songs/" + id));
    state.current = id; renderList();
    const box = el("div");
    const head = el("div", "song-head");
    const h = el("div");
    h.append(el("h2", null, s.title));
    if (s.subtitle) h.append(el("div", "sub", s.subtitle));
    head.append(h, el("span", "grow"));
    if (s.key) head.append(el("span", "chip", "Key: " + s.key));
    if ((s.numbers || []).length) head.append(el("span", "chip", "Pjesmarica " + s.numbers.join(", ")));
    head.append(el("span", "chip", "#" + s.id));
    box.append(head);
    box.append(renderSongTags(s));
    if (!s.has_text) box.append(el("div", "info", "Pjesma zasad ima samo note. Tekst (i akordi) dodaju se sami " +
                                   "kad uvezeš PDF s tekstom pjesme istog naslova."));
    let jobs = [];
    if (OMR && (s.notes || []).length) {
      try { jobs = (await json(await fetch("db/songs/" + id + "/omr"))).jobs; } catch (_e) {}
    }
    box.append(renderNotes(s, jobs));
    box.append(renderLinks(s));
    clearTimeout(omrTimer);
    if (jobs.some(j => j.status === "queued" || j.status === "running"))
      omrTimer = setTimeout(() => { if (state.current === id) refreshAll(); }, 5000);
    const act = el("div", "actions");
    const a1 = el("a", "btn", "⬇ PDF (izvorni izgled)"); a1.href = "db/songs/" + id + "/pdf";
    const a2 = el("a", "btn", "⬇ ChordPro"); a2.href = "db/songs/" + id + "/chordpro";
    const a3 = el("a", "btn", "JSON"); a3.href = "db/songs/" + id; a3.target = "_blank";
    const del = el("button", "btn danger", "Obriši");
    del.onclick = async () => {
      if (!confirm("Obrisati pjesmu \"" + s.title + "\" iz baze?")) return;
      try { await json(await api("db/songs/" + id, { method: "DELETE" }));
            state.sel.delete(id); state.current = null; toast("Pjesma obrisana.");
            $("view").classList.add("hidden"); $("welcome").classList.remove("hidden");
            await refreshAll(); }
      catch (e) { toast(e.message, true); }
    };
    act.append(a1, a2, a3, del);
    box.append(act);
    box.append(renderSheet(s));
    const src = (s.sources || []).map(x => x.file + (x.pages && x.pages.length ? " (str. " + x.pages.join(", ") + ")" : "")).join("; ");
    if (src) box.append(el("p", "muted", "Izvor: " + src));
    const top = $("main").scrollTop;
    showView(box, keepScroll);
    if (keepScroll) $("main").scrollTop = top;
  } catch (e) { toast(e.message, true); }
}

/* ── Uvoz ── */
const drop = $("drop");
drop.onclick = () => $("fileInput").click();
$("fileInput").onchange = () => { importFiles([...$("fileInput").files]); $("fileInput").value = ""; };
["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => importFiles([...e.dataTransfer.files]));

const IMPORT_EXT = [".pdf", ".zip", ..."__NOTE_ACCEPT__".split(",")];
async function importFiles(files) {
  files = files.filter(f => IMPORT_EXT.some(x => f.name.toLowerCase().endsWith(x)));
  if (!files.length) { toast("Odaberi PDF, ZIP ili datoteke nota.", true); return; }
  const tags = $("importTags").value.trim();
  const reports = [];
  let usedTags = [];
  try {
    for (let i = 0; i < files.length; i++) {
      const zip = files[i].name.toLowerCase().endsWith(".zip");
      busy("Uvozim " + (i + 1) + "/" + files.length + ": " + files[i].name +
           (zip ? " (ZIP s puno datoteka može potrajati par minuta)" : ""));
      const fd = new FormData(); fd.append("file", files[i]); fd.append("tags", tags);
      try {
        const data = await json(await api("db/import", { method: "POST", body: fd }));
        reports.push(...data.reports); usedTags = data.tags || usedTags;
      } catch (e) { reports.push({ file: files[i].name, error: e.message }); }
    }
  } finally { busy(null); }
  await Promise.all([loadList(), refreshStats(), loadTags()]);
  showReport(reports, usedTags);
}
const NOTE_STATUS = { attached: "uz pjesmu", new_song: "nova pjesma (zasad samo note)",
                      duplicate: "već imamo (preskočeno)", error: "greška" };
function showReport(reports, tags) {
  const box = el("div");
  box.append(el("h2", null, "Uvoz završen"));
  if (tags && tags.length) box.append(el("p", "muted", "Nove pjesme su dobile oznake: " + tags.join(", ")));
  let anyConflict = false;
  reports.forEach(r => {
    const c = el("div", "card");
    c.append(el("h3", null, r.file));
    if (r.error) { c.append(el("div", "bad", "✕ " + r.error)); box.append(c); return; }
    const ul = el("ul", "rep");
    const li = (cls, t) => ul.append(el("li", cls, t));
    if (r.added.length || !(r.notes || []).length)
      li("ok", "✓ dodano: " + r.added.length + (r.added.length ? " — " + r.added.map(x => x.title + (x.filled ? " (dopunjen tekst pjesme koja je imala samo note)" : "")).join(", ") : ""));
    (r.notes || []).forEach(n => {
      const item = el("li", n.status === "duplicate" ? "muted" : n.status === "error" ? "bad" : "ok");
      item.append(document.createTextNode("🎼 note" + (n.title ? " „" + n.title + "”" : "") +
                  (n.pages && n.pages.length ? " (str. " + n.pages.join(", ") + ")" : "") + " → "));
      if (n.song_id) {
        const a = el("a", null, n.song_title || ("#" + n.song_id)); a.href = "#";
        a.onclick = e => { e.preventDefault(); openSong(n.song_id); };
        item.append(a);
      }
      item.append(document.createTextNode(" · " + (NOTE_STATUS[n.status] || n.status) + (n.error ? ": " + n.error : "")));
      ul.append(item);
    });
    const same = r.duplicates.filter(x => x.reason === "same_song");
    const exact = r.duplicates.filter(x => x.reason !== "same_song");
    if (exact.length) li("muted", "= već u bazi, identične (preskočeno " + exact.length + "): " + exact.map(x => x.title).join(", "));
    if (same.length) li("muted", "= već imamo, druga verzija iste pjesme (preskočeno " + same.length + "): " +
       same.map(x => x.title + " → " + x.existing_title).join(", "));
    if (r.conflicts.length) {
      anyConflict = true;
      li("warn", "⚠ čekaju tvoju odluku (" + r.conflicts.length + "): " +
         r.conflicts.map(x => x.title + " ~ " + x.existing_title +
           (x.reason === "longer" ? " (nova ima više teksta)" : " " + Math.round(x.similarity * 100) + " %")).join(", "));
    }
    (r.warnings || []).forEach(w => li("muted", w));
    c.append(ul); box.append(c);
  });
  if (anyConflict) {
    const b = el("button", "btn primary", "Riješi slične pjesme →"); b.onclick = showPending; box.append(b);
  }
  showView(box);
}

/* ── Slične pjesme: odluka ── */
async function showPending() {
  try {
    const data = await json(await fetch("db/pending"));
    const box = el("div");
    box.append(el("h2", null, "Slične pjesme — koju zadržati?"));
    if (!data.items.length) { box.append(el("p", "muted", "Nema ničega za riješiti. ✓")); showView(box); return; }
    box.append(el("p", "muted", "Retci koji se razlikuju su označeni bojom."));
    if (data.items.length > 1) {
      const bulk = el("div", "actions");
      bulk.append(el("span", "muted", "Za sve (" + data.items.length + "): "));
      [["keep_existing", "Zadrži postojeće"], ["replace", "Zamijeni novima"], ["keep_both", "Zadrži obje"]].forEach(([a, label]) => {
        const b = el("button", "btn", label);
        b.onclick = async () => {
          if (!confirm(label + " — za svih " + data.items.length + " pjesama?")) return;
          try { const r = await json(await api("db/pending/all", { method: "POST", headers: { "Content-Type": "application/json" },
                                                                body: JSON.stringify({ action: a }) }));
                toast("Riješeno: " + r.resolved); await refreshAll(); showPending(); }
          catch (e) { toast(e.message, true); }
        };
        bulk.append(b);
      });
      box.append(bulk);
    }
    data.items.forEach(p => {
      const c = el("div", "card");
      const h = el("h3");
      h.append(document.createTextNode(p.new.title + "  "),
               el("span", "simbadge", p.reason === "longer" ? "ista pjesma, nova ima više teksta"
                                                            : Math.round(p.similarity * 100) + " % slično"));
      c.append(h);
      const src = p.source && p.source.file ? (p.source.file + (p.source.pages && p.source.pages.length ? ", str. " + p.source.pages.join(", ") : "")) : "";
      const cmp = el("div", "cmp");
      const left = el("div"), right = el("div");
      left.append(el("h4", null, "Postojeća #" + p.existing.id + " · " + p.existing.title + (p.existing.key ? " · Key: " + p.existing.key : "")),
                  renderSheet(p.existing, p.new));
      right.append(el("h4", null, "Nova" + (src ? " (" + src + ")" : "") + (p.new.key ? " · Key: " + p.new.key : "")),
                   renderSheet(p.new, p.existing));
      cmp.append(left, right); c.append(cmp);
      const act = el("div", "actions");
      [["keep_existing", "Zadrži postojeću", ""], ["replace", "Zamijeni novom", "primary"], ["keep_both", "Zadrži obje", ""]]
        .forEach(([a, label, cls]) => {
          const b = el("button", "btn " + cls, label);
          b.onclick = async () => {
            try { await json(await api("db/pending/" + p.id, { method: "POST", headers: { "Content-Type": "application/json" },
                                                         body: JSON.stringify({ action: a }) }));
                  toast("Spremljeno."); await refreshAll(); showPending(); }
            catch (e) { toast(e.message, true); }
          };
          act.append(b);
        });
      c.append(act); box.append(c);
    });
    showView(box);
  } catch (e) { toast(e.message, true); }
}
$("pendingBar").onclick = showPending;

/* ── Odabrane pjesme ── */
function selectedIds() { return state.songs.filter(s => state.sel.has(s.id)).map(s => s.id); }
$("btnSelPdf").onclick = async () => {
  busy("Pripremam PDF…");
  try {
    const res = await fetch("db/pdf", { method: "POST", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ ids: selectedIds() }) });
    if (!res.ok) await json(res);
    const a = document.createElement("a"); a.href = URL.createObjectURL(await res.blob());
    a.download = "pjesme_iz_baze.pdf"; a.click(); URL.revokeObjectURL(a.href);
  } catch (e) { toast(e.message, true); } finally { busy(null); }
};
$("btnSelEditor").onclick = async () => {
  try {
    const old = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    if (old && old.songs && old.songs.length &&
        !confirm("U uredniku već postoji započeta pjesmarica (" + old.songs.length + " pjesama). Zamijeniti je odabranim pjesmama iz baze?")) return;
    const data = await json(await fetch("db/to-editor", { method: "POST", headers: { "Content-Type": "application/json" },
                                                          body: JSON.stringify({ ids: selectedIds() }) }));
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ filename: "pjesmarica_iz_baze",
      songs: data.songs.map(s => ({ ...s, include: true })), sel: 0, savedAt: Date.now() }));
    location.href = "./#nastavi";
  } catch (e) { toast(e.message, true); }
};

Promise.all([loadTags(), loadList(), refreshStats()]).catch(e => toast(e.message, true));
</script>
</body>
</html>"""
