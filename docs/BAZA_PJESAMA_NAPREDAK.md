# Baza pjesama — dnevnik napretka (za nastavak rada ako sesija nestane)

Zadnje ažuriranje: 2026-10-15 — značajka gotova, PR otvoren

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
- `songdb_web.py` ✅ (registriran u app.py) — Blueprint: /baza (UI), /db/* (pisanje, opcionalno DB_PASSWORD),
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
- [x] UI/API: test u pregledniku (playwright, desktop+mobitel, bez JS grešaka), lozinka/token OK
- [x] Docker: volumen ./data, .env (DB_PASSWORD, API_TOKEN); slika testirana u sandboxu (bez apt
      zbog proxyja): uvoz, perzistencija nakon restarta, PDF iz kontejnera 5/5 identičan
- [x] tests/test_songdb.py (58 testova ukupno prolazi; privatni PDF-ovi u tests/fixtures/private/ — gitignore)
- [x] README (Baza pjesama + API), DEPLOY (podaci, .env, backup), print.caddy (/api/v1 bez basic_auth), VERSION 2026.10.15
- [x] pregled koda: zaštita od lažnih stupaca, PDF bez naslova → jasna greška, nazivi datoteka bez dijakritika
- [x] 61 test prolazi; UI s lozinkom testiran u pregledniku
- [ ] PR + CI (draft PR otvoren — provjeriti CI, zatim spajanje i deploy na server)

## Deploy na server (kad se PR spoji)
```
cd /opt/pjesme_print && git pull
echo "DB_PASSWORD=..." > .env && echo "API_TOKEN=$(openssl rand -hex 24)" >> .env
docker compose up -d --build
# ako je basic_auth u /etc/caddy/print.caddy: matcher -> not path /health /api/v1*
```

## Ideje za dalje
- uređivanje pjesme u bazi (tekst/akordi) iz /baza
- transponiranje akorda
- pjesmarice kao spremljeni popisi pjesama u bazi

## Grana / PR
Grana `claude/app-migration-hostinger-vps-q2z32b`, nova od main nakon PR #15.

---
# Faza 2 — uvoz pjesmarica iz Worda (TABOR PJESMARICA 2017)

PR #16 (baza) spojen u main. Nova grana od main-a.

Izvor: Google Drive "TABOR PJESMARICA 2017.pdf" (fileId 1rhycukB6t1ut1qY3kdt4aHIsLF1eMjLV),
110 str., Acrobat PDFMaker for Word, font Maiandra GD. NIJE u repou.
- str. 1 naslovnica; str. 2–110 = po jedna pjesma (109), str. 31 i 32 isti naslov
  "HVALOSPJEV LJUBAVI" ali različite pjesme (podnaslov "(KEFA)" / "(Frama Široki Brijeg)")
- glava/podnožje: slike + crte (rect) na y≈64 i y≈770 → sadržaj između
- naslov: MaiandraGD-DemiBold 16 crni, centriran, podcrtan (rect ispod, y≈130); str.16 Arial-Bold
- ispod naslova ponekad DemiBold 12/14: "(KEFA)" → podnaslov; "capo 3 fret" (+ superskript "rd" 7pt) → meta Capo
- akordi: CRVENI (1,0,0), DemiBold/Regular 15, redak s pravim razmacima (Word), centriran
- tekst: Regular 16 crni (ponekad Italic ili DemiBold = istaknuto), centriran; redci ~19.2 pt
- prazan odlomak (~+19 pt) = nova sekcija; nema labela Verse/Chorus
- tokeni poput "DG", "AHE", "FGa" = više akorda na jednom slogu (čuvaju se kako su napisani)
- sidrenje: VIZUALNO (x akorda → slovo ispod), jer su redci centrirani neovisno

Pravilo duplikata (korisnik 2026-10-16): "ako već imamo pjesmu ne dodajemo je, samo one koje nemamo"
→ tekst ≥ 95 % isti = već imamo (preskoči tiho), 80–95 % = pitaj, < 80 % = dodaj.

## Status faze 2
- [x] songbook_word.py: Tabor 109/109 pjesama, 2250/2250 akorda i 7483/7483 riječi sačuvano
      (provjera: svaki crveni token i svaka riječ sa stranice je u pjesmi), 5.7 s za 110 str.
- [x] sidrenje: najbliži početak SLOVA (ne interpunkcije), izjednačenje → lijevo
      (kalibrirano na str. 2, 3, 16; vizualna usporedba str. 2, 3, 16, 31, 42, 85 OK)
- [x] kurziv iz Worda = nagib u matrici (m[2] ≈ 0.33·size); čuva se u fmt. NE označava se "Refren":
      u Taboru je kurziv nekad kitica (npr. "Jona": kurziv = "Tu je Kralj kraljeva" = Verse)
- [x] renderer: podebljani/kurzivni redci s akordima (OpenSans-Italic 1.10 dodan), najviše 1 vidljiva crtica
- [x] pravilo duplikata: ista pjesma (naslov ≥0.8 + sadržanost ≥0.9, ili tekst ≥0.95) → ne dodaje se;
      dulja verzija (>15 % više riječi) → pita; tekst ≥0.8 drugi naslov → pita. Skupna odluka /db/pending/all.
      Simulacija: baza 17 + Tabor → dodano 105, preskočeno 3 (Dubine, Gospode trebam te, Sveto Tijelo),
      pita 1 (Jona, Tabor ima više kitica). Migracija: pending.reason (ALTER TABLE).
- [x] testovi Word formata (sintetska pjesmarica) — 67 prolazi
- [ ] PR

---
# Faza 3 — oznake (tagovi) pjesama (zahtjev 2026-10-16)
Korisnik: oznake npr. Klanjanje, Ulazna, Završna, Pričest, Gospodine (nepromjenjivi dio mise),
Psalam, Korizma, Kroz godinu; pjesma može imati više oznaka; "možeš li sam prepoznati".
Plan: tablice tags + song_tags(source auto|user); songtags.py pravila:
 - izvor (naziv PDF-a: klanjanje/klajanje, pričest, kroz godinu, korizma...) → jako
 - naslov: psalam, Gospodine smiluj se/Slava/Svet/Jaganjče/Agnus Dei, Aleluja → jako
 - ključne riječi u tekstu (Klanjanje, Pričest, Korizma, Marijanska, Prinos) → prijedlog
 - Ulazna/Završna/Kroz godinu → samo iz naziva izvora ili ručno
Auto oznake = prijedlog (isprekidani rub), klik = potvrdi, ✕ = makni. Filter po oznaci, skupno dodavanje.
PR #17 (Tabor) još nije spojen → nastavak na istoj grani.

## Status faze 3 (oznake)
- [x] songtags.py (DEFAULT_TAGS 11, SOURCE_RULES, liturgijski tekst, KEYWORD_RULES s pragovima)
      evaluacija na 126 pjesama: Klanjanje 14 (izvor klajanje), Aleluja 5, Marijanska 8, Korizma 7-8,
      Pričest 3-5, Ulazna 2, Prinos 1, Psalam 1; 84 bez prijedloga (opće pjesme hvale) — namjerno oprezno
- [x] songdb: tags, song_tags(source auto|user), songs.tagged (migracija), suggest_all, bulk_tag, filter tag/"-"
- [x] web: /db/tags, /db/tags/suggest, /db/tags/bulk, /db/songs/<id>/tags (POST/DELETE), /api/v1/tags, ?tag=
- [x] UI: traka oznaka (filter + "bez oznake" + ✨ Predloži), oznake u popisu, uređivanje na pjesmi,
      ☑ Sve + skupna oznaka; testirano u pregledniku (Playwright), 72 testa prolaze
- [ ] PR #17 opis dopuniti, CI

---
# Faza 4 — note (notni zapis) uz pjesme (zahtjev 2026-10-16)
"možeš li dati podršku da se imaju i note? da se odvojeno spremaju?" → notni zapis kao privitak.
Plan: tablica attachments(song_id FK cascade, name, filename, stored, mime, kind, size, created_at);
datoteke u <DB dir>/note/<uuid>.<ext> (izvan JSON-a pjesme); dopušteno PDF, PNG/JPG/WebP,
MusicXML (.musicxml/.xml/.mxl), MuseScore (.mscz/.mscx), MIDI; provjera magic bajtova; SVG/HTML ne.
Web: GET/POST /db/songs/<id>/notes, GET /db/notes/<aid>, PATCH (ime), DELETE; API /api/v1/notes/<aid>,
song JSON "notes". Pisanje uz DB_PASSWORD. UI: sekcija 🎼 Note na pjesmi, 🎼 u popisu.
MusicXML prikaz (OpenSheetMusicDisplay) NE sada — CDN nedostupan u sandboxu, ne može se testirati.
PR #17 još otvoren → ista grana.

## Status faze 4 (note)
- [x] songnotes.py (TYPES s provjerom sadržaja, clean_name, atomski zapis), tablica attachments
- [x] songdb: add/list/get/rename/delete_note, delete_song briše datoteke, notes_count u popisu, notes u pjesmi/exportu
- [x] web: /db/songs/<id>/notes GET/POST (više datoteka), /db/notes/<id> GET(?download=1)/PATCH/DELETE,
      /api/v1/notes/<id>; inline samo PDF/slike, nosniff; pisanje uz DB_PASSWORD
- [x] UI: sekcija 🎼 Note (sličice, Otvori, ⬇, ✎, ✕), 🎼 u popisu; Playwright OK; 76 testova
- [x] README/DEPLOY (backup data/note)

---
# Faza 5 — note iz PDF-a (A) + prepoznavanje nota (B), ZIP, oznake zbirki, snimke (2026-10-16)
Zahtjevi korisnika:
- "dali ima podrška da note čita iz pdf-a?" → "a, ali ostavi podršku i za B"
  A = stranice s notama iz PDF-a spremi kao note uz pjesmu; B = OMR (Audiveris) → MusicXML
- "print.josip.cloud podržava i da zip priložim (više pdf-ova od jednom); mogu imati samo tekst,
  tekst + akorde, tekst + note, tekst + akorde + note"
- "dodaj podršku za tag #pučkepjesme #velikizbor pa ako budem dodavao više pjesama (koje nam neće trebati)"
- "dodaj podršku da mogu dodati link s youtube-a po glasovima ili link za audio file s google diska"
Korisnikov primjer: "Isus Najljepše ime - Full Score.pdf" (Sibelius 7.5, SATB, 2 str., 3 kitice, akordi).

## Što je napravljeno
- scorepages.py: notna stranica = crtovlja (5/6 jednako razmaknutih crta; vektor: lines/rects/curves,
  spajanje komada, druge crte između ne smetaju) ili ≥40 znakova notnog fonta, ili crtovlja na slici
  (8 uskih pruga, projekcija redaka, zbroj sa susjednim retkom, 1 crta smije nedostajati, potvrda u 2 pruge).
  "mixed" = note + ≥3 retka teksta izvan pojasa nota → tekst se čita bez nota, stranica ide i kao note.
  Naslov iznad prvog crtovlja (najveća slova), riječi ispod nota (slogovi spojeni), otisak sadržaja.
  Provjera: korisnikov Sibelius 8+4 crtovlja, naslov OK; sken (zakrenut 0,4°, šum, JPEG) 8/8;
  Verovio vektor 8/8; Tabor 110 str., Misa*, klajanje, Aleluja* — 0 lažnih.
- songimport.py: PDF / ZIP / datoteka nota. Note idu uz: pjesmu iz istog PDF-a (jedna pjesma, susjedna
  stranica, mixed) → bazu po naslovu (strože: "Aleluja" ≠ "Aleluja (…)") ili tekstu ispod nota →
  jedinu pjesmu iz iste mape ZIP-a → inače NOVA pjesma samo s notama (popuni se tekstom kad stigne PDF
  istog naslova; add_song → _notes_only_song → _replace). Duplikat nota = isti sadržaj stranica.
- oznake zbirki: tags.grp ("" / "zbirka"), COLLECTION_TAGS Pučke pjesme + Veliki zbor, #hashtag →
  postojeća oznaka (tag_key bez razmaka/dijakritika); oznake pri uvozu samo NOVIM pjesmama (i sličnoj
  kad se zadrži kao nova); list_songs(exclude=…), UI 👁 sakrij (localStorage).
- snimke: songlinks.py (YouTube watch/youtu.be/shorts/embed/live/playlist, t=, Google disk file/d|open?id,
  audio .mp3…, ostalo link), tablica song_links (voice, title, url, kind), UI player (youtube-nocookie,
  drive /preview, <audio>), API "links".
- omr.py: Audiveris 5.11.0 (deb, ubuntu24.04, raspakiran dpkg-deb -x u python:3.12-slim/trixie) +
  tessdata 4.1.0 hrv+eng; posao u pozadini (omr_jobs, nit, flock — jedan u kontejneru), rezultat .mxl
  (origin omr). Potrebno: fontconfig + fontovi, GDK_SCALE=1 (inače traži GTK), dpi prema veličini
  stranice (Audiveris odbija > 20 Mpx), velike fotografije se smanje. Docker: WITH_OMR=1 (.env).
  Izmjereno: korisnikov PDF 2 str. → 32–36 s, ~960 MB RAM; slika +354 MB.
  Točnost na korisnikovom PDF-u: dionice, ključ, 6/8, note gotovo sve točne, 3 kitice teksta,
  akordi u 2. sustavu; greške: "SUS", lažni "p", dio akorda u 1. sustavu → ispravci u MuseScoreu.
- Verovio 6.3.0 (preglednik MusicXML + transpozicija ±6): Docker ga preuzme s npm-a u static/vendor,
  CDN rezerva. UI: 🔍 Prepoznaj (status, osvježavanje), ▶ Prikaži, ⇄ premjesti, "samo note" značka.
- CI: novi posao docker-omr (WITH_OMR=1 build → uvoz tests/fixtures/ljestvica.pdf → OMR mora uspjeti).
- gunicorn --timeout 900, MAX_UPLOAD_MB 200 (ZIP).

## Status faze 5
- [x] A (note iz PDF-a), B (OMR), ZIP, oznake zbirki, snimke — backend + UI + 92 testa
- [x] Playwright: uvoz nota (#velikizbor), ZIP (#pučkepjesme), sakrij zbirku, snimke + player,
      OMR u pregledniku (36 s), prikaz + transpozicija, premještanje nota
- [x] Docker sandbox: WITH_OMR=1 slika, uvoz preko HTTP-a + OMR posao u kontejneru = done
- [ ] CI zelen (docker-omr), PR #17 opis dopuniti, korisnik spaja

---
# 2026-10-20 — "pogledaj kako unaprijediti sve ovo"
Pregled koda (PR #17) → 10 nalaza, svi ispravljeni (commit 2c9be5a, 97 testova; Tabor simulacija
nepromijenjena: 105/3/1). Prijedlozi za dalje (čeka izbor korisnika):
1. uređivanje pjesme u bazi (naslov, tekst, akordi) 2. transpozicija akorda (prikaz + PDF)
3. popis za misu (Ulazna→Završna, PDF/link za zbor) 4. automatski backup (dnevno, čuva 14)
5. vježbanje glasova iz MusicXML (zvuk po dionici) 6. tekst iz nota za pjesme "samo note"
7. uvoz velikog ZIP-a u pozadini s napretkom 8. spajanje dvije pjesme (duplikati)
9. podjela songdb_web.py (HTML/JS u zasebne datoteke)

---
# Faza 6 — "radi 1, 2, 3 i 4" (2026-10-21): uređivanje, transpozicija, popis za misu, backup
PR #17 spojen (98ab25b); nova grana od main, PR #18.
- [x] transpose.py — hrvatski (H, B=B♭, fis/es, malo = mol, spojeni "AHE"/"FGa") i engleski zapis;
      povisilice/snizilice po ciljnom tonalitetu; ?transpose=n (pjesma, PDF, ChordPro, API, urednik)
- [x] songedit.py — tekst s akordima iznad, [Oznaka]; 122/122 pjesama identično nakon spremanja bez
      izmjena; song_versions (čuva 30), vraćanje; line_to_text_rows: akordi se ne slijepe
- [x] backup.py — dnevno pri prvom zahtjevu (flock), sqlite backup + note hard link, BACKUP_KEEP 14, ZIP
- [x] setlists.py — dijelovi mise, prijedlozi (pjevano za taj dio → oznaka), transpozicija po stavci,
      PDF s naslovnicom, tekst za poruku, urednik, javni /p/<token> (mobitel), novi link; Caddy /p/*
- [x] 110 testova; Playwright: transpozicija, urednik + povijest + vraćanje, popis (prijedlog Pričest,
      tonalitet po stavci), stranica za zbor na mobitelu, backup — bez JS grešaka
