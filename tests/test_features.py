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
