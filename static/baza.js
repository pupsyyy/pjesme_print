"use strict";
const $ = id => document.getElementById(id);
const state = { songs: [], total: 0, sel: new Set(), current: null, q: "", tag: "", tags: [], untagged: 0,
                hidden: new Set(), tr: {}, view: "" };
const PW_KEY = "pjesme-dbpw", DRAFT_KEY = "pjesme-draft", HIDDEN_KEY = "pjesme-hidden-tags";
try { state.hidden = new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]")); } catch (_e) {}

function applyTheme(t) {
  document.body.classList.toggle("light", t === "light");
  $("btnTheme").textContent = t === "light" ? "🌙" : "☀️";
}
applyTheme(localStorage.getItem("pjesme-theme") || "dark");
$("btnTheme").onclick = () => {
  const t = document.body.classList.contains("light") ? "dark" : "light";
  localStorage.setItem("pjesme-theme", t); applyTheme(t);
};
function toast(msg, isErr) {
  const el = $("toast"); el.textContent = msg; el.classList.toggle("err", !!isErr);
  el.classList.add("show"); clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), isErr ? 6000 : 3000);
}
function busy(text) {
  if (text) { $("busyText").textContent = text; $("busy").classList.remove("hidden"); }
  else $("busy").classList.add("hidden");
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

/* fetch s lozinkom baze (ako je server traži) */
async function api(url, opts = {}, retry = true) {
  opts.headers = Object.assign({}, opts.headers || {});
  const pw = localStorage.getItem(PW_KEY);
  if (pw) opts.headers["X-DB-Password"] = pw;
  const res = await fetch(url, opts);
  if (res.status === 401 && retry) {
    const p = prompt("Lozinka baze pjesama:");
    if (p) { localStorage.setItem(PW_KEY, p); return api(url, opts, false); }
  }
  return res;
}
async function json(res) {
  let data = {};
  try { data = await res.json(); } catch (_e) {}
  if (!res.ok) throw new Error(data.error || ("Greška " + res.status));
  return data;
}

/* ── Prikaz pjesme s akordima ── */
function renderLine(ln, mark) {
  const st = (ln.fmt || {}).text;
  const div = el("div", "ln" + (mark ? " diff" : "") + (st === "italic" ? " it" : st === "bold" ? " bo" : ""));
  const text = ln.text || "";
  const chords = (ln.chords || []).slice().sort((a, b) => a[0] - b[0]);
  if (!chords.length) { div.textContent = text || " "; return div; }
  if (!text.trim()) {
    div.classList.add("conly");
    let s = "";
    chords.forEach(([c, ch]) => { if (s.length && s.length >= c) s += " "; while (s.length < c) s += " "; s += ch; });
    div.textContent = s; return div;
  }
  const first = Math.min(chords[0][0], text.length);
  if (first > 0) {
    const seg = el("span", "seg"); seg.append(el("span", "ch", ""), el("span", "ly", text.slice(0, first)));
    div.append(seg);
  }
  chords.forEach(([i, ch], k) => {
    const start = Math.min(i, text.length);
    const end = k + 1 < chords.length ? Math.min(chords[k + 1][0], text.length) : text.length;
    const seg = el("span", "seg");
    seg.append(el("span", "ch", ch), el("span", "ly", text.slice(start, Math.max(start, end))));
    div.append(seg);
  });
  return div;
}
function lineKey(ln) { return (ln.text || "").trim() + "|" + JSON.stringify(ln.chords || []); }
function renderSheet(song, other) {
  const otherKeys = other ? new Set(other.sections.flatMap(s => s.lines.map(lineKey))) : null;
  const wrap = el("div", "sheet scroll-x");
  (song.meta || []).forEach(m => {
    const d = el("div", "sec meta");
    d.append(el("div", "lab", m.label || ""));
    (m.values || []).forEach(v => d.append(el("div", "ln", v)));
    wrap.append(d);
  });
  (song.sections || []).forEach(sec => {
    const d = el("div", "sec");
    if (sec.label) d.append(el("div", "lab", sec.label));
    sec.lines.forEach(ln => d.append(renderLine(ln, otherKeys && !otherKeys.has(lineKey(ln)))));
    wrap.append(d);
  });
  return wrap;
}
/* Kao izvorna aplikacija: kad je akord širi od sloga usred riječi, crtica ("alelu-ja") */
function fixHyphens(root) {
  root.querySelectorAll(".ln").forEach(ln => {
    const segs = [...ln.querySelectorAll(".seg")];
    segs.forEach((seg, k) => {
      const next = segs[k + 1];
      if (!next) return;
      const ch = seg.firstChild, ly = seg.lastChild, t = ly.textContent, nt = next.lastChild.textContent;
      if (t && nt && /[\p{L}\p{N}]$/u.test(t) && /^[\p{L}\p{N}]/u.test(nt) &&
          ch.offsetWidth > ly.offsetWidth + 1) ly.classList.add("hy");
    });
  });
}
function showView(node, quiet) {
  $("welcome").classList.add("hidden");
  const v = $("view"); v.innerHTML = ""; v.append(node); v.classList.remove("hidden");
  $("main").scrollTop = 0;
  fixHyphens(v);
  if (!quiet && window.matchMedia("(max-width: 820px)").matches) v.scrollIntoView({ behavior: "smooth" });
}

/* ── Popis ── */
async function loadList() {
  const exclude = [...state.hidden].filter(t => t !== state.tag).join(",");
  const data = await json(await fetch("db/songs?limit=1000&q=" + encodeURIComponent(state.q) +
                                      "&tag=" + encodeURIComponent(state.tag) +
                                      "&exclude=" + encodeURIComponent(exclude)));
  state.songs = data.items; state.total = data.total;
  renderList();
}
function tagChip(t, opts = {}) {
  const c = el("span", "tag" + (t.auto ? " auto" : "") + (opts.on ? " on" : ""));
  c.append(document.createTextNode(t.name));
  if (t.auto) c.title = "Automatski prijedlog — klikni za potvrdu";
  return c;
}
async function loadTags() {
  const d = await json(await fetch("db/tags"));
  state.tags = d.tags; state.untagged = d.untagged;
  const bar = $("tagbar"); bar.innerHTML = "";
  const all = el("span", "tag" + (state.tag === "" ? " on" : ""), "Sve");
  all.onclick = () => setTagFilter("");
  bar.append(all);
  d.tags.filter(t => t.count || state.tag === t.name).forEach(t => {
    const zb = t.group === "zbirka", hid = state.hidden.has(t.name);
    const c = el("span", "tag" + (state.tag === t.name ? " on" : "") + (zb ? " zb" : "") + (hid ? " hid" : ""));
    c.append(document.createTextNode(t.name + " "), el("span", "n", String(t.count)));
    c.title = (t.description || t.name) + (zb ? " — zbirka" : "");
    c.onclick = () => setTagFilter(state.tag === t.name ? "" : t.name);
    if (zb || hid) {
      const eye = el("span", "eye", hid ? "🚫" : "👁");
      eye.title = hid ? "Skriveno u popisu — klikni da se opet prikazuje"
                      : "Sakrij pjesme s ovom oznakom iz popisa (npr. koje nam inače ne trebaju)";
      eye.onclick = e => {
        e.stopPropagation();
        hid ? state.hidden.delete(t.name) : state.hidden.add(t.name);
        try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...state.hidden])); } catch (_e) {}
        loadTags(); loadList().catch(er => toast(er.message, true));
      };
      c.append(eye);
    }
    bar.append(c);
  });
  if (d.untagged) {
    const u = el("span", "tag" + (state.tag === "-" ? " on" : ""));
    u.append(document.createTextNode("bez oznake "), el("span", "n", String(d.untagged)));
    u.onclick = () => setTagFilter(state.tag === "-" ? "" : "-");
    bar.append(u);
  }
  const sug = el("span", "tag", "✨ Predloži oznake");
  sug.title = "Automatski predloži oznake pjesmama koje ih još nisu dobile";
  sug.onclick = async () => {
    try { const r = await json(await api("db/tags/suggest", { method: "POST" }));
          toast(r.songs ? ("Prijedlog za " + r.songs + " pjesama: " + r.tags + " oznaka (isprekidano = potvrdi)")
                        : "Sve pjesme već imaju predložene oznake.");
          await refreshAll(); }
    catch (e) { toast(e.message, true); }
  };
  bar.append(sug);
  const sel = $("selTag"); sel.innerHTML = '<option value="">+ oznaka…</option>';
  d.tags.forEach(t => sel.append(new Option(t.name, t.name)));
  sel.append(new Option("Nova oznaka…", "__new__"));
}
function setTagFilter(t) { state.tag = t; loadTags(); loadList().catch(e => toast(e.message, true)); }
async function refreshAll() {
  await Promise.all([loadList(), refreshStats(), loadTags()]);
  // pjesmu ponovo prikaži samo ako je ona na ekranu (ne preko odluka/izvještaja)
  if (state.current && state.view === "song") await openSong(state.current, true);
}
function renderList() {
  const ul = $("list"); ul.innerHTML = "";
  state.songs.forEach(s => {
    const li = el("li", state.current === s.id ? "sel" : "");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = state.sel.has(s.id);
    cb.onclick = e => { e.stopPropagation(); cb.checked ? state.sel.add(s.id) : state.sel.delete(s.id); renderSel(); };
    const box = el("div");
    const t = el("div", "t", s.title + (s.notes_count ? "  🎼" : "") + (s.links_count ? " 🎧" : ""));
    if (!s.has_text) t.append(el("span", "badge", "samo note"));
    box.append(t);
    const meta = [s.subtitle, s.key && ("Key: " + s.key), (s.numbers || []).join(", ")].filter(Boolean).join(" · ");
    if (meta) box.append(el("div", "m", meta));
    if ((s.tags || []).length) {
      const tg = el("div", "tags");
      s.tags.forEach(t => tg.append(tagChip(t)));
      box.append(tg);
    }
    li.append(cb, box);
    li.onclick = () => openSong(s.id);
    ul.append(li);
  });
  if (!state.songs.length) ul.append(el("li", "muted", state.q ? "Nema rezultata." : "Baza je prazna."));
  if (state.total > state.songs.length)
    ul.append(el("li", "muted", "Prikazano " + state.songs.length + " od " + state.total + " — suzi pretragu."));
  const hidden = [...state.hidden].filter(t => t !== state.tag);
  if (hidden.length) ul.append(el("li", "muted", "Skriveno oznakama: " + hidden.join(", ") + " (👁 u oznakama)"));
  renderSel();
}
function renderSel() {
  const n = state.sel.size;
  $("selInfo").textContent = n ? ("Odabrano: " + n) : "Odaberi pjesme kvačicom";
  $("btnSelPdf").disabled = $("btnSelEditor").disabled = $("selTag").disabled = !n;
}
$("btnSelAll").onclick = () => {
  const all = state.songs.length && state.songs.every(s => state.sel.has(s.id));
  state.songs.forEach(s => all ? state.sel.delete(s.id) : state.sel.add(s.id));
  renderList();
};
$("selTag").onchange = async () => {
  let name = $("selTag").value;
  $("selTag").value = "";
  if (!name) return;
  if (name === "__new__") { name = (prompt("Naziv nove oznake:") || "").trim(); if (!name) return; }
  try {
    const r = await json(await api("db/tags/bulk", { method: "POST", headers: { "Content-Type": "application/json" },
                                                    body: JSON.stringify({ ids: [...state.sel], name, action: "add" }) }));
    toast("Oznaka „" + name + "” dodana na " + r.changed + " pjesama.");
    await refreshAll();
  } catch (e) { toast(e.message, true); }
};
async function refreshStats() {
  const s = await json(await fetch("db/stats"));
  $("stats").textContent = s.songs + " pjesama";
  const bar = $("pendingBar");
  bar.classList.toggle("hidden", !s.pending);
  bar.textContent = "⚠ " + s.pending + (s.pending === 1 ? " slična pjesma čeka" : " sličnih pjesama čeka") + " tvoju odluku →";
  const ib = $("importBar");
  ib.classList.toggle("hidden", !s.importing);
  if (s.importing) {
    ib.textContent = "⏳ Uvoz ZIP-a u tijeku… (klikni za napredak)";
    clearTimeout(ib._t);
    ib._t = setTimeout(() => { refreshStats(); loadList(); }, 4000);
  }
}
$("importBar").onclick = async () => {
  try { const d = await json(await fetch("db/import-jobs"));
        if (d.jobs.length) followImports(d.jobs.map(j => j.id), [], []); }
  catch (e) { toast(e.message, true); }
};
let qTimer = null;
$("q").addEventListener("input", () => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { state.q = $("q").value.trim(); loadList().catch(e => toast(e.message, true)); }, 220);
});

