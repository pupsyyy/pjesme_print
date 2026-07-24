#!/usr/bin/env python3
"""
Web sučelje za pjesmaricu: upload PDF-a, uređivanje pjesama (tekst, redoslijed,
odabir), opcije formatiranja i izvoz u novi PDF ili Word dokument.

Pokretanje (razvoj):
    python app.py                # http://localhost:8000

Produkcija (gunicorn):
    gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 300 app:app
"""

import io
import os
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from werkzeug.utils import secure_filename

from pdf_to_word import (FONT_CHOICES, SECTION_LABELS, build_docx,
                         build_docx_compact, is_chord_line, parse_pdf)
from pdf_writer import build_pdf, build_pdf_compact

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
MAX_SONGS = 500

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


# ── Pretvorbe: parsirana pjesma <-> polja u editoru ───────────────────────────

def song_to_editable(song):
    """Parsirana pjesma -> polja za editor (body = notes + sekcije kao tekst)."""
    lines = list(song.get("notes") or [])
    for label, sec_lines in song.get("sections") or []:
        if lines and lines[-1] != "":
            lines.append("")
        if label:
            lines.append(label)
        lines.extend(sec_lines)
    return {
        "title": song.get("title") or "",
        "key": song.get("key") or "",
        "subtitle": song.get("subtitle") or "",
        "pjesmarica": "\n".join(song.get("pjesmarica") or []),
        "body": "\n".join(lines),
    }


def body_to_parts(text):
    """Tekst iz editora -> (notes, sections); labele sekcija u svom retku."""
    notes, sections = [], []
    current_label, current_lines = None, []
    in_notes = True

    for raw in (text or "").splitlines():
        tl = raw.strip()
        if not tl:
            if current_label is not None or current_lines:
                current_lines.append("")
            elif in_notes and notes:
                notes.append("")
            continue
        if SECTION_LABELS.match(tl):
            if in_notes and current_lines:
                notes.extend(current_lines)
                current_lines = []
            elif current_label is not None or current_lines:
                while current_lines and current_lines[-1] == "":
                    current_lines.pop()
                sections.append([current_label, current_lines])
                current_lines = []
            current_label = tl
            in_notes = False
        else:
            if in_notes:
                notes.append(tl)
            else:
                current_lines.append(tl)

    if current_label is not None or current_lines:
        while current_lines and current_lines[-1] == "":
            current_lines.pop()
        sections.append([current_label, current_lines])
    while notes and notes[-1] == "":
        notes.pop()
    return notes, sections


def editable_to_song(d):
    """Polja iz editora -> struktura pjesme za izvoz."""
    key_lines = [ln.strip() for ln in str(d.get("key") or "").splitlines()
                 if ln.strip()]
    pj_lines = [ln.strip() for ln in str(d.get("pjesmarica") or "").splitlines()
                if ln.strip()]
    notes, sections = body_to_parts(str(d.get("body") or ""))
    return {
        "title": str(d.get("title") or "").strip() or "(bez naslova)",
        "key": "\n".join(key_lines),
        "subtitle": str(d.get("subtitle") or "").strip() or None,
        "pjesmarica": pj_lines,
        "notes": notes,
        "sections": sections,
    }


def strip_chord_lines(song):
    """Ukloni retke koji sadrže samo akorde (i sažmi nastale prazne retke)."""
    def clean(lines):
        out = []
        for ln in lines:
            if ln and is_chord_line(ln):
                continue
            if ln == "" and out and out[-1] == "":
                continue
            out.append(ln)
        while out and out[-1] == "":
            out.pop()
        return out

    song["notes"] = clean(song["notes"])
    song["sections"] = [[lab, clean(ls)] for lab, ls in song["sections"]]
    return song


def parse_options(d):
    d = d or {}

    def clamp_num(val, lo, hi, default, cast):
        try:
            return max(lo, min(hi, cast(val)))
        except (TypeError, ValueError):
            return default

    font = d.get("font")
    layout = d.get("layout")
    return {
        "layout": layout if layout in ("kompaktno", "klasicno") else "kompaktno",
        "font": font if font in FONT_CHOICES else FONT_CHOICES[0],
        "title_size": clamp_num(d.get("title_size"), 12, 48, 26, int),
        "body_size": clamp_num(d.get("body_size"), 7, 20, 9, int),
        "margin_cm": clamp_num(d.get("margin_cm"), 0.3, 4.0, 0.5, float),
        "page_break": bool(d.get("page_break", True)),
        "n_cols": clamp_num(d.get("n_cols"), 2, 3, 3, int),
        "strip_chords": bool(d.get("strip_chords", True)),
    }


