"""Testovi baze pjesama: parser akorda, iscrtavanje izvornog izgleda,
usporedba (round trip), SQLite baza s duplikatima i web/API.

Testni PDF-ovi se rade rendererom (chord_render) — isti raspored kao izvorna
aplikacija. Ako u tests/fixtures/private/ postoje pravi izvorni PDF-ovi
(ne idu u git), provjerava se i da se iz baze rekreiraju identično.
"""

import copy
import glob
import io
import json
import os

import pytest

import chord_compare
import chord_extract
import chord_render
import songdb

HERE = os.path.dirname(os.path.abspath(__file__))


def _song_v2():
    return {
        "title": "Aleluja (Bože svet si)", "key": "G", "subtitle": "Papa Band",
        "meta": [{"label": "Pjesmarica", "values": ["203"]}],
        "sections": [
            {"label": "Verse 1", "lines": [
                {"text": "Bože svet si aleluja,", "chords": [[5, "G"], [16, "C"]]},
                # C G D gusto: aplikacija ubaci crticu "alelu-ja", D iza kraja
                {"text": "slavimo te aleluja", "chords": [[5, "D"], [14, "C"], [16, "G"], [18, "D"]]},
                {"text": "Isus Krist je Bog 2x", "chords": [[0, "F"], [5, "G"], [14, "C"]]},
            ]},
            {"label": "Chorus", "lines": [
                {"text": "", "chords": [[0, "C"], [6, "a"]]},
                {"text": "Aa le luu ja", "chords": [[0, "C"], [6, "a"]], "fmt": {"text": "mono"}},
                {"text": "Aaa       lee luu jaaa  2x", "chords": [[0, "D/FIS*"], [11, "G"], [16, "h"], [20, "A4"]],
                 "fmt": {"chord_text": " D/FIS*   G    h    A4"}},
                {"text": "običan redak bez akorda", "chords": []},
            ]},
            {"label": None, "lines": [
                # akord iza razmaka na kraju retka
                {"text": "U Tebi mirna ostat ću ", "chords": [[0, "C"], [22, "G"]]},
            ]},
        ],
        "layout": {"profile": "v2"},
    }


def _song_v1():
    return {
        "title": "Oče mi ti se klanjamo", "key": "A (G)", "subtitle": "",
        "meta": [{"label": "Pjesmarica", "values": ["1016"]}],
        "sections": [
            {"label": "Chorus", "lines": [
                {"text": "OČE, MI TI SE KLANJAMO", "chords": [[0, "C"], [5, "Dm"], [17, "G"]]},
                {"text": "MI TE LJUBIMO!", "chords": [[0, "Am"], [3, "Dm"]]},   # Dm se pomakne (v1)
                {"text": "SINE, MI TI SE KLANJAMO...", "chords": []},
            ]},
        ],
        "layout": {"profile": "v1", "page_numbers": True},
    }


def _longer(song):
    """Ista pjesma s dodatnom kiticom (dulja verzija)."""
    s = copy.deepcopy(song)
    s["sections"].append({"label": "Verse 2", "lines": [
        {"text": "Blagoslovljen aleluja, budi uvijek aleluja", "chords": [[0, "G"]]},
        {"text": "Hosana tebi aleluja, dođi sada aleluja", "chords": [[0, "D"]]},
        {"text": "Svet si Gospode, svet si Bože naš", "chords": [[0, "C"]]}]})
    return s


def _retitled_similar(song):
    """Drukčiji naslov, tekst ~85-90 % isti."""
    s = copy.deepcopy(song)
    s["title"] = "Posve drukčiji naslov"
    s["sections"][0]["lines"][2]["text"] = "Isus Krist je naš Gospodin i Kralj"
    s["sections"][2]["lines"][0]["text"] = "U Tebi mirno ostajem "
    return s


def _roundtrip(song, first_page=1):
    pdf1 = chord_render.render_pdf([song], first_page=first_page)
    res = chord_extract.extract(pdf1, "test.pdf")
    assert len(res["songs"]) == 1
    back = json.loads(json.dumps(res["songs"][0], ensure_ascii=False))
    pdf2 = chord_render.render_pdf([back], first_page=first_page)
    return pdf1, back, pdf2


# ── Parser + renderer ─────────────────────────────────────────────────────────

def test_layout_pair_matches_source_measurements():
    """Izmjereno na izvornom PDF-u: D 52.827, C 104.882, G 119.143, D 132.714."""
    text, _extra, placed = chord_render.layout_pair(
        "slavimo te aleluja", [[5, "D"], [14, "C"], [16, "G"], [18, "D"]], 12,
        chord_extract.PROFILES["v2"])
    assert text == "slavimo te alelu-ja"
    assert [round(x, 3) for x, _ in placed] == [52.827, 104.882, 119.143, 132.714]


def test_roundtrip_v2_identical_and_semantics_preserved():
    song = _song_v2()
    pdf1, back, pdf2 = _roundtrip(song)
    rep = chord_compare.compare(pdf1, pdf2)
    assert rep["identical"], chord_compare.summary(rep)

    assert back["title"] == song["title"] and back["key"] == "G"
    assert back["subtitle"] == "Papa Band"
    assert back["meta"] == [{"label": "Pjesmarica", "values": ["203"]}]
    v1, ch = back["sections"][0], back["sections"][1]
    assert v1["label"] == "Verse 1" and ch["label"] == "Chorus"
    # ubačena crtica nije dio teksta; akordi na istim slovima
    assert v1["lines"] == song["sections"][0]["lines"]
    assert ch["lines"][0] == {"text": "", "chords": [[0, "C"], [6, "a"]]}
    assert ch["lines"][1] == song["sections"][1]["lines"][1]
    assert ch["lines"][2]["fmt"] == {"chord_text": " D/FIS*   G    h    A4"}
    assert ch["lines"][2]["text"] == "Aaa       lee luu jaaa  2x"
    assert ch["lines"][3] == {"text": "običan redak bez akorda", "chords": []}
    assert back["sections"][2]["lines"][0] == song["sections"][2]["lines"][0]