/* ── Pjesma ── */
function renderSongTags(s) {
  const box = el("div", "songtags");
  (s.tags || []).forEach(t => {
    const c = tagChip(t);
    if (t.auto) c.onclick = () => tagOp(s.id, "POST", t.name);
    const x = el("span", "x", "✕"); x.title = "Makni oznaku";
    x.onclick = e => { e.stopPropagation(); tagOp(s.id, "DELETE", t.name); };
    c.append(x);
    box.append(c);
  });
  const sel = el("select");
  sel.append(new Option("+ oznaka…", ""));
  state.tags.filter(t => !(s.tags || []).some(x => x.name === t.name))
    .forEach(t => sel.append(new Option(t.name, t.name)));
  sel.append(new Option("Nova oznaka…", "__new__"));
  sel.onchange = () => {
    let name = sel.value;
    if (name === "__new__") name = (prompt("Naziv nove oznake:") || "").trim();
    if (name) tagOp(s.id, "POST", name);
  };
  box.append(sel);
  if ((s.tags || []).some(t => t.auto)) box.append(el("span", "muted", "isprekidano = prijedlog, klik potvrđuje"));
  return box;
}
async function tagOp(id, method, name) {
  try {
    const url = "db/songs/" + id + "/tags" + (method === "DELETE" ? "/" + encodeURIComponent(name) : "");
    const opts = { method };
    if (method === "POST") { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify({ name }); }
    await json(await api(url, opts));
    await refreshAll();
  } catch (e) { toast(e.message, true); }
}
function fmtSize(n) {
  return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " kB";
}
const NOTE_ICON = { pdf: "📄", image: "🖼", musicxml: "🎼", musescore: "🎼", midi: "🎹" };
const NOTE_KIND = { pdf: "PDF", image: "slika", musicxml: "MusicXML", musescore: "MuseScore", midi: "MIDI" };
const NOTE_ORIGIN = { "import": "iz uvoza", omr: "prepoznato (Audiveris)" };
let omrTimer = null;
function renderNotes(s, jobs) {
  const box = el("div", "notes");
  const head = el("div", "head");
  head.append(el("span", null, "🎼 Note"), el("span", "grow"));
  const add = el("button", "btn", "＋ Dodaj note");
  add.title = "PDF, slika, MusicXML (.musicxml/.mxl), MuseScore (.mscz) ili MIDI — sprema se odvojeno od teksta";
  add.onclick = () => { $("noteInput").dataset.song = s.id; $("noteInput").click(); };
  head.append(add);
  box.append(head);
  const notes = s.notes || [];
  if (!notes.length) {
    box.append(el("p", "empty-n", "Nema nota. Dodaj PDF ili sliku nota, MusicXML ili MuseScore datoteku."));
    return box;
  }
  const byNote = {};
  (jobs || []).forEach(j => { byNote[j.note_id] = j; });
  const list = el("div", "notelist");
  notes.forEach(n => {
    const card = el("div", "note");
    const url = "db/notes/" + n.id;
    const thumb = el("div", "thumb");
    if (n.kind === "image") {
      const img = el("img"); img.src = url; img.alt = n.name; img.loading = "lazy";
      thumb.append(img);
    } else thumb.textContent = NOTE_ICON[n.kind] || "📎";
    const viewable = n.kind === "musicxml";
    thumb.title = viewable ? "Prikaži note" : n.inline ? "Otvori" : "Preuzmi";
    thumb.onclick = () => viewable ? showScore(n, s) : window.open(n.inline ? url : url + "?download=1", "_blank");
    card.append(thumb, el("div", "nm", n.name),
                el("div", "sub", [NOTE_KIND[n.kind] || n.kind, fmtSize(n.size), NOTE_ORIGIN[n.origin], n.filename]
                                   .filter(Boolean).join(" · ")));
    const job = byNote[n.id];
    if (job && (job.status === "queued" || job.status === "running"))
      card.append(el("div", "st", "⏳ Prepoznajem note… (≈ pola minute po stranici)"));
    else if (job && job.status === "error") card.append(el("div", "st bad", "✕ " + job.message));
    const acts = el("div", "acts");
    if (viewable) { const v = el("button", "btn", "▶ Prikaži"); v.title = "Note u pregledniku: transpozicija i vježbanje glasova";
                    v.onclick = () => showScore(n, s); acts.append(v);
                    const lt = el("button", "btn", "📝 Tekst iz nota");
                    lt.title = "Kitice (i akordi) iz teksta ispod nota — otvori se urednik da provjeriš prije spremanja";
                    lt.onclick = () => lyricsFromNotes(n, s); acts.append(lt); }
    if (n.inline) { const o = el("a", "btn", "Otvori"); o.href = url; o.target = "_blank"; acts.append(o); }
    if (OMR && (n.kind === "pdf" || n.kind === "image") &&
        !(job && (job.status === "queued" || job.status === "running"))) {
      const o = el("button", "btn", "🔍 Prepoznaj");
      o.title = "Pročitaj note (Audiveris) i spremi ih kao MusicXML — za prikaz, transpoziciju i MuseScore";
      o.onclick = async () => {
        try { await json(await api(url + "/omr", { method: "POST" }));
              toast("Prepoznavanje je pokrenuto — rezultat se pojavi ovdje kad bude gotov.");
              openSong(s.id, true); }
        catch (e) { toast(e.message, true); }
      };
      acts.append(o);
    }
    const d = el("a", "btn", "⬇"); d.href = url + "?download=1"; d.title = "Preuzmi";
    const r = el("button", "btn", "✎"); r.title = "Preimenuj (npr. Sopran, Gitara, Klavir)";
    r.onclick = async () => {
      const name = (prompt("Naziv nota (npr. Sopran, Gitara, Klavir):", n.name) || "").trim();
      if (!name || name === n.name) return;
      try { await json(await api(url, { method: "PATCH", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ name }) }));
            openSong(s.id, true); }
      catch (e) { toast(e.message, true); }
    };
    const m = el("button", "btn", "⇄"); m.title = "Premjesti na drugu pjesmu (ako su pogrešno pridružene)";
    m.onclick = () => pickSong("Premjesti note „" + n.name + "” na pjesmu:", s.id, async target => {
      try { await json(await api(url + "/move", { method: "POST", headers: { "Content-Type": "application/json" },
                                                  body: JSON.stringify({ song_id: target.id }) }));
            toast("Note premještene na „" + target.title + "”.");
            state.current = target.id; await refreshAll(); }
      catch (e) { toast(e.message, true); }
    });
    const x = el("button", "btn danger", "✕"); x.title = "Obriši note";
    x.onclick = async () => {
      if (!confirm("Obrisati note „" + n.name + "”?")) return;
      try { await json(await api(url, { method: "DELETE" })); await refreshAll(); }
      catch (e) { toast(e.message, true); }
    };
    acts.append(d, r, m, x);
    card.append(acts);
    list.append(card);
  });
  box.append(list);
  return box;
}

