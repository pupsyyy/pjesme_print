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
import re
import tempfile
from pathlib import Path

import pdfplumber
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
    wm_level = d.get("watermark_level")
    return {
        "layout": layout if layout in ("kompaktno", "klasicno") else "kompaktno",
        "font": font if font in FONT_CHOICES else FONT_CHOICES[0],
        "title_size": clamp_num(d.get("title_size"), 12, 48, 26, int),
        "body_size": clamp_num(d.get("body_size"), 7, 20, 9, int),
        "margin_cm": clamp_num(d.get("margin_cm"), 0.3, 4.0, 0.5, float),
        "page_break": bool(d.get("page_break", True)),
        "n_cols": clamp_num(d.get("n_cols"), 2, 3, 3, int),
        "strip_chords": bool(d.get("strip_chords", True)),
        "watermark": bool(d.get("watermark", False)),
        "watermark_level": wm_level if wm_level in ("vrlo_blijedo", "srednje")
                           else "vrlo_blijedo",
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
    # makni sufiks izlaza da se kod ponovnog uploada ne gomila (_print_print...)
    stem = re.sub(r"_(print|out)$", "", stem) or "pjesmarica"

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "ulaz.pdf"
        file.save(pdf_path)
        try:
            songs = parse_pdf(str(pdf_path))
            with pdfplumber.open(pdf_path) as pdf_doc:
                n_landscape = sum(1 for p in pdf_doc.pages if p.width > p.height)
        except Exception as exc:
            app.logger.exception("Parsiranje nije uspjelo")
            return jsonify(error=f"Ne mogu pročitati PDF: {exc}"), 500

    if not songs:
        return jsonify(error="U PDF-u nije pronađena nijedna pjesma."), 422

    # Zaštita od konvertiranja već konvertiranog ispisa: takav ulaz ima
    # ležeće stranice i/ili crte razdjelnice kao tekst, a stupci se pri
    # čitanju pomiješaju u jedan red.
    warnings = []
    all_lines = [ln for s in songs
                 for ln in (s["notes"] + [x for _l, ls in s["sections"] for x in ls])]
    has_divider = any(ln.strip().startswith("____") for ln in all_lines)
    if n_landscape or has_divider:
        warnings.append(
            "⚠ Ova datoteka izgleda kao VEĆ KONVERTIRANI ispis (ležeće "
            "stranice/crte između pjesama), a ne originalna pjesmarica. "
            "Stupci se pri čitanju pomiješaju — učitaj originalni PDF "
            "(uspravan, jedna pjesma po stranici).")

    return jsonify(filename=stem, warnings=warnings,
                   songs=[song_to_editable(s) for s in songs])


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
        buf, as_attachment=True, download_name=f"{stem}_print.docx",
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
        download_name=f"{stem}_print.pdf", mimetype="application/pdf")


# ── Sučelje (jedna stranica, tamna tema) ──────────────────────────────────────