def export_payload():
    data = request.get_json(silent=True)
    if not data:
        return None, None, None, (jsonify(error="Neispravan zahtjev."), 400)
    raw_songs = data.get("songs") or []
    if not raw_songs:
        return None, None, None, (
            jsonify(error="Nijedna pjesma nije odabrana za izvoz."), 400)
    if len(raw_songs) > MAX_SONGS:
        return None, None, None, (
            jsonify(error=f"Previše pjesama (maksimum {MAX_SONGS})."), 400)
    options = parse_options(data.get("options"))
    songs = [editable_to_song(s) for s in raw_songs]
    if options["strip_chords"]:
        songs = [strip_chord_lines(s) for s in songs]
    stem = secure_filename(str(data.get("filename") or ""))[:80] or "pjesmarica"
    return songs, options, stem, None


# ── Rute ──────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return PAGE.replace("__MAX_MB__", str(MAX_UPLOAD_MB))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error=f"Datoteka je prevelika (maksimum {MAX_UPLOAD_MB} MB)."), 413


@app.post("/parse")
def parse():
    file = request.files.get("pdf")
    if file is None or not file.filename:
        return jsonify(error="Odaberi PDF datoteku."), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify(error="Datoteka mora biti PDF (.pdf)."), 400

    stem = Path(secure_filename(file.filename)).stem[:80] or "pjesmarica"

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "ulaz.pdf"
        file.save(pdf_path)
        try:
            songs = parse_pdf(str(pdf_path))
        except Exception as exc:
            app.logger.exception("Parsiranje nije uspjelo")
            return jsonify(error=f"Ne mogu pročitati PDF: {exc}"), 500

    if not songs:
        return jsonify(error="U PDF-u nije pronađena nijedna pjesma."), 422

    return jsonify(filename=stem, songs=[song_to_editable(s) for s in songs])


