# pjesme_print

Konverzija PDF pjesmarice u Word dokument (`.docx`). Svaka stranica PDF-a =
jedna pjesma; skripta prepoznaje naslov, tonalitet (Key/Capo), blok
"Pjesmarica", napomene i sekcije (Verse, Chorus, Bridge…).

## Web aplikacija

Upload PDF-a kroz preglednik, download gotovog Word dokumenta.

```bash
pip install -r requirements.txt
python app.py            # http://localhost:8000
```

Produkcija: vidi **[DEPLOY.md](DEPLOY.md)** — upute za Hostinger VPS
(Docker ili systemd + nginx, s HTTPS-om).

## Komandna linija

```bash
python pdf_to_word.py ulaz.pdf izlaz.docx
python pdf_to_word.py ulaz.pdf          # sprema kao ulaz.docx
```