def test_roundtrip_v1_page_numbers_and_pushed_chords():
    pdf1, back, pdf2 = _roundtrip(_song_v1(), first_page=7)
    rep = chord_compare.compare(pdf1, pdf2)
    assert rep["identical"], chord_compare.summary(rep)
    assert back["layout"]["profile"] == "v1" and back["layout"].get("page_numbers")
    first = back["sections"][0]["lines"][0]
    assert first == _song_v1()["sections"][0]["lines"][0]
    # pomaknuti akord (v1): sidro je slovo ispod njega, ne izvorni indeks
    pushed = back["sections"][0]["lines"][1]["chords"][1]
    assert pushed == [4, "Dm"]          # izvorno [3, "Dm"] (T), prikazan iznad E


def test_roundtrip_two_columns():
    song = _song_v1()
    song["layout"]["col_lefts"] = [63.02, 408.56]
    song["meta"][0]["col"] = 0
    song["sections"][0]["col"] = 1
    pdf1, back, pdf2 = _roundtrip(song)
    rep = chord_compare.compare(pdf1, pdf2)
    assert rep["identical"], chord_compare.summary(rep)
    assert back["layout"]["columns"] == 2


def test_chordpro_and_text_rows():
    song = _song_v2()
    cho = chord_extract.to_chordpro(song)
    assert "{title: Aleluja (Bože svet si)}" in cho
    assert "{key: G}" in cho and "{meta: Pjesmarica 203}" in cho
    assert "Bože [G]svet si ale[C]luja," in cho
    assert "slavi[D]mo te ale[C]lu[G]ja[D]" in cho
    assert "{start_of_chorus: Chorus}" in cho
    rows = chord_extract.line_to_text_rows(song["sections"][0]["lines"][0])
    assert rows == ["     G          C", "Bože svet si aleluja,"]
    assert chord_extract.lyrics_text(song).startswith("Bože svet si aleluja,\nslavimo te aleluja")


def test_chord_token_regex():
    for ok in ["C", "Am", "h", "fis", "Fis", "Bb", "H7", "A4", "Dsus4", "Cmaj7", "D/FIS*", "Am7/G", "Es", "C#m"]:
        assert chord_extract.is_chord(ok), ok
    for bad in ["Aaa", "luu", "Isus", "2x", "Bog", "ja"]:
        assert not chord_extract.is_chord(bad), bad


def test_extract_scan_and_garbage():
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.rect(100, 100, 200, 200)
    c.showPage()
    c.save()
    res = chord_extract.extract(buf.getvalue())
    assert res["songs"] == [] and res["skipped"] == [1]


# ── Baza ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def conn(tmp_path):
    c = songdb.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_norm_and_similarity():
    assert songdb.norm("Bože, ĐURO — Čuj! 2x") == "boze duro cuj"
    a = "jedan dva tri četiri pet šest sedam osam devet deset".split()
    b = a[:8] + ["x", "y"]
    assert 0.79 < songdb.similarity(a, b) < 0.81
    assert songdb.similarity(a, a) == 1.0


def test_import_duplicate_conflict_resolve(conn):
    song = _song_v2()
    r = songdb.import_songs(conn, [song], "a.pdf")
    assert len(r["added"]) == 1
    sid = r["added"][0]["id"]

    # isto -> preskoči (i iz druge datoteke / druge verzije izgleda)
    other_layout = copy.deepcopy(song)
    other_layout["layout"] = {"profile": "v1"}
    r = songdb.import_songs(conn, [song, other_layout], "b.pdf")
    assert len(r["duplicates"]) == 2 and not r["added"]

    # isti tekst, drugi akord/tonalitet -> već imamo, ne dodaje se i ne pita
    changed = copy.deepcopy(song)
    changed["sections"][0]["lines"][0]["chords"][0][1] = "Em"
    changed["key"] = "A"
    r = songdb.import_songs(conn, [changed], "c.pdf")
    assert not r["added"] and not r["conflicts"]
    assert r["duplicates"][0]["reason"] == "same_song" and r["duplicates"][0]["existing_id"] == sid

    # dulja verzija iste pjesme -> pita (možda je bolja)
    longer = _longer(song)
    r = songdb.import_songs(conn, [longer], "d.pdf")
    assert len(r["conflicts"]) == 1
    c = r["conflicts"][0]
    assert c["existing_id"] == sid and c["reason"] == "longer"
    # ponovni uvoz iste dulje verzije ne stvara drugu odluku
    r = songdb.import_songs(conn, [longer], "d.pdf")
    assert len(r["duplicates"]) == 1 and not r["conflicts"]
    pend = songdb.list_pending(conn)
    assert len(pend) == 1 and pend[0]["reason"] == "longer"
    assert pend[0]["new"]["sections"][-1]["label"] == "Verse 2"

    # zamijeni -> isti id, nova verzija, izvori se pamte
    assert songdb.resolve(conn, c["pending_id"], "replace") == sid
    s = songdb.get_song(conn, sid)
    assert s["sections"][-1]["label"] == "Verse 2"
    assert [x["file"] for x in s["sources"]] == ["a.pdf", "b.pdf", "c.pdf", "d.pdf"]
    assert songdb.stats(conn) == {"songs": 1, "pending": 0, "imports": 5,
                                  "notes": 0, "links": 0, "notes_only": 0, "importing": 0}
    assert songdb.resolve(conn, c["pending_id"], "replace") is None


