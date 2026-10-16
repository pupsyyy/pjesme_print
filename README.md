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

## Baza pjesama (`/baza`)

Gumb **🗂 Baza pjesama** u zaglavlju. Ubaciš PDF-ove (više odjednom) ili
**ZIP** s više PDF-ova i datoteka nota, i od njih nastaje baza (SQLite).
Pjesma može imati: samo tekst · tekst + akorde · tekst + note · tekst +
akorde + note (ili zasad samo note). Za svaku pjesmu je:

- **metapodaci**: naslov, podnaslov (izvođač), tonalitet (Key), blokovi
  poput „Pjesmarica" s brojevima, izvorna datoteka i stranica
- **tekst** po sekcijama (Verse 1, Chorus…)
- **akordi s točnim mjestom** — za svaki akord indeks slova iznad kojeg
  počinje (kao `[G]` u ChordPro formatu):
  `{"text": "Bože svet si aleluja,", "chords": [[5, "G"], [16, "C"]]}`

**Duplikati** — pjesme koje već imamo ne dodaju se:
- identična pjesma → preskače se;
- **druga verzija iste pjesme** (sličan naslov i kraća verzija ≥ 90 % sadržana
  u duljoj, ili tekst ≥ 95 % isti — npr. ista pjesma s drugim akordima ili
  bez jedne kitice) → preskače se, uz napomenu u izvještaju;
- ako nova verzija ima **osjetno više teksta** (dodatne kitice) ili je tekst
  **≥ 80 % sličan** pjesmi drugog naslova → aplikacija pita: usporedba jedne
  uz drugu (razlike označene) i odluka *Zadrži postojeću* / *Zamijeni novom*
  (isti id) / *Zadrži obje* — pojedinačno ili „Za sve".

**Pjesmarice iz Worda** (npr. TABOR PJESMARICA): svaka stranica s naslovom
na vrhu = pjesma; glava i podnožje stranice se preskaču; akordi su redak
iznad teksta (crveni ili samo akordi) i vežu se na slovo iznad kojeg stoje;
podnaslov u zagradi i „capo" ulaze u metapodatke, a kurziv/podebljani
retci se čuvaju. Ispis iz baze je u istom izgledu kao i sve ostale pjesme.
Provjereno na TABOR PJESMARICI 2017: 109 pjesama, svih 2 250 akorda i
7 483 riječi sačuvano.

**Točnost**: iz baze se pjesma iscrtava u izvornom izgledu aplikacije
(Open Sans 1.10, isti raspored) i uspoređuje s izvornim PDF-om glif po glif.
Na stvarnim PDF-ovima: **17/17 pjesama identično** (3 744 znaka, odstupanje
≤ 0,003 pt). Provjera bilo kojeg PDF-a:

```bash
python3 tools/roundtrip.py izvor.pdf [još.pdf ...] [--png slike/]
```

**Oznake** (Ulazna, Gospodine — nepromjenjivi dijelovi mise, Psalam,
Aleluja, Prinos, Pričest, Klanjanje, Završna, Marijanska, Korizma, Kroz
godinu, i vlastite): jedna pjesma može imati više oznaka. Kod uvoza se
oznake **automatski predlože** — prema nazivu pjesmarice (npr.
„…_Klanjanje.pdf" → Klanjanje), liturgijskom tekstu (Gospodine smiluj se,
Slava Bogu na visini, Svet… Hosana, Jaganjče Božji… smiluj se), naslovu
(Psalam, Aleluja) i ključnim riječima (klanjanje, pričest, korizma, Marija,
prinos). Prijedlog je isprekidan — klik ga potvrdi, ✕ makne. Ulazna,
Završna i Kroz godinu su liturgijski izbor pa se uglavnom dodaju ručno ili
skupno (filtriraj, „☑ Sve", „+ oznaka"). Gumb „✨ Predloži oznake" radi
prijedlog za pjesme koje ga još nisu dobile (npr. one uvezene prije oznaka).

**Note (notni zapis)** uz pjesmu — „🎼 Note → ＋ Dodaj note": PDF, slika
(PNG/JPG/WebP), MusicXML (.musicxml/.xml/.mxl), MuseScore (.mscz/.mscx) ili
MIDI, više datoteka po pjesmi (npr. Sopran, Gitara, Klavir). Spremaju se
**odvojeno** od teksta i akorda: datoteke u `data/note/` (pod nasumičnim
imenom), u bazi samo popis. Vrsta se provjerava po sadržaju datoteke; slike
i PDF se otvaraju u pregledniku, ostalo se preuzima. Brisanjem pjesme brišu
se i njezine note.

**Note iz PDF-a** — kod uvoza se stranice s notama prepoznaju same
(crtovlja kao vektorske crte, notni fontovi Sibeliusa/MuseScorea/Finalea…,
ili crtovlja na skenu/fotografiji) i spremaju se kao note — ne kao tekst:
- uz pjesmu iz **istog PDF-a** (PDF s jednom pjesmom; note odmah iza/ispred
  teksta pjesme; stranica s melodijom gore i kiticama ispod),