/* ── Snimke: YouTube po glasovima, Google disk, audio ── */
const LINK_ICON = { youtube: "▶️", drive: "☁️", audio: "🎵", link: "🔗" };
const LINK_KIND = { youtube: "YouTube", drive: "Google disk", audio: "audio datoteka", link: "link" };
function renderLinks(s) {
  const box = el("div", "links");
  const head = el("div", "head");
  head.append(el("span", null, "🎧 Snimke"), el("span", "grow"));
  box.append(head);
  const links = s.links || [];
  if (!links.length) box.append(el("p", "empty-n muted", "Nema snimki. Dodaj YouTube link (npr. za svaki glas) ili audio s Google diska."));
  links.forEach(l => {
    const row = el("div", "linkrow");
    row.append(el("span", "voice", (LINK_ICON[l.kind] || "🔗") + " " + (l.voice || "Snimka")));
    const lt = el("span", "lt", l.title || LINK_KIND[l.kind] || l.url); lt.title = l.url;
    row.append(lt);
    const player = el("div", "player hidden");
    if (l.embed) {
      const play = el("button", "btn", "▶ Slušaj");
      play.onclick = () => {
        if (!player.classList.contains("hidden")) { player.innerHTML = ""; player.classList.add("hidden");
                                                    play.textContent = "▶ Slušaj"; return; }
        player.innerHTML = "";
        if (l.kind === "audio") {
          const a = el("audio"); a.controls = true; a.src = l.embed; a.preload = "none"; player.append(a);
        } else {
          const f = el("iframe", l.kind === "youtube" ? "yt" : "drive");
          f.src = l.embed; f.loading = "lazy"; f.referrerPolicy = "strict-origin-when-cross-origin";
          f.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen"; f.allowFullscreen = true;
          player.append(f);
        }
        player.classList.remove("hidden"); play.textContent = "■ Zatvori";
      };
      row.append(play);
    }
    const open = el("a", "btn", "↗"); open.href = l.url; open.target = "_blank"; open.rel = "noopener noreferrer";
    open.title = "Otvori u novoj kartici";
    const ed = el("button", "btn", "✎"); ed.title = "Promijeni glas / naziv";
    ed.onclick = async () => {
      const voice = prompt("Glas (" + VOICES.join(", ") + " ili drugo):", l.voice);
      if (voice === null) return;
      const title = prompt("Naziv (neobavezno):", l.title);
      if (title === null) return;
      try { await json(await api("db/links/" + l.id, { method: "PATCH", headers: { "Content-Type": "application/json" },
                                                      body: JSON.stringify({ voice, title }) }));
            openSong(s.id, true); }
      catch (e) { toast(e.message, true); }
    };
    const x = el("button", "btn danger", "✕"); x.title = "Obriši snimku";
    x.onclick = async () => {
      if (!confirm("Obrisati snimku " + (l.voice || l.url) + "?")) return;
      try { await json(await api("db/links/" + l.id, { method: "DELETE" })); await refreshAll(); }
      catch (e) { toast(e.message, true); }
    };
    row.append(open, ed, x);
    box.append(row, player);
  });
  const form = el("div", "addlink");
  const voice = el("select");
  VOICES.forEach(v => voice.append(new Option(v, v)));
  voice.append(new Option("drugo…", "__other__"));
  const urlIn = el("input", "url"); urlIn.type = "url";
  urlIn.placeholder = "YouTube ili Google disk link (drive.google.com/file/d/…)";
  const titleIn = el("input"); titleIn.placeholder = "naziv (neobavezno)"; titleIn.style.width = "9rem";
  const add = el("button", "btn primary", "＋ Dodaj");
  const submit = async () => {
    let v = voice.value;
    if (v === "__other__") { v = (prompt("Naziv glasa / snimke:") || "").trim(); if (!v) return; }
    try {
      await json(await api("db/songs/" + s.id + "/links", { method: "POST", headers: { "Content-Type": "application/json" },
                                                           body: JSON.stringify({ url: urlIn.value, voice: v, title: titleIn.value }) }));
      toast("Snimka dodana."); await refreshAll();
    } catch (e) { toast(e.message, true); }
  };
  add.onclick = submit;
  urlIn.onkeydown = e => { if (e.key === "Enter") submit(); };
  form.append(voice, urlIn, titleIn, add);
  box.append(form);
  box.append(el("div", "muted", "Google disk: datoteka mora biti dijeljena s „Svi koji imaju link”."));
  box.lastChild.style.fontSize = ".75rem";
  return box;
}

/* ── Odabir pjesme (premještanje nota) ── */
function pickSong(title, excludeId, onPick, part) {
  const modal = el("div", "modal");
  const win = el("div", "win"); win.style.width = "min(560px, 100%)";
  const bar = el("div", "bar");
  bar.append(el("b", null, title), el("span", "grow"));
  const close = el("button", "btn", "✕"); close.onclick = () => modal.remove();
  bar.append(close);
  const body = el("div", "body");
  const q = el("input"); q.type = "search"; q.placeholder = "Traži naslov ili tekst…"; q.style.width = "100%";
  q.className = "search"; q.style.padding = ".45rem .7rem";
  const ul = el("ul", "picklist");
  let t = null;
  const search = async () => {
    const qq = q.value.trim();
    let items;
    if (part && !qq) {           // prijedlozi za dio mise (već pjevane, s oznakom)
      items = (await json(await fetch("db/setlists/suggest?part=" + encodeURIComponent(part)))).songs
        .map(x => ({ ...x, has_text: true, hint: x.used ? "pjevano " + x.used + "×" : (x.tag === "auto" ? "prijedlog" : "") }));
      if (!items.length) {
        const d = await json(await fetch("db/songs?limit=50&q="));
        items = d.items;
      }
    } else {
      items = (await json(await fetch("db/songs?limit=50&q=" + encodeURIComponent(qq)))).items;
    }
    ul.innerHTML = "";
    items.filter(x => x.id !== excludeId).forEach(x => {
      const li = el("li", null, x.title + (x.subtitle ? " — " + x.subtitle : "") + (x.key ? "  (" + x.key + ")" : ""));
      if (!x.has_text) li.append(el("span", "badge", "samo note"));
      if (x.hint) li.append(el("span", "badge", x.hint));
      li.onclick = () => { modal.remove(); onPick(x); };
      ul.append(li);
    });
    if (!ul.children.length) ul.append(el("li", "muted", "Nema rezultata."));
  };
  q.oninput = () => { clearTimeout(t); t = setTimeout(() => search().catch(e => toast(e.message, true)), 200); };
  body.append(q, ul);
  win.append(bar, body); modal.append(win);
  modal.onclick = e => { if (e.target === modal) modal.remove(); };
  document.body.append(modal);
  q.focus(); search().catch(e => toast(e.message, true));
}

