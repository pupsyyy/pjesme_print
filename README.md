# pjesme_print — Pjesmarica konverter

Web aplikacija za pjesmaricu: učitaš PDF (svaka stranica = jedna pjesma),
urediš pjesme u pregledniku i preuzmeš **novi PDF ili Word**.

## Mogućnosti

- **Učitavanje** PDF pjesmarice (klik ili drag & drop); prepoznaju se naslov,
  tonalitet (Key/Capo), podnaslov, blok "Pjesmarica", napomene i sekcije
  (Verse, Chorus, Bridge…)
- **Uređivanje**: tekst svake pjesme, naslov, tonalitet; redoslijed (↑↓),
  uključivanje/isključivanje pjesama kvačicom, dodavanje novih i brisanje
- **Dva izgleda izvoza**:
  - **Kompaktno** (zadano, original konverter): A4 ležeće, 2–3 balansirana
    stupca, bez naslova i tonaliteta, refren/bridge uvučen **bold+kurziv**,
    crta između pjesama — za ispis na jedan list
  - **Klasično**: A4 uspravno, naslov + tonalitet desno, boldane labele,
    stranica po pjesmi
- **Uklanjanje akorda**: retci koji sadrže samo akorde (D, Am7, Fis, G/B…)
  automatski se izostavljaju iz dokumenta (opcija, može se isključiti)
- **Opcije**: font (Liberation Serif/Sans, FreeSerif/Sans — svi s punim
  hrvatskim dijakriticima), veličina teksta, broj stupaca, margine
- **Izvoz**: PDF i Word (.docx)
- **Vodeni žig**: logo blijedo iza teksta (ugrađeni Papa Band ili vlastiti
  PNG — „Postavi logo…", bijela pozadina se automatski ukloni; pamti se u
  pregledniku)
- **Autosave**: uređivanje se sprema u preglednik — osvježavanje ili
  zatvaranje ne briše rad; „Nastavi zadnje uređivanje" na početnom zaslonu
- **Upozorenja**: već konvertirani ispis kao ulaz, sken bez teksta,
  preskočene stranice bez prepoznate pjesme
- **Mobitel**: može se dodati na početni zaslon kao aplikacija (PWA)
- **Tamna tema** (zadano) sa prekidačem na svijetlu; verzija aplikacije
  vidljiva na početnom zaslonu i na `/health`

## Pokretanje

```bash
pip install -r requirements.txt
python app.py            # http://localhost:8000
```

Produkcija: vidi **[DEPLOY.md](DEPLOY.md)** — upute za Hostinger VPS
(Docker, Caddy na putanji domene).

## Komandna linija (samo konverzija u Word)

```bash
python pdf_to_word.py ulaz.pdf izlaz.docx
python pdf_to_word.py ulaz.pdf          # sprema kao ulaz.docx
```

## Testovi

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Testovi sami generiraju PDF-ove (reportlab), pa nemaju vanjskih ovisnosti.
GitHub Actions (`.github/workflows/ci.yml`) na svaki push/PR pokreće testove
i builda Docker sliku te provjerava da se aplikacija u njoj pokreće.

## Verzija

Verzija je u datoteci `VERSION` (prikazana na početnom zaslonu i na
`/health`). Kod objave nove verzije povećaj je — tako se odmah vidi je li
deploy na serveru prošao.

## Poznata ograničenja

- **Ulaz mora biti PDF s tekstualnim slojem** (izvoz iz aplikacije s
  pjesmama). Skenovi/fotografije nemaju tekst — aplikacija to prepozna i
  javi.
- **Detekcija akorda** briše retke koji sadrže *samo* akorde. Redak koji je
  samo „A", „E" ili „H" (bez drugih riječi) time se tretira kao akord; u
  praksi rijetko, a opcija „Ukloni akorde" može se isključiti.
- **Labele sekcija**: samostalno „C" ili „B" u retku prepoznaje se kao
  labela (kao u originalnom konverteru).
- **Naslov** je prvi neprazni redak stranice (lijeva polovica). Stranica bez
  takvog retka se preskače, a aplikacija javi koliko ih je preskočeno.
