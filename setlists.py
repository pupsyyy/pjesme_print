#!/usr/bin/env python3
"""
Popis za misu (ili bilo koji popis pjesama): pjesme po dijelovima mise
(Ulazna, Gospodine, Slava, Psalam, Aleluja, Prinos, Svet, Jaganjče Božji,
Pričest, Zahvala, Završna), svaka s vlastitom transpozicijom.

Iz popisa: PDF (naslovnica s popisom + pjesme u izvornom izgledu), tekst za
poruku (WhatsApp), pjesmarica u uredniku i JAVNI LINK za zbor (/p/<token>) —
stranica za mobitel s pjesmama, akordima, snimkama i notama, bez lozinke.
"""

import html
import io
import json
import secrets
from datetime import datetime, timezone

import chord_render
import opensans
import songdb
import transpose

PARTS = ["Ulazna", "Gospodine", "Slava", "Psalam", "Aleluja", "Prinos", "Svet",
         "Jaganjče Božji", "Pričest", "Zahvala", "Završna"]
# oznaka pjesme koja se predlaže za dio mise
PART_TAG = {"Ulazna": "Ulazna", "Gospodine": "Gospodine", "Slava": "Gospodine",
            "Psalam": "Psalam", "Aleluja": "Aleluja", "Prinos": "Prinos", "Svet": "Gospodine",
            "Jaganjče Božji": "Gospodine", "Pričest": "Pričest", "Zahvala": "Klanjanje",
            "Završna": "Završna"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS setlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    date        TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    token       TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setlist_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    setlist_id  INTEGER NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    part        TEXT NOT NULL DEFAULT '',
    song_id     INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    transpose   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS setlist_items_list ON setlist_items(setlist_id);
"""


class SetlistError(ValueError):
    """Poruka za korisnika."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean(text, limit):
    return " ".join((text or "").split())[:limit]


def part_order(part):
    return PARTS.index(part) if part in PARTS else (len(PARTS) if part else len(PARTS) + 1)


# ── Popisi ────────────────────────────────────────────────────────────────────

def create(conn, title="", date=""):
    title = _clean(title, 120) or "Misa"
    now = _now()
    return conn.execute(
        "INSERT INTO setlists (title, date, token, created_at, updated_at) VALUES (?,?,?,?,?)",
        (title, _clean(date, 20), secrets.token_urlsafe(12), now, now)).lastrowid


def list_setlists(conn):
    return [dict(r) for r in conn.execute(
        "SELECT s.id, s.title, s.date, s.updated_at, COUNT(i.id) AS count FROM setlists s"
        " LEFT JOIN setlist_items i ON i.setlist_id = s.id GROUP BY s.id"
        " ORDER BY s.date DESC, s.id DESC")]


def _touch(conn, setlist_id):
    conn.execute("UPDATE setlists SET updated_at=? WHERE id=?", (_now(), setlist_id))


def update(conn, setlist_id, title=None, date=None, note=None):
    r = conn.execute("SELECT * FROM setlists WHERE id=?", (setlist_id,)).fetchone()
    if r is None:
        return False
    conn.execute("UPDATE setlists SET title=?, date=?, note=?, updated_at=? WHERE id=?",
                 (_clean(title, 120) or r["title"] if title is not None else r["title"],
                  _clean(date, 20) if date is not None else r["date"],
                  (note or "")[:1000] if note is not None else r["note"], _now(), setlist_id))
    return True


def delete(conn, setlist_id):
    return conn.execute("DELETE FROM setlists WHERE id=?", (setlist_id,)).rowcount > 0


def new_token(conn, setlist_id):
    """Novi javni link (stari prestaje vrijediti)."""
    tok = secrets.token_urlsafe(12)
    ok = conn.execute("UPDATE setlists SET token=? WHERE id=?", (tok, setlist_id)).rowcount
    return tok if ok else None


def get(conn, setlist_id=None, token=None):
    if token is not None:
        r = conn.execute("SELECT * FROM setlists WHERE token=?", (token,)).fetchone()
    else:
        r = conn.execute("SELECT * FROM setlists WHERE id=?", (setlist_id,)).fetchone()
    if r is None:
        return None
    out = {k: r[k] for k in ("id", "title", "date", "note", "token", "updated_at")}
    items = []
    for it in conn.execute(
            "SELECT i.*, s.title, s.song_key, s.data, s.lyrics FROM setlist_items i"
            " JOIN songs s ON s.id = i.song_id WHERE i.setlist_id=?", (r["id"],)):
        data = json.loads(it["data"])
        items.append({"id": it["id"], "part": it["part"], "song_id": it["song_id"],
                      "title": it["title"], "transpose": it["transpose"],
                      "key": transpose.key_after(data, it["transpose"]) if it["transpose"]
                      else it["song_key"],
                      "position": it["position"],
                      "has_text": bool(it["lyrics"]) or bool(data.get("sections")),
                      "options": transpose.options(data) if data.get("sections") else []})
    items.sort(key=lambda x: (part_order(x["part"]), x["position"], x["id"]))
    out["items"] = items
    return out


# ── Stavke ────────────────────────────────────────────────────────────────────

def add_item(conn, setlist_id, song_id, part="", transpose_by=0):
    if conn.execute("SELECT 1 FROM setlists WHERE id=?", (setlist_id,)).fetchone() is None:
        return None
    if conn.execute("SELECT 1 FROM songs WHERE id=?", (song_id,)).fetchone() is None:
        raise SetlistError("Pjesma ne postoji.")
    pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM setlist_items WHERE setlist_id=?",
                       (setlist_id,)).fetchone()[0]
    iid = conn.execute(
        "INSERT INTO setlist_items (setlist_id, position, part, song_id, transpose) VALUES (?,?,?,?,?)",
        (setlist_id, pos, _clean(part, 40), song_id, transpose.normalize(transpose_by))).lastrowid
    _touch(conn, setlist_id)
    return iid