/* ── Zvuk iz nota (WebAudio): vježbanje glasova ── */
function playNotes(ev, opts, onProgress, onEnd) {
  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC();
  const spb = 60 / (ev.tempo * (opts.tempoPct || 100) / 100);
  const t0 = ctx.currentTime + 0.2;
  const master = ctx.createGain(); master.gain.value = 0.22; master.connect(ctx.destination);
  let end = 0;
  ev.tracks.forEach((tr, i) => {
    if (opts.mode === "solo" && i !== opts.track) return;
    const vol = opts.mode === "emph" ? (i === opts.track ? 1 : 0.22) : 0.8;
    const type = opts.mode !== "all" && i === opts.track ? "square" : "triangle";
    tr.notes.forEach(([t, d, m]) => {
      const start = t0 + t * spb, dur = Math.max(0.06, d * spb);
      end = Math.max(end, t + d);
      const o = ctx.createOscillator(); o.type = type;
      o.frequency.value = 440 * Math.pow(2, (m + (opts.transpose || 0) - 69) / 12);
      const g = ctx.createGain();
      const peak = (type === "square" ? 0.35 : 0.6) * vol;
      g.gain.setValueAtTime(0, start);
      g.gain.linearRampToValueAtTime(peak, start + 0.02);
      g.gain.setValueAtTime(peak, start + dur * 0.8);
      g.gain.linearRampToValueAtTime(0, start + dur * 0.98);
      o.connect(g); g.connect(master);
      o.start(start); o.stop(start + dur);
    });
  });
  const total = end * spb;
  let raf = 0, stopped = false;
  const tick = () => {
    if (stopped) return;
    const frac = Math.min(1, (ctx.currentTime - t0) / (total || 1));
    onProgress(Math.max(0, frac));
    if (frac >= 1) { onEnd(); return; }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return { stop() { stopped = true; cancelAnimationFrame(raf); ctx.close(); } };
}

/* ── Prikaz MusicXML nota (Verovio), transpozicija ── */
let verovioReady = null;
function loadVerovio() {
  if (!verovioReady) verovioReady = new Promise((resolve, reject) => {
    const sc = document.createElement("script");
    sc.src = VEROVIO_URL; sc.crossOrigin = "anonymous";
    sc.onload = () => {
      if (window.verovio && verovio.module) {
        if (verovio.module.calledRun) resolve(); else verovio.module.onRuntimeInitialized = () => resolve();
      } else reject(new Error("Preglednik nota se nije učitao."));
    };
    sc.onerror = () => { verovioReady = null; reject(new Error("Preglednik nota se ne može učitati (nema interneta?).")); };
    document.head.append(sc);
  });
  return verovioReady;
}
async function showScore(n, s) {
  busy("Učitavam note…");
  let tk, buf;
  try {
    const [, res] = await Promise.all([loadVerovio(), fetch("db/notes/" + n.id)]);
    if (!res.ok) await json(res);
    buf = await res.arrayBuffer();
    tk = new verovio.toolkit();
  } catch (e) { busy(null); toast(e.message, true); return; }
  busy(null);
  const modal = el("div", "modal");
  const win = el("div", "win");
  const bar = el("div", "bar");
  bar.append(el("b", null, s.title + " — " + n.name), el("span", "grow"));
  const tr = el("select"); tr.title = "Transponiraj (polutonovi)";
  for (let k = -6; k <= 6; k++) tr.append(new Option(k === 0 ? "izvorni ton" : (k > 0 ? "+" : "") + k + " polut.", String(k)));
  tr.value = "0";
  const zoom = el("select"); zoom.title = "Veličina";
  [["30", "sitno"], ["40", "normalno"], ["55", "veće"]].forEach(([v, t]) => zoom.append(new Option(t, v)));
  zoom.value = "40";
  const prev = el("button", "btn", "◀"), next = el("button", "btn", "▶"), info = el("span", "muted");
  const dl = el("a", "btn", "⬇ MusicXML"); dl.href = "db/notes/" + n.id + "?download=1";
  const close = el("button", "btn", "✕ Zatvori");
  bar.append(el("span", null, "Transpozicija:"), tr, zoom, prev, info, next, dl, close);
  // vježbanje glasova: zvuk iz nota (svi / samo jedan / jedan naglašen)
  const pbar = el("div", "bar");
  const voice = el("select"); voice.title = "Koji glas";
  voice.append(new Option("🎧 Svi glasovi", "all"));
  const tempo = el("select"); tempo.title = "Tempo";
  [["50", "50 %"], ["75", "75 %"], ["100", "100 % tempa"], ["125", "125 %"]].forEach(([v, t]) => tempo.append(new Option(t, v)));
  tempo.value = "100";
  const play = el("button", "btn primary", "▶ Sviraj");
  const prog = el("div", "progress"); prog.style.flex = "1"; prog.style.minWidth = "6rem";
  const fill = el("div"); fill.style.width = "0%"; prog.append(fill);
  pbar.append(el("span", null, "Vježbanje:"), voice, tempo, play, prog);
  let ev = null, player = null;
  fetch("db/notes/" + n.id + "/events").then(r => r.json()).then(d => {
    if (!d.tracks) { pbar.classList.add("hidden"); return; }
    ev = d;
    d.tracks.forEach((t, i) => {
      voice.append(new Option("samo " + t.name, "solo:" + i));
      voice.append(new Option(t.name + " naglašeno", "emph:" + i));
    });
  }).catch(() => pbar.classList.add("hidden"));
  const stopPlay = () => { if (player) { player.stop(); player = null; } play.textContent = "▶ Sviraj"; fill.style.width = "0%"; };
  play.onclick = () => {
    if (player) { stopPlay(); return; }
    if (!ev) { toast("Note se još učitavaju…"); return; }
    const [mode, idx] = voice.value.split(":");
    player = playNotes(ev, { mode, track: Number(idx), tempoPct: Number(tempo.value), transpose: Number(tr.value) },
                       frac => { fill.style.width = Math.round(frac * 100) + "%"; }, stopPlay);
    play.textContent = "■ Stani";
  };
  close.onclick = () => { stopPlay(); modal.remove(); };
  const body = el("div", "body"), score = el("div", "score");
  body.append(score);
  win.append(bar, pbar, body); modal.append(win);
  modal.onclick = e => { if (e.target === modal) { stopPlay(); modal.remove(); } };
  document.body.append(modal);
  let page = 1, pages = 1;
  const isZip = new Uint8Array(buf.slice(0, 2))[0] === 0x50;
  const text = isZip ? null : new TextDecoder().decode(buf);
  const render = () => {
    tk.setOptions({ scale: Number(zoom.value), transpose: tr.value === "0" ? "" : tr.value,
                    pageWidth: Math.max(800, Math.round((body.clientWidth - 30) * 100 / Number(zoom.value))),
                    adjustPageHeight: true, footer: "none" });
    const ok = isZip ? tk.loadZipDataBuffer(buf) : tk.loadData(text);
    if (!ok) { score.textContent = "Note se ne mogu prikazati (neispravan MusicXML)."; return; }
    pages = tk.getPageCount(); page = Math.min(page, pages);
    score.innerHTML = tk.renderToSVG(page);
    info.textContent = page + " / " + pages;
    prev.disabled = page <= 1; next.disabled = page >= pages;
  };
  tr.onchange = zoom.onchange = render;
  prev.onclick = () => { page--; render(); };
  next.onclick = () => { page++; render(); };
  try { render(); } catch (e) { score.textContent = "Greška pri prikazu: " + e.message; }
}

$("noteInput").onchange = async () => {
  const input = $("noteInput"), id = input.dataset.song, files = [...input.files];
  input.value = "";
  if (!files.length || !id) return;
  busy("Spremam note…");
  try {
    const fd = new FormData();
    files.forEach(f => fd.append("file", f));
    const r = await json(await api("db/songs/" + id + "/notes", { method: "POST", body: fd }));
    toast("Dodano: " + r.added.length + (r.errors.length ? " · " + r.errors.join(" ") : ""), r.errors.length > 0);
    await refreshAll();
  } catch (e) { toast(e.message, true); } finally { busy(null); }
};
async function openSong(id, keepScroll) {
  try {
    const tr = state.tr[id] || 0, q = tr ? "?transpose=" + tr : "";
    const s = await json(await fetch("db/songs/" + id + q));
    state.current = id; renderList();
    const box = el("div");
    const head = el("div", "song-head");
    const h = el("div");
    h.append(el("h2", null, s.title));
    if (s.subtitle) h.append(el("div", "sub", s.subtitle));
    head.append(h, el("span", "grow"));
    if ((s.transpose_options || []).length) {
      const sel = el("select", "trsel"); sel.title = "Transponiraj akorde (prikaz, PDF, ChordPro)";
      s.transpose_options.forEach(o => sel.append(new Option(
        (o.n === 0 ? "Izvorno" : (o.n > 0 ? "+" : "") + o.n) + (o.key ? " · " + o.key : ""), String(o.n))));
      sel.value = String(tr);
      sel.onchange = () => { state.tr[id] = Number(sel.value); openSong(id, true); };
      const lab = el("label", "chip trchip"); lab.append(document.createTextNode("🎚 Tonalitet "), sel);
      head.append(lab);
    } else if (s.key) head.append(el("span", "chip", "Key: " + s.key));
    if ((s.numbers || []).length) head.append(el("span", "chip", "Pjesmarica " + s.numbers.join(", ")));
    head.append(el("span", "chip", "#" + s.id));
    box.append(head);
    box.append(renderSongTags(s));
    if (!s.has_text) box.append(el("div", "info", "Pjesma zasad ima samo note. Tekst (i akordi) dodaju se sami " +
                                   "kad uvezeš PDF s tekstom pjesme istog naslova."));
    let jobs = [];
    if (OMR && (s.notes || []).length) {
      try { jobs = (await json(await fetch("db/songs/" + id + "/omr"))).jobs; } catch (_e) {}
    }
    box.append(renderNotes(s, jobs));
    box.append(renderLinks(s));
    clearTimeout(omrTimer);
    if (jobs.some(j => j.status === "queued" || j.status === "running"))
      omrTimer = setTimeout(() => { if (state.current === id) refreshAll(); }, 5000);
    const act = el("div", "actions");
    const a1 = el("a", "btn", "⬇ PDF (izvorni izgled)" + (tr ? " · " + s.key : "")); a1.href = "db/songs/" + id + "/pdf" + q;
    const a2 = el("a", "btn", "⬇ ChordPro"); a2.href = "db/songs/" + id + "/chordpro" + q;
    const a3 = el("a", "btn", "JSON"); a3.href = "db/songs/" + id; a3.target = "_blank";
    const ed = el("button", "btn primary", "✎ Uredi"); ed.title = "Uredi naslov, tekst i akorde";
    ed.onclick = () => showEditor(id);
    const toSl = el("button", "btn", "📋 U popis za misu");
    toSl.onclick = () => addToSetlist(id, tr);
    const mg = el("button", "btn", "⇄ Spoji s…");
    mg.title = "Spoji s duplikatom: note, snimke, oznake i popisi prelaze na ovu pjesmu, druga nestaje";
    mg.onclick = () => pickSong("Spoji s „" + s.title + "” — odaberi duplikat:", id, other => mergeDialog(s, other));
    act.append(ed, toSl, mg);
    const del = el("button", "btn danger", "Obriši");
    del.onclick = async () => {
      if (!confirm("Obrisati pjesmu \"" + s.title + "\" iz baze?")) return;
      try { await json(await api("db/songs/" + id, { method: "DELETE" }));
            state.sel.delete(id); state.current = null; state.view = ""; toast("Pjesma obrisana.");
            $("view").classList.add("hidden"); $("welcome").classList.remove("hidden");
            await refreshAll(); }
      catch (e) { toast(e.message, true); }
    };
    act.append(a1, a2, a3, del);
    box.append(act);
    box.append(renderSheet(s));
    const src = (s.sources || []).map(x => x.file + (x.pages && x.pages.length ? " (str. " + x.pages.join(", ") + ")" : "")).join("; ");
    if (src) box.append(el("p", "muted", "Izvor: " + src));
    const top = $("main").scrollTop;
    state.view = "song";
    showView(box, keepScroll);
    if (keepScroll) $("main").scrollTop = top;
  } catch (e) { toast(e.message, true); }
}

/* ── Tekst pjesme iz nota (MusicXML) ── */
async function lyricsFromNotes(n, s) {
  if (s.has_text && !confirm("Pjesma već ima tekst. Otvoriti urednik s tekstom iz nota umjesto postojećeg? " +
                             "(ništa se ne mijenja dok ne spremiš; stari tekst ostaje u povijesti)")) return;
  busy("Čitam tekst iz nota…");
  try {
    const r = await json(await fetch("db/notes/" + n.id + "/lyrics"));
    busy(null);
    showEditor(s.id, { body: r.body, info: "Prijedlog iz nota „" + n.name + "”: " + r.verses +
      (r.verses === 1 ? " kitica" : " kitice") + ", " + r.chords + " akorda. Provjeri tekst " +
      "(kod prepoznatih nota zna biti krivo pročitanih slova ili slogova) pa spremi." });
  } catch (e) { busy(null); toast(e.message, true); }
}

/* ── Spajanje pjesama ── */
function mergeDialog(s, other) {
  const modal = el("div", "modal"), win = el("div", "win"); win.style.width = "min(520px, 100%)";
  const bar = el("div", "bar"); bar.append(el("b", null, "Spoji pjesme"), el("span", "grow"));
  const close = el("button", "btn", "✕"); close.onclick = () => modal.remove(); bar.append(close);
  const body = el("div", "body");
  body.append(el("p", null, "„" + other.title + "” nestaje; njezine note, snimke, oznake, mjesta u popisima " +
                            "za misu i izvori prelaze na „" + s.title + "”."));
  body.append(el("p", "muted", "Koji tekst (i naslov) ostaje?"));
  const opt = (val, label, checked) => {
    const l = el("label"); l.style.display = "block"; l.style.margin = ".3rem 0";
    const r = el("input"); r.type = "radio"; r.name = "tf"; r.value = val; r.checked = checked;
    l.append(r, document.createTextNode(" " + label)); body.append(l); return r;
  };
  const keepHas = s.has_text, otherHas = other.has_text !== false;
  opt("keep", "„" + s.title + "”" + (keepHas ? "" : " (nema teksta)"), keepHas || !otherHas);
  opt("other", "„" + other.title + "”" + (otherHas ? "" : " (nema teksta)"), !keepHas && otherHas);
  const ok = el("button", "btn primary", "⇄ Spoji"); ok.style.marginTop = ".6rem";
  ok.onclick = async () => {
    const tf = body.querySelector("input[name=tf]:checked").value;
    try {
      await json(await api("db/songs/" + s.id + "/merge", { method: "POST", headers: { "Content-Type": "application/json" },
                                                           body: JSON.stringify({ other_id: other.id, text_from: tf }) }));
      modal.remove(); toast("Spojeno. Stara verzija je u povijesti pjesme.");
      state.sel.delete(other.id); state.current = s.id; state.view = "song"; await refreshAll();
    } catch (e) { toast(e.message, true); }
  };
  body.append(ok);
  win.append(bar, body); modal.append(win);
  modal.onclick = e => { if (e.target === modal) modal.remove(); };
  document.body.append(modal);
}

/* ── Uređivanje pjesme ── */
async function showEditor(id, prefill) {
  let f;
  try { f = await json(await fetch("db/songs/" + id + "/edit")); }
  catch (e) { toast(e.message, true); return; }
  if (prefill && prefill.body) f.body = prefill.body;
  const box = el("div");
  const head = el("div", "song-head");
  head.append(el("h2", null, "Uređivanje: " + f.title), el("span", "grow"));
  const hist = el("button", "btn", "🕘 Povijest"); hist.onclick = () => showVersions(id);
  head.append(hist);
  box.append(head);
  if (prefill && prefill.info) box.append(el("div", "info", prefill.info));
  const wrap = el("div", "editor");
  const left = el("div"), right = el("div", "prev");
  const fields = el("div", "fields");
  const inp = (label, value) => { const l = el("label", null, label); const i = el("input"); i.value = value || "";
                                  l.append(i); fields.append(l); return i; };
  const title = inp("Naslov", f.title), sub = inp("Podnaslov (izvođač…)", f.subtitle), key = inp("Tonalitet", f.key);
  left.append(fields);
  const body = el("textarea"); body.rows = 26; body.value = f.body; body.spellcheck = false;
  left.append(body);
  const help = el("div", "help");
  help.innerHTML = "Akordi u retku <b>iznad</b> teksta (stupac = slovo ispod). " +
    "<code>[Refren]</code> u svom retku = nova sekcija, prazan redak = nova sekcija bez oznake. " +
    "Redak samo s akordima bez teksta ispod = međuigra/uvod. Nepromijenjeni retci ostaju točno kakvi su bili.";
  left.append(help);
  const ml = el("label", null, "Metapodaci (po retku, npr. Pjesmarica: 203)");
  const meta = el("textarea"); meta.rows = 3; meta.value = f.meta; meta.spellcheck = false; ml.append(meta);
  left.append(ml);
  const act = el("div", "actions");
  const save = el("button", "btn primary", "💾 Spremi"), cancel = el("button", "btn", "Odustani");
  cancel.onclick = () => openSong(id);
  save.onclick = async () => {
    save.disabled = true;
    try {
      const r = await json(await api("db/songs/" + id, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.value, subtitle: sub.value, key: key.value, body: body.value, meta: meta.value }) }));
      toast(r.changed ? "Spremljeno (stara verzija je u povijesti)." : "Nema promjena.");
      state.view = "song"; await refreshAll(); openSong(id);
    } catch (e) { toast(e.message, true); } finally { save.disabled = false; }
  };
  act.append(save, cancel);
  left.append(act);
  right.append(el("div", "muted", "Pregled"));
  const prevBox = el("div"); right.append(prevBox);
  let t = null;
  const preview = async () => {
    try {
      const r = await json(await fetch("db/songs/" + id + "/preview", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: body.value }) }));
      prevBox.innerHTML = "";
      prevBox.append(renderSheet({ sections: r.sections, meta: [] }));
      fixHyphens(prevBox);
    } catch (e) { prevBox.textContent = e.message; }
  };
  body.oninput = () => { clearTimeout(t); t = setTimeout(preview, 350); };
  wrap.append(left, right); box.append(wrap);
  state.view = "edit";
  showView(box);
  preview();
}
async function showVersions(id) {
  try {
    const d = await json(await fetch("db/songs/" + id + "/versions"));
    const box = el("div");
    box.append(el("h2", null, "Povijest pjesme"));
    if (!d.versions.length) box.append(el("p", "muted", "Nema starijih verzija — pjesma još nije uređivana."));
    const ul = el("ul", "versions");
    d.versions.forEach(v => {
      const li = el("li");
      li.append(el("span", null, new Date(v.created_at).toLocaleString("hr-HR")),
                el("span", "muted", v.title + (v.key ? " · " + v.key : "") + " · " + v.lines + " redaka · " + v.note),
                el("span", "grow"));
      const b = el("button", "btn", "↶ Vrati ovu");
      b.onclick = async () => {
        if (!confirm("Vratiti pjesmu na verziju od " + new Date(v.created_at).toLocaleString("hr-HR") + "? (trenutna ide u povijest)")) return;
        try { await json(await api("db/songs/" + id + "/versions/" + v.id + "/restore", { method: "POST" }));
              toast("Vraćeno."); state.view = "song"; await refreshAll(); openSong(id); }
        catch (e) { toast(e.message, true); }
      };
      li.append(b); ul.append(li);
    });
    box.append(ul);
    const back = el("button", "btn", "← Natrag na pjesmu"); back.onclick = () => openSong(id);
    box.append(back);
    state.view = "versions";
    showView(box);
  } catch (e) { toast(e.message, true); }
}