def test_keep_both_keep_existing_and_threshold(conn):
    base = _song_v2()
    sid = songdb.import_songs(conn, [base], "a.pdf")["added"][0]["id"]

    # bitno drukčija pjesma (< 80 %) -> doda se bez pitanja
    other = copy.deepcopy(base)
    other["title"] = "Druga pjesma"
    other["sections"] = [{"label": "Verse", "lines": [
        {"text": "posve drugi tekst o nečem sasvim drugom", "chords": [[0, "D"]]},
        {"text": "i još jedan redak bez veze s prvom", "chords": []}]}]
    assert len(songdb.import_songs(conn, [other], "o.pdf")["added"]) == 1

    # drukčiji naslov, tekst ≥ 80 % -> pita
    p1 = songdb.import_songs(conn, [_retitled_similar(base)], "k.pdf")["conflicts"][0]
    assert p1["reason"] == "similar" and 0.8 <= p1["similarity"] < 0.95
    p2 = songdb.import_songs(conn, [_longer(base)], "s.pdf")["conflicts"][0]["pending_id"]

    assert songdb.resolve(conn, p1["pending_id"], "keep_existing") == sid
    assert songdb.get_song(conn, sid)["title"] == base["title"]
    new_id = songdb.resolve(conn, p2, "keep_both")
    assert new_id != sid and len(songdb.get_song(conn, new_id)["sections"]) == 4
    with pytest.raises(ValueError):
        songdb.resolve(conn, 1, "obrisi_sve")


def test_same_song_other_version_and_same_title_other_song(conn):
    """Slučajevi iz pjesmarica: kraća verzija pod duljim naslovom = ista pjesma;
    isti naslov s drugim tekstom = druga pjesma."""
    jona = {"title": "Nešto Više Od Jone", "key": "A", "subtitle": "", "meta": [], "layout": {},
            "sections": [{"label": "Chorus", "lines": [
                {"text": "Evo, ovdje je nešto više od Jone", "chords": [[0, "Am"]]},
                {"text": "Evo, ovdje je nešto više od Salomona", "chords": []},
                {"text": "Evo, ovdje je nešto veće od Hrama", "chords": []}]}]}
    sid = songdb.import_songs(conn, [jona], "app.pdf")["added"][0]["id"]
    kraca = copy.deepcopy(jona)
    kraca["title"] = "EVO, OVDJE JE NEŠTO VIŠE OD JONE"
    kraca["sections"][0]["lines"] = kraca["sections"][0]["lines"][:2]
    r = songdb.import_songs(conn, [kraca], "tabor.pdf")
    assert r["duplicates"][0]["existing_id"] == sid and not r["added"] and not r["conflicts"]

    hv1 = {"title": "HVALOSPJEV LJUBAVI", "subtitle": "KEFA", "key": "", "meta": [], "layout": {},
           "sections": [{"label": None, "lines": [
               {"text": "Kada jezike bih sve govorio a ljubavi ne bih imao", "chords": []},
               {"text": "Bio bih mjed što ječi, cimbal što zveči", "chords": []}]}]}
    hv2 = {"title": "HVALOSPJEV LJUBAVI", "subtitle": "Frama", "key": "", "meta": [], "layout": {},
           "sections": [{"label": None, "lines": [
               {"text": "Ljubav je strpljiva, ljubav je dobrostiva", "chords": []},
               {"text": "Ona ne zavidi, ne hvasta se, ne oholi", "chords": []}]}]}
    r = songdb.import_songs(conn, [hv1, hv2], "tabor.pdf")
    assert len(r["added"]) == 2


def test_search_and_delete_cascade(conn):
    song = _song_v2()
    sid = songdb.import_songs(conn, [song], "a.pdf")["added"][0]["id"]
    assert songdb.list_songs(conn, "boze svet")["total"] == 1      # bez dijakritika
    assert songdb.list_songs(conn, "203")["total"] == 1            # broj u pjesmarici
    assert songdb.list_songs(conn, "papa band")["total"] == 1
    assert songdb.list_songs(conn, "nema toga")["total"] == 0
    songdb.import_songs(conn, [_longer(song)], "b.pdf")
    assert songdb.stats(conn)["pending"] == 1
    assert songdb.delete_song(conn, sid)
    assert songdb.stats(conn) == {"songs": 0, "pending": 0, "imports": 2,
                                  "notes": 0, "links": 0, "notes_only": 0, "importing": 0}


def test_song_from_db_renders_identically(conn):
    pdf1 = chord_render.render_pdf([_song_v2()])
    res = chord_extract.extract(pdf1, "x.pdf")
    sid = songdb.import_songs(conn, res["songs"], "x.pdf")["added"][0]["id"]
    pdf2 = chord_render.render_pdf([songdb.song_data(conn, sid)])
    assert chord_compare.compare(pdf1, pdf2)["identical"]


# ── Web i API ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("API_TOKEN", raising=False)
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def _upload(client, pdf, name="pjesma.pdf", headers=None):
    return client.post("/db/import", data={"pdf": (io.BytesIO(pdf), name)},
                       content_type="multipart/form-data", headers=headers or {})