def update_item(conn, item_id, part=None, transpose_by=None):
    r = conn.execute("SELECT * FROM setlist_items WHERE id=?", (item_id,)).fetchone()
    if r is None:
        return False
    conn.execute("UPDATE setlist_items SET part=?, transpose=? WHERE id=?",
                 (_clean(part, 40) if part is not None else r["part"],
                  transpose.normalize(transpose_by) if transpose_by is not None else r["transpose"],
                  item_id))
    _touch(conn, r["setlist_id"])
    return True


def move_item(conn, item_id, step):
    """Pomakni stavku gore (-1) / dolje (+1) unutar istog dijela mise."""
    r = conn.execute("SELECT * FROM setlist_items WHERE id=?", (item_id,)).fetchone()
    if r is None:
        return False
    same = [x for x in get(conn, r["setlist_id"])["items"] if x["part"] == r["part"]]
    idx = next(i for i, x in enumerate(same) if x["id"] == item_id)
    j = idx + (1 if step > 0 else -1)
    if 0 <= j < len(same):
        a, b = same[idx], same[j]
        conn.execute("UPDATE setlist_items SET position=? WHERE id=?", (b["position"], a["id"]))
        conn.execute("UPDATE setlist_items SET position=? WHERE id=?", (a["position"], b["id"]))
        if a["position"] == b["position"]:
            conn.execute("UPDATE setlist_items SET position=position+1 WHERE id=?",
                         (b["id"] if step < 0 else a["id"],))
    _touch(conn, r["setlist_id"])
    return True


def remove_item(conn, item_id):
    r = conn.execute("SELECT setlist_id FROM setlist_items WHERE id=?", (item_id,)).fetchone()
    if r is None:
        return False
    conn.execute("DELETE FROM setlist_items WHERE id=?", (item_id,))
    _touch(conn, r["setlist_id"])
    return True


def suggestions(conn, part, limit=40):
    """Pjesme za dio mise: najprije one koje su se već pjevale za taj dio,
    pa one s odgovarajućom oznakom (potvrđene prije prijedloga)."""
    used = {r[0]: r[1] for r in conn.execute(
        "SELECT song_id, COUNT(*) FROM setlist_items WHERE part=? GROUP BY song_id", (part,))}
    tag = PART_TAG.get(part)
    tagged = {}
    if tag:
        for r in conn.execute(
                "SELECT st.song_id, st.source FROM song_tags st JOIN tags t ON t.id = st.tag_id"
                " WHERE t.name = ? COLLATE NOCASE", (tag,)):
            tagged[r[0]] = r[1]
    ids = set(used) | set(tagged)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, title, subtitle, song_key FROM songs WHERE id IN ({marks})",
                        list(ids)).fetchall()
    out = [{"id": r["id"], "title": r["title"], "subtitle": r["subtitle"], "key": r["song_key"],
            "used": used.get(r["id"], 0), "tag": tagged.get(r["id"])} for r in rows]
    out.sort(key=lambda x: (-x["used"], x["tag"] != "user", x["title"].casefold()))
    return out[:limit]