/* ── Popis za misu ── */
let SL_PARTS = [];
function nextSunday() {
  const d = new Date(); d.setDate(d.getDate() + ((7 - d.getDay()) % 7 || 7));
  return d.toISOString().slice(0, 10);
}
function hrDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-"); return d ? (+d) + ". " + (+m) + ". " + y + "." : iso;
}
async function showSetlists() {
  try {
    const d = await json(await fetch("db/setlists"));
    SL_PARTS = d.parts;
    const box = el("div");
    box.append(el("h2", null, "Popisi za misu"));
    box.append(el("p", "muted", "Pjesme po dijelovima mise, svaka u svom tonalitetu. Iz popisa: PDF, " +
                  "tekst za poruku i link za zbor (mobitel, bez lozinke)."));
    const head = el("div", "sl-head");
    const t = el("input"); t.value = "Misa"; t.placeholder = "Naslov";
    const dt = el("input"); dt.type = "date"; dt.value = nextSunday();
    const add = el("button", "btn primary", "＋ Novi popis");
    add.onclick = async () => {
      try { const r = await json(await api("db/setlists", { method: "POST", headers: { "Content-Type": "application/json" },
                                                            body: JSON.stringify({ title: t.value, date: dt.value }) }));
            showSetlist(r.setlist.id); }
      catch (e) { toast(e.message, true); }
    };
    head.append(t, dt, add);
    box.append(head);
    const ul = el("ul", "versions");
    if (!d.setlists.length) ul.append(el("li", "muted", "Još nema popisa."));
    d.setlists.forEach(sl => {
      const li = el("li");
      const a = el("a", null, sl.title); a.href = "#"; a.onclick = e => { e.preventDefault(); showSetlist(sl.id); };
      li.append(a, el("span", "muted", hrDate(sl.date) + " · " + sl.count + " pjesama"), el("span", "grow"));
      ul.append(li);
    });
    box.append(ul);
    state.view = "setlists";
    showView(box);
  } catch (e) { toast(e.message, true); }
}
$("btnSetlists").onclick = showSetlists;

