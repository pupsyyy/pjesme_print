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
- **Tamna tema** (zadano) sa prekidačem na svijetlu

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
