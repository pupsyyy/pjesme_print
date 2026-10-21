#!/usr/bin/env python3
"""
Čitanje MusicXML nota (.musicxml / .xml / .mxl) — za:
  * tekst pjesme iz nota: kitice iz teksta ispod nota (slogovi spojeni u riječi,
    retci po sustavima/interpunkciji) i akordi iz oznaka akorda (harmony);
  * vježbanje glasova: note po dionici (sopran, alt, tenor, bas...) kao
    događaji (početak, trajanje u dobama, MIDI visina) za zvuk u pregledniku.

Ne treba vanjske biblioteke; podržan je uobičajeni "score-partwise" (MuseScore,
Sibelius, Finale, Audiveris).
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile

import songedit

STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
KIND = {"major": "", "minor": "m", "augmented": "aug", "diminished": "dim", "dominant": "7",
        "major-seventh": "maj7", "minor-seventh": "m7", "diminished-seventh": "dim7",
        "augmented-seventh": "aug7", "half-diminished": "m7b5", "major-minor": "m(maj7)",
        "major-sixth": "6", "minor-sixth": "m6", "dominant-ninth": "9", "major-ninth": "maj9",
        "minor-ninth": "m9", "dominant-11th": "11", "dominant-13th": "13",
        "suspended-second": "sus2", "suspended-fourth": "sus4", "power": "5", "none": ""}
MAX_BYTES = 20 * 1024 * 1024
PUNCT = ",.;:!?"


class MusicXmlError(ValueError):
    """Poruka za korisnika."""


def load(data):
    """Bajtovi (.musicxml ili .mxl) -> korijen XML-a score-partwise."""
    if len(data) > MAX_BYTES:
        raise MusicXmlError("MusicXML je prevelik.")
    if data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            names = z.namelist()
            target = None
            if "META-INF/container.xml" in names:
                m = re.search(rb'full-path="([^"]+)"', z.read("META-INF/container.xml"))
                target = m.group(1).decode() if m else None
            target = target or next((n for n in names if n.lower().endswith((".xml", ".musicxml"))
                                     and not n.startswith("META-INF")), None)
            if not target:
                raise MusicXmlError("U .mxl datoteci nema MusicXML-a.")
            data = z.read(target)
        except zipfile.BadZipFile as exc:
            raise MusicXmlError("Neispravna .mxl datoteka.") from exc
    if b"<!ENTITY" in data[:5000]:
        raise MusicXmlError("MusicXML s ENTITY deklaracijama nije dopušten.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise MusicXmlError("Neispravan MusicXML.") from exc
    if root.tag != "score-partwise":
        raise MusicXmlError("Podržan je samo MusicXML 'score-partwise' (izvoz iz MuseScorea i sl.).")
    return root


def _txt(el, path, default=""):
    x = el.find(path)
    return (x.text or "").strip() if x is not None and x.text else default


def chord_name(h):
    """<harmony> -> "Am7", "D/F#"."""
    step = _txt(h, "root/root-step")
    if not step:
        return ""
    alter = _txt(h, "root/root-alter", "0")
    acc = {"1": "#", "-1": "b"}.get(alter.split(".")[0], "")
    kind_el = h.find("kind")
    kind = ""
    if kind_el is not None:
        kind = kind_el.get("text")
        if kind is None:
            kind = KIND.get((kind_el.text or "").strip(), "")
    name = step + acc + kind
    bass = _txt(h, "bass/bass-step")
    if bass:
        b_alter = {"1": "#", "-1": "b"}.get(_txt(h, "bass/bass-alter", "0").split(".")[0], "")
        name += "/" + bass + b_alter
    return name


def parse(data):
    """MusicXML -> {"title", "tempo", "parts": [{"id","name","tracks": {voice: [nota]}}],
    "harmony": [(vrijeme, akord)], "breaks": [vrijeme početka sustava]}.

    Vrijeme je u dobama (četvrtinkama) od početka. Nota: {"t","d","midi" ili None,
    "lyrics": {broj kitice: (syllabic, tekst)}, "chord": bool}."""
    root = load(data)
    title = _txt(root, "work/work-title") or _txt(root, "movement-title")
    names = {sp.get("id"): _txt(sp, "part-name") for sp in root.findall("part-list/score-part")}
    tempo = None
    parts, harmony, breaks = [], [], set()
    for pi, part in enumerate(root.findall("part")):
        divisions = 1
        t = 0.0
        tracks = {}
        last_start = {}
        ties = {}
        for measure in part.findall("measure"):
            m_start = t
            pr = measure.find("print")
            if pr is not None and (pr.get("new-system") == "yes" or pr.get("new-page") == "yes"):
                breaks.add(round(m_start, 4))
            for el in measure:
                tag = el.tag
                if tag == "attributes":
                    d = _txt(el, "divisions")
                    if d:
                        divisions = float(d) or 1
                elif tag == "direction":
                    snd = el.find("sound")
                    if snd is not None and snd.get("tempo") and tempo is None:
                        tempo = float(snd.get("tempo"))
                    pm = _txt(el, "direction-type/metronome/per-minute")
                    if pm and tempo is None:
                        try:
                            tempo = float(re.sub(r"[^\d.]", "", pm))
                        except ValueError:
                            pass
                elif tag == "sound" and el.get("tempo") and tempo is None:
                    tempo = float(el.get("tempo"))
                elif tag == "harmony":
                    if pi == 0 or not harmony or all(h[0] != t for h in harmony):
                        name = chord_name(el)
                        if name and not any(abs(h[0] - t) < 1e-6 for h in harmony):
                            harmony.append((t, name))
                elif tag == "backup":
                    t -= float(_txt(el, "duration", "0")) / divisions
                elif tag == "forward":
                    t += float(_txt(el, "duration", "0")) / divisions
                elif tag == "note":
                    if el.find("grace") is not None:
                        continue
                    dur = float(_txt(el, "duration", "0")) / divisions
                    voice = _txt(el, "voice", "1")
                    is_chord = el.find("chord") is not None
                    start = last_start.get(voice, t) if is_chord else t
                    midi = None
                    if el.find("rest") is None:
                        p = el.find("pitch")
                        if p is not None:
                            alter = float(_txt(p, "alter", "0") or 0)
                            midi = 12 * (int(_txt(p, "octave", "4")) + 1) + STEP.get(_txt(p, "step", "C"), 0) \
                                + int(round(alter))
                    lyrics = {}
                    for ly in el.findall("lyric"):
                        txt = "".join(x.text or "" for x in ly.findall("text")).strip()
                        if txt:
                            lyrics[ly.get("number", "1")] = (_txt(ly, "syllabic", "single"), txt)
                    tie_types = {x.get("type") for x in el.findall("tie")}
                    note = {"t": round(start, 4), "d": dur, "midi": midi, "lyrics": lyrics,
                            "chord": is_chord}
                    key = (voice, midi)
                    if midi is not None and "stop" in tie_types and key in ties:
                        ties[key]["d"] += dur                   # vezana nota: produži prethodnu
                        if "start" not in tie_types:
                            ties.pop(key)
                    else:
                        tracks.setdefault(voice, []).append(note)
                        if midi is not None and "start" in tie_types:
                            ties[key] = note
                    if not is_chord:
                        last_start[voice] = t
                        t += dur
            # mjera može biti kraća/dulja zbog backup/forward: kraj = najdalja točka
        parts.append({"id": part.get("id"), "name": names.get(part.get("id")) or f"Dionica {pi + 1}",
                      "tracks": tracks})
    harmony.sort()
    return {"title": title, "tempo": tempo or 90.0, "parts": parts, "harmony": harmony,
            "breaks": sorted(breaks)}


# ── Vježbanje glasova ─────────────────────────────────────────────────────────

def events(data):
    """Za zvuk u pregledniku: {"tempo", "tracks": [{"name", "notes": [[t, d, midi]]}]}."""
    sc = parse(data)
    tracks = []
    for p in sc["parts"]:
        voices = {v: n for v, n in p["tracks"].items() if any(x["midi"] is not None for x in n)}
        for v, notes in sorted(voices.items()):
            name = p["name"] if len(voices) == 1 else f"{p['name']} ({v}. glas)"
            tracks.append({"name": name, "notes": [[n["t"], round(n["d"], 4), n["midi"]]
                                                   for n in notes if n["midi"] is not None]})
    return {"title": sc["title"], "tempo": sc["tempo"], "tracks": tracks}


# ── Tekst pjesme iz nota ──────────────────────────────────────────────────────

def _lyric_track(sc):
    best, best_n = None, 0
    for p in sc["parts"]:
        for notes in p["tracks"].values():
            n = sum(1 for x in notes if x["lyrics"])
            if n > best_n:
                best, best_n = notes, n
    return best or []


def _fix_case(text):
    """OMR često pročita 'sus' kao 'SUS' — riječ velikim slovima: na početku
    retka 'Isus', usred retka mala slova."""
    words = text.split(" ")
    out = []
    for i, w in enumerate(words):
        core = re.sub(r"[^\w]", "", w)
        if len(core) > 1 and core.isupper():
            w = w.capitalize() if i == 0 else w.lower()
        out.append(w)
    return " ".join(out)


def _fix_syllable(txt):
    """Česta greška prepoznavanja: 'Ije', 'Iju' (veliko I) umjesto 'lje', 'lju'."""
    return re.sub(r"^Ij(?=[a-zčćžšđ])", "lj", txt)


def _norm_word(w):
    return re.sub(r"[^\w]", "", w.lower())


def _segment(syls, vocab):
    """Granice riječi u nizu slogova [(vrsta, tekst)] -> spoji[i] (slog i ide uz i+1).

    S rječnikom (riječi iz baze i iz PDF-a nota): podjela s najviše poznatih
    riječi; bez njega (ili kad ne pomaže): oznake slogova iz MusicXML-a."""
    n = len(syls)
    if not vocab:
        return [k in ("begin", "middle") and not t[-1:] in PUNCT for k, t in syls[:-1]] + [False]
    NEG = -1e9
    best = [NEG] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(max(0, j - 6), j):
            if best[i] == NEG:
                continue
            parts = [syls[k][1] for k in range(i, j)]
            # interpunkcija završava riječ; veliko slovo je počinje
            if any(p[-1:] in PUNCT for p in parts[:-1]):
                continue
            if any(p[:1].isupper() and not p.isupper() for p in parts[1:]):
                continue                            # "Spa", "Bo" počinju riječ ("SUS" ne)
            word = _norm_word("".join(parts))
            cnt = vocab.get(word, 0)
            kinds = [syls[k][0] for k in range(i, j)]
            # riječ kakvu opisuju oznake slogova (begin, middle…, end / single)
            consistent = (kinds == ["single"] if j - i == 1 else
                          kinds[0] == "begin" and kinds[-1] == "end"
                          and all(k == "middle" for k in kinds[1:-1]))
            sc = 2.0 if consistent else 0.0
            if cnt:
                sc += 2.0 * len(word) + min(cnt, 5) * 0.2      # poznata riječ: jak dokaz
            elif j - i > 1 and not consistent:
                sc -= 1.5 * (j - i)                            # nepoznato i protiv oznaka
            if best[i] + sc > best[j]:
                best[j], back[j] = best[i] + sc, i
    joins = [False] * n
    j = n
    while j > 0:
        i = back[j]
        for k in range(i, j - 1):
            joins[k] = True
        j = i
    return joins


def lyrics(data, vocab=None):
    """MusicXML -> {"sections": [...], "verses": n, "chords": n} (sekcije "1.", "2."...).

    vocab: {riječ: broj} (mala slova) — poznate riječi za spajanje slogova kad
    oznake slogova nisu pouzdane (prepoznate note)."""
    sc = parse(data)
    notes = _lyric_track(sc)
    breaks = sc["breaks"]
    harmony = sc["harmony"]
    verse_ids = sorted({k for n in notes for k in n["lyrics"]}, key=lambda v: (len(v), v))
    sections = []
    for vid in verse_ids:
        syl_notes = [n for n in notes if n["lyrics"].get(vid)
                     and n["lyrics"][vid][1].strip("-_ ")]
        syls = [(n["lyrics"][vid][0], _fix_syllable(n["lyrics"][vid][1].strip("-_ "))) for n in syl_notes]
        joins = dict(zip((id(n) for n in syl_notes), _segment(syls, vocab)))
        lines, cur, cur_chords, joined = [], "", [], False
        hi = 0
        next_break = iter(b for b in breaks if b > 0)
        nb = next(next_break, None)

        def flush():
            nonlocal cur, cur_chords
            text = _fix_case(cur.rstrip())
            if text:
                lines.append({"text": text, "chords": [[min(i, len(text)), c] for i, c in cur_chords]})
            cur, cur_chords = "", []

        for n in notes:
            if nb is not None and n["t"] >= nb - 1e-6:
                if not joined and len(cur) >= 30:
                    flush()                         # novi sustav nota, a redak je već dug
                while nb is not None and n["t"] >= nb - 1e-6:
                    nb = next(next_break, None)
            # akordi do ovog trenutka idu na ovo mjesto u retku
            while hi < len(harmony) and harmony[hi][0] <= n["t"] + 1e-6:
                if not cur_chords or cur_chords[-1][0] != len(cur):
                    cur_chords.append([len(cur), harmony[hi][1]])
                else:
                    cur_chords.append([len(cur) + 1, harmony[hi][1]])
                hi += 1
            if id(n) not in joins:
                continue
            txt = _fix_syllable(n["lyrics"][vid][1].strip("-_ "))
            cur += txt
            joined = joins[id(n)]
            if not joined:
                cur += " "
                if txt[-1] in PUNCT and len(cur) >= 18:
                    flush()                         # redak = fraza do zareza/točke
                elif len(cur) > 48:
                    flush()
        flush()
        if lines:
            sections.append({"label": f"{vid}." if len(verse_ids) > 1 else None, "lines": lines})
    if not sections:
        raise MusicXmlError("U notama nema teksta (riječi ispod nota).")
    return {"sections": sections, "verses": len(sections),
            "chords": sum(len(ln["chords"]) for s in sections for ln in s["lines"]),
            "title": sc["title"]}


def lyrics_text(data, vocab=None):
    """Za urednik: tekst s akordima iznad (format songedit)."""
    res = lyrics(data, vocab)
    body, _meta = songedit.to_text({"sections": res["sections"]})
    return dict(res, body=body)
