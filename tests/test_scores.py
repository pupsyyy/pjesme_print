"""Testovi: note u PDF-u (prepoznavanje stranica, uvoz uz pjesmu), ZIP uvoz,
oznake zbirki pri uvozu (#pučkepjesme), snimke (YouTube / Google disk) i
neobavezno prepoznavanje nota (OMR, s lažnim Audiverisom).

Testni PDF-ovi s notama crtaju se ovdje (crtovlja kao crte, slika crtovlja
kao sken) — bez pravih nota, jer se ne čita notni zapis nego raspored.
"""

import io
import os
import stat
import time
import zipfile

import pdfplumber
import pytest
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import chord_render
import omr
import scorepages
import songdb
import songimport
import songlinks
import songtags
from test_songdb import MUSICXML, _song_v2, _zip

W, H = A4


def _staves(c, top, systems=2, staves=4, sp=5.0, x0=50, x1=W - 40, lyrics=()):
    """Crtovlja kao vektorske crte (+ tekst ispod prvog crtovlja sustava)."""
    y = H - top
    for _ in range(systems):
        for k in range(staves):
            for i in range(5):
                c.line(x0, y - i * sp, x1, y - i * sp)
            if k == 0:
                c.setFont("Helvetica", 9)
                for j, text in enumerate(lyrics):
                    c.drawString(x0 + 20, y - 4 * sp - 12 - j * 11, text)
            y -= 4 * sp + 50
        y -= 30


def _score_pdf(title="Isus najljepše ime", pages=2, lyrics=("I - sus naj-lje-pše i - me,",
                                                          "Spa-si-telj lju - di"),
               text_below=0):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for p in range(pages):
        if p == 0 and title:
            c.setFont("Helvetica-Bold", 20)
            c.drawCentredString(W / 2, H - 60, title)
            c.setFont("Helvetica", 9)
            c.drawRightString(W - 40, H - 80, "arr.: netko")
        else:
            c.setFont("Helvetica", 9)
            c.drawString(W / 2, H - 30, str(p + 1))
        _staves(c, 120, systems=1 if text_below else 2, lyrics=lyrics)
        if text_below:
            c.setFont("Helvetica", 11)
            y = 380
            for k in range(text_below):
                c.drawString(70, y, f"{k + 2}. Kitica broj {k + 2}, redak teksta pjesme ispod nota")
                y -= 16
        c.showPage()
    c.save()
    return buf.getvalue()


def _raster_score_pdf(skew=0.4):
    img = Image.new("L", (1240, 1754), 255)
    d = ImageDraw.Draw(img)
    y = 300
    for _ in range(6):
        for i in range(5):
            d.line((100, y + i * 11, 1150, y + i * 11), fill=0, width=2)
        for x in range(160, 1100, 90):                 # glave nota preko crta
            d.ellipse((x, y + 8, x + 14, y + 20), fill=0)
        y += 190
    img = img.rotate(skew, fillcolor=255)
    b = io.BytesIO()
    img.save(b, "JPEG", quality=75)
    b.seek(0)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawImage(ImageReader(b), 0, 0, W, H)
    c.showPage()
    c.save()
    return buf.getvalue()