async function slApi(url, method, body) {
  const opts = { method };
  if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
  return json(await api(url, opts));
}
async function showSetlist(id, keepScroll) {
  let d;
  try { d = await json(await fetch("db/setlists/" + id)); } catch (e) { toast(e.message, true); return; }
  SL_PARTS = d.parts;
  const sl = d.setlist;
  const box = el("div");
  const head = el("div", "sl-head");
  const t = el("input"); t.value = sl.title; t.style.fontSize = "1.1rem"; t.style.fontWeight = "600";
  const dt = el("input"); dt.type = "date"; dt.value = sl.date;
  const saveHead = async () => { try { await slApi("db/setlists/" + id, "PATCH", { title: t.value, date: dt.value }); }
                                 catch (e) { toast(e.message, true); } };
  t.onchange = saveHead; dt.onchange = saveHead;
  head.append(t, dt, el("span", "grow"));
  box.append(head);
  const acts = el("div", "actions");
  const pdf = el("a", "btn primary", "⬇ PDF"); pdf.href = "db/setlists/" + id + "/pdf";
  pdf.title = "Naslovnica s popisom + pjesme s akordima, u odabranim tonalitetima";
  const txt = el("button", "btn", "📋 Kopiraj popis");
  txt.onclick = async () => {
    try { const r = await json(await fetch("db/setlists/" + id + "/text"));
          await navigator.clipboard.writeText(r.text); toast("Popis kopiran — zalijepi ga u poruku."); }
    catch (e) { toast("Kopiranje nije uspjelo: " + e.message, true); }
  };
  const share = el("button", "btn", "🔗 Link za zbor");
  const url = new URL("p/" + sl.token, location.href).href;
  share.title = url;
  share.onclick = async () => {
    try { await navigator.clipboard.writeText(url); toast("Link kopiran: " + url); }
    catch (_e) { prompt("Link za zbor (kopiraj):", url); }
  };
  const open = el("a", "btn", "👁 Otvori kao zbor"); open.href = "p/" + sl.token; open.target = "_blank";
  const editor = el("button", "btn", "Napravi pjesmaricu");
  editor.title = "Otvori pjesme iz popisa u uredniku pjesmarice (kompaktno/klasično, Word)";
  editor.onclick = async () => {
    try {
      const r = await json(await fetch("db/setlists/" + id + "/editor"));
      const old = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
      if (old && old.songs && old.songs.length &&
          !confirm("U uredniku već postoji započeta pjesmarica (" + old.songs.length + " pjesama). Zamijeniti je?")) return;
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ filename: r.title, songs: r.songs.map(s => ({ ...s, include: true })),
                                                       sel: 0, savedAt: Date.now() }));
      location.href = "./#nastavi";
    } catch (e) { toast(e.message, true); }
  };
  const relink = el("button", "btn", "↻ Novi link");
  relink.title = "Stari link za zbor prestaje raditi (npr. ako je proslijeđen krivim ljudima)";
  relink.onclick = async () => {
    if (!confirm("Napraviti novi link? Stari link više neće raditi.")) return;
    try { await slApi("db/setlists/" + id + "/token", "POST"); showSetlist(id, true); } catch (e) { toast(e.message, true); }
  };
  const del = el("button", "btn danger", "Obriši popis");
  del.onclick = async () => {
    if (!confirm("Obrisati popis „" + sl.title + "”? (pjesme ostaju u bazi)")) return;
    try { await slApi("db/setlists/" + id, "DELETE"); showSetlists(); } catch (e) { toast(e.message, true); }
  };
  acts.append(pdf, txt, share, open, editor, relink, del);
  box.append(acts);
  const parts = [...SL_PARTS];
  sl.items.forEach(it => { if (!parts.includes(it.part)) parts.push(it.part); });
  parts.forEach(part => {
    const row = el("div", "sl-part");
    row.append(el("b", null, part || "Ostalo"));
    const col = el("div");
    sl.items.filter(it => it.part === part).forEach(it => {
      const r = el("div", "sl-item");
      const a = el("a", null, it.title); a.onclick = () => { state.tr[it.song_id] = it.transpose; openSong(it.song_id); };
      r.append(a);
      if (!it.has_text) r.append(el("span", "badge", "samo note"));
      if (it.options.length) {
        const sel = el("select"); sel.title = "Tonalitet za ovaj popis";
        it.options.forEach(o => sel.append(new Option((o.n === 0 ? "izvorno" : (o.n > 0 ? "+" : "") + o.n) +
                                                      (o.key ? " · " + o.key : ""), String(o.n))));
        sel.value = String(it.transpose);
        sel.onchange = async () => { try { await slApi("db/setlist-items/" + it.id, "PATCH", { transpose: Number(sel.value) });
                                           showSetlist(id, true); } catch (e) { toast(e.message, true); } };
        r.append(sel);
      }
      const up = el("button", "btn", "↑"), dn = el("button", "btn", "↓"), x = el("button", "btn danger", "✕");
      up.onclick = async () => { await slApi("db/setlist-items/" + it.id + "/move", "POST", { step: -1 }); showSetlist(id, true); };
      dn.onclick = async () => { await slApi("db/setlist-items/" + it.id + "/move", "POST", { step: 1 }); showSetlist(id, true); };
      x.onclick = async () => { await slApi("db/setlist-items/" + it.id, "DELETE"); showSetlist(id, true); };
      r.append(up, dn, x);
      col.append(r);
    });
    const add = el("button", "btn", "＋ dodaj");
    add.onclick = () => pickSong("Pjesma za: " + (part || "Ostalo"), null, async song => {
      try { await slApi("db/setlists/" + id + "/items", "POST", { song_id: song.id, part });
            showSetlist(id, true); }
      catch (e) { toast(e.message, true); }
    }, part);
    col.append(add);
    row.append(col);
    box.append(row);
  });
  const other = el("button", "btn", "＋ Drugi dio (npr. Klanjanje, Na kraju)");
  other.style.marginTop = ".6rem";
  other.onclick = () => {
    const part = (prompt("Naziv dijela:") || "").trim();
    if (part) pickSong("Pjesma za: " + part, null, async song => {
      try { await slApi("db/setlists/" + id + "/items", "POST", { song_id: song.id, part }); showSetlist(id, true); }
      catch (e) { toast(e.message, true); }
    });
  };
  box.append(other);
  const top = $("main").scrollTop;
  state.view = "setlist";
  showView(box, keepScroll);
  if (keepScroll) $("main").scrollTop = top;
}
async function addToSetlist(songId, tr) {
  try {
    const d = await json(await fetch("db/setlists"));
    const modal = el("div", "modal"), win = el("div", "win"); win.style.width = "min(480px, 100%)";
    const bar = el("div", "bar"); bar.append(el("b", null, "Dodaj u popis za misu"), el("span", "grow"));
    const close = el("button", "btn", "✕"); close.onclick = () => modal.remove(); bar.append(close);
    const body = el("div", "body");
    const sel = el("select"); sel.style.width = "100%";
    d.setlists.forEach(sl => sel.append(new Option(sl.title + (sl.date ? " · " + hrDate(sl.date) : ""), String(sl.id))));
    sel.append(new Option("＋ Novi popis (Misa, " + hrDate(nextSunday()) + ")", "__new__"));
    const part = el("select"); part.style.width = "100%"; part.style.marginTop = ".5rem";
    d.parts.forEach(p => part.append(new Option(p, p)));
    const ok = el("button", "btn primary", "Dodaj"); ok.style.marginTop = ".7rem";
    ok.onclick = async () => {
      try {
        let sid = sel.value;
        if (sid === "__new__") sid = (await slApi("db/setlists", "POST", { title: "Misa", date: nextSunday() })).setlist.id;
        await slApi("db/setlists/" + sid + "/items", "POST", { song_id: songId, part: part.value, transpose: tr || 0 });
        modal.remove(); toast("Dodano u popis.");
      } catch (e) { toast(e.message, true); }
    };
    body.append(sel, part, ok);
    win.append(bar, body); modal.append(win);
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.body.append(modal);
  } catch (e) { toast(e.message, true); }
}

