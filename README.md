# pjesme_print

Web aplikacija za uređivanje PDF pjesmarice: učitaš PDF (svaka stranica =
jedna pjesma), urediš pjesme u pregledniku i preuzmeš **novi PDF ili Word**.

## Mogućnosti

- **Učitavanje** PDF pjesmarice (klik ili drag & drop); prepoznaju se naslov,
  tonalitet (Key/Capo), podnaslov, blok "Pjesmarica", napomene i sekcije
  (Verse, Chorus, Bridge…)
- **Uređivanje**: tekst svake pjesme, naslov, tonalitet; redoslijed (↑↓),
  uključivanje/isključivanje pjesama kvačicom, dodavanje novih i brisanje
- **Opcije formatiranja**: font, veličina naslova i teksta, margine,
  pjesma na novoj stranici da/ne
- **Izvoz**: PDF (s ispravnim hrvatskim dijakriticima) ili Word (.docx)
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