def _ruled_pdf():
    """Bilježnica / tablica: puno jednako razmaknutih crta nije crtovlje."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, H - 60, "Popis pjesama")
    for i in range(40):
        c.line(50, H - 100 - i * 6, W - 50, H - 100 - i * 6)
    c.showPage()
    c.save()
    return buf.getvalue()


def _merge(*pdfs):
    import pypdfium2 as pdfium
    dst = pdfium.PdfDocument.new()
    for data in pdfs:
        src = pdfium.PdfDocument(data)
        dst.import_pages(src)
    out = io.BytesIO()
    dst.save(out)
    return out.getvalue()


def _classify(pdf):
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return [scorepages.classify(p) for p in doc.pages]


# ── Prepoznavanje stranica s notama ──────────────────────────────────────────

def test_classify_vector_raster_mixed_and_text():
    first, second = _classify(_score_pdf())
    assert first["kind"] == "score" and first["staves"] == 8
    assert first["title"] == "Isus najljepše ime"
    assert second["kind"] == "score" and second["title"] == ""        # nastavak (samo broj str.)
    (raster,) = _classify(_raster_score_pdf())
    assert raster["kind"] == "score" and raster["staves"] >= 5
    (mixed,) = _classify(_score_pdf(pages=1, text_below=8))
    assert mixed["kind"] == "mixed"
    assert _classify(chord_render.render_pdf([_song_v2()])) == [None]  # tekst s akordima
    assert _classify(_ruled_pdf()) == [None]


def test_lyric_words_and_groups():
    with pdfplumber.open(io.BytesIO(_score_pdf())) as doc:
        info = scorepages.classify(doc.pages[0])
        words = scorepages.lyric_words(doc.pages[0], info)
    assert "Isus" in words and "najljepše" in words and "Spasitelj" in words
    found = {1: {"kind": "score", "title": "A"}, 2: {"kind": "score", "title": ""},
             3: {"kind": "score", "title": "B"}, 5: {"kind": "mixed", "title": ""}}
    assert [g["pages"] for g in scorepages.group_pages(found)] == [[1, 2], [3], [5]]


def test_title_from_filename():
    assert scorepages.title_from_filename("Isus Najljepše ime - Full Score.pdf") == "Isus Najljepše ime"
    assert scorepages.title_from_filename("948d9a35-Abba_Oče_-_partitura.pdf") == "Abba Oče"
    assert scorepages.title_from_filename("Tebe pjevam SATB sken.pdf") == "Tebe pjevam"


def test_extract_pages():
    pdf = _merge(chord_render.render_pdf([_song_v2()]), _score_pdf())
    part = scorepages.extract_pages(pdf, [2, 3])
    with pdfplumber.open(io.BytesIO(part)) as doc:
        assert len(doc.pages) == 2 and "Isus" in doc.pages[0].extract_text()


# ── Uvoz: note uz pjesmu ──────────────────────────────────────────────────────

@pytest.fixture()
def conn(tmp_path):
    c = songdb.connect(tmp_path / "t.db")
    yield c
    c.close()


def _song(title, *lines):
    return {"title": title, "key": "", "subtitle": "", "meta": [],
            "sections": [{"label": "1.", "lines": [{"text": t, "chords": []} for t in lines]}],
            "layout": {"profile": "v2"}}


def test_score_only_pdf_creates_notes_song_then_text_fills_it(conn):
    rep = songimport.import_upload(conn, "Isus najljepše ime - Full Score.pdf", _score_pdf(),
                                   ["Veliki zbor"])[0]
    (n,) = rep["notes"]
    assert n["status"] == "new_song" and n["pages"] == [1, 2] and n["title"] == "Isus najljepše ime"
    s = songdb.get_song(conn, n["song_id"])
    assert not s["has_text"] and [t["name"] for t in s["tags"]] == ["Veliki zbor"]
    assert s["notes"][0]["kind"] == "pdf" and s["notes"][0]["origin"] == "import"
    assert songdb.stats(conn)["notes_only"] == 1
    # isti PDF ponovo -> note već imamo
    again = songimport.import_upload(conn, "kopija.pdf", _score_pdf())[0]["notes"][0]
    assert again["status"] == "duplicate" and again["song_id"] == n["song_id"]
    # stigne tekst iste pjesme -> ista pjesma dobiva tekst, note ostaju
    text = chord_render.render_pdf([_song("Isus najljepše ime", "Isus najljepše ime, Spasitelj ljudi",
                                          "Isus raspet na križu, Spasitelj ljudi")])
    rep = songimport.import_upload(conn, "tekst.pdf", text)[0]
    assert rep["added"][0]["id"] == n["song_id"] and rep["added"][0]["filled"]
    s = songdb.get_song(conn, n["song_id"])
    assert s["has_text"] and len(s["notes"]) == 1 and songdb.stats(conn)["songs"] == 1


def test_text_and_score_in_one_pdf_and_score_by_lyrics(conn):
    pdf = _merge(chord_render.render_pdf([_song_v2()]), _score_pdf(title=""))
    rep = songimport.import_upload(conn, "aleluja s notama.pdf", pdf)[0]
    sid = rep["added"][0]["id"]
    assert [(n["status"], n["song_id"], n["pages"]) for n in rep["notes"]] == [("attached", sid, [2, 3])]
    note = songdb.list_notes(conn, sid)[0]
    assert note["name"] == "Aleluja (Bože svet si) (str. 2–3)"
    # note bez naslova, ali tekst ispod nota je tekst pjesme iz baze
    songdb.import_songs(conn, [_song("Spasitelj", "I - sus najljepše ime", "Isus najljepše ime Spasitelj ljudi",
                                     "Isus raspet na križu Spasitelj ljudi naš brat i Gospodin")], "s.pdf")
    lyr = ("Isus najljepše ime Spasitelj ljudi", "Isus raspet na križu Spasitelj ljudi naš brat i Gospodin")
    rep = songimport.import_upload(conn, "skenirano_0001.pdf", _score_pdf(title="", lyrics=lyr))[0]
    assert rep["notes"][0]["status"] == "attached" and rep["notes"][0]["song_title"] == "Spasitelj"


def test_notes_title_match_is_strict(conn):
    songdb.import_songs(conn, [_song("Aleluja (Isus Krist je Bog)", "Isus Krist je Bog aleluja")], "a.pdf")
    rep = songimport.import_upload(conn, "x.pdf", _score_pdf(title="Aleluja", lyrics=()))[0]
    assert rep["notes"][0]["status"] == "new_song"           # "Aleluja" nije "Aleluja (...)"


def _zipfile(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_import_folders_note_files_and_errors(conn):
    data = _zipfile({
        "Aleluja/aleluja.pdf": chord_render.render_pdf([_song_v2()]),
        "Aleluja/aleluja zbor.mxl": _zip("score.xml", MUSICXML),
        "Isus najljepše ime/sken.pdf": _raster_score_pdf(),
        "Isus najljepše ime/note.musicxml": MUSICXML,
        "__MACOSX/._aleluja.pdf": b"x",
        "readme.txt": b"hi",
        "lazni.pdf": b"nije pdf",
    })
    reps = songimport.import_upload(conn, "paket.zip", data, ["Pučke pjesme"])
    by = {r["file"]: r for r in reps}
    sid = by["paket.zip/Aleluja/aleluja.pdf"]["added"][0]["id"]
    assert by["paket.zip/Aleluja/aleluja zbor.mxl"]["notes"][0]["song_id"] == sid
    sken = by["paket.zip/Isus najljepše ime/sken.pdf"]["notes"][0]
    assert sken["status"] == "new_song" and sken["song_title"] == "Isus najljepše ime"
    assert by["paket.zip/Isus najljepše ime/note.musicxml"]["notes"][0]["song_id"] == sken["song_id"]
    assert by["paket.zip/lazni.pdf"]["error"] == "Nije PDF datoteka."
    assert "readme.txt" in by["paket.zip"]["warnings"][0]
    for s in songdb.list_songs(conn)["items"]:
        assert [t["name"] for t in s["tags"] if t["name"] == "Pučke pjesme"]
    assert songimport.import_upload(conn, "x.zip", b"PK nije zip")[0]["error"] == "ZIP se ne može otvoriti."
    assert "nema" in songimport.import_upload(conn, "y.zip", _zipfile({"a.txt": b"x"}))[0]["error"]


def test_import_tags_only_for_new_songs_and_hidden_filter(conn):
    songdb.import_songs(conn, [_song("Postojeća", "neki tekst koji već imamo u bazi pjesama")], "a.pdf")
    assert songtags.parse_tag_input("#pučkepjesme #velikizbor") == ["pučkepjesme", "velikizbor"]
    assert songtags.parse_tag_input("Pučke pjesme, Veliki zbor") == ["Pučke pjesme", "Veliki zbor"]
    tags = [songdb.tag_name(conn, t) for t in songtags.parse_tag_input("#pučkepjesme #mojazbirka")]
    assert tags == ["Pučke pjesme", "mojazbirka"]
    rep = songdb.import_songs(conn, [_song("Postojeća", "neki tekst koji već imamo u bazi pjesama"),
                                     _song("Nova", "sasvim drukčiji tekst nove pjesme ovdje")],
                              "b.pdf", tags=tags)
    rep.pop("order")
    assert len(rep["added"]) == 1 and len(rep["duplicates"]) == 1
    names = {s["title"]: [t["name"] for t in s["tags"]] for s in songdb.list_songs(conn)["items"]}
    assert names == {"Postojeća": [], "Nova": ["Pučke pjesme", "mojazbirka"]}
    groups = {t["name"]: t["group"] for t in songdb.list_tags(conn)["tags"]}
    assert groups["Pučke pjesme"] == groups["Veliki zbor"] == groups["mojazbirka"] == "zbirka"
    assert groups["Ulazna"] == ""
    visible = songdb.list_songs(conn, exclude=["#pučkepjesme"])["items"]
    assert [s["title"] for s in visible] == ["Postojeća"]
    assert [s["title"] for s in songdb.list_songs(conn, tag="#mojazbirka")["items"]] == ["Nova"]
    # slična pjesma koja čeka odluku dobiva oznake tek kad se zadrži kao nova
    sim = _song("Drugi naslov", "sasvim drukčiji tekst nove pjesme ovdje", "amen")
    pid = songdb.import_songs(conn, [sim], "c.pdf", tags=["Veliki zbor"])["conflicts"][0]["pending_id"]
    new_id = songdb.resolve(conn, pid, "keep_both")
    assert [t["name"] for t in songdb.get_song(conn, new_id)["tags"]] == ["Veliki zbor"]


def test_move_note_removes_empty_notes_only_song(conn):
    rep = songimport.import_upload(conn, "Neka pjesma.pdf", _score_pdf(title="Neka pjesma"))[0]
    empty_id = rep["notes"][0]["song_id"]
    sid = songdb.import_songs(conn, [_song_v2()], "a.pdf")["added"][0]["id"]
    nid = songdb.list_notes(conn, empty_id)[0]["id"]
    assert songdb.move_note(conn, nid, sid)
    assert songdb.get_song(conn, empty_id) is None and len(songdb.list_notes(conn, sid)) == 1
    assert not songdb.move_note(conn, nid, 999) and not songdb.move_note(conn, 999, sid)


# ── Snimke ────────────────────────────────────────────────────────────────────

def test_links_parse():
    p = songlinks.parse
    assert p("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1m5s")[1:] == (
        "youtube", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?start=65")
    assert p("youtu.be/dQw4w9WgXcQ")[1:] == ("youtube", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ")
    assert p("https://youtube.com/shorts/dQw4w9WgXcQ")[2].endswith("/embed/dQw4w9WgXcQ")
    assert p("https://www.youtube.com/playlist?list=PLabcdef123")[2].endswith("videoseries?list=PLabcdef123")
    assert p("https://drive.google.com/file/d/1AbCdEfGhIjK_lm-n/view?usp=sharing")[1:] == (
        "drive", "https://drive.google.com/file/d/1AbCdEfGhIjK_lm-n/preview")
    assert p("https://drive.google.com/open?id=1AbCdEfGhIjK")[1] == "drive"
    assert p("https://primjer.hr/snimke/sopran.mp3")[1:] == ("audio", "https://primjer.hr/snimke/sopran.mp3")
    assert p("https://primjer.hr/stranica")[1:] == ("link", "")
    for bad in ("", "nije link", "javascript:alert(1)", "https://user:pw@youtube.com/watch?v=dQw4w9WgXcQ",
                "https://www.youtube.com/watch?v=", "https://drive.google.com/drive/folders",
                "https://x.hr/a b", "ftp://x.hr/a.mp3"):
        with pytest.raises(songlinks.LinkError):
            p(bad)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.setattr(omr, "AUDIVERIS", str(tmp_path / "nema-audiverisa"))
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def _post_file(client, data, name, tags="", headers=None):
    return client.post("/db/import", data={"file": (io.BytesIO(data), name), "tags": tags},
                       content_type="multipart/form-data", headers=headers or {})


def test_web_links_import_zip_move_and_exclude(client):
    rep = _post_file(client, _zipfile({"a/aleluja.pdf": chord_render.render_pdf([_song_v2()]),
                                       "b/Nešto drugo.pdf": _score_pdf(title="Nešto drugo")}),
                     "paket.zip", "#velikizbor").get_json()
    # ZIP se uvozi u pozadini: odgovor odmah, napredak preko /db/import-jobs
    assert rep["tags"] == ["Veliki zbor"] and rep["jobs"][0]["status"] in ("queued", "running", "done")
    job = _wait_import(client, rep["jobs"][0]["id"])
    assert job["status"] == "done" and job["done"] == job["total"] == 2
    assert [r["file"] for r in job["reports"]] == ["paket.zip/a/aleluja.pdf", "paket.zip/b/Nešto drugo.pdf"]
    assert client.get("/db/stats").get_json()["notes_only"] == 1
    assert client.get("/db/import-jobs").get_json()["jobs"] == []
    songs = {s["title"]: s for s in client.get("/db/songs").get_json()["items"]}
    sid, other = songs["Aleluja (Bože svet si)"]["id"], songs["Nešto drugo"]["id"]
    assert not songs["Nešto drugo"]["has_text"] and songs["Nešto drugo"]["notes_count"] == 1
    assert client.get("/db/songs?exclude=Veliki zbor").get_json()["total"] == 0
    assert client.get("/api/v1/songs?exclude=velikizbor,x").get_json()["total"] == 0
    # snimke
    r = client.post(f"/db/songs/{sid}/links", json={"url": "https://youtu.be/dQw4w9WgXcQ", "voice": "Sopran"})
    lid = r.get_json()["link"]["id"]
    assert r.status_code == 200 and r.get_json()["link"]["kind"] == "youtube"
    client.post(f"/db/songs/{sid}/links", json={"url": "https://drive.google.com/file/d/1AbCdEfGhIjK/view",
                                                "voice": "Svi glasovi"})
    bad = client.post(f"/db/songs/{sid}/links", json={"url": "nije link"})
    assert bad.status_code == 400 and "link" in bad.get_json()["error"]
    assert client.post("/db/songs/999/links", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).status_code == 404
    song = client.get(f"/api/v1/songs/{sid}").get_json()
    assert [(l["voice"], l["kind"]) for l in song["links"]] == [("Svi glasovi", "drive"), ("Sopran", "youtube")]
    assert client.get("/db/songs").get_json()["items"][0]["links_count"] == 2
    assert client.patch(f"/db/links/{lid}", json={"voice": "Alt", "title": "proba"}).get_json()["ok"]
    assert client.delete(f"/db/links/{lid}").get_json()["ok"]
    assert client.delete(f"/db/links/{lid}").status_code == 404
    assert "links" in json_export(client)[0]
    # premjesti note s pjesme koja ima samo note -> ta pjesma nestaje
    nid = client.get(f"/db/songs/{other}").get_json()["notes"][0]["id"]
    assert client.post(f"/db/notes/{nid}/move", json={"song_id": sid}).get_json()["ok"]
    assert client.get(f"/db/songs/{other}").status_code == 404
    assert client.post(f"/db/notes/{nid}/move", json={"song_id": "x"}).status_code == 400
    # OMR nije instaliran
    assert client.post(f"/db/notes/{nid}/omr").status_code == 503
    assert client.get(f"/db/songs/{sid}/omr").get_json() == {"available": False, "jobs": []}
    page = client.get("/baza").data
    assert b"const OMR = false" in page and b'"Sopran"' in page and b".zip" in page


def _wait_import(client, job_id):
    for _ in range(200):
        job = client.get(f"/db/import-jobs/{job_id}").get_json()["job"]
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    return job


def json_export(client):
    import json
    return json.loads(client.get("/api/v1/export").data)["songs"]


def test_web_new_routes_need_password(client, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "tajna")
    pw = {"X-DB-Password": "tajna"}
    assert _post_file(client, _score_pdf(), "a.pdf").status_code == 401
    _post_file(client, _score_pdf(), "a.pdf", headers=pw)
    sid = client.get("/db/songs").get_json()["items"][0]["id"]
    nid = client.get(f"/db/songs/{sid}").get_json()["notes"][0]["id"]
    assert client.post(f"/db/songs/{sid}/links", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).status_code == 401
    assert client.post(f"/db/notes/{nid}/move", json={"song_id": sid}).status_code == 401
    assert client.post(f"/db/notes/{nid}/omr").status_code == 401
    assert client.delete("/db/links/1").status_code == 401


# ── OMR s lažnim Audiverisom ─────────────────────────────────────────────────

FAKE_AUDIVERIS = """#!/bin/sh
# lažni Audiveris: zapiše MusicXML (.mxl) u mapu iza -output
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-output" ]; then out="$2"; fi
  if [ "$1" = "--" ]; then shift; in="$1"; fi
  shift
