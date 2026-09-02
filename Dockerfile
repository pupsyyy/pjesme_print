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

COPY pdf_to_word.py pdf_writer.py watermark.py app.py VERSION ./
COPY assets/ ./assets/
COPY static/ ./static/

EXPOSE 8000

# Docker sam prati je li aplikacija živa (docker compose ps -> healthy)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)"

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "300", "app:app"]