/* ── Backup ── */
async function showBackups() {
  try {
    const d = await json(await fetch("db/backups"));
    const box = el("div");
    box.append(el("h2", null, "Backup baze i nota"));
    box.append(el("p", "muted", d.disabled ? "Automatski backup je isključen (BACKUP_DISABLE)." :
      "Automatski jednom dnevno; čuva se zadnjih " + d.keep + ". Kopije su na serveru u data/backup — " +
      "za zaštitu od kvara diska povremeno preuzmi ZIP i spremi ga drugdje."));
    const now = el("button", "btn primary", "💾 Napravi backup sada");
    now.onclick = async () => {
      busy("Radim backup…");
      try { await json(await api("db/backups", { method: "POST" })); toast("Backup napravljen."); showBackups(); }
      catch (e) { toast(e.message, true); } finally { busy(null); }
    };
    const acts = el("div", "actions"); acts.append(now); box.append(acts);
    const ul = el("ul", "versions");
    if (!d.backups.length) ul.append(el("li", "muted", "Još nema backupa."));
    d.backups.forEach(b => {
      const li = el("li");
      li.append(el("span", null, b.created ? new Date(b.created).toLocaleString("hr-HR") : b.name),
                el("span", "muted", (b.songs ?? "?") + " pjesama · " + (b.notes ?? 0) + " nota · " +
                   fmtSize(b.size || 0) + (b.reason ? " · " + b.reason : "")),
                el("span", "grow"));
      const dl = el("button", "btn", "⬇ ZIP");
      dl.onclick = async () => {
        busy("Pakiram backup…");
        try {
          const res = await api("db/backups/" + encodeURIComponent(b.name) + ".zip");
          if (!res.ok) await json(res);
          const a = document.createElement("a"); a.href = URL.createObjectURL(await res.blob());
          a.download = "pjesme-backup-" + b.name + ".zip"; a.click(); URL.revokeObjectURL(a.href);
        } catch (e) { toast(e.message, true); } finally { busy(null); }
      };
      li.append(dl); ul.append(li);
    });
    box.append(ul);
    state.view = "backup";
    showView(box);
  } catch (e) { toast(e.message, true); }
}
$("btnBackup").onclick = showBackups;