def test_web_import_list_view_and_api(client):
    pdf = chord_render.render_pdf([_song_v2(), _song_v1()])
    r = _upload(client, pdf)
    assert r.status_code == 200
    rep = r.get_json()["reports"][0]
    assert len(rep["added"]) == 2 and rep["pages"] == 2
    assert _upload(client, pdf).get_json()["reports"][0]["duplicates"]

    lst = client.get("/db/songs?q=bože").get_json()
    assert lst["total"] == 1
    sid = lst["items"][0]["id"]

    j = client.get(f"/api/v1/songs/{sid}")
    assert j.status_code == 200 and j.headers["Access-Control-Allow-Origin"] == "*"
    data = j.get_json()
    assert data["sections"][0]["lines"][1]["chords"] == [[5, "D"], [14, "C"], [16, "G"], [18, "D"]]
    assert "Bože [G]svet" in data["chordpro"] and data["numbers"] == ["203"]

    cho = client.get(f"/api/v1/songs/{sid}.cho")
    assert cho.status_code == 200 and cho.mimetype == "text/plain"
    assert client.get(f"/api/v1/songs/{sid}.pdf").data.startswith(b"%PDF")
    assert client.get(f"/db/songs/{sid}/pdf").headers["Content-Disposition"].startswith("attachment")
    assert len(json.loads(client.get("/api/v1/export").data)["songs"]) == 2
    assert client.get("/api/v1").get_json()["songs"] == 2
    assert client.get("/api/v1/songs/999").status_code == 404

    ed = client.post("/db/to-editor", json={"ids": [sid]}).get_json()["songs"][0]
    assert ed["key"] == "Key: G" and ed["pjesmarica"] == "203"
    assert ed["body"].startswith("Verse 1\n     G          C\nBože svet si aleluja,")

    many = client.post("/db/pdf", json={"ids": [sid, sid + 1]})
    assert many.status_code == 200 and many.data.startswith(b"%PDF")
    assert client.post("/db/pdf", json={"ids": []}).status_code == 400

    page = client.get("/baza")
    assert page.status_code == 200 and b"Baza pjesama" in page.data
    assert b"__VERSION__" not in page.data and b'href="baza"' in client.get("/").data


def test_web_conflict_flow_and_errors(client):
    _upload(client, chord_render.render_pdf([_song_v2()]))
    changed = _longer(_song_v2())
    changed["key"] = "A"
    rep = _upload(client, chord_render.render_pdf([changed])).get_json()["reports"][0]
    assert len(rep["conflicts"]) == 1
    pend = client.get("/db/pending").get_json()["items"]
    assert len(pend) == 1 and pend[0]["new"]["key"] == "A" and pend[0]["existing"]["key"] == "G"
    assert pend[0]["reason"] == "longer"
    pid = pend[0]["id"]
    assert client.post(f"/db/pending/{pid}", json={"action": "x"}).status_code == 400
    r = client.post(f"/db/pending/{pid}", json={"action": "replace"})
    assert r.status_code == 200 and client.get(f"/db/songs/{r.get_json()['song_id']}").get_json()["key"] == "A"
    assert client.post(f"/db/pending/{pid}", json={"action": "replace"}).status_code == 404

    bad = _upload(client, b"nije pdf", "x.pdf").get_json()["reports"][0]
    assert "Nije PDF" in bad["error"]
    assert client.post("/db/import").status_code == 400
    sid = client.get("/db/songs").get_json()["items"][0]["id"]
    assert client.delete(f"/db/songs/{sid}").status_code == 200
    assert client.delete(f"/db/songs/{sid}").status_code == 404


def test_web_password_and_api_token(client, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "tajna")
    monkeypatch.setenv("API_TOKEN", "tok")
    pdf = chord_render.render_pdf([_song_v2()])
    r = _upload(client, pdf)
    assert r.status_code == 401 and r.get_json()["need_password"]
    assert _upload(client, pdf, headers={"X-DB-Password": "kriva"}).status_code == 401
    assert _upload(client, pdf, headers={"X-DB-Password": "tajna"}).status_code == 200
    assert client.get("/db/songs").status_code == 200           # stranica čita bez lozinke
    assert client.delete("/db/songs/1").status_code == 401

    assert client.get("/api/v1/songs").status_code == 401
    assert client.get("/api/v1/songs", headers={"Authorization": "Bearer tok"}).status_code == 200
    assert client.get("/api/v1/songs?token=tok").status_code == 200
    assert client.open("/api/v1/songs", method="OPTIONS").status_code == 204


# ── Pravi izvorni PDF-ovi (lokalno, nisu u gitu) ──────────────────────────────

PRIVATE = sorted(glob.glob(os.path.join(HERE, "fixtures", "private", "*.pdf")))


@pytest.mark.skipif(not PRIVATE, reason="nema privatnih izvornih PDF-ova")
@pytest.mark.parametrize("path", PRIVATE, ids=[os.path.basename(p) for p in PRIVATE])
def test_private_sources_recreated_identically(path):
    pymupdf = pytest.importorskip("pymupdf")
    with open(path, "rb") as fh:
        src = fh.read()
    for song in chord_extract.extract(src, os.path.basename(path))["songs"]:
        d = pymupdf.open(stream=src)
        d.select([p - 1 for p in song["source"]["pages"]])
        out = chord_render.render_pdf([json.loads(json.dumps(song))],
                                      first_page=song["source"]["pages"][0])
        rep = chord_compare.compare(d.tobytes(), out)
        assert rep["identical"], song["title"] + "\n" + chord_compare.summary(rep)


def test_long_indented_lines_do_not_split_columns():
    song = _song_v2()
    song["sections"].append({"label": "Outro", "lines": [
        {"text": " " * 45 + "x2 na kraju", "chords": []},
        {"text": " " * 45 + "još jedan uvučen", "chords": []},
        {"text": "dugi redak koji se proteže preko sredine stranice i dalje desno", "chords": [[0, "G"]]},
    ]})
    pdf1, back, pdf2 = _roundtrip(song)
    assert chord_compare.compare(pdf1, pdf2)["identical"]
    assert "columns" not in back["layout"]
    assert back["sections"][-1]["lines"] == song["sections"][-1]["lines"]


