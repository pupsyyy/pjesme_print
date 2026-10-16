FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# TTF fontovi za PDF izvoz (hrvatske dijakritike; isti izbor kao original)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       fonts-liberation fonts-freefont-ttf fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# svi Python moduli iz korijena repoa (testovi i alati su u .dockerignore) —
# novi modul se tako ne može zaboraviti na popisu
COPY *.py VERSION ./
COPY assets/ ./assets/
COPY static/ ./static/

# ── Neobavezno: prepoznavanje nota iz PDF-a/slike (Audiveris -> MusicXML) ─────
# Uključi s WITH_OMR=1 u .env (docker-compose.yml ga prosljeđuje). Slika je
# tada ~250 MB veća, a jedno prepoznavanje treba ~1 GB RAM-a. Bez toga
# aplikacija radi isto, samo bez gumba „Prepoznaj note".
ARG WITH_OMR=0
ARG AUDIVERIS_VERSION=5.11.0
ARG TESSDATA_VERSION=4.1.0
RUN if [ "$WITH_OMR" = "1" ]; then \
      set -eux; \
      [ "$(dpkg --print-architecture)" = "amd64" ] || { echo "Audiveris: samo amd64"; exit 1; }; \
      apt-get update; \
      apt-get install -y --no-install-recommends fontconfig; \
      rm -rf /var/lib/apt/lists/*; \
      python -c "import urllib.request as u; u.urlretrieve('https://github.com/Audiveris/audiveris/releases/download/${AUDIVERIS_VERSION}/Audiveris-${AUDIVERIS_VERSION}-ubuntu24.04-x86_64.deb', '/tmp/audiveris.deb')"; \
      dpkg-deb -x /tmp/audiveris.deb /; \
      rm /tmp/audiveris.deb; \
      mkdir -p /opt/tessdata; \
      for l in hrv eng; do \
        python -c "import sys, urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/tesseract-ocr/tessdata/${TESSDATA_VERSION}/' + sys.argv[1] + '.traineddata', '/opt/tessdata/' + sys.argv[1] + '.traineddata')" "$l"; \
      done; \
      test -x /opt/audiveris/bin/Audiveris; \
    fi
# jezik teksta ispod nota (Tesseract); GDK_SCALE: Audiveris bez GTK-a
ENV OMR_TESSDATA=/opt/tessdata \
    GDK_SCALE=1

# Baza pjesama (SQLite) — u docker-compose.yml montirana kao ./data
ENV DB_PATH=/app/data/pjesme.db
RUN mkdir -p /app/data

EXPOSE 8000

# Docker sam prati je li aplikacija živa (docker compose ps -> healthy)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)"

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "300", "app:app"]
