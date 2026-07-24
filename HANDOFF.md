# Pjesme Print — Handoff dokument

> Sažetak stanja projekta za nastavak rada u novom razgovoru.
> Zadnje ažuriranje: 2026-07-24.

## ✅ ODLUKA (2026-07-24): originalna verzija A je referentni format

Dogovoreno s korisnikom: **Verzija A (Streamlit "gusti tisak") je originalni,
referentni format i izvor istine za IZGLED izlaza.** Zadatak dalje je
**prilagoditi Flask verziju (B) da proizvodi ISTI format kao A** (landscape,
2–3 stupca, bez naslova/labela, refren uvučen bold-italic, razdjelnici, akordi
uklonjeni). Flask ostaje kao sučelje/editor + VPS deploy, ali njegov PDF/Word
izlaz mora izgledati kao A.

Zato: kad radiš izgled — **A je spec**. Kad radiš sučelje/hosting — gledaj B.

### Gdje je koja verzija (VAŽNO — grana se prepisuje force-pushom)

Dva paralelna razgovora pushaju na `claude/zealous-bohr-YwV9B`, pa se ta grana
naizmjenično prepisuje. Zato su verzije spremljene na **stabilne grane**:

| | Verzija A — Streamlit "gusti tisak" (SPEC) | Verzija B — Flask editor |
|---|---|---|
| **Stabilna grana** | `claude/zealous-bohr-YwV9B` (trenutno) | **`flask-editor`** (sačuvana ovdje!) |
| **Sučelje** | Streamlit (upload → download) | Flask, editor u pregledniku, tamna tema |
| **Izgled** | A4 **landscape, 2–3 stupca**, bez naslova/labela; refren uvučen bold-italic; razdjelnik `___` | A4 **portret**, naslov+Key, bold labele, 1 pjesma/stranica |
| **Akordi** | **automatski uklonjeni** | zadržani |
| **Deploy** | Streamlit Cloud | Hostinger VPS (Docker/nginx/Caddy) |
| **Ključne datoteke** | `app.py` (streamlit), `pdf_to_word.py`, `packages.txt` | `app.py` (flask), `pdf_to_word.py`, `pdf_writer.py`, `DEPLOY.md`, `deploy/`, `Dockerfile` |

- Verzija A dohvat: `git checkout claude/zealous-bohr-YwV9B` (ili grana s `import streamlit` u `app.py`).
- Verzija B dohvat: `git checkout flask-editor` (`from flask import` u `app.py`).
- Ako se grana `claude/zealous-bohr-YwV9B` opet prepiše na Flask, verzija A se
  može vratiti iz git povijesti (Streamlit commitovi, npr. `Redizajn outputa:
  A4 landscape … stupca`, `Detektira i uklanja redove s akordima`).

### Konkretan sljedeći zadatak
Prenijeti logiku izgleda iz A u B:
1. U B-ov PDF izvoz (`pdf_writer.py`) i Word izvoz (`pdf_to_word.py:build_docx`)
   dodati landscape + `BalancedColumns`/stupce, izbaciti naslov/labele, uvući
   refren (bold-italic), umetnuti razdjelnike među pjesmama.
2. Dodati **uklanjanje akorda** (`is_chord_line` iz verzije A) u B-ov parser.
3. Dodati opcije u Flask UI: broj stupaca (2/3, default 3), veličina teksta
   (default 9pt), font.

---

## 1. Što je projekt

Alat koji od **PDF pjesmarice** (jedna pjesma po stranici, export iz aplikacije
tipa OnSong/SongSelect s naslovom, tonalitetom, sekcijama Verse/Chorus/Bridge)
radi čist dokument za tisak — **PDF i/ili Word (.docx)**.

- **Repo:** `pupsyyy/pjesme_print`
- **Grana:** `claude/zealous-bohr-YwV9B`
- **Jezik:** hrvatski (UI, komentari, commit poruke)
- **Vlasnik:** jivancic.st@gmail.com

## 2. Verzija A — Streamlit "gusti tisak" (trenutno na disku)

### Datoteke
- `app.py` — Streamlit sučelje: upload PDF-a, opcije (slider veličine teksta
  7–14pt, radio 2/3 stupca, dropdown font), gumbi za download `.docx` i `.pdf`.
- `pdf_to_word.py` — parsiranje **i** izvoz. Glavne funkcije:
  - `load_songs(pdf_path)` → lista pjesama (svaka = lista blokova `(tip, linije)`,
    tip ∈ `"verse"`/`"chorus"`)
  - `setup_document(font_size, n_cols, font)` → Word `Document`
  - `write_song(doc, blocks, first, font_size, font)`
  - `convert_pdf_to_pdf(songs, out_path, font_size, n_cols, font)` — reportlab
  - `FONT_CHOICES`, `DEFAULT_FONT`, `DEFAULT_FONT_SIZE=9`, `DEFAULT_N_COLS=3`
  - `is_chord_line(line)` — detekcija reda s akordima
- `packages.txt` — `fonts-liberation`, `fonts-freefont-ttf` (za Streamlit Cloud)
- `requirements.txt` — streamlit, pdfplumber, python-docx, reportlab