done
if [ -n "$FAKE_FAIL" ]; then echo "greska"; exit 1; fi
cp "$FAKE_MXL" "$out/note.mxl"
"""


def test_omr_job_with_fake_audiveris(conn, tmp_path, monkeypatch):
    fake = tmp_path / "Audiveris"
    fake.write_text(FAKE_AUDIVERIS)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    mxl = tmp_path / "rezultat.mxl"
    mxl.write_bytes(_zip("score.xml", MUSICXML))
    monkeypatch.setattr(omr, "AUDIVERIS", str(fake))
    monkeypatch.setenv("FAKE_MXL", str(mxl))
    assert omr.available()
    rep = songimport.import_upload(conn, "Isus.pdf", _score_pdf())[0]
    sid = rep["notes"][0]["song_id"]
    nid = songdb.list_notes(conn, sid)[0]["id"]
    job = omr.enqueue(conn, nid)
    assert omr.enqueue(conn, nid)["id"] == job["id"] or omr.get_job(conn, job["id"])["status"] == "done"
    for _ in range(100):
        job = omr.get_job(conn, job["id"])
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    notes = songdb.list_notes(conn, sid)
    assert [(n["kind"], n["origin"]) for n in notes] == [("pdf", "import"), ("musicxml", "omr")]
    assert notes[1]["name"].endswith("(prepoznato)") and job["result_note_id"] == notes[1]["id"]
    assert omr.jobs_for_song(conn, sid)[0]["status"] == "done"
    with pytest.raises(omr.OmrError):
        omr.enqueue(conn, notes[1]["id"])                    # MusicXML se ne prepoznaje
    # neuspjeh Audiverisa -> razumljiva poruka
    monkeypatch.setenv("FAKE_FAIL", "1")
    with pytest.raises(omr.OmrError, match="nije prepoznao"):
        omr.run_audiveris(_write(tmp_path / "x.pdf", b"%PDF"), ".pdf")


def _write(path, data):
    path.write_bytes(data)
    return path


def test_omr_unavailable(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(omr, "AUDIVERIS", str(tmp_path / "nema"))
    assert not omr.available()
    with pytest.raises(omr.OmrError):
        omr.enqueue(conn, 1)
    assert os.path.basename(omr.AUDIVERIS) == "nema"


def test_engraved_fixture_and_audiveris_input_size(tmp_path):
    """Note iz programa za note (Verovio: crtovlja kao putanje, A4 povećan)."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "ljestvica.pdf")
    data = open(path, "rb").read()
    (info,) = _classify(data)
    assert info["kind"] == "score" and info["title"] == "Ljestvica za test"
    # veća stranica od A4 -> manja rezolucija, da Audiveris ne odbije sliku
    dpi = omr.pdf_dpi(path)
    assert 200 < dpi < 300
    with pdfplumber.open(path) as doc:
        p = doc.pages[0]
        assert (p.width / 72 * dpi) * (p.height / 72 * dpi) <= omr.MAX_PIXELS
    big = tmp_path / "foto.webp"
    Image.new("RGB", (6000, 4000), "white").save(big, "WEBP")
    small = tmp_path / "foto.png"
    omr._shrink_image(big, small, convert=True)
    with Image.open(small) as im:
        assert im.format == "PNG" and im.width * im.height <= omr.MAX_PIXELS