def test_web_rejects_pdf_without_song_titles(client):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(A4))
    c.setFont("Helvetica", 9)
    c.drawString(40, 550, "Bože svet si aleluja")
    c.drawString(40, 538, "slavimo te aleluja")
    c.showPage()
    c.save()
    rep = _upload(client, buf.getvalue()).get_json()["reports"][0]
    assert "nisu prepoznate pjesme" in rep["error"]
    assert client.get("/db/stats").get_json()["songs"] == 0


def test_download_names_without_diacritics():
    from songdb_web import _ascii_name
    assert _ascii_name("Aleluja (Bože svet si ) – Đurđevak") == "Aleluja_Boze_svet_si_Durdevak"
    assert _ascii_name("!!!") == "pjesma"


def test_crowded_chords_one_visible_hyphen_roundtrip():
    song = _song_v2()
    song["sections"] = [{"label": None, "lines": [
        {"text": "Kada jezike bih sve govorio a ljubavi", "chords": [[0, "G"], [24, "Gsus4"], [26, "C"], [28, "Gmaj7"]]}]}]
    text, _extra, _placed = chord_render.layout_pair(
        song["sections"][0]["lines"][0]["text"], song["sections"][0]["lines"][0]["chords"], 12,
        chord_extract.PROFILES["v2"])
    assert text.count("-") <= 2 and "--" not in text
    pdf1, back, pdf2 = _roundtrip(song)
    assert chord_compare.compare(pdf1, pdf2)["identical"]
    assert back["sections"][0]["lines"] == song["sections"][0]["lines"]


def test_web_resolve_all(client):
    _upload(client, chord_render.render_pdf([_song_v2()]))
    a, b = _longer(_song_v2()), _retitled_similar(_song_v2())
    rep = _upload(client, chord_render.render_pdf([a, b])).get_json()["reports"][0]
    assert len(rep["conflicts"]) == 2
    assert client.post("/db/pending/all", json={"action": "nista"}).status_code == 400
    r = client.post("/db/pending/all", json={"action": "keep_existing"})
    assert r.get_json()["resolved"] == 2
    assert client.get("/db/stats").get_json() == {"songs": 1, "pending": 0, "imports": 2,
                                                 "notes": 0, "links": 0, "notes_only": 0, "importing": 0}


def test_migration_adds_pending_reason(tmp_path):
    import sqlite3
    path = tmp_path / "stara.db"
    old = sqlite3.connect(path)
    old.executescript(songdb.SCHEMA.replace(
        "    source      TEXT NOT NULL DEFAULT '{}',\n    created_at  TEXT NOT NULL\n);\n\nCREATE TABLE IF NOT EXISTS imports",
        "    source      TEXT NOT NULL DEFAULT '{}',\n    created_at  TEXT NOT NULL\n);\nCREATE TABLE IF NOT EXISTS imports"))
    cols = {r[1] for r in old.execute("PRAGMA table_info(pending)")}
    old.execute("ALTER TABLE pending DROP COLUMN reason") if "reason" in cols else None
    old.commit()
    old.close()
    c = songdb.connect(path)
    assert "reason" in {r["name"] for r in c.execute("PRAGMA table_info(pending)")}
    songdb.import_songs(c, [_song_v2()], "a.pdf")
    assert songdb.import_songs(c, [_longer(_song_v2())], "b.pdf")["conflicts"][0]["reason"] == "longer"


# ── Pjesmarica iz Worda (kao TABOR PJESMARICA) ───────────────────────────────