# ── Izlaz ─────────────────────────────────────────────────────────────────────

def songs(conn, setlist):
    """[(stavka, podaci pjesme transponirani)] redom popisa (samo pjesme s tekstom)."""
    out = []
    for it in setlist["items"]:
        data = songdb.song_data(conn, it["song_id"], it["transpose"])
        if data and data.get("sections"):
            out.append((it, data))
    return out


def as_text(setlist):
    """Popis za poruku: 'Ulazna: Pjesma (G)'."""
    head = setlist["title"] + (f" — {_hr_date(setlist['date'])}" if setlist["date"] else "")
    lines = [head]
    for it in setlist["items"]:
        lines.append(f"{it['part'] or '•'}: {it['title']}" + (f" ({it['key']})" if it["key"] else ""))
    return "\n".join(lines)


def _hr_date(d):
    try:
        y, m, dd = d.split("-")
        return f"{int(dd)}. {int(m)}. {y}."
    except ValueError:
        return d


def cover_pdf(setlist):
    """Naslovnica: naslov, datum i popis pjesama po dijelovima mise."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    reg, bold = opensans.register()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    y = H - 80
    c.setFont(bold, 22)
    c.drawString(60, y, setlist["title"])
    y -= 26
    if setlist["date"]:
        c.setFont(reg, 13)
        c.drawString(60, y, _hr_date(setlist["date"]))
        y -= 22
    y -= 14
    for it in setlist["items"]:
        if y < 70:
            c.showPage()
            y = H - 70
        c.setFont(bold, 12)
        c.drawString(60, y, it["part"] or "•")
        c.setFont(reg, 12)
        c.drawString(185, y, it["title"][:60] + (f"   ({it['key']})" if it["key"] else ""))
        y -= 21
    if setlist.get("note"):
        y -= 10
        c.setFont(reg, 10)
        for row in setlist["note"].splitlines()[:12]:
            c.drawString(60, y, row[:100])
            y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()


def pdf(conn, setlist):
    """Naslovnica + pjesme u izvornom izgledu (transponirane kako je zadano)."""
    import pypdfium2 as pdfium

    cover = cover_pdf(setlist)
    items = songs(conn, setlist)
    if not items:
        return cover
    body = chord_render.render_pdf([d for _it, d in items])
    dst = pdfium.PdfDocument.new()
    for data in (cover, body):
        src = pdfium.PdfDocument(data)
        dst.import_pages(src)
    out = io.BytesIO()
    dst.save(out)
    return out.getvalue()


# ── Javna stranica za zbor (/p/<token>) ───────────────────────────────────────

def _line_html(ln):
    text = ln.get("text") or ""
    chords = sorted(ln.get("chords") or [], key=lambda c: c[0])
    esc = html.escape
    if not chords:
        return f'<div class="ln">{esc(text) or "&nbsp;"}</div>'
    if not text.strip():
        s = ""
        for col, ch in chords:
            s += " " * max(col - len(s), 1 if s else 0) + ch
        return f'<div class="ln co">{esc(s)}</div>'
    parts = []
    first = min(chords[0][0], len(text))
    if first:
        parts.append(f'<span class="sg"><b></b><i>{esc(text[:first])}</i></span>')
    for k, (i, ch) in enumerate(chords):
        a = min(i, len(text))
        b = min(chords[k + 1][0], len(text)) if k + 1 < len(chords) else len(text)
        parts.append(f'<span class="sg"><b>{esc(ch)}</b><i>{esc(text[a:max(a, b)])}</i></span>')
    return f'<div class="ln">{"".join(parts)}</div>'


def public_html(conn, setlist, base):
    """Stranica popisa za mobitel (bez lozinke, samo čitanje)."""
    esc = html.escape
    toc, body = [], []
    for n, it in enumerate(setlist["items"], start=1):
        song = songdb.get_song(conn, it["song_id"], it["transpose"])
        if song is None:
            continue
        anchor = f"p{n}"
        toc.append(f'<li><a href="#{anchor}"><span>{esc(it["part"] or "•")}</span>'
                   f'{esc(it["title"])}{" (" + esc(it["key"]) + ")" if it["key"] else ""}</a></li>')
        sec = [f'<section id="{anchor}"><div class="part">{esc(it["part"] or "")}</div>'
               f'<h2>{esc(song["title"])}</h2>']
        if song["subtitle"]:
            sec.append(f'<div class="sub">{esc(song["subtitle"])}</div>')
        if song["key"]:
            sec.append(f'<div class="sub">Tonalitet: {esc(song["key"])}</div>')
        extras = []
        for lk in song.get("links") or []:
            extras.append(f'<a class="chip" href="{esc(lk["url"])}" target="_blank" rel="noopener noreferrer">'
                          f'🎧 {esc(lk["voice"] or "Snimka")}</a>')
        for nt in song.get("notes") or []:
            extras.append(f'<a class="chip" href="{esc(base)}/notes/{nt["id"]}" target="_blank">'
                          f'🎼 {esc(nt["name"])}</a>')
        if extras:
            sec.append('<div class="chips">' + "".join(extras) + "</div>")
        for s in song.get("sections") or []:
            sec.append('<div class="sec">')
            if s.get("label"):
                sec.append(f'<div class="lab">{esc(s["label"])}</div>')
            sec.extend(_line_html(ln) for ln in s.get("lines") or [])
            sec.append("</div>")
        if not song.get("sections"):
            sec.append('<p class="sub">(samo note)</p>')
        sec.append('<a class="top" href="#top">↑ popis</a></section>')
        body.append("".join(sec))
    date = f'<div class="sub">{esc(_hr_date(setlist["date"]))}</div>' if setlist["date"] else ""
    note = f'<p class="note">{esc(setlist["note"])}</p>' if setlist.get("note") else ""
    return PUBLIC_PAGE.format(title=esc(setlist["title"]), date=date, note=note,
                              toc="".join(toc), body="".join(body), pdf=esc(base) + "/pdf")


PUBLIC_PAGE = """<!doctype html>
<html lang="hr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>
 :root {{ --bg:#faf9f5; --fg:#141413; --mut:#6b6862; --acc:#c56144; --line:#e8e6dc; --card:#fff; }}
 @media (prefers-color-scheme: dark) {{ :root {{ --bg:#141413; --fg:#faf9f5; --mut:#b0aea5; --acc:#e0895f;
   --line:#35332e; --card:#1e1d1b; }} }}
 body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.4 "Open Sans",system-ui,sans-serif; }}
 main {{ max-width:760px; margin:0 auto; padding:16px; }}
 h1 {{ margin:.2em 0 0; font-size:1.5rem; }} h2 {{ margin:.1em 0; font-size:1.25rem; }}
 .sub {{ color:var(--mut); font-size:.9rem; }}
 ol {{ list-style:none; padding:0; margin:1rem 0; }} ol li a {{ display:flex; gap:.6rem; padding:.55rem .2rem;
   border-bottom:1px solid var(--line); color:var(--fg); text-decoration:none; }}
 ol li span {{ color:var(--acc); font-weight:600; min-width:7.5rem; }}
 section {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px;
   margin:18px 0; overflow-x:auto; }}
 .part {{ color:var(--acc); font-weight:700; text-transform:uppercase; font-size:.8rem; letter-spacing:.05em; }}
 .lab {{ font-weight:700; margin-top:.8em; }} .sec {{ margin-bottom:.6em; }}
 .ln {{ white-space:pre; min-height:1.4em; }} .co {{ color:var(--acc); font-weight:700; }}
 .sg {{ display:inline-flex; flex-direction:column; vertical-align:bottom; white-space:pre; }}
 .sg b {{ color:var(--acc); min-height:1.3em; padding-right:.3em; }} .sg i {{ font-style:normal; }}
 .chips {{ display:flex; flex-wrap:wrap; gap:.4rem; margin:.5rem 0; }}
 .chip {{ border:1px solid var(--line); border-radius:999px; padding:.2rem .7rem; color:var(--fg);
   text-decoration:none; font-size:.85rem; }}
 .top {{ display:inline-block; margin-top:.5rem; color:var(--mut); font-size:.85rem; }}
 .btn {{ display:inline-block; margin-top:.4rem; padding:.45rem .9rem; border-radius:10px; background:var(--acc);
   color:#fff; text-decoration:none; font-weight:600; }}
 .note {{ white-space:pre-wrap; }}
</style></head>
<body><main id="top">
<h1>{title}</h1>{date}{note}
<a class="btn" href="{pdf}">⬇ PDF (s akordima)</a>
<ol>{toc}</ol>
{body}
</main></body></html>"""