- uz pjesmu u bazi **istog naslova** (naslov iznad nota ili iz imena
  datoteke, npr. „Isus najljepše ime - Full Score.pdf") ili s **istim
  tekstom ispod nota**,
- uz jedinu pjesmu iz **iste mape ZIP-a**,
- inače nastaje **nova pjesma sa samo notama**; kad kasnije uvezeš PDF s
  tekstom istog naslova, ta pjesma dobije tekst (note ostaju uz nju).

Note koje već imamo (isti sadržaj) ne dodaju se ponovo. Pogrešno pridružene
note premjesti gumbom ⇄ (prazna pjesma koja je imala samo te note se briše).

**Prepoznavanje nota (neobavezno, Audiveris)** — gumb „🔍 Prepoznaj" na PDF-u
ili slici nota pročita notni zapis i spremi ga kao **MusicXML** uz istu
pjesmu (u pozadini, ≈ pola minute po stranici). Dobro radi na čistim notama
iz programa (Sibelius, MuseScore, Finale) i kvalitetnom tisku; skenovi i
fotografije trebaju ispravke u MuseScoreu. Uključuje se pri izgradnji Docker
slike (`WITH_OMR=1`, vidi DEPLOY.md). MusicXML note (prepoznate ili dodane)
se gumbom „▶ Prikaži" prikažu u pregledniku, uz **transpoziciju** ±6 polutonova.

**Snimke** — „🎧 Snimke": YouTube link po glasovima (Svi glasovi, Sopran, Alt,
Tenor, Bas, Matrica, Solo ili vlastiti naziv) ili audio s **Google diska**
(datoteka dijeljena s „Svi koji imaju link"); „▶ Slušaj" ih pušta na stranici.

**Oznake zbirki pri uvozu** — u polje ispod gumba za uvoz upiši npr.
`#pučkepjesme #velikizbor` i sve **nove** pjesme iz tog uvoza dobiju te
oznake (pjesme koje već imamo ne). Oznake zbirki (Pučke pjesme, Veliki zbor
i vlastite) imaju 👁 — klik **sakrije** te pjesme iz popisa (pamti se u
pregledniku), npr. kad uvezeš puno pjesama koje inače ne trebaš.

Iz baze: pretraga (naslov, broj, tekst, bez obzira na dijakritike), filter po oznaci,
pregled s akordima, preuzimanje PDF-a (izvorni izgled), ChordPro i JSON,
te **„Napravi pjesmaricu"** — odabrane pjesme otvore se u uredniku
(kompaktno/klasično, PDF/Word).

### API za druge aplikacije (samo čitanje)

| Zahtjev | Odgovor |
|---|---|
| `GET /api/v1` | broj pjesama, popis endpointa |
| `GET /api/v1/songs?q=&tag=&exclude=&limit=&offset=` | popis (id, naslov, podnaslov, key, brojevi, oznake, `notes_count`, `links_count`, `has_text`); `tag=-` = bez oznake; `exclude=Veliki zbor,Pučke pjesme` = bez tih oznaka |
| `GET /api/v1/tags` | oznake s brojem pjesama |
| `GET /api/v1/songs/<id>` | cijela pjesma: `meta`, `sections` (tekst + akordi), `lyrics`, `chordpro`, `sources`, `tags`, `notes`, `links` (snimke: `voice`, `url`, `kind` youtube/drive/audio/link, `embed`) |
| `GET /api/v1/notes/<id>` | datoteka nota (id iz `notes` pjesme) |
| `GET /api/v1/songs/<id>.cho` | ChordPro tekst |
| `GET /api/v1/songs/<id>.pdf` | PDF u izvornom izgledu |
| `GET /api/v1/export` | sve pjesme (JSON) — ujedno i backup |

Ako je postavljen `API_TOKEN`, šalje se `Authorization: Bearer <token>`
(ili `?token=`). CORS je dopušten. Primjer:

```bash
curl -H "Authorization: Bearer $API_TOKEN" https://print.josip.cloud/api/v1/songs?q=aleluja
```

Format pjesme: `sections[].lines[]` = `{"text", "chords": [[indeks, "akord"], ...]}`;
redak samo s akordima ima `"text": ""` (indeks = stupac). Polja `fmt`,
`layout`, `space`, `col` služe samo za vjerno iscrtavanje izvornika.

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
- **Baza pjesama** razumije PDF-ove aplikacije za pjesmarice (dart_pdf,
  Open Sans; novija i starija verzija). Najmanji razmak između akorda u
  novijoj verziji izmjeren je između 2,75 i 4,25 pt (uzet je 3,117 pt =
  razmak) — ako se ikad pojavi pjesma koja se ne rekreira identično,
  `tools/roundtrip.py` pokaže gdje.
- Kod starije verzije aplikacije akord koji nema mjesta samo se pomakne
  udesno; tada se pamti slovo **ispod** akorda (izvorno mjesto se iz PDF-a
  ne može znati) — izgled je isti.
- **Naslov** je prvi neprazni redak stranice (lijeva polovica). Stranica bez
  takvog retka se preskače, a aplikacija javi koliko ih je preskočeno.
