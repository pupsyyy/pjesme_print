# Baza pjesama — dnevnik napretka (za nastavak rada ako sesija nestane)

Zadnje ažuriranje: 2026-10-15 (sesija u tijeku)

## Zahtjev korisnika
Upload PDF-ova → baza pjesama (SQLite): tekst, metapodaci (naslov, podnaslov/izvođač,
tonalitet, broj u pjesmarici…), akordi s TOČNIM mjestom (indeks slova iznad kojeg akord
počinje). API da druge aplikacije čitaju pjesme. Bez duplikata; ako su pjesme ≥80 %
slične → pitati korisnika koju zadržati (u aplikaciji: zadrži postojeću / zamijeni /
zadrži obje). Dokaz točnosti: iz baze ponovno iscrtati izvorne PDF-ove i usporediti.
Kasnije: pjesmarice iz baze.

## Izvorni PDF-ovi (analiza gotova)
Primjeri: 5 korisnikovih "Aleluja …" PDF-ova (novija verzija aplikacije, "v2") +
"klajanje (08-10-2025).pdf" s Google Drivea (fileId 1q98kluYGzQT30AVDAR0SRt02ZcTqnvfu,
starija verzija "v1"). Proizvođač: dart_pdf. PDF-ovi NISU u repou (privatni);
lokalno su bili u scratchpadu.

- Font: **Open Sans 1.10** (širine svih 121 korištenih znakova = floor(1.10 metrika);
  v3.003 se NE poklapa). Bundlan u `assets/fonts/` (Apache 2.0).
- A4, lijeva/gornja margina 28.3465 pt (1 cm). Naslov Bold 21.6, "Key: X" Regular 13.8
  poravnat desno do 566.93, podnaslov Regular 12 odmah ispod naslova, labele
  (Pjesmarica, Verse 1, Chorus…) Bold 13.8, akordi Bold 12, tekst Regular 12.
- Vertikalno v2: visina retka = size·2789/2048 (12pt → 16.342); akord→tekst baseline
  13.342 (= redak − 3); razmak između blokova 18 pt; baseline = vrh + size·2189/2048.
  v1: redak 16.0, akord→tekst 13.0, labela visina 19.0/ascent 14.335, redak baseline 12.411,
  podnaslov visina 16.757; ima brojeve stranica (Helvetica 12 dolje desno).
- Vodoravno: akord x = lijevo + širina(tekst[:indeks]) (točno, Open Sans 1.10).
  Ako bi akord udario u prethodni: v2 → min razmak (2.754–4.254 pt izmjereno, uzet 3.117 =
  razmak): ubacuju se crtice u tekst dok ne stane — usred riječi vidljive ("alelu-ja"),
  na kraju retka nevidljive (samo pomak 3.861 pt).
  v1 → razmak min 8.0, tekst se nikad ne mijenja (samo se akord pomakne).
- Posebni redci: (a) bold redak akorda bez teksta = "monospace" stupci (x = lijevo +
  stupac·7.2); (b) bold mono redak teksta odmah ispod (Isus Krist "Aa le luu ja") =
  akordi-iznad-teksta utipkani u mono stupcima; (c) Regular redak samih akorda
  ("D/FIS*   G    h    A4") + redak teksta = akordi utipkani kao običan tekst.
- v1 ima i stranice u 2 stupca (klajanje str. 4) → parser dijeli po stupcima.

## Arhitektura (u izradi)
- `opensans.py` — metrike/registracija fonta ✅
- `chord_extract.py` — parser PDF → pjesme s akordima (indeksi) ✅ (`python3 chord_extract.py x.pdf` ispiše ChordPro)
- `chord_render.py` — iscrtavanje pjesme u izvornom izgledu (v2/v1 profil, stupci) ✅
- `chord_compare.py` — usporedba izvornog i rekreiranog PDF-a (glif po glif) ✅
- `tools/roundtrip.py izvor.pdf ... [--png DIR]` — dokaz: PDF→baza(JSON)→PDF→usporedba ✅
- `songdb.py` — SQLite (songs, pending, imports), normalizacija, dedup ≥0.80, ChordPro ✅
  (fingerprint = semantički sadržaj bez izgleda; sličnost = difflib nad normaliziranim riječima teksta)
- `songdb_web.py` — Blueprint: /baza (UI), /db/* (pisanje, opcionalno DB_PASSWORD),
  /api/v1/* (čitanje za druge aplikacije, opcionalno API_TOKEN, CORS)
- docker-compose: volumen ./data:/app/data, env DB_PATH

## Format retka u bazi
`{"text": "Bože svet si aleluja,", "chords": [[5,"G"],[16,"C"]]}`;
samo akordi: `{"text": "", "chords": [[0,"C"],[6,"a"]]}` (stupci);
`fmt` za vjerni ispis: `{"text":"mono"}`, `{"chord_text":" D/FIS*   G    h    A4"}`, `{"text":"bold"}`.
Sekcija: `{"label": "Verse 1"|null, "lines": [...], "attached"?: true, "space"?: n, "col"?: i}`.
Akord iza kraja teksta: tekst dobije razmake na kraju (ako ih je izvornik imao).
Pjesma: title, key, subtitle, meta [{label, values}], sections, layout {profile}, source.

## Status
- [x] analiza formata, font, pravila rasporeda
- [x] parser chord_extract.py radi na svih 5 + klajanje (crtice uklonjene, v1 pomaknuti akordi → slovo ispod)
- [x] renderer + usporedba: **17/17 pjesama identično** (5 korisnikovih v2 + 12 iz klajanje v1,
      3744 glifa, max odstupanje 0.0025 pt; pikseli se razlikuju samo u zaglađivanju rubova)
- [x] baza (testirano: uvoz 17, ponovni uvoz → duplikat, izmjena akorda → konflikt 100 %, replace)
- [ ] UI/API (songdb_web.py Blueprint)  - [ ] testovi/docs/PR

## Grana / PR
Grana `claude/app-migration-hostinger-vps-q2z32b`, nova od main nakon PR #15.
