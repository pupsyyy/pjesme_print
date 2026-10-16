#!/usr/bin/env python3
"""
Web dio baze pjesama (Flask Blueprint):

  /baza                 stranica: uvoz PDF-ova, slične pjesme (odluka),
                        popis i pretraga, pregled s akordima, preuzimanja
  /db/...               JSON za stranicu (pisanje: lozinka DB_PASSWORD ako je zadana)
  /api/v1/...           samo čitanje, za druge aplikacije (token API_TOKEN ako je
                        zadan; CORS dopušten)
"""

import hashlib
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
import songdb

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

def _is_password_error(exc):
    msg = (str(exc) + " " + type(exc).__name__).lower()
    return "password" in msg or "encrypt" in msg


@bp.post("/db/import")
def db_import():
    denied = _require_write()
    if denied:
        return denied
    files = request.files.getlist("pdf")
    if not files:
        return jsonify(error="Nije poslan nijedan PDF."), 400
    conn = _conn()
    reports = []
    for f in files:
        name = os.path.basename(f.filename or "dokument.pdf")
        data = f.read()
        if not data.startswith(b"%PDF"):
            reports.append({"file": name, "error": "Nije PDF datoteka."})
            continue
        try:
            res = chord_extract.extract(data, name)
        except Exception as exc:  # noqa: BLE001 — poruka korisniku, ne 500
            msg = ("PDF je zaštićen lozinkom." if _is_password_error(exc)
                   else "PDF se ne može pročitati.")
            reports.append({"file": name, "error": msg})
            continue
        if not res["songs"] and len(res["skipped"]) == res["pages"]:
            reports.append({"file": name, "error": "U PDF-u nema teksta pjesama "
                            "(skenirani PDF?).", "warnings": res["warnings"]})
            continue
        if not res["songs"] or not res["titled"]:
            reports.append({"file": name, "error": "U PDF-u nisu prepoznate pjesme "
                            "(nema naslova). Baza prima PDF-ove iz aplikacije za "
                            "pjesmarice i pjesmarice iz Worda — pjesma s naslovom na "
                            "vrhu stranice, akordi u retku iznad teksta."})
            continue
        rep = songdb.import_songs(conn, res["songs"], name,
                                  hashlib.sha256(data).hexdigest(), res["pages"])
        rep["warnings"] = res["warnings"]
        rep["pages"] = res["pages"]
        reports.append(rep)
    return jsonify(reports=reports, stats=songdb.stats(conn))


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
    return jsonify(songdb.list_songs(_conn(), q, _int_arg("limit", 200, 1, 1000),
                                     _int_arg("offset", 0, 0, 10**9)))


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


@bp.get("/db/pending")
def db_pending():
    return jsonify(items=songdb.list_pending(_conn()))


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
                   endpoints=["/api/v1/songs?q=&limit=&offset=", "/api/v1/songs/<id>",
                              "/api/v1/songs/<id>.cho", "/api/v1/songs/<id>.pdf",
                              "/api/v1/export"])


@bp.get("/api/v1/songs")
def api_songs():
    return db_songs()


@bp.get("/api/v1/songs/<int:song_id>")
def api_song(song_id):
    return db_song(song_id)


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
            .replace("__MAX_MB__", str(MAX_UPLOAD_MB)))
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
      <b>＋ Uvezi PDF-ove</b> (klikni ili dovuci)
      <small>Više datoteka odjednom · do __MAX_MB__ MB po datoteci</small>
      <input type="file" id="fileInput" accept=".pdf,application/pdf" multiple hidden>
    </div>
    <div id="pendingBar" class="hidden"></div>
    <div class="search"><input id="q" type="search" placeholder="Traži naslov, broj, tekst…" autocomplete="off"></div>
    <ul id="list"></ul>
    <div class="selbar">
      <span id="selInfo" class="muted">Odaberi pjesme kvačicom</span>
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
      <p>Iste pjesme se ne dupliraju, a za pjesme koje su ≥ 80 % slične postojećoj<br>
         aplikacija te pita koju zadržati.</p>
      <p class="muted" style="font-size:.85rem">Druge aplikacije čitaju bazu preko
         <a href="api/v1" target="_blank">api/v1</a> · v__VERSION__</p>
    </div>
    <div id="view" class="hidden"></div>
  </section>
</div>

<div id="busy" class="hidden"><div class="box"><div class="spin"></div><span id="busyText">Radim…</span></div></div>
<div id="toast"></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const state = { songs: [], total: 0, sel: new Set(), current: null, q: "" };
const PW_KEY = "pjesme-dbpw", DRAFT_KEY = "pjesme-draft";

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
  const div = el("div", "ln" + (mark ? " diff" : ""));
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
function showView(node) {
  $("welcome").classList.add("hidden");
  const v = $("view"); v.innerHTML = ""; v.append(node); v.classList.remove("hidden");
  $("main").scrollTop = 0;
  fixHyphens(v);
  if (window.matchMedia("(max-width: 820px)").matches) v.scrollIntoView({ behavior: "smooth" });
}

