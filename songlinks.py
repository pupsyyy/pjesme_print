#!/usr/bin/env python3
"""
Snimke uz pjesmu: linkovi na YouTube (npr. po glasovima — sopran, alt, tenor,
bas) ili na audio datoteku na Google disku. U bazi je samo link; ništa se ne
preuzima. Za prikaz na stranici računa se adresa ugrađenog playera.
"""

import re
from urllib.parse import parse_qs, urlsplit

VOICES = ["Svi glasovi", "Sopran", "Alt", "Tenor", "Bas", "Matrica", "Solo"]
AUDIO_EXT = (".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".flac", ".aac")
MAX_URL = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS song_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id     INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    voice       TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL,
    kind        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS song_links_song ON song_links(song_id);
"""

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
             "youtube-nocookie.com", "www.youtube-nocookie.com"}
_DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
_ID = re.compile(r"^[A-Za-z0-9_-]{6,80}$")


class LinkError(ValueError):
    """Neispravan link (poruka je za korisnika)."""


def _seconds(t):
    """'1m30s' / '90' / '90s' -> 90."""
    if not t:
        return 0
    if t.isdigit():
        return int(t)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if not m:
        return 0
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


def parse(url):
    """Provjeri link. Vrati (čisti url, vrsta, ugradbeni url ili "").

    vrsta: youtube | drive | audio | link"""
    url = (url or "").strip()
    if not url:
        raise LinkError("Upiši link.")
    if len(url) > MAX_URL:
        raise LinkError("Link je predug.")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    try:
        u = urlsplit(url)
    except ValueError as exc:
        raise LinkError("Neispravan link.") from exc
    host = (u.hostname or "").lower()
    if u.scheme.lower() not in ("http", "https") or not host or "." not in host:
        raise LinkError("To nije ispravan link — zalijepi cijelu adresu "
                        "(YouTube, Google disk ili audio datoteka).")
    if u.username or u.password or re.search(r"[\s<>\"']", url):
        raise LinkError("Neispravan link.")
    q = parse_qs(u.query)

    if host in _YT_HOSTS or host == "youtu.be":
        vid = ""
        if host == "youtu.be":
            vid = u.path.strip("/").split("/")[0]
        elif u.path == "/watch":
            vid = (q.get("v") or [""])[0]
        else:
            m = re.match(r"^/(?:shorts|embed|live|v)/([^/?#]+)", u.path)
            vid = m.group(1) if m else ""
        start = _seconds((q.get("t") or q.get("start") or [""])[0])
        lst = (q.get("list") or [""])[0]
        if vid and _ID.match(vid):
            embed = f"https://www.youtube-nocookie.com/embed/{vid}"
            if start:
                embed += f"?start={start}"
            return url, "youtube", embed
        if lst and _ID.match(lst):
            return url, "youtube", f"https://www.youtube-nocookie.com/embed/videoseries?list={lst}"
        raise LinkError("Ne prepoznajem YouTube video u tom linku.")

    if host in _DRIVE_HOSTS:
        m = re.match(r"^/file/d/([^/?#]+)", u.path)
        fid = m.group(1) if m else (q.get("id") or [""])[0]
        if fid and _ID.match(fid):
            return url, "drive", f"https://drive.google.com/file/d/{fid}/preview"
        raise LinkError("Ne prepoznajem datoteku u linku s Google diska "
                        "(treba link oblika drive.google.com/file/d/…).")

    if u.path.lower().endswith(AUDIO_EXT):
        return url, "audio", url
    return url, "link", ""


def clean_text(text, limit=60):
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", text or "").split())[:limit]


def voice_order(voice):
    try:
        return VOICES.index(voice)
    except ValueError:
        return len(VOICES)
