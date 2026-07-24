# Pjesme Print — Handoff dokument

> Sažetak stanja projekta za nastavak rada u novom razgovoru.
> Zadnje ažuriranje: 2026-07-24.

## 1. Što je projekt

Web aplikacija koja od **PDF pjesmarice** radi uredljivu verziju i izvozi
**novi PDF ili Word (.docx)**. Ulazni PDF ima **jednu pjesmu po stranici**
(export iz aplikacije tipa OnSong / SongSelect: naslov, tonalitet, broj u
pjesmarici, sekcije Verse/Chorus/Bridge…). Aplikacija to parsira, korisnik
uredi u pregledniku i preuzme čist dokument za tisak.

- **Repo:** `pupsyyy/pjesme_print`
- **Aktivna grana:** `claude/zealous-bohr-YwV9B` (na njoj je sav aktualni kod;
  `main` je iza nje)
- **Jezik sučelja i koda:** hrvatski (komentari, poruke, UI)
- **Vlasnik:** jivancic.st@gmail.com

## 2. Arhitektura / datoteke

| Datoteka | Uloga |
|----------|-------|
| `app.py` | **Flask** web aplikacija: rute `/`, `/parse`, `/export/docx`, `/export/pdf`, `/health`. Sadrži i cijeli frontend (jedna HTML/JS stranica u varijabli `PAGE`, tamna tema). |
| `pdf_to_word.py` | Parsiranje PDF-a (`parse_pdf`, `parse_page`) **i** izvoz u Word (`build_docx`, `write_song`, `setup_document`). Sadrži `SECTION_LABELS` regex. Radi i kao CLI: `python pdf_to_word.py ulaz.pdf izlaz.docx`. |
| `pdf_writer.py` | Izvoz u PDF preko **reportlab** (`build_pdf(songs, style)`). Ugrađuje TTF fontove (Liberation/DejaVu) radi ispravnih **hrvatskih dijakritika**. |
| `requirements.txt` | flask, gunicorn, pdfplumber, python-docx, reportlab |
| `Dockerfile`, `docker-compose.yml` | Docker deploy (gunicorn, vezan na `127.0.0.1:8010:8000`). |
| `deploy/` | `pjesme.service` (systemd), `nginx-pjesme.conf`, `Caddyfile-primjer`. |
| `DEPLOY.md` | Detaljne upute za Hostinger VPS (Docker ili venv+systemd+nginx, Caddy za putanju domene). |
| `README.md` | Kratki opis + pokretanje. |

## 3. Model podataka (pjesma)

`parse_pdf` vraća listu pjesama; svaka je dict:

```python
{
    "title": str,               # naslov (prvi, najveći red)
    "key": str,                 # tonalitet, npr. "Key: A (Ab)\nCapo 1" (desno gore)
    "subtitle": str | None,     # red ispod naslova (npr. "Papa Band")
    "pjesmarica": list[str],    # brojevi ispod labele "Pjesmarica"
    "notes": list[str],         # tekst prije prve sekcije (upute za sviranje…)
    "sections": list[[label, list[str]]],  # label = "Verse"/"Chorus"/… ili None
}
```

Frontend ovo pretvara u uredljiva polja (`song_to_editable`) i natrag
(`editable_to_song` / `body_to_parts` u `app.py`). Sekcije se u editoru
prikazuju kao običan tekst gdje labela stoji u svom retku.

## 4. Opcije formatiranja (izvoz)

Šalju se iz frontenda, validiraju u `parse_options` (`app.py`):

- `font` — jedan od `FONTS = (Calibri, Arial, Verdana, Cambria, Times New Roman, Georgia)`
- `title_size` (12–48, default 26)
- `body_size` (7–20, default 11)
- `margin_cm` (0.5–4.0, default 2.5)
- `page_break` (bool, default True — svaka pjesma na novoj stranici)

U PDF-u se Word-font mapira na obitelj (`_FAMILY_BY_FONT` u `pdf_writer.py`):
Calibri/Arial/Verdana → sans (Liberation Sans / DejaVu Sans),
Cambria/Times/Georgia → serif (Liberation Serif / DejaVu Serif).

## 5. Trenutni izgled izlaza

**Portret A4, jedna pjesma po stranici.** Raspored:
- Naslov (bold, velik) lijevo + tonalitet desno (desni tab / tablica)
- Podnaslov ispod naslova
- "Pjesmarica" (bold) + brojevi
- Napomene
- Sekcije: **bold labela** (Verse/Chorus/…) pa stihovi normalno

## 6. Pokretanje

```bash
pip install -r requirements.txt
python app.py                      # http://localhost:8000
# ili produkcijski:
gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 300 app:app
```

Deploy na Hostinger VPS → vidi `DEPLOY.md` (Docker `docker compose up -d --build`,
app na `127.0.0.1:8010`, izvana kroz Caddy/nginx na putanji domene).

## 7. Ključni detalji implementacije

- **Parsiranje pozicijom:** `parse_page` grupira riječi po `y` koordinati;
  desna polovina stranice (`x > 0.55*width`) tretira se kao zona tonaliteta (Key/Capo).
- **Hrvatski znakovi u PDF-u:** obavezni ugrađeni TTF fontovi. Standardni
  reportlab fontovi (Times/Helvetica) **ne** podržavaju š/č/ć/ž/đ →
  fallback samo ako TTF ne postoji. Docker slika instalira `fonts-liberation`.
- **Upload limit:** `MAX_UPLOAD_MB` (env, default 50), `MAX_SONGS = 500`.
- **`SECTION_LABELS` regex** prepoznaje: Chorus, Verse N, Bridge, Intro, Outro,
  Pre-Chorus, Tag, V1/V2…, C1/C2…, B…

## 8. Povijest / alternativni pravac (VAŽNO za kontekst)

Ranije je postojao **drugi dizajn izlaza** (bio na sada obrisanoj grani
`streamlit`) koji NIJE u trenutnom kodu, ali ga je korisnik želio:

- **A4 landscape, 2–3 stupca** (default 3), gusto za tisak s minimalnim marginama
- **Bez naslova i labela sekcija** — samo tekst; **refren (Chorus) uvučen + bold/italic**
- Pjesme odvojene **linijom razdjelnika** (`____`)
- **Automatsko uklanjanje redova s akordima** (hrv./njem. notacija: D, A, Fis,
  H, Cis, Es, Am, G/B, Dsus4, Am7…) — red je "akordni" ako su svi tokeni akordi
- Bio deployan kao **Streamlit** app (`share.streamlit.io`, grana `streamlit`)

Ako korisnik traži "stupce / landscape / bez naslova / makni akorde", misli na
OVAJ format. Trenutni Flask editor to (još) ne radi — to je moguć sljedeći korak:
dodati u opcije izbor "način izgleda" (editor-portret vs. gusti landscape-stupci).

## 9. Otvorena pitanja / mogući sljedeći koraci

- [ ] Objediniti dva formata izlaza (portret-editor + landscape-stupci) kao opciju
- [ ] Vratiti detekciju/uklanjanje akorda ako se koristi landscape format
- [ ] Deploy: korisnik je razmatrao Streamlit Cloud i Hostinger VPS; VPS upute su gotove
- [ ] Mobitel: korisnik želi pristup s mobitela (web app rješava; native .apk je odbačen kao nepraktičan)

## 10. Konvencije rada

- Sve promjene idu na granu `claude/zealous-bohr-YwV9B`, uz commit + push.
- Generirani `.docx`/`.pdf` i `__pycache__` su u `.gitignore`.
- Commit poruke na hrvatskom, opisne.