### Ključni detalji
- **Izgled:** A4 landscape, `n_cols` stupaca (`BalancedColumns`), margine 0.5cm,
  razdjelnik `"_"*35` između pjesama. Verse = normalno, Chorus/Bridge = uvučeno
  (18pt), bold+italic.
- **Uklanjanje akorda:** `is_chord_line` — red je akordni ako su **svi** tokeni
  validni akordi (hrv./njem. notacija: `[CDEFGAHB]` + is/es/#/b + m/maj/dim/sus…
  + broj + slash-akord), i svaki token ≤ 7 znakova. Takvi se redovi preskaču.
- **Hrvatski znakovi u PDF-u:** obavezni TTF fontovi. `_find_font_file()`
  dinamički traži fajl po više putanja + `find` fallback (jer se Streamlit Cloud
  putanje razlikuju od lokalnih). Fontovi: Liberation Serif/Sans, FreeSerif/Sans.
- **Parsiranje** (`parse_page`): preskače naslov, podnaslov, "Pjesmarica" blok i
  napomene; ostatak dijeli na sekcije preko `ANY_SECTION`/`CHORUS_TYPES` regexa
  (prepoznaje i `Chorus 1`, `C1`, `Verse 2`, `V2`…).
- **CLI:** `python pdf_to_word.py ulaz.pdf` → `ulaz_out.docx` + `ulaz_out.pdf`;
  `python pdf_to_word.py ulaz.pdf izlaz.pdf` → samo taj format.

### Deploy (Streamlit Cloud)
share.streamlit.io → New app → repo `pupsyyy/pjesme_print`, grana
`claude/zealous-bohr-YwV9B` (ili posebna), main file `app.py`. Repo mora biti
javan ILI dati Streamlitu pristup. Automatski redeploy na svaki push.
(Korisnik je već imao app na `pjesme.streamlit.app`.)

## 3. Verzija B — Flask editor (bila na disku ranije danas)

> Nije trenutno na disku, ali je kompletna i na remoteu je bila do prije par
> commitova. Vraća se s `git checkout 10bab02 -- .` ili s odgovarajuće grane/PR-a
> (PR #1–#6, poruke "Novo sučelje…", "…hostinger-vps…").

- `app.py` (Flask): rute `/`, `/parse`, `/export/docx`, `/export/pdf`, `/health`;
  cijeli frontend u varijabli `PAGE` (jedna stranica, tamna tema, editor sa
  redoslijedom ↑↓, uključi/isključi pjesmu, uredi tekst).
- `pdf_to_word.py` (druga verzija!): `parse_pdf`, `parse_page`, `build_docx`,
  `write_song`, `setup_document`, `SECTION_LABELS`. Model pjesme je **dict**:
  `{title, key, subtitle, pjesmarica, notes, sections:[[label,lines]]}`.
- `pdf_writer.py`: `build_pdf(songs, style)` (reportlab, TTF za dijakritike).
- Opcije: `FONTS=(Calibri,Arial,Verdana,Cambria,Times New Roman,Georgia)`,
  `title_size` 12–48/26, `body_size` 7–20/11, `margin_cm` 0.5–4/2.5, `page_break`.
- `DEPLOY.md` + `deploy/` (systemd `pjesme.service`, `nginx-pjesme.conf`,
  `Caddyfile-primjer`) + `Dockerfile`/`docker-compose.yml`: Hostinger VPS,
  app na `127.0.0.1:8010`, iza Caddy/nginx na putanji domene.

### Pokretanje B
```bash
pip install -r requirements.txt   # flask, gunicorn, pdfplumber, python-docx, reportlab
python app.py                     # http://localhost:8000
```

## 4. Kontekst želja korisnika (iz razgovora)

- Traži **gust format za tisak**: landscape, 2–3 stupca (default 3), male margine,
  bez naslova/labela, **akordi uklonjeni**, refren vizualno odvojen (uvučen). → to je Verzija A.
- Font i veličina teksta trebaju biti **izborni**. Default 9pt, 3 stupca.
- Želi koristiti **s mobitela** → web app (native `.apk` odbačen kao nepraktičan).
- Razmatrao i Streamlit Cloud i Hostinger VPS za hosting.

## 5. Mogući sljedeći koraci

- [ ] **Odlučiti A vs B** (ili spojiti: jedan app, opcija "način izgleda").
- [ ] Ako A: očistiti repo od Flask/deploy datoteka ili obrnuto — trenutno se
      miješaju kroz force-push.
- [ ] Dogovoriti **stabilnu granu po verziji** da se razgovori ne prepisuju.
- [ ] Deploy odabrane verzije (Streamlit Cloud za A, VPS upute gotove za B).

## 6. Konvencije rada

- Promjene na grani `claude/zealous-bohr-YwV9B`, commit + push (uz `--rebase`
  pull jer druga strana push-a na istu granu).
- `.gitignore`: `__pycache__`, `*.pyc`, (u nekim verzijama i `*.docx`/`*.pdf`).
- Commit poruke na hrvatskom, opisne.
