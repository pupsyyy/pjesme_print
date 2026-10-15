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

    # jedan akord drukčiji -> 100 % sličan tekst -> čeka odluku
    changed = copy.deepcopy(song)
    changed["sections"][0]["lines"][0]["chords"][0][1] = "Em"
    r = songdb.import_songs(conn, [changed], "c.pdf")
    assert len(r["conflicts"]) == 1
    c = r["conflicts"][0]
    assert c["existing_id"] == sid and c["similarity"] == 1.0
    # ponovni uvoz iste izmjene ne stvara drugu odluku
    r = songdb.import_songs(conn, [changed], "c.pdf")
    assert len(r["duplicates"]) == 1 and not r["conflicts"]
    pend = songdb.list_pending(conn)
    assert len(pend) == 1 and pend[0]["new"]["sections"][0]["lines"][0]["chords"][0][1] == "Em"

    # zamijeni -> isti id, nova verzija, izvori se pamte
    assert songdb.resolve(conn, c["pending_id"], "replace") == sid
    s = songdb.get_song(conn, sid)
    assert s["sections"][0]["lines"][0]["chords"][0][1] == "Em"
    assert [x["file"] for x in s["sources"]] == ["a.pdf", "b.pdf", "c.pdf"]
    assert songdb.stats(conn) == {"songs": 1, "pending": 0, "imports": 4}
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

    v2 = copy.deepcopy(base)
    v2["key"] = "A"
    p1 = songdb.import_songs(conn, [v2], "k.pdf")["conflicts"][0]["pending_id"]
    v3 = copy.deepcopy(base)
    v3["subtitle"] = "Netko drugi"
    p2 = songdb.import_songs(conn, [v3], "s.pdf")["conflicts"][0]["pending_id"]

    assert songdb.resolve(conn, p1, "keep_existing") == sid
    assert songdb.get_song(conn, sid)["key"] == "G"
    new_id = songdb.resolve(conn, p2, "keep_both")
    assert new_id != sid and songdb.get_song(conn, new_id)["subtitle"] == "Netko drugi"
    with pytest.raises(ValueError):
        songdb.resolve(conn, 1, "obrisi_sve")


def test_search_and_delete_cascade(conn):
    song = _song_v2()
    sid = songdb.import_songs(conn, [song], "a.pdf")["added"][0]["id"]
    assert songdb.list_songs(conn, "boze svet")["total"] == 1      # bez dijakritika
    assert songdb.list_songs(conn, "203")["total"] == 1            # broj u pjesmarici
    assert songdb.list_songs(conn, "papa band")["total"] == 1
    assert songdb.list_songs(conn, "nema toga")["total"] == 0
    changed = copy.deepcopy(song)
    changed["key"] = "D"
    songdb.import_songs(conn, [changed], "b.pdf")
    assert songdb.stats(conn)["pending"] == 1
    assert songdb.delete_song(conn, sid)
    assert songdb.stats(conn) == {"songs": 0, "pending": 0, "imports": 2}


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
    changed = _song_v2()
    changed["key"] = "A"
    rep = _upload(client, chord_render.render_pdf([changed])).get_json()["reports"][0]
    assert len(rep["conflicts"]) == 1
    pend = client.get("/db/pending").get_json()["items"]
    assert len(pend) == 1 and pend[0]["new"]["key"] == "A" and pend[0]["existing"]["key"] == "G"
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