PAGE = r"""<!doctype html>
<html lang="hr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pjesmarica konverter</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700&family=Lora:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
<style>
  :root {
    /* Claude / Anthropic tamna paleta */
    --bg: #141413; --panel: #1e1d1b; --panel2: #26241f; --border: #35332e;
    --text: #faf9f5; --muted: #b0aea5; --accent: #d97757; --accent2: #c56144;
    --ok: #788c5d; --err: #cf6a4c; --ring: rgba(217,119,87,.35);
    --radius: 14px; --radius-sm: 9px;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 12px 32px rgba(0,0,0,.28);
    --ui: "Poppins", Arial, sans-serif;
    --serif: "Lora", Georgia, serif;
    --edit: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  body.light {
    /* Claude / Anthropic svijetla paleta (topla krem) */
    --bg: #faf9f5; --panel: #ffffff; --panel2: #f3f1ea; --border: #e8e6dc;
    --text: #141413; --muted: #6b6862; --accent: #d97757; --accent2: #bf5b3b;
    --ok: #5f7346; --err: #b8492c; --ring: rgba(217,119,87,.28);
    --shadow: 0 1px 2px rgba(20,20,19,.05), 0 10px 30px rgba(20,20,19,.07);
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 15px/1.5 var(--serif);
         display: flex; flex-direction: column;
         -webkit-font-smoothing: antialiased; }
  button, input, select, textarea { font-family: var(--ui); color: inherit; }
  h1, h2, h3 { font-family: var(--ui); letter-spacing: -.01em; }

  header { display: flex; align-items: center; gap: .8rem;
           padding: .7rem 1.15rem; border-bottom: 1px solid var(--border);
           background: var(--panel); }
  .logo { width: 2.1rem; height: 2.1rem; flex: none; border-radius: 10px;
          display: grid; place-items: center; font-size: 1.1rem;
          background: linear-gradient(135deg, var(--accent), #e0895f);
          color: #fff; box-shadow: 0 2px 8px var(--ring); }
  header h1 { margin: 0; font-size: 1.14rem; font-weight: 600; line-height: 1.1; }
  header .sub { display: block; color: var(--muted); font-size: .8rem;
                font-family: var(--serif); }
  header .grow { flex: 1; }
  #btnTheme { background: var(--panel2); border: 1px solid var(--border);
              border-radius: 10px; padding: .4rem .6rem; cursor: pointer;
              line-height: 1; transition: border-color .15s, background .15s; }
  #btnTheme:hover { border-color: var(--accent); }

  main { flex: 1; display: flex; flex-direction: column; min-height: 0; }
  .hidden { display: none !important; }

  /* Upload */
  #viewUpload { flex: 1; display: flex; align-items: center; justify-content: center;
                padding: 1.2rem; }
  .upcard { background: var(--panel); border: 1px solid var(--border);
            border-radius: 18px; padding: 2.4rem 2.2rem; max-width: 35rem;
            width: 100%; box-shadow: var(--shadow); }
  .upcard h2 { margin: 0 0 .4rem; font-size: 1.5rem; font-weight: 600; }
  .upcard p { color: var(--muted); margin: 0 0 1.4rem; font-size: .95rem; }
  #drop { border: 2px dashed var(--border); border-radius: var(--radius);
          padding: 2.6rem 1rem; text-align: center; cursor: pointer;
          background: var(--panel2);
          transition: border-color .18s, background .18s, transform .18s; }
  #drop:hover { border-color: var(--accent); }
  #drop.over { border-color: var(--accent); background: var(--ring);
               transform: scale(1.01); }
  .drop-ico { width: 3rem; height: 3rem; margin: 0 auto .7rem; border-radius: 14px;
              display: grid; place-items: center; font-size: 1.5rem;
              background: var(--panel); color: var(--accent);
              border: 1px solid var(--border); }
  #drop .big { font-family: var(--ui); font-weight: 500; font-size: 1.02rem; }
  #drop strong { color: var(--accent); font-weight: 600; }
  #drop .small { color: var(--muted); font-size: .82rem; margin-top: .25rem;
                 font-family: var(--serif); }
  .upnote { font-size: .8rem; color: var(--muted); text-align: center;
            margin-top: 1rem; }

  /* Options bar */
  .optsbar { display: flex; flex-wrap: wrap; gap: .5rem .9rem; align-items: center;
             padding: .65rem 1.15rem; background: var(--panel);
             border-bottom: 1px solid var(--border); }
  .optsbar label { display: flex; align-items: center; gap: .4rem;
                   font-size: .8rem; color: var(--muted); font-family: var(--ui);
                   font-weight: 500; }
  .optsbar select, .optsbar input[type=number] {
      background: var(--panel2); border: 1px solid var(--border);
      border-radius: 8px; padding: .35rem .5rem; transition: border-color .15s; }
  .optsbar select:focus, .optsbar input:focus {
      outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--ring); }
  .optsbar input[type=number] { width: 4rem; }
  .optsbar input[type=checkbox] { accent-color: var(--accent);
      width: 1.05rem; height: 1.05rem; }
  .optsbar .grow { flex: 1; }
  .btn { border: 1px solid var(--border); background: var(--panel2);
         border-radius: 10px; padding: .48rem .9rem; cursor: pointer;
         font-weight: 500; font-size: .88rem;
         transition: border-color .15s, background .15s, transform .1s; }
  .btn:hover { border-color: var(--accent); }
  .btn:active { transform: translateY(1px); }
  .btn.primary { background: var(--accent); border-color: var(--accent);
                 color: #fff; font-weight: 600; box-shadow: 0 2px 8px var(--ring); }
  .btn.primary:hover { background: var(--accent2); border-color: var(--accent2); }

  /* Editor */
  .editor { flex: 1; display: grid; grid-template-columns: 20rem 1fr;
            min-height: 0; }
  aside { border-right: 1px solid var(--border); background: var(--panel);
          display: flex; flex-direction: column; min-height: 0; }
  .listhead { display: flex; align-items: center; justify-content: space-between;
              padding: .7rem .85rem; border-bottom: 1px solid var(--border);
              font-size: .8rem; color: var(--muted); font-family: var(--ui);
              font-weight: 500; text-transform: uppercase; letter-spacing: .03em; }
  #songList { list-style: none; margin: 0; padding: .45rem; overflow-y: auto;
              flex: 1; }
  #songList li { display: flex; align-items: center; gap: .4rem;
                 padding: .45rem .5rem; border-radius: 10px; cursor: pointer;
                 border: 1px solid transparent; font-family: var(--ui);
                 font-size: .88rem; transition: background .12s; }
  #songList li:hover { background: var(--panel2); }
  #songList li.sel { background: var(--ring); border-color: var(--accent); }
  #songList li.off .t { opacity: .4; text-decoration: line-through; }
  #songList input[type=checkbox] { accent-color: var(--accent);
      width: 1.05rem; height: 1.05rem; flex: none; }
  #songList .t { flex: 1; white-space: nowrap; overflow: hidden;
                 text-overflow: ellipsis; }
  #songList .mini { background: none; border: 0; color: var(--muted);
                    cursor: pointer; padding: .15rem .3rem; border-radius: 6px;
                    font-size: .9rem; line-height: 1; }
  #songList .mini:hover { color: var(--accent); background: var(--panel); }

  #songForm { display: flex; flex-direction: column; gap: .8rem;
              padding: 1.1rem 1.3rem; overflow-y: auto; min-height: 0; }
  #songForm label { display: flex; flex-direction: column; gap: .3rem;
                    font-size: .78rem; color: var(--muted); font-family: var(--ui);
                    font-weight: 500; }
  #songForm input, #songForm textarea {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 9px; padding: .55rem .65rem; resize: vertical;
      font-family: var(--edit); font-size: .92rem;
      transition: border-color .15s, box-shadow .15s; }
  #songForm input:focus, #songForm textarea:focus {
      outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--ring); }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; }
  #fBody { min-height: 46vh; line-height: 1.55; }
  .hint { color: var(--muted); font-size: .8rem; margin: 0; font-family: var(--serif);
          line-height: 1.5; }
  .hint b { color: var(--text); }

  /* Upozorenje + toast + busy */
  #warnBar { margin: 0; padding: .65rem 1.15rem; font-size: .86rem;
             background: color-mix(in srgb, var(--accent) 16%, var(--panel));
             border-bottom: 1px solid var(--accent); color: var(--text);
             font-family: var(--serif); }
  #toast { position: fixed; left: 50%; bottom: 1.3rem; transform: translateX(-50%);
           background: var(--panel); border: 1px solid var(--border);
           border-left: 4px solid var(--ok); border-radius: 12px;
           padding: .7rem 1.1rem; max-width: 90%; box-shadow: var(--shadow);
           font-family: var(--ui); font-size: .9rem;
           opacity: 0; pointer-events: none; transition: opacity .2s, transform .2s;
           z-index: 30; }
  #toast.show { opacity: 1; }
  #toast.err { border-left-color: var(--err); }
  #busy { position: fixed; inset: 0; background: rgba(20,20,19,.5);
          backdrop-filter: blur(2px);
          display: flex; align-items: center; justify-content: center; z-index: 20; }
  #busy .box { background: var(--panel); border: 1px solid var(--border);
               border-radius: 14px; padding: 1.1rem 1.7rem; box-shadow: var(--shadow);
               display: flex; gap: .8rem; align-items: center;
               font-family: var(--ui); font-weight: 500; }
  .spin { width: 1.15rem; height: 1.15rem; border: 3px solid var(--border);
          border-top-color: var(--accent); border-radius: 50%;
          animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  @media (max-width: 760px) {
    header .sub { display: none; }
    .editor { grid-template-columns: 1fr; grid-template-rows: 32vh 1fr; }
    aside { border-right: 0; border-bottom: 1px solid var(--border); }
    .row2 { grid-template-columns: 1fr; }
    #fBody { min-height: 38vh; }
    .upcard { padding: 1.8rem 1.4rem; }
  }
</style>
</head>
<body>
<header>
  <div class="logo">♪</div>
  <div>
    <h1>Pjesmarica konverter</h1>
    <span class="sub">Ubaci PDF → uredi → novi PDF ili Word</span>
  </div>
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
        <div class="drop-ico">↑</div>
        <div class="big"><strong>Klikni</strong> ili dovuci PDF ovdje</div>
        <div class="small">originalna pjesmarica — jedna pjesma po stranici</div>
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
      <label title="Papa Band logo kao blijedi vodeni žig iza teksta">
        <input type="checkbox" id="optWm"> Logo u pozadini</label>
      <label id="lblWmLevel" class="hidden">Žig
        <select id="optWmLevel">
          <option value="vrlo_blijedo">vrlo blijedo</option>
          <option value="srednje">srednje</option>
        </select>
      </label>
      <span class="grow"></span>
      <button class="btn primary" id="btnPdf">⬇ PDF</button>
      <button class="btn primary" id="btnDocx">⬇ Word</button>
    </div>
    <div id="warnBar" class="hidden"></div>
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
    const wb = $("warnBar");
    wb.textContent = (data.warnings || []).join(" ");
    wb.classList.toggle("hidden", !(data.warnings || []).length);
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

/* Vodeni žig: pokaži izbor jačine samo kad je uključen */
function applyWm() { $("lblWmLevel").classList.toggle("hidden", !$("optWm").checked); }
$("optWm").addEventListener("change", applyWm);
applyWm();

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
    watermark: $("optWm").checked,
    watermark_level: $("optWmLevel").value,
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
    a.download = state.filename + "_print" + (kind === "pdf" ? ".pdf" : ".docx");
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