@app.post("/export/docx")
def export_docx():
    songs, options, stem, err = export_payload()
    if err:
        return err
    try:
        builder = build_docx_compact if options["layout"] == "kompaktno" else build_docx
        doc = builder(songs, options)
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
    except Exception as exc:
        app.logger.exception("Word izvoz nije uspio")
        return jsonify(error=f"Izvoz nije uspio: {exc}"), 500
    return send_file(
        buf, as_attachment=True, download_name=f"{stem}.docx",
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document")


@app.post("/export/pdf")
def export_pdf():
    songs, options, stem, err = export_payload()
    if err:
        return err
    try:
        builder = build_pdf_compact if options["layout"] == "kompaktno" else build_pdf
        pdf_bytes = builder(songs, options)
    except Exception as exc:
        app.logger.exception("PDF izvoz nije uspio")
        return jsonify(error=f"Izvoz nije uspio: {exc}"), 500
    return send_file(
        io.BytesIO(pdf_bytes), as_attachment=True,
        download_name=f"{stem}.pdf", mimetype="application/pdf")


# ── Sučelje (jedna stranica, tamna tema) ──────────────────────────────────────

PAGE = r"""<!doctype html>
<html lang="hr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pjesmarica konverter</title>
<style>
  :root {
    --bg: #0e1116; --panel: #161b23; --panel2: #1c2330; --border: #29323f;
    --text: #e8ecf3; --muted: #93a0b4; --accent: #4f8cff; --accent2: #2f6ae0;
    --ok: #37b26c; --err: #e5534b; --radius: 10px;
  }
  body.light {
    --bg: #f3f5f8; --panel: #ffffff; --panel2: #eef1f6; --border: #d7dde6;
    --text: #1c222c; --muted: #5b6674; --accent: #2f6ae0; --accent2: #2456bd;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
         display: flex; flex-direction: column; }
  button, input, select, textarea { font: inherit; color: inherit; }

  header { display: flex; align-items: center; gap: .75rem;
           padding: .7rem 1.1rem; border-bottom: 1px solid var(--border);
           background: var(--panel); }
  header h1 { margin: 0; font-size: 1.12rem; }
  header .sub { color: var(--muted); font-size: .85rem; }
  header .grow { flex: 1; }
  #btnTheme { background: none; border: 1px solid var(--border);
              border-radius: var(--radius); padding: .35rem .6rem; cursor: pointer; }

  main { flex: 1; display: flex; flex-direction: column; min-height: 0; }
  .hidden { display: none !important; }

  /* Upload */
  #viewUpload { flex: 1; display: flex; align-items: center; justify-content: center;
                padding: 1rem; }
  .upcard { background: var(--panel); border: 1px solid var(--border);
            border-radius: 14px; padding: 2rem; max-width: 34rem; width: 100%; }
  .upcard h2 { margin: 0 0 .3rem; font-size: 1.25rem; }
  .upcard p { color: var(--muted); margin: 0 0 1.2rem; }
  #drop { border: 2px dashed var(--border); border-radius: var(--radius);
          padding: 2.2rem 1rem; text-align: center; cursor: pointer;
          transition: border-color .15s, background .15s; }
  #drop.over { border-color: var(--accent); background: var(--panel2); }
  #drop strong { color: var(--accent); }
  .upnote { font-size: .8rem; color: var(--muted); text-align: center;
            margin-top: .9rem; }

  /* Options bar */
  .optsbar { display: flex; flex-wrap: wrap; gap: .55rem 1rem; align-items: center;
             padding: .6rem 1.1rem; background: var(--panel);
             border-bottom: 1px solid var(--border); }
  .optsbar label { display: flex; align-items: center; gap: .4rem;
                   font-size: .85rem; color: var(--muted); }
  .optsbar select, .optsbar input[type=number] {
      background: var(--panel2); border: 1px solid var(--border);
      border-radius: 7px; padding: .3rem .45rem; }
  .optsbar input[type=number] { width: 4.3rem; }
  .optsbar .grow { flex: 1; }
  .btn { border: 1px solid var(--border); background: var(--panel2);
         border-radius: var(--radius); padding: .45rem .85rem; cursor: pointer; }
  .btn:hover { border-color: var(--accent); }
  .btn.primary { background: var(--accent); border-color: var(--accent);
                 color: #fff; font-weight: 600; }
  .btn.primary:hover { background: var(--accent2); }

  /* Editor */
  .editor { flex: 1; display: grid; grid-template-columns: 19rem 1fr;
            min-height: 0; }
  aside { border-right: 1px solid var(--border); background: var(--panel);
          display: flex; flex-direction: column; min-height: 0; }
  .listhead { display: flex; align-items: center; justify-content: space-between;
              padding: .6rem .8rem; border-bottom: 1px solid var(--border);
              font-size: .85rem; color: var(--muted); }
  #songList { list-style: none; margin: 0; padding: .4rem; overflow-y: auto;
              flex: 1; }
  #songList li { display: flex; align-items: center; gap: .35rem;
                 padding: .35rem .45rem; border-radius: 8px; cursor: pointer;
                 border: 1px solid transparent; }
  #songList li:hover { background: var(--panel2); }
  #songList li.sel { background: var(--panel2); border-color: var(--accent); }
  #songList li.off .t { opacity: .38; text-decoration: line-through; }
  #songList .t { flex: 1; white-space: nowrap; overflow: hidden;
                 text-overflow: ellipsis; }
  #songList .mini { background: none; border: 0; color: var(--muted);
                    cursor: pointer; padding: .1rem .22rem; border-radius: 5px;
                    font-size: .85rem; }
  #songList .mini:hover { color: var(--text); background: var(--border); }

  #songForm { display: flex; flex-direction: column; gap: .7rem;
              padding: .9rem 1.1rem; overflow-y: auto; min-height: 0; }
  #songForm label { display: flex; flex-direction: column; gap: .25rem;
                    font-size: .82rem; color: var(--muted); }
  #songForm input, #songForm textarea {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 8px; padding: .5rem .6rem; resize: vertical; }
  #songForm input:focus, #songForm textarea:focus {
      outline: none; border-color: var(--accent); }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: .7rem; }
  #fBody { min-height: 45vh; line-height: 1.5; }
  .hint { color: var(--muted); font-size: .78rem; margin: 0; }

  /* Toast + busy */
  #toast { position: fixed; left: 50%; bottom: 1.2rem; transform: translateX(-50%);
           background: var(--panel); border: 1px solid var(--border);
           border-left: 4px solid var(--ok); border-radius: var(--radius);
           padding: .6rem 1rem; max-width: 90%; box-shadow: 0 6px 24px rgba(0,0,0,.35);
           opacity: 0; pointer-events: none; transition: opacity .2s; z-index: 30; }
  #toast.show { opacity: 1; }
  #toast.err { border-left-color: var(--err); }
  #busy { position: fixed; inset: 0; background: rgba(0,0,0,.45);
          display: flex; align-items: center; justify-content: center; z-index: 20; }
  #busy .box { background: var(--panel); border: 1px solid var(--border);
               border-radius: var(--radius); padding: 1rem 1.6rem;
               display: flex; gap: .7rem; align-items: center; }
  .spin { width: 1.1rem; height: 1.1rem; border: 3px solid var(--border);
          border-top-color: var(--accent); border-radius: 50%;
          animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  @media (max-width: 760px) {
    .editor { grid-template-columns: 1fr; grid-template-rows: 34vh 1fr; }
    aside { border-right: 0; border-bottom: 1px solid var(--border); }
    .row2 { grid-template-columns: 1fr; }
    #fBody { min-height: 38vh; }
  }
</style>
</head>
<body>
<header>
  <h1>🎵 Pjesmarica konverter</h1>
  <span class="sub">Ubaci PDF → uredi → novi PDF ili Word</span>
  <span class="grow"></span>
  <button id="btnTheme" title="Svijetla/tamna tema">☀️</button>
</header>

<main>
  <div id="viewUpload">
    <div class="upcard">
      <h2>Učitaj pjesmaricu</h2>
      <p>Svaka stranica PDF-a = jedna pjesma. Nakon učitavanja uređuješ tekst,
         redoslijed i odabir pjesama pa preuzmeš novi PDF ili Word — kompaktno
         u stupcima (kao za misu) ili klasično sa stranicom po pjesmi.</p>
      <div id="drop">
        <strong>Klikni</strong> ili dovuci PDF ovdje
        <input type="file" id="fileInput" accept=".pdf,application/pdf" hidden>
      </div>
      <p class="upnote">Maksimalna veličina: __MAX_MB__ MB · Datoteka se obrađuje
         na serveru i ne sprema se trajno.</p>
    </div>
  </div>

  <div id="viewEditor" class="hidden">
    <div class="optsbar">
      <button class="btn" id="btnBack">← Nova datoteka</button>
      <label>Izgled
        <select id="optLayout">
          <option value="kompaktno">Kompaktno (stupci)</option>
          <option value="klasicno">Klasično (stranica po pjesmi)</option>
        </select>
      </label>
      <label>Font
        <select id="optFont">
          <option>Liberation Serif</option><option>Liberation Sans</option>
          <option>FreeSerif</option><option>FreeSans</option>
        </select>
      </label>
      <label>Tekst <input type="number" id="optBody" min="7" max="20" value="9"></label>
      <label id="lblCols">Stupci
        <select id="optCols"><option>2</option><option selected>3</option></select>
      </label>
      <label id="lblTitle" class="hidden">Naslov
        <input type="number" id="optTitle" min="12" max="48" value="26"></label>
      <label id="lblMargin" class="hidden">Margina cm
        <input type="number" id="optMargin" min="0.3" max="4" step="0.1" value="0.5"></label>
      <label id="lblBreak" class="hidden">
        <input type="checkbox" id="optBreak" checked> Pjesma = nova stranica</label>
      <label title="Retci koji sadrže samo akorde (D, Am7, Fis...) izostavljaju se iz dokumenta">
        <input type="checkbox" id="optChords" checked> Ukloni akorde</label>
      <span class="grow"></span>
      <button class="btn primary" id="btnPdf">⬇ PDF</button>
      <button class="btn primary" id="btnDocx">⬇ Word</button>
    </div>
    <div class="editor">
      <aside>
        <div class="listhead">
          <span id="songCount"></span>
          <button class="btn" id="btnAdd" style="padding:.25rem .6rem">＋ Nova</button>
        </div>
        <ul id="songList"></ul>
      </aside>
      <section id="songForm">
        <div class="row2">
          <label>Naslov <input id="fTitle"></label>
          <label>Tonalitet (desno od naslova)
            <textarea id="fKey" rows="2" placeholder="Key: A&#10;Capo 1"></textarea></label>
        </div>
        <div class="row2">
          <label>Podnaslov <input id="fSubtitle" placeholder="npr. Papa Band"></label>
          <label>Pjesmarica (brojevi)
            <textarea id="fPjesmarica" rows="2" placeholder="1 - 23"></textarea></label>
        </div>
        <label>Tekst pjesme <textarea id="fBody" spellcheck="false"></textarea></label>
        <p class="hint">Labelu sekcije (Verse 1, Chorus, Bridge…) napiši samu u
           retku. Kompaktni izgled: Chorus/Bridge/Tag/Outro izlaze <b>uvučeno,
           podebljano i u kurzivu</b>, naslovi i tonaliteti se izostavljaju,
           pjesme dijeli crta. Klasični izgled: svaka pjesma na svojoj stranici,
           s naslovom i <b>boldanim</b> labelama.</p>
      </section>
    </div>
  </div>
</main>

<div id="busy" class="hidden"><div class="box"><div class="spin"></div>
  <span id="busyText">Radim…</span></div></div>
<div id="toast"></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const state = { filename: "pjesmarica", songs: [], sel: -1 };

/* Tema (zadano tamna) */
function applyTheme(t) {
  document.body.classList.toggle("light", t === "light");
  $("btnTheme").textContent = t === "light" ? "🌙" : "☀️";
}
applyTheme(localStorage.getItem("pjesme-theme") || "dark");
$("btnTheme").onclick = () => {
  const t = document.body.classList.contains("light") ? "dark" : "light";
  localStorage.setItem("pjesme-theme", t); applyTheme(t);
};

/* Pomoćno */
function toast(msg, isErr) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.toggle("err", !!isErr);
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), isErr ? 6000 : 3000);
}
function busy(text) {
  if (text) { $("busyText").textContent = text; $("busy").classList.remove("hidden"); }
  else $("busy").classList.add("hidden");
}
function showEditor(on) {
  $("viewUpload").classList.toggle("hidden", on);
  $("viewEditor").classList.toggle("hidden", !on);
}

/* Popis pjesama */
function renderList() {
  const ul = $("songList");
  ul.innerHTML = "";
  state.songs.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = (i === state.sel ? "sel " : "") + (s.include ? "" : "off");

    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = s.include;
    cb.title = "Uključi u izvoz";
    cb.onclick = e => { e.stopPropagation(); s.include = cb.checked; renderList(); };

    const t = document.createElement("span");
    t.className = "t";
    t.textContent = (i + 1) + ". " + (s.title || "(bez naslova)");

    const up = mini("↑", "Pomakni gore", e => { e.stopPropagation(); moveSong(i, -1); });
    const dn = mini("↓", "Pomakni dolje", e => { e.stopPropagation(); moveSong(i, 1); });
    const del = mini("✕", "Obriši pjesmu", e => {
      e.stopPropagation();
      if (confirm("Obrisati pjesmu \"" + (s.title || "(bez naslova)") + "\"?")) {
        state.songs.splice(i, 1);
        if (state.sel >= state.songs.length) state.sel = state.songs.length - 1;
        renderList(); loadForm();
      }
    });

    li.append(cb, t, up, dn, del);
    li.onclick = () => { saveForm(); state.sel = i; renderList(); loadForm(); };
    ul.appendChild(li);
  });
  $("songCount").textContent = state.songs.length + " pjesama";
}
function mini(txt, title, fn) {
  const b = document.createElement("button");
  b.className = "mini"; b.textContent = txt; b.title = title; b.onclick = fn;
  return b;
}
function moveSong(i, d) {
  const j = i + d;
  if (j < 0 || j >= state.songs.length) return;
  saveForm();
  [state.songs[i], state.songs[j]] = [state.songs[j], state.songs[i]];
  if (state.sel === i) state.sel = j; else if (state.sel === j) state.sel = i;
  renderList();
}

/* Forma odabrane pjesme */
const FIELDS = [["fTitle", "title"], ["fKey", "key"], ["fSubtitle", "subtitle"],
                ["fPjesmarica", "pjesmarica"], ["fBody", "body"]];
function loadForm() {
  const s = state.songs[state.sel];
  const off = !s;
  FIELDS.forEach(([id, k]) => { $(id).value = s ? (s[k] || "") : ""; $(id).disabled = off; });
}
function saveForm() {
  const s = state.songs[state.sel];
  if (!s) return;
  FIELDS.forEach(([id, k]) => { s[k] = $(id).value; });
}
FIELDS.forEach(([id, k]) => {
  $(id).addEventListener("input", () => {
    const s = state.songs[state.sel];
    if (!s) return;
    s[k] = $(id).value;
    if (k === "title") {
      const li = $("songList").children[state.sel];
      if (li) li.querySelector(".t").textContent =
        (state.sel + 1) + ". " + (s.title || "(bez naslova)");
    }
  });
});
$("btnAdd").onclick = () => {
  saveForm();
  state.songs.push({ title: "Nova pjesma", key: "", subtitle: "",
                     pjesmarica: "", body: "Verse 1\n", include: true });
  state.sel = state.songs.length - 1;
  renderList(); loadForm();
  $("fTitle").focus(); $("fTitle").select();
};

/* Upload i parsiranje */
const drop = $("drop"), fileInput = $("fileInput");
drop.onclick = () => fileInput.click();
fileInput.onchange = () => { if (fileInput.files[0]) doParse(fileInput.files[0]); };
["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", e => {
  const f = e.dataTransfer.files[0];
  if (f) doParse(f);
});

async function doParse(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    toast("Datoteka mora biti PDF (.pdf).", true); return;
  }
  busy("Čitam pjesmaricu…");
  try {
    const fd = new FormData();
    fd.append("pdf", file);
    const res = await fetch("parse", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || ("Greška " + res.status));
    state.filename = data.filename || "pjesmarica";
    state.songs = data.songs.map(s => ({ ...s, include: true }));
    state.sel = state.songs.length ? 0 : -1;
    renderList(); loadForm(); showEditor(true);
    toast("Učitano " + state.songs.length + " pjesama.");
  } catch (err) {
    toast(err.message, true);
  } finally {
    busy(null);
    fileInput.value = "";
  }
}

$("btnBack").onclick = () => {
  if (!state.songs.length ||
      confirm("Učitati novu datoteku? Trenutne izmjene se gube.")) {
    showEditor(false);
  }
};

/* Opcije izgleda */
const LAYOUT_DEFAULTS = {
  kompaktno: { body: 9, margin: 0.5 },
  klasicno: { body: 11, margin: 2.5 },
};
function applyLayout() {
  const compact = $("optLayout").value === "kompaktno";
  $("lblCols").classList.toggle("hidden", !compact);
  $("lblTitle").classList.toggle("hidden", compact);
  $("lblMargin").classList.toggle("hidden", compact);
  $("lblBreak").classList.toggle("hidden", compact);
}
$("optLayout").addEventListener("change", () => {
  const d = LAYOUT_DEFAULTS[$("optLayout").value];
  $("optBody").value = d.body;
  $("optMargin").value = d.margin;
  applyLayout();
});
applyLayout();

/* Izvoz */
function options() {
  return {
    layout: $("optLayout").value,
    font: $("optFont").value,
    title_size: parseInt($("optTitle").value, 10) || 26,
    body_size: parseInt($("optBody").value, 10) || 9,
    margin_cm: parseFloat($("optMargin").value) || 0.5,
    page_break: $("optBreak").checked,
    n_cols: parseInt($("optCols").value, 10) || 3,
    strip_chords: $("optChords").checked,
  };
}
async function doExport(kind) {
  saveForm();
  const songs = state.songs.filter(s => s.include)
    .map(({ include, ...rest }) => rest);
  if (!songs.length) { toast("Nijedna pjesma nije uključena (kvačice).", true); return; }
  busy(kind === "pdf" ? "Pripremam PDF…" : "Pripremam Word…");
  try {
    const res = await fetch("export/" + kind, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: state.filename, options: options(), songs }),
    });
    if (!res.ok) {
      let msg = "Greška " + res.status;
      try { msg = (await res.json()).error || msg; } catch (_e) {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = state.filename + (kind === "pdf" ? ".pdf" : ".docx");
    a.click();
    URL.revokeObjectURL(a.href);
    toast("Preuzimanje pokrenuto (" + songs.length + " pjesama).");
  } catch (err) {
    toast(err.message, true);
  } finally {
    busy(null);
  }
}
$("btnPdf").onclick = () => doExport("pdf");
$("btnDocx").onclick = () => doExport("docx");

busy(null);
loadForm();
</script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