# ── Regresije iz pregleda koda ────────────────────────────────────────────────

def test_short_different_song_with_contained_title_asks(conn):
    songdb.import_songs(conn, [_song("Isus", "Isus je Gospodin moj i Bog nad svima",
                                     "on je put istina i život vječni", "slavimo ga svi zajedno danas",
                                     "pjevajmo mu novu pjesmu radosno")], "a.pdf")
    r = songdb.import_songs(conn, [_song("Isus je Gospodin", "Isus je Gospodin moj i Bog")], "b.pdf")
    assert not r["duplicates"] and r["conflicts"][0]["reason"] == "similar"


def test_split_gap_tolerates_rounded_coordinates():
    import chord_extract
    import opensans
    sw, hw = opensans.SPACE * 12, chord_extract.HYPHEN_W * 12
    assert chord_extract._split_gap(2 * sw + hw, sw, hw, False, True) == (2, 1)
    assert chord_extract._split_gap(2 * sw + hw + 0.05, sw, hw, False, True) == (2, 1)
    assert chord_extract._split_gap(3 * sw - 0.04, sw, hw, False, True) == (3, 0)


def test_untitled_text_with_score_imports_only_notes(conn):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    for i in range(6):
        c.drawString(60, H - 80 - i * 14, f"zahvala zboru i svima koji su pomogli, redak {i}")
    c.showPage()
    c.save()
    pdf = _merge(buf.getvalue(), _score_pdf(title="Moja pjesma"))
    rep = songimport.import_upload(conn, "x.pdf", pdf)[0]
    assert not rep["added"] and rep["notes"][0]["song_title"] == "Moja pjesma"
    assert [s["title"] for s in songdb.list_songs(conn)["items"]] == ["Moja pjesma"]


