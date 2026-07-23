#!/usr/bin/env python3
"""
Web sučelje za pdf_to_word.py — upload PDF pjesmarice, download Word dokumenta.

Pokretanje (razvoj):
    python app.py                # http://localhost:8000

Produkcija (gunicorn):
    gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 300 app:app
"""

import io
import os
import tempfile
from pathlib import Path

from flask import Flask, render_template_string, request, send_file
from werkzeug.utils import secure_filename

from pdf_to_word import convert_pdf_to_docx

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

PAGE = """<!doctype html>
<html lang="hr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pjesme Print — PDF u Word</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         background: #f4f4f5; margin: 0; display: flex; min-height: 100vh;
         align-items: center; justify-content: center; }
  .card { background: #fff; border-radius: 12px; padding: 2.5rem;
          box-shadow: 0 2px 12px rgba(0,0,0,.08); max-width: 30rem; width: 90%; }
  h1 { margin: 0 0 .25rem; font-size: 1.5rem; }
  p.sub { margin: 0 0 1.5rem; color: #555; }
  input[type=file] { display: block; width: 100%; padding: .75rem;
                     border: 2px dashed #bbb; border-radius: 8px; margin-bottom: 1rem;
                     box-sizing: border-box; }
  button { background: #1d4ed8; color: #fff; border: 0; border-radius: 8px;
           padding: .75rem 1.5rem; font-size: 1rem; cursor: pointer; width: 100%; }
  button:hover { background: #1e40af; }
  .error { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca;
           border-radius: 8px; padding: .75rem 1rem; margin-bottom: 1rem; }
  small { color: #777; display: block; margin-top: 1rem; text-align: center; }
</style>
</head>
<body>
<div class="card">
  <h1>Pjesme Print</h1>
  <p class="sub">Konverzija PDF pjesmarice u Word dokument.<br>
     Svaka stranica PDF-a = jedna pjesma.</p>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form action="/convert" method="post" enctype="multipart/form-data">
    <input type="file" name="pdf" accept=".pdf,application/pdf" required>
    <button type="submit">Konvertiraj u Word</button>
  </form>
  <small>Maksimalna veličina datoteke: {{ max_mb }} MB</small>
</div>
</body>
</html>"""


def _page(error=None, status=200):
    return render_template_string(PAGE, error=error, max_mb=MAX_UPLOAD_MB), status


@app.get("/")
def index():
    return _page()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.errorhandler(413)
def too_large(_e):
    return _page(f"Datoteka je prevelika (maksimum {MAX_UPLOAD_MB} MB).", 413)


@app.post("/convert")
def convert():
    file = request.files.get("pdf")
    if file is None or not file.filename:
        return _page("Odaberi PDF datoteku.", 400)
    if not file.filename.lower().endswith(".pdf"):
        return _page("Datoteka mora biti PDF (.pdf).", 400)

    download_stem = Path(secure_filename(file.filename)).stem or "pjesme"

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "ulaz.pdf"
        docx_path = Path(tmpdir) / "izlaz.docx"
        file.save(pdf_path)

        try:
            convert_pdf_to_docx(str(pdf_path), str(docx_path))
        except Exception as exc:
            app.logger.exception("Konverzija nije uspjela")
            return _page(f"Konverzija nije uspjela: {exc}", 500)

        if not docx_path.exists():
            return _page("U PDF-u nije pronađena nijedna pjesma.", 422)

        # Pročitaj u memoriju prije nego se privremeni direktorij obriše
        data = io.BytesIO(docx_path.read_bytes())

    return send_file(
        data,
        as_attachment=True,
        download_name=f"{download_stem}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
