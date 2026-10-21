"""Testovi: transpozicija akorda, uređivanje pjesama (s verzijama), popis za
misu (PDF, javni link) i automatski backup."""

import io
import json

import pytest

import chord_render
import songdb
import transpose
from test_songdb import _song_v2

T = transpose.transpose_song


def _one(chords, key="", n=1):
    song = {"key": key, "sections": [{"lines": [{"text": "x", "chords": [[0, c] for c in chords]}]}]}
    out = T(song, n)
    return out["key"], [c[1] for c in out["sections"][0]["lines"][0]["chords"]]


# ── Transpozicija ─────────────────────────────────────────────────────────────

def test_transpose_croatian_notation():
    # H = h-dur, B = B♭, malo slovo = mol, spojeni akordi iz Worda
    assert _one(["A", "H", "E", "fis", "AHE", "FGa", "(D", "E)", "Esus4"], "A", 1) == (
        "B", ["B", "C", "F", "g", "BCF", "GesAsb", "(Es", "F)", "Fsus4"])
    assert _one(["G", "C", "D", "e", "h", "D/FIS*"], "G", 2) == (
        "A", ["A", "D", "E", "f#", "c#", "E/GIS*"])
    assert _one(["Fis", "Cis", "h"], "D", 1) == ("Es", ["G", "D", "c"])


def test_transpose_english_notation_and_flats():
    assert _one(["Am", "F", "C", "G", "D/F#", "Bm", "F#"], "Am", 3) == (
        "Cm", ["Cm", "Ab", "Eb", "Bb", "F/A", "Dm", "A"])
    assert _one(["Bb", "Eb", "F", "Gm", "Bbmaj7"], "Bb", 2) == ("C", ["C", "F", "G", "Am", "Cmaj7"])
    assert _one(["Gadd9", "Cmaj7", "Dsus4", "Em7"], "G", 5) == ("C", ["Cadd9", "Fmaj7", "Gsus4", "Am7"])
    assert _one(["G"], "G", 12) == ("G", ["G"]) and _one(["G"], "G", -7) == ("C", ["C"])


def test_transpose_song_keeps_positions_and_mono_row():
    song = _song_v2()
    up = T(song, 2)
    assert up["key"] == "A"
    for a, b in zip(song["sections"], up["sections"]):
        for la, lb in zip(a["lines"], b["lines"]):
            assert [c[0] for c in la["chords"]] == [c[0] for c in lb["chords"]]
            assert la["text"] == lb["text"]
    mono = up["sections"][1]["lines"][2]["fmt"]["chord_text"]
    assert mono.split() == ["E/GIS*", "A", "c#", "H4"]
    assert T(song, 0) == song and song["key"] == "G"            # izvornik netaknut
    opts = transpose.options(song)
    assert [o["n"] for o in opts] == list(range(-5, 7))
    assert {o["n"]: o["key"] for o in opts}[2] == "A"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.setenv("BACKUP_DISABLE", "1")
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def _upload(client, pdf, name="pjesma.pdf"):
    return client.post("/db/import", data={"file": (io.BytesIO(pdf), name)},
                       content_type="multipart/form-data")


def test_web_transpose(client):
    _upload(client, chord_render.render_pdf([_song_v2()]))
    sid = client.get("/db/songs").get_json()["items"][0]["id"]
    s = client.get(f"/db/songs/{sid}?transpose=2").get_json()
    assert s["key"] == "A" and s["transpose"] == 2 and "[A]" in s["chordpro"]
    assert s["sections"][0]["lines"][0]["chords"] == [[5, "A"], [16, "D"]]
    assert client.get(f"/api/v1/songs/{sid}?transpose=-2").get_json()["key"] == "F"
    pdf = client.get(f"/db/songs/{sid}/pdf?transpose=2")
    assert pdf.status_code == 200 and "_A.pdf" in pdf.headers["Content-Disposition"]
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf.data)) as doc:
        assert "Key: A" in doc.pages[0].extract_text()
    assert "[A]" in client.get(f"/db/songs/{sid}/chordpro?transpose=2").get_data(as_text=True)
    ed = client.post("/db/to-editor", json={"ids": [sid], "transpose": {str(sid): 2}}).get_json()
    assert ed["songs"][0]["key"] == "Key: A"
    assert client.get(f"/db/songs/{sid}?transpose=x").get_json()["transpose"] == 0