/* ── Uvoz ── */
const drop = $("drop");
drop.onclick = () => $("fileInput").click();
$("fileInput").onchange = () => { importFiles([...$("fileInput").files]); $("fileInput").value = ""; };
["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => importFiles([...e.dataTransfer.files]));

const IMPORT_EXT = [".pdf", ".zip", ...NOTE_ACCEPT.split(",")];
async function importFiles(files) {
  files = files.filter(f => IMPORT_EXT.some(x => f.name.toLowerCase().endsWith(x)));
  if (!files.length) { toast("Odaberi PDF, ZIP ili datoteke nota.", true); return; }
  const tags = $("importTags").value.trim();
  const reports = [], jobs = [];
  let usedTags = [];
  try {
    for (let i = 0; i < files.length; i++) {
      const zip = files[i].name.toLowerCase().endsWith(".zip");
      busy((zip ? "Šaljem " : "Uvozim ") + (i + 1) + "/" + files.length + ": " + files[i].name);
      const fd = new FormData(); fd.append("file", files[i]); fd.append("tags", tags);
      try {
        const data = await json(await api("db/import", { method: "POST", body: fd }));
        reports.push(...data.reports); jobs.push(...(data.jobs || [])); usedTags = data.tags || usedTags;
      } catch (e) { reports.push({ file: files[i].name, error: e.message }); }
    }
  } finally { busy(null); }
  if (jobs.length) { await followImports(jobs.map(j => j.id), reports, usedTags); return; }
  await Promise.all([loadList(), refreshStats(), loadTags()]);
  showReport(reports, usedTags);
}

/* ZIP se uvozi u pozadini: prikaz napretka dok ne završi (stranica se smije i zatvoriti) */
async function followImports(ids, reports, tags) {
  const box = el("div");
  box.append(el("h2", null, "Uvoz u tijeku…"));
  box.append(el("p", "muted", "ZIP se uvozi na serveru. Možeš raditi i drugo ili zatvoriti stranicu — uvoz se nastavlja."));
  const rows = {};
  ids.forEach(id => {
    const c = el("div", "card"); const h = el("h3", null, "…"); const bar = el("div", "progress");
    const fill = el("div"); fill.style.width = "0%"; bar.append(fill); const st = el("div", "muted");
    c.append(h, bar, st); box.append(c); rows[id] = { h, fill, st };
  });
  state.view = "import";
  showView(box);
  const done = {};
  while (Object.keys(done).length < ids.length) {
    for (const id of ids) {
      if (done[id]) continue;
      try {
        const j = (await json(await fetch("db/import-jobs/" + id))).job;
        const r = rows[id]; r.h.textContent = j.filename;
        const pct = j.total ? Math.round(100 * j.done / j.total) : 0;
        r.fill.style.width = pct + "%";
        r.st.textContent = j.status === "queued" ? "čeka…" :
          (j.done + "/" + (j.total || "?") + (j.current ? " · " + j.current : ""));
        if (j.status === "done" || j.status === "error") {
          done[id] = j;
          if (j.status === "error") reports.push({ file: j.filename, error: j.message });
          else reports.push(...j.reports);
        }
      } catch (e) { done[id] = { error: e.message }; reports.push({ file: "ZIP", error: e.message }); }
    }
    if (Object.keys(done).length < ids.length) await new Promise(res => setTimeout(res, 1500));
    if (state.view !== "import") {           // korisnik je otišao drugdje: traka u izborniku prati dalje
      refreshStats(); return;
    }
  }
  await Promise.all([loadList(), refreshStats(), loadTags()]);
  showReport(reports, tags);
}
const NOTE_STATUS = { attached: "uz pjesmu", new_song: "nova pjesma (zasad samo note)",
                      duplicate: "već imamo (preskočeno)", error: "greška" };
function showReport(reports, tags) {
  const box = el("div");
  box.append(el("h2", null, "Uvoz završen"));
  if (tags && tags.length) box.append(el("p", "muted", "Nove pjesme su dobile oznake: " + tags.join(", ")));
  let anyConflict = false;
  reports.forEach(r => {
    const c = el("div", "card");
    c.append(el("h3", null, r.file));
    if (r.error) { c.append(el("div", "bad", "✕ " + r.error)); box.append(c); return; }
    const ul = el("ul", "rep");
    const li = (cls, t) => ul.append(el("li", cls, t));
    if (r.added.length || !(r.notes || []).length)
      li("ok", "✓ dodano: " + r.added.length + (r.added.length ? " — " + r.added.map(x => x.title + (x.filled ? " (dopunjen tekst pjesme koja je imala samo note)" : "")).join(", ") : ""));
    (r.notes || []).forEach(n => {
      const item = el("li", n.status === "duplicate" ? "muted" : n.status === "error" ? "bad" : "ok");
      item.append(document.createTextNode("🎼 note" + (n.title ? " „" + n.title + "”" : "") +
                  (n.pages && n.pages.length ? " (str. " + n.pages.join(", ") + ")" : "") + " → "));
      if (n.song_id) {
        const a = el("a", null, n.song_title || ("#" + n.song_id)); a.href = "#";
        a.onclick = e => { e.preventDefault(); openSong(n.song_id); };
        item.append(a);
      }
      item.append(document.createTextNode(" · " + (NOTE_STATUS[n.status] || n.status) + (n.error ? ": " + n.error : "")));
      ul.append(item);
    });
    const same = r.duplicates.filter(x => x.reason === "same_song");
    const exact = r.duplicates.filter(x => x.reason !== "same_song");
    if (exact.length) li("muted", "= već u bazi, identične (preskočeno " + exact.length + "): " + exact.map(x => x.title).join(", "));
    if (same.length) li("muted", "= već imamo, druga verzija iste pjesme (preskočeno " + same.length + "): " +
       same.map(x => x.title + " → " + x.existing_title).join(", "));
    if (r.conflicts.length) {
      anyConflict = true;
      li("warn", "⚠ čekaju tvoju odluku (" + r.conflicts.length + "): " +
         r.conflicts.map(x => x.title + " ~ " + x.existing_title +
           (x.reason === "longer" ? " (nova ima više teksta)" : " " + Math.round(x.similarity * 100) + " %")).join(", "));
    }
    (r.warnings || []).forEach(w => li("muted", w));
    c.append(ul); box.append(c);
  });
  if (anyConflict) {
    const b = el("button", "btn primary", "Riješi slične pjesme →"); b.onclick = showPending; box.append(b);
  }
  state.view = "report";
  showView(box);
}

/* ── Slične pjesme: odluka ── */
async function showPending() {
  try {
    const data = await json(await fetch("db/pending"));
    const box = el("div");
    box.append(el("h2", null, "Slične pjesme — koju zadržati?"));
    state.view = "pending";
    if (!data.items.length) { box.append(el("p", "muted", "Nema ničega za riješiti. ✓")); showView(box); return; }
    box.append(el("p", "muted", "Retci koji se razlikuju su označeni bojom."));
    if (data.items.length > 1) {
      const bulk = el("div", "actions");
      bulk.append(el("span", "muted", "Za sve (" + data.items.length + "): "));
      [["keep_existing", "Zadrži postojeće"], ["replace", "Zamijeni novima"], ["keep_both", "Zadrži obje"]].forEach(([a, label]) => {
        const b = el("button", "btn", label);
        b.onclick = async () => {
          if (!confirm(label + " — za svih " + data.items.length + " pjesama?")) return;
          try { const r = await json(await api("db/pending/all", { method: "POST", headers: { "Content-Type": "application/json" },
                                                                body: JSON.stringify({ action: a }) }));
                toast("Riješeno: " + r.resolved); await refreshAll(); showPending(); }
          catch (e) { toast(e.message, true); }
        };
        bulk.append(b);
      });
      box.append(bulk);
    }
    data.items.forEach(p => {
      const c = el("div", "card");
      const h = el("h3");
      h.append(document.createTextNode(p.new.title + "  "),
               el("span", "simbadge", p.reason === "longer" ? "ista pjesma, nova ima više teksta"
                                                            : Math.round(p.similarity * 100) + " % slično"));
      c.append(h);
      const src = p.source && p.source.file ? (p.source.file + (p.source.pages && p.source.pages.length ? ", str. " + p.source.pages.join(", ") : "")) : "";
      const cmp = el("div", "cmp");
      const left = el("div"), right = el("div");
      left.append(el("h4", null, "Postojeća #" + p.existing.id + " · " + p.existing.title + (p.existing.key ? " · Key: " + p.existing.key : "")),
                  renderSheet(p.existing, p.new));
      right.append(el("h4", null, "Nova" + (src ? " (" + src + ")" : "") + (p.new.key ? " · Key: " + p.new.key : "")),
                   renderSheet(p.new, p.existing));
      cmp.append(left, right); c.append(cmp);
      const act = el("div", "actions");
      [["keep_existing", "Zadrži postojeću", ""], ["replace", "Zamijeni novom", "primary"], ["keep_both", "Zadrži obje", ""]]
        .forEach(([a, label, cls]) => {
          const b = el("button", "btn " + cls, label);
          b.onclick = async () => {
            try { await json(await api("db/pending/" + p.id, { method: "POST", headers: { "Content-Type": "application/json" },
                                                         body: JSON.stringify({ action: a }) }));
                  toast("Spremljeno."); await refreshAll(); showPending(); }
            catch (e) { toast(e.message, true); }
          };
          act.append(b);
        });
      c.append(act); box.append(c);
    });
    showView(box);
  } catch (e) { toast(e.message, true); }
}
$("pendingBar").onclick = showPending;

/* ── Odabrane pjesme ── */
function selectedIds() { return state.songs.filter(s => state.sel.has(s.id)).map(s => s.id); }
$("btnSelPdf").onclick = async () => {
  busy("Pripremam PDF…");
  try {
    const res = await fetch("db/pdf", { method: "POST", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ ids: selectedIds(), transpose: state.tr }) });
    if (!res.ok) await json(res);
    const a = document.createElement("a"); a.href = URL.createObjectURL(await res.blob());
    a.download = "pjesme_iz_baze.pdf"; a.click(); URL.revokeObjectURL(a.href);
  } catch (e) { toast(e.message, true); } finally { busy(null); }
};
$("btnSelEditor").onclick = async () => {
  try {
    const old = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    if (old && old.songs && old.songs.length &&
        !confirm("U uredniku već postoji započeta pjesmarica (" + old.songs.length + " pjesama). Zamijeniti je odabranim pjesmama iz baze?")) return;
    const data = await json(await fetch("db/to-editor", { method: "POST", headers: { "Content-Type": "application/json" },
                                                          body: JSON.stringify({ ids: selectedIds(), transpose: state.tr }) }));
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ filename: "pjesmarica_iz_baze",
      songs: data.songs.map(s => ({ ...s, include: true })), sel: 0, savedAt: Date.now() }));
    location.href = "./#nastavi";
  } catch (e) { toast(e.message, true); }
};

Promise.all([loadTags(), loadList(), refreshStats()]).catch(e => toast(e.message, true));