/* ── Popis ── */
async function loadList() {
  const data = await json(await fetch("db/songs?limit=1000&q=" + encodeURIComponent(state.q)));
  state.songs = data.items; state.total = data.total;
  renderList();
}
function renderList() {
  const ul = $("list"); ul.innerHTML = "";
  state.songs.forEach(s => {
    const li = el("li", state.current === s.id ? "sel" : "");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = state.sel.has(s.id);
    cb.onclick = e => { e.stopPropagation(); cb.checked ? state.sel.add(s.id) : state.sel.delete(s.id); renderSel(); };
    const box = el("div");
    box.append(el("div", "t", s.title));
    const meta = [s.subtitle, s.key && ("Key: " + s.key), (s.numbers || []).join(", ")].filter(Boolean).join(" · ");
    if (meta) box.append(el("div", "m", meta));
    li.append(cb, box);
    li.onclick = () => openSong(s.id);
    ul.append(li);
  });
  if (!state.songs.length) ul.append(el("li", "muted", state.q ? "Nema rezultata." : "Baza je prazna."));
  if (state.total > state.songs.length)
    ul.append(el("li", "muted", "Prikazano " + state.songs.length + " od " + state.total + " — suzi pretragu."));
  renderSel();
}
function renderSel() {
  const n = state.sel.size;
  $("selInfo").textContent = n ? ("Odabrano: " + n) : "Odaberi pjesme kvačicom";
  $("btnSelPdf").disabled = $("btnSelEditor").disabled = !n;
}
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
async function openSong(id) {
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
            await Promise.all([loadList(), refreshStats()]); }
      catch (e) { toast(e.message, true); }
    };
    act.append(a1, a2, a3, del);
    box.append(act);
    box.append(renderSheet(s));
    const src = (s.sources || []).map(x => x.file + (x.pages && x.pages.length ? " (str. " + x.pages.join(", ") + ")" : "")).join("; ");
    if (src) box.append(el("p", "muted", "Izvor: " + src));
    showView(box);
  } catch (e) { toast(e.message, true); }
}

/* ── Uvoz ── */
const drop = $("drop");
drop.onclick = () => $("fileInput").click();
$("fileInput").onchange = () => { importFiles([...$("fileInput").files]); $("fileInput").value = ""; };
["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => importFiles([...e.dataTransfer.files]));

async function importFiles(files) {
  files = files.filter(f => f.name.toLowerCase().endsWith(".pdf"));
  if (!files.length) { toast("Odaberi PDF datoteke.", true); return; }
  const reports = [];
  try {
    for (let i = 0; i < files.length; i++) {
      busy("Uvozim " + (i + 1) + "/" + files.length + ": " + files[i].name);
      const fd = new FormData(); fd.append("pdf", files[i]);
      try {
        const data = await json(await api("db/import", { method: "POST", body: fd }));
        reports.push(...data.reports);
      } catch (e) { reports.push({ file: files[i].name, error: e.message }); }
    }
  } finally { busy(null); }
  await Promise.all([loadList(), refreshStats()]);
  showReport(reports);
}
function showReport(reports) {
  const box = el("div");
  box.append(el("h2", null, "Uvoz završen"));
  let anyConflict = false;
  reports.forEach(r => {
    const c = el("div", "card");
    c.append(el("h3", null, r.file));
    if (r.error) { c.append(el("div", "bad", "✕ " + r.error)); box.append(c); return; }
    const ul = el("ul", "rep");
    const li = (cls, t) => ul.append(el("li", cls, t));
    li("ok", "✓ dodano: " + r.added.length + (r.added.length ? " — " + r.added.map(x => x.title).join(", ") : ""));
    if (r.duplicates.length) li("muted", "= već u bazi (preskočeno): " + r.duplicates.map(x => x.title).join(", "));
    if (r.conflicts.length) {
      anyConflict = true;
      li("warn", "⚠ slične postojećima (čekaju odluku): " +
         r.conflicts.map(x => x.title + " ~ " + x.existing_title + " " + Math.round(x.similarity * 100) + " %").join(", "));
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
    data.items.forEach(p => {
      const c = el("div", "card");
      const h = el("h3");
      h.append(document.createTextNode(p.new.title + "  "), el("span", "simbadge", Math.round(p.similarity * 100) + " % slično"));
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
                  toast("Spremljeno."); await Promise.all([loadList(), refreshStats()]); showPending(); }
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

Promise.all([loadList(), refreshStats()]).catch(e => toast(e.message, true));
</script>
</body>
</html>"""