# ── Uređivanje pjesme ─────────────────────────────────────────────────────────

def test_edit_text_roundtrip_is_identical():
    import songedit
    from test_songdb import _song_v1
    for song in (_song_v2(), _song_v1()):
        body, meta = songedit.to_text(song)
        assert songedit.apply(song, song["title"], song["subtitle"], song["key"], body, meta) == song


def test_edit_parse_sections_chords_and_compound():
    import songedit
    secs = songedit.parse_text("[Refren]\n  G     DG\nSlava Tebi\n\nC  a\n\nAHE  7\nSveti, sveti\n"
                               "Kitica 2\nobičan redak\n")
    assert secs[0] == {"label": "Refren", "lines": [{"text": "Slava Tebi", "chords": [[2, "G"], [8, "DG"]]}]}
    assert secs[1]["lines"] == [{"text": "", "chords": [[0, "C"], [3, "a"]]}]
    assert secs[2]["lines"] == [{"text": "Sveti, sveti", "chords": [[0, "AHE"], [5, "7"]]}]
    assert secs[3] == {"label": "Kitica 2", "lines": [{"text": "običan redak", "chords": []}]}
    with pytest.raises(songedit.EditError):
        songedit.apply(_song_v2(), "  ", "", "", "x", "")


def test_web_edit_versions_restore(client):
    _upload(client, chord_render.render_pdf([_song_v2()]))
    sid = client.get("/db/songs").get_json()["items"][0]["id"]
    f = client.get(f"/db/songs/{sid}/edit").get_json()
    assert f["title"] == "Aleluja (Bože svet si)" and f["meta"] == "Pjesmarica: 203"
    assert "[Verse 1]" in f["body"] and "     G          C\nBože svet si aleluja," in f["body"]
    before = client.get(f"/db/songs/{sid}").get_json()
    same = client.put(f"/db/songs/{sid}", json=f).get_json()
    assert same["ok"] and not same["changed"]
    body = f["body"].replace("Bože svet si aleluja,", "Bože, svet si, aleluja!")
    r = client.put(f"/db/songs/{sid}", json=dict(f, title="Aleluja", key="A", body=body))
    s = r.get_json()["song"]
    assert r.get_json()["changed"] and s["title"] == "Aleluja" and s["key"] == "A"
    assert s["sections"][0]["lines"][0]["text"] == "Bože, svet si, aleluja!"
    assert s["sections"][1:] == before["sections"][1:]          # ostalo netaknuto (i fmt)
    assert client.get("/db/songs?q=bože svet si").get_json()["total"] == 1   # pretraga po novom tekstu
    prev = client.post(f"/db/songs/{sid}/preview", json={"body": "G\nx"}).get_json()
    assert prev["sections"][0]["lines"] == [{"text": "x", "chords": [[0, "G"]]}]
    vers = client.get(f"/db/songs/{sid}/versions").get_json()["versions"]
    assert len(vers) == 1 and vers[0]["title"] == "Aleluja (Bože svet si)"
    assert client.post(f"/db/songs/{sid}/versions/{vers[0]['id']}/restore").get_json()["ok"]
    assert client.get(f"/db/songs/{sid}").get_json()["title"] == "Aleluja (Bože svet si)"
    assert len(client.get(f"/db/songs/{sid}/versions").get_json()["versions"]) == 2
    assert client.put(f"/db/songs/{sid}", json=dict(f, title="")).status_code == 400
    assert client.put("/db/songs/999", json=f).status_code == 404


def test_web_edit_needs_password(client, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "tajna")
    client.post("/db/import", data={"file": (io.BytesIO(chord_render.render_pdf([_song_v2()])), "a.pdf")},
                content_type="multipart/form-data", headers={"X-DB-Password": "tajna"})
    sid = client.get("/db/songs").get_json()["items"][0]["id"]
    f = client.get(f"/db/songs/{sid}/edit").get_json()
    assert client.put(f"/db/songs/{sid}", json=f).status_code == 401
    assert client.post(f"/db/songs/{sid}/versions/1/restore").status_code == 401