def _word_songbook():
    """Sintetska pjesmarica u stilu Worda: naslovnica, glava/podnožje s crtama,
    centriran podcrtan naslov, crveni akordi iznad centriranog teksta,
    kurzivni refren, podnaslov i capo."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas
    W, H = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 48)
    c.drawCentredString(W / 2, H / 2, "PJESMARICA 2017")      # naslovnica
    c.showPage()

    def page(title, sub, capo, blocks):
        c.setFont("Times-Roman", 12)
        c.drawString(70, H - 47, "Bog moj i sve moje")           # glava
        c.rect(69, H - 64, 456, 0.5, fill=1, stroke=0)
        c.rect(69, H - 771, 456, 0.5, fill=1, stroke=0)
        c.drawString(70, H - 785, "PJESMARICA")                   # podnožje
        y = H - 129
        c.setFont("Helvetica-Bold", 16)
        tw = stringWidth(title, "Helvetica-Bold", 16)
        c.drawCentredString(W / 2, y, title)
        c.setFillColorRGB(1, 0, 0)
        c.rect(W / 2 - tw / 2, y - 3, tw, 1.5, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        for small in (sub, capo):
            if small:
                y -= 15
                c.setFont("Helvetica-Bold", 12)
                c.drawCentredString(W / 2, y, small)
        y -= 20
        for italic, lines in blocks:
            font = "Helvetica-Oblique" if italic else "Helvetica"
            for text, chords in lines:
                lw = stringWidth(text, font, 16)
                x0 = W / 2 - lw / 2
                if chords:
                    c.setFillColorRGB(1, 0, 0)
                    c.setFont("Helvetica-Bold", 15)
                    for idx, ch in chords:      # akord malo lijevo od sloga, kao u Wordu
                        c.drawString(x0 + stringWidth(text[:idx], font, 16) - 2.5, y, ch)
                    c.setFillColorRGB(0, 0, 0)
                    y -= 19
                c.setFont(font, 16)
                c.drawString(x0, y, text)
                y -= 19
            y -= 19                               # prazan odlomak
        c.showPage()

    # (standardni PDF fontovi nemaju č/ć — u pravoj pjesmarici su TTF fontovi)
    page("ABBA OCE", None, None, [
        (False, [("Abba, Oce! Abba, Oce!", [[0, "G"], [6, "C"], [11, "D"], [17, "G"]]),
                 ("Duša moja traži Te!", [[0, "h"], [5, "F#"], [16, "h"], [19, "D7"]])]),
        (True, [("Uvijek Tebe ja cu ljubiti!", [[0, "h"], [7, "C"], [12, "a"], [18, "G"], [21, "DG"]]),
                ("Uvijek Tebe ja cu ljubiti!", [])]),
    ])
    page("HVALOSPJEV LJUBAVI", "(KEFA)", "capo 3 fret", [
        (False, [("Kada jezike bih sve govorio", [[0, "G"], [21, "D"]]),
                 ("a ljubavi ne bih imao", [])]),
    ])
    c.save()
    return buf.getvalue()


def test_word_songbook_import():
    res = chord_extract.extract(_word_songbook(), "tabor.pdf")
    assert [s["title"] for s in res["songs"]] == ["ABBA OCE", "HVALOSPJEV LJUBAVI"]
    assert any("Stranica 1 preskočena" in w for w in res["warnings"])
    abba, hv = res["songs"]
    assert abba["layout"]["source_format"] == "word"
    v, r = abba["sections"]
    assert v["label"] is None and r["label"] is None   # kurziv se čuva, ali nije "Refren"
    assert v["lines"][0] == {"text": "Abba, Oce! Abba, Oce!",
                             "chords": [[0, "G"], [6, "C"], [11, "D"], [17, "G"]]}
    assert v["lines"][1]["chords"] == [[0, "h"], [5, "F#"], [16, "h"], [19, "D7"]]
    assert r["lines"][0]["chords"] == [[0, "h"], [7, "C"], [12, "a"], [18, "G"], [21, "DG"]]
    assert r["lines"][0]["fmt"] == {"text": "italic"} and r["lines"][1]["chords"] == []
    assert hv["subtitle"] == "KEFA"
    assert hv["meta"] == [{"label": "Capo", "values": ["3 fret"]}]
    assert hv["sections"][0]["lines"][0]["chords"] == [[0, "G"], [21, "D"]]
    # glava i podnožje nisu ušli u tekst
    assert "Bog moj" not in chord_extract.lyrics_text(abba) + chord_extract.lyrics_text(hv)
    # iz baze se iscrtava u standardnom izgledu (bez greške, s kurzivom)
    pdf = chord_render.render_pdf(res["songs"])
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        fonts = {c["fontname"].split("+")[-1] for c in doc.pages[0].chars}
    assert "OpenSans-Italic" in fonts


def test_word_songbook_web_import(client):
    rep = _upload(client, _word_songbook(), "tabor.pdf").get_json()["reports"][0]
    assert len(rep["added"]) == 2 and not rep.get("error")
    rep = _upload(client, _word_songbook(), "tabor.pdf").get_json()["reports"][0]
    assert len(rep["duplicates"]) == 2 and not rep["added"]


# ── Oznake ────────────────────────────────────────────────────────────────────

def test_tag_suggestions_rules():
    import songtags
    s = _song_v2()
    assert songtags.suggest(s, ["Papa band pjesmarica_Klanjanje.pdf"]) == ["Aleluja", "Klanjanje"]
    assert songtags.suggest(s, ["klajanje (08-10-2025).pdf"]) == ["Aleluja", "Klanjanje"]
    assert "Aleluja" in songtags.suggest(s)                      # naslov "Aleluja (...)"
    def song(title, *lines):
        return {"title": title, "sections": [{"label": None, "lines": [{"text": t, "chords": []} for t in lines]}]}
    assert songtags.suggest(song("Psalam 34 (Gledajte u Gospodina)", "Kušajte i vidite")) == ["Psalam"]
    assert songtags.suggest(song("Gospodine", "Gospodine, smiluj se", "Kriste, smiluj se")) == ["Gospodine"]
    assert songtags.suggest(song("Svet", "Svet, svet, svet Gospodin Bog Sabaot", "Hosana u visini")) == ["Gospodine"]
    # obrada "Agnus Dei" (pjesma hvale) nije liturgijski Jaganjče Božji
    assert "Gospodine" not in songtags.suggest(song("Agnus Dei", "Aleluja", "Sveti, sveti, svet si Bože svemogući"))
    assert songtags.suggest(song("Dođi Kruše života", "Ti skrivena tajno, u meni se nastani")) == ["Pričest"]
    assert songtags.suggest(song("Zdravo Marijo", "Zdravo Marijo, milosti puna")) == ["Marijanska"]
    assert songtags.suggest(song("Golgota", "Na križu za nas razapet")) == ["Korizma"]
    assert songtags.suggest(song("Isuse znam da si tu", "Kruh i vino prinosimo Bože")) == ["Prinos"]
    assert songtags.suggest(song("Bog te ljubi", "Tvoju su lađu vjetrovi odnijeli")) == []


def test_tags_db_auto_confirm_remove_bulk_filter(conn):
    r = songdb.import_songs(conn, [_song_v2()], "Papa band pjesmarica_Klanjanje.pdf")
    sid = r["added"][0]["id"]
    tags = songdb.get_song(conn, sid)["tags"]
    assert {t["name"] for t in tags} == {"Aleluja", "Klanjanje"} and all(t["auto"] for t in tags)
    assert songdb.list_tags(conn)["tags"][0]["name"] == "Ulazna"          # zadani popis
    # potvrdi prijedlog, dodaj novu oznaku, makni prijedlog
    songdb.set_tag(conn, sid, "Klanjanje")
    songdb.set_tag(conn, sid, "Mladi")
    songdb.remove_tag(conn, sid, "Aleluja")
    assert songdb.get_song(conn, sid)["tags"] == [{"name": "Klanjanje", "auto": False},
                                                  {"name": "Mladi", "auto": False}]
    # ponovni prijedlog ne vraća maknute oznake
    assert songdb.suggest_all(conn) == (0, 0)
    assert songdb.list_songs(conn, tag="Mladi")["total"] == 1
    assert songdb.list_songs(conn, tag="Pričest")["total"] == 0
    other = copy.deepcopy(_song_v2())
    other["title"] = "Druga"
    other["sections"] = [{"label": None, "lines": [{"text": "posve drugi tekst bez ičega", "chords": []}]}]
    oid = songdb.import_songs(conn, [other], "x.pdf")["added"][0]["id"]
    assert songdb.list_songs(conn, tag="-")["total"] == 1                  # bez oznake
    assert songdb.bulk_tag(conn, [sid, oid], "Kroz godinu") == 2
    assert songdb.list_songs(conn, tag="kroz godinu")["total"] == 2         # bez obzira na velika slova
    assert songdb.bulk_tag(conn, [sid, oid], "Kroz godinu", "remove") == 2
    with pytest.raises(ValueError):
        songdb.set_tag(conn, sid, "   ")
    assert songdb.delete_song(conn, sid)
    assert conn.execute("SELECT COUNT(*) FROM song_tags WHERE song_id=?", (sid,)).fetchone()[0] == 0


def test_tags_migration_and_suggest_for_old_songs(tmp_path):
    import sqlite3
    path = tmp_path / "stara.db"
    c = songdb.connect(path)
    songdb.import_songs(c, [_song_v2()], "Pričest_žuto.pdf")
    # simuliraj bazu iz prethodne verzije: bez oznaka i bez stupca "tagged"
    c.execute("DELETE FROM song_tags")
    c.execute("ALTER TABLE songs DROP COLUMN tagged")
    c.close()
    songdb._READY.clear()                 # nova verzija aplikacije = novi proces
    c = songdb.connect(path)
    assert songdb.suggest_all(c) == (1, 2)                                 # Aleluja + Pričest (izvor)
    sid = songdb.list_songs(c)["items"][0]["id"]
    assert {t["name"] for t in songdb.get_song(c, sid)["tags"]} == {"Aleluja", "Pričest"}


def test_web_tags(client):
    _upload(client, chord_render.render_pdf([_song_v2()]), "klanjanje.pdf")
    sid = client.get("/db/songs").get_json()["items"][0]["id"]
    assert {t["name"] for t in client.get(f"/db/songs/{sid}").get_json()["tags"]} == {"Aleluja", "Klanjanje"}
    r = client.post(f"/db/songs/{sid}/tags", json={"name": "Završna"})
    assert {"name": "Završna", "auto": False} in r.get_json()["tags"]
    r = client.delete(f"/db/songs/{sid}/tags/Aleluja")
    assert "Aleluja" not in {t["name"] for t in r.get_json()["tags"]}
    assert client.post(f"/db/songs/{sid}/tags", json={"name": ""}).status_code == 400
    assert client.post("/db/songs/999/tags", json={"name": "X"}).status_code == 404
    assert client.post("/db/tags/bulk", json={"ids": [sid], "name": "Pričest"}).get_json()["changed"] == 1
    assert client.post("/db/tags/bulk", json={"ids": [sid], "name": "X", "action": "zbrisi"}).status_code == 400
    assert client.post("/db/tags/suggest").get_json() == {"ok": True, "songs": 0, "tags": 0}
    tags = {t["name"]: t["count"] for t in client.get("/api/v1/tags").get_json()["tags"]}
    assert tags["Pričest"] == 1 and tags["Završna"] == 1 and tags["Aleluja"] == 0
    assert client.get("/api/v1/songs?tag=Završna").get_json()["total"] == 1
    assert "tags" in json.loads(client.get("/api/v1/export").data)["songs"][0]


def test_web_tags_need_password(client, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "tajna")
    _upload(client, chord_render.render_pdf([_song_v2()]), headers={"X-DB-Password": "tajna"})
    sid = client.get("/db/songs").get_json()["items"][0]["id"]
    assert client.post(f"/db/songs/{sid}/tags", json={"name": "X"}).status_code == 401
    assert client.delete(f"/db/songs/{sid}/tags/X").status_code == 401
    assert client.post("/db/tags/bulk", json={"ids": [sid], "name": "X"}).status_code == 401
    assert client.post("/db/tags/suggest").status_code == 401
    assert client.get("/db/tags").status_code == 200


# ── Note (notni zapis) ────────────────────────────────────────────────────────

def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, "PNG")
    return buf.getvalue()


def _zip(name, content=b"<x/>"):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, content)
    return buf.getvalue()


MUSICXML = (b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE score-partwise>\n'
            b'<score-partwise version="3.1"><part-list/></score-partwise>')


def test_notes_types_and_validation(tmp_path):
    import songnotes
    ok = {
        "Abba Oče.pdf": chord_render.render_pdf([_song_v2()]),
        "sopran.png": _png(),
        "gitara.jpg": b"\xff\xd8\xff\xe0" + b"0" * 20,
        "x.webp": b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"0" * 10,
        "zbor.musicxml": MUSICXML,
        "zbor.xml": MUSICXML,
        "zbor.mxl": _zip("score.xml", MUSICXML),
        "klavir.mscz": _zip("score.mscx"),
        "klavir.mscx": b"<?xml version='1.0'?><museScore version='4.0'></museScore>",
        "melodija.mid": b"MThd\x00\x00\x00\x06\x00\x01",
    }
    for name, data in ok.items():
        songnotes.check_file(name, data)
    bad = {
        "slika.svg": b"<svg></svg>",
        "virus.exe": b"MZ",
        "lazni.pdf": _png(),                       # PNG preimenovan u .pdf
        "nije.xml": b"<html></html>",              # XML koji nije MusicXML
        "prazno.png": b"",
        "nije.mxl": b"PK nije zip",
    }
    for name, data in bad.items():
        with pytest.raises(songnotes.NoteError):
            songnotes.check_file(name, data)
    assert songnotes.clean_name("../../etc/passwd", "x") == "passwd"
    assert songnotes.clean_name("a\x00b\nc.pdf", "x") == "abc.pdf"


def test_notes_db_add_list_rename_delete(conn, tmp_path):
    sid = songdb.import_songs(conn, [_song_v2()], "a.pdf")["added"][0]["id"]
    n1 = songdb.add_note(conn, sid, "Abba Oče – sopran.png", _png())
    n2 = songdb.add_note(conn, sid, "zbor.musicxml", MUSICXML, name="Zbor SATB")
    assert n1["name"] == "Abba Oče – sopran" and n1["kind"] == "image" and n1["inline"]
    assert n2["name"] == "Zbor SATB" and n2["kind"] == "musicxml" and not n2["inline"]
    assert songdb.add_note(conn, 999, "x.png", _png()) is None
    notes = songdb.get_song(conn, sid)["notes"]
    assert [n["id"] for n in notes] == [n1["id"], n2["id"]]
    assert songdb.list_songs(conn)["items"][0]["notes_count"] == 2
    # datoteka je odvojeno, pod nasumičnim imenom
    meta, path = songdb.get_note(conn, n1["id"])
    assert path.parent.name == "note" and path.read_bytes() == _png()
    assert "Abba" not in path.name
    # tekst i akordi pjesme se ne mijenjaju
    assert songdb.get_song(conn, sid)["sections"] == _song_v2()["sections"]
    assert songdb.rename_note(conn, n1["id"], "Sopran")
    with pytest.raises(ValueError):
        songdb.rename_note(conn, n1["id"], "  ")
    assert songdb.delete_note(conn, n1["id"]) and not path.exists()
    assert not songdb.delete_note(conn, n1["id"])
    _m2, path2 = songdb.get_note(conn, n2["id"])
    assert songdb.delete_song(conn, sid) and not path2.exists()     # note idu s pjesmom
    assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0


def test_web_notes(client):
    _upload(client, chord_render.render_pdf([_song_v2()]))
    sid = client.get("/db/songs").get_json()["items"][0]["id"]
    pdf = chord_render.render_pdf([_song_v2()])
    r = client.post(f"/db/songs/{sid}/notes", data={"file": [(io.BytesIO(pdf), "Abba Oče.pdf"),
                                                            (io.BytesIO(b"<svg/>"), "x.svg"),
                                                            (io.BytesIO(MUSICXML), "zbor.musicxml")]},
                    content_type="multipart/form-data")
    body = r.get_json()
    assert r.status_code == 200 and len(body["added"]) == 2 and len(body["errors"]) == 1
    pid, xid = body["added"][0]["id"], body["added"][1]["id"]
    f = client.get(f"/db/notes/{pid}")
    assert f.status_code == 200 and f.mimetype == "application/pdf" and f.data == pdf
    assert "inline" in f.headers["Content-Disposition"] and f.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in client.get(f"/db/notes/{pid}?download=1").headers["Content-Disposition"]
    assert "attachment" in client.get(f"/db/notes/{xid}").headers["Content-Disposition"]   # MusicXML se preuzima
    assert client.patch(f"/db/notes/{xid}", json={"name": "Zbor"}).get_json()["ok"]
    song = client.get(f"/api/v1/songs/{sid}").get_json()
    assert [n["name"] for n in song["notes"]] == ["Abba Oče", "Zbor"]
    assert client.get(f"/api/v1/notes/{pid}").data == pdf
    assert "notes" in json.loads(client.get("/api/v1/export").data)["songs"][0]
    assert client.get("/db/songs").get_json()["items"][0]["notes_count"] == 2
    bad = client.post(f"/db/songs/{sid}/notes", data={"file": (io.BytesIO(b"MZ"), "x.exe")},
                      content_type="multipart/form-data")
    assert bad.status_code == 400 and "PDF" in bad.get_json()["error"]
    assert client.post(f"/db/songs/{sid}/notes").status_code == 400
    assert client.post("/db/songs/999/notes", data={"file": (io.BytesIO(pdf), "a.pdf")},
                       content_type="multipart/form-data").status_code == 404
    assert client.delete(f"/db/notes/{pid}").get_json()["ok"]
    assert client.get(f"/db/notes/{pid}").status_code == 404
    assert b'accept=".pdf,.png' in client.get("/baza").data


def test_web_notes_need_password_and_token(client, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "tajna")
    monkeypatch.setenv("API_TOKEN", "tok")
    pw = {"X-DB-Password": "tajna"}
    _upload(client, chord_render.render_pdf([_song_v2()]), headers=pw)
    sid = client.get("/db/songs").get_json()["items"][0]["id"]
    up = lambda h: client.post(f"/db/songs/{sid}/notes", data={"file": (io.BytesIO(_png()), "a.png")},
                               content_type="multipart/form-data", headers=h)
    assert up({}).status_code == 401
    nid = up(pw).get_json()["added"][0]["id"]
    assert client.delete(f"/db/notes/{nid}").status_code == 401
    assert client.patch(f"/db/notes/{nid}", json={"name": "x"}).status_code == 401
    assert client.get(f"/db/notes/{nid}").status_code == 200              # stranica čita
    assert client.get(f"/api/v1/notes/{nid}").status_code == 401           # API traži token
    assert client.get(f"/api/v1/notes/{nid}?token=tok").status_code == 200