def test_note_extraction_failure_is_reported_not_raised(conn, monkeypatch):
    def boom(*_a):
        raise RuntimeError("pdfium")
    monkeypatch.setattr(scorepages, "extract_pages", boom)
    pdf = _merge(chord_render.render_pdf([_song_v2()]), _score_pdf(title="Druga"))
    rep = songimport.import_upload(conn, "x.pdf", pdf)[0]
    assert rep["added"] and rep["notes"][0]["status"] == "error"
    assert songdb.stats(conn)["songs"] == 1                  # nema prazne pjesme


def _fake_audiveris(tmp_path, monkeypatch):
    fake = tmp_path / "Audiveris"
    fake.write_text(FAKE_AUDIVERIS)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    mxl = tmp_path / "rezultat.mxl"
    mxl.write_bytes(_zip("score.xml", MUSICXML))
    monkeypatch.setattr(omr, "AUDIVERIS", str(fake))
    monkeypatch.setenv("FAKE_MXL", str(mxl))


def _wait(conn, job_id):
    for _ in range(100):
        job = omr.get_job(conn, job_id)
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    return job


def test_omr_resume_after_restart_and_job_crash(conn, tmp_path, monkeypatch):
    _fake_audiveris(tmp_path, monkeypatch)
    rep = songimport.import_upload(conn, "a.pdf", _score_pdf(title="Prva"))[0]
    nid = songdb.list_notes(conn, rep["notes"][0]["song_id"])[0]["id"]
    # posao je "radio" kad se kontejner srušio
    jid = conn.execute("INSERT INTO omr_jobs (note_id, status, created_at, started_at)"
                       " VALUES (?, 'running', '2026-01-01', '2099-01-01')", (nid,)).lastrowid
    omr._resumed.discard(str(conn.db_file))
    omr.resume(conn.db_file)
    assert _wait(conn, jid)["status"] == "done"
    # neočekivana greška u jednom poslu ne zaustavlja ostale
    rep2 = songimport.import_upload(conn, "b.pdf", _score_pdf(title="Druga", lyrics=("x",)))[0]
    nid2 = songdb.list_notes(conn, rep2["notes"][0]["song_id"])[0]["id"]
    real = omr._run
    calls = []

    def flaky(c, job):
        calls.append(job["id"])
        if len(calls) == 1:
            raise RuntimeError("baza zaključana")
        return real(c, job)
    monkeypatch.setattr(omr, "_run", flaky)
    j1 = conn.execute("INSERT INTO omr_jobs (note_id, created_at) VALUES (?, 'x')", (nid,)).lastrowid
    j2 = omr.enqueue(conn, nid2)["id"]
    assert _wait(conn, j1)["status"] == "error" and "Neočekivana" in omr.get_job(conn, j1)["message"]
    assert _wait(conn, j2)["status"] == "done"
