"use strict";
/* Goldfish Brain local UI. Plain JS, no build step. All text from the API is inserted
   with textContent (never innerHTML). */

const app = document.getElementById("app");
const topbar = document.getElementById("top");
let S = null;               // last /api/state
let step = 0;               // welcome step
let timers = [];            // intervals to clear on route change
let watchingJob = false;    // user started (or is watching) a setup job
let searchToken = 0;

// ── helpers ─────────────────────────────────────────────────────────────────
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "hidden") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "Content-Type": "application/json" } };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  try {
    const r = await fetch("/api" + path, init);
    let data;
    try { data = await r.json(); } catch { data = { ok: false, message: "Goldfish Brain gave an unexpected answer." }; }
    return { ok: r.ok, status: r.status, data };
  } catch {
    return { ok: false, status: 0,
             data: { ok: false, message: "Goldfish Brain's engine isn't responding. Quit and reopen Goldfish Brain." } };
  }
}

function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 2200);
}

function initial(name) { return (name || "?").trim().charAt(0).toUpperCase() || "?"; }

function poster(url, name) {
  return url ? h("img", { class: "poster", src: url, alt: "", loading: "lazy" })
             : h("div", { class: "poster empty", "aria-hidden": "true", text: initial(name) });
}

function clearTimers() { timers.forEach(clearInterval); timers = []; }
function every(ms, fn) { timers.push(setInterval(fn, ms)); }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function refresh() {
  const r = await api("/state");
  if (r.ok) S = r.data;
  return r.ok;
}

// ── routing ─────────────────────────────────────────────────────────────────
function routeName() { return (location.hash.replace(/^#\/?/, "") || "").split("/")[0]; }

async function render() {
  clearTimers();
  if (!(await refresh())) {
    topbar.classList.add("hide-nav");
    app.replaceChildren(h("div", { class: "card stack" },
      h("h2", { text: "Goldfish Brain isn't responding" }),
      h("p", { class: "muted", text: "Quit and reopen Goldfish Brain from the menu bar. If this keeps happening, run ./start.sh from Terminal to see why." }),
      h("button", { class: "primary", onclick: render, text: "Try again" })));
    return;
  }
  let name = routeName();
  // Without a TMDB key nothing else works, so first-run is the only page.
  if (!S.setup.tmdb_key && name !== "welcome") { location.hash = "#/welcome"; return; }
  if (!name) name = S.shows.length ? "library" : "add";
  topbar.classList.toggle("hide-nav", name === "welcome");
  document.querySelectorAll("nav a").forEach(a => {
    if (a.dataset.route === (name === "map" ? "library" : name)) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const view = { welcome: viewWelcome, library: viewLibrary, add: viewAdd, settings: viewSettings, map: viewMap }[name] || viewLibrary;
  view();
}
window.addEventListener("hashchange", render);
window.addEventListener("focus", () => { if (routeName() === "welcome" && step === 1) viewWelcome(); });

// ── welcome / first run ─────────────────────────────────────────────────────
function dots(n) { return h("div", { class: "dots", "aria-hidden": "true" }, [0, 1, 2].map(i => h("i", { class: i <= n ? "on" : "" }))); }

async function viewWelcome() {
  if (step === 0) {
    app.replaceChildren(h("div", { class: "hero" },
      h("h1", { text: "Know who’s on screen." }),
      h("p", {}, "Press ", h("kbd", { text: "⌘⇧X" }), " while you watch. Goldfish Brain tells you who a character is, without spoiling what you haven’t seen yet."),
      h("button", { class: "primary", onclick: () => { step = 1; viewWelcome(); }, text: "Get started" })));
    return;
  }
  if (step === 1) {
    const p = (await api("/permissions")).data;
    const granted = p.screen_recording === true;
    app.replaceChildren(h("div", { class: "card stack" }, dots(0),
      h("h2", { text: "Let Goldfish Brain see your screen" }),
      h("p", { class: "muted", text: "Goldfish Brain takes a screenshot only when you press the shortcut, finds faces, and matches them on this Mac. Nothing leaves your computer for this." }),
      h("div", { class: "row spread" },
        h("div", {}, h("strong", { text: "Screen Recording" }), h("div", { class: "help", text: "Required to identify characters." })),
        granted ? h("span", { class: "pill ok", text: "✓ Allowed" })
                : h("button", { onclick: () => api("/open", { method: "POST", body: { target: "screen_recording" } }), text: "Open System Settings" })),
      !granted && h("p", { class: "help", text: p.screen_recording === false
        ? "Turn on Goldfish Brain (or Terminal, if you start it from there), then quit and reopen Goldfish Brain — macOS only applies this after a restart."
        : "Couldn’t check automatically. You can continue and fix this later in Settings." }),
      h("div", { class: "row spread" },
        h("div", {}, h("strong", { text: "Microphone" }), h("div", { class: "help", text: "Optional — only for Watch Buddy’s “react at quiet moments” mode." })),
        h("span", { class: "pill", text: "macOS asks when needed" })),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: () => { step = 2; viewWelcome(); }, text: "Continue" }),
        !granted && h("button", { class: "link", onclick: viewWelcome, text: "Check again" }))));
    return;
  }
  if (step === 2) return viewKeys();
}

function keyField({ name, label, optional, helpText, linkTarget, linkText, isSet }) {
  const input = h("input", { type: "password", autocomplete: "off", spellcheck: "false",
    placeholder: isSet ? "Saved — paste a new one to replace" : "Paste your key" });
  const msg = h("div", { class: "msg", role: "status" });
  const save = h("button", { class: "primary", text: "Check & save" });
  async function submit() {
    save.disabled = true; msg.className = "msg"; msg.textContent = "Checking…";
    const r = await api("/keys", { method: "POST", body: { name, value: input.value } });
    save.disabled = false;
    msg.className = "msg " + (r.data.ok ? "ok" : "bad");
    msg.textContent = r.data.ok ? "✓ Saved. " + (r.data.message || "") : r.data.message;
    if (r.data.ok) { input.value = ""; input.placeholder = "Saved — paste a new one to replace"; await refresh(); keyField.onSaved && keyField.onSaved(); }
  }
  save.addEventListener("click", submit);
  input.addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
  return h("div", {},
    h("label", { class: "field", for: name, text: label + (optional ? " (optional)" : "") }),
    Object.assign(input, { id: name }),
    h("div", { class: "help" }, helpText, " ",
      h("button", { class: "link", onclick: () => api("/open", { method: "POST", body: { target: linkTarget } }), text: linkText })),
    h("div", { class: "row", style: "margin-top:10px" }, save, isSet && h("span", { class: "pill ok", text: "✓ Key saved" })),
    msg);
}

function viewKeys() {
  const nextBtn = h("button", { class: "primary", text: "Continue" });
  function sync() {
    nextBtn.disabled = !(S && S.setup.tmdb_key);
  }
  keyField.onSaved = sync;
  nextBtn.addEventListener("click", () => { step = 0; location.hash = "#/add"; });
  app.replaceChildren(h("div", { class: "card stack" }, dots(1),
    h("h2", { text: "Two keys, one minute" }),
    h("p", { class: "muted", text: "Goldfish Brain runs on your Mac, so it uses your own keys. They’re stored in your Mac’s Keychain, never in a file." }),
    keyField({ name: "TMDB_API_KEY", label: "TMDB key", helpText: "Free. Used to search shows and download cast photos.", linkTarget: "tmdb_key_page", linkText: "Get a free key", isSet: S.setup.tmdb_key }),
    keyField({ name: "ANTHROPIC_API_KEY", label: "Anthropic (Claude) key", optional: true, helpText: "Turns on character notes and Watch Buddy. Uses Claude Haiku, the cheapest model — pennies per show. Face recognition works without it.", linkTarget: "anthropic_key_page", linkText: "Get a key", isSet: S.setup.anthropic_key }),
    h("div", { class: "row" }, nextBtn)));
  sync();
}

// ── add a show ──────────────────────────────────────────────────────────────
function viewAdd() {
  const job = S.job;
  if (job && job.state === "running") watchingJob = true;
  if (job && watchingJob) return viewJob();
  const input = h("input", { type: "search", id: "q", placeholder: "Search for a show, e.g. Breaking Bad", autocomplete: "off", "aria-label": "Search for a show" });
  const results = h("div", { class: "stack" });
  const detail = h("div");
  app.replaceChildren(h("div", { class: "stack" },
    h("div", {}, h("h1", { text: "Add a show" }), h("p", { class: "muted", text: "Search, pick the right poster, and Goldfish Brain learns the cast’s faces. It runs in the background." })),
    input, results, detail));
  input.focus();
  const run = debounce(async () => {
    const q = input.value.trim(); const mine = ++searchToken;
    detail.replaceChildren();
    if (!q) { results.replaceChildren(); return; }
    results.replaceChildren(h("p", { class: "muted", text: "Searching…" }));
    const r = await api("/shows/search?q=" + encodeURIComponent(q));
    if (mine !== searchToken) return;
    if (!r.ok) { results.replaceChildren(h("p", { class: "msg bad", text: r.data.message })); return; }
    const list = r.data.results;
    if (!list.length) { results.replaceChildren(h("p", { class: "muted", text: "No shows found. Try the full title." })); return; }
    results.replaceChildren(h("div", { class: "grid" }, list.map(s =>
      h("button", { class: "poster-card", onclick: () => pick(s, detail), "aria-label": s.name + (s.year ? " (" + s.year + ")" : "") },
        poster(s.poster_url, s.name),
        h("div", { class: "meta" }, h("div", { class: "name", text: s.name }),
          h("div", { class: "muted small", text: [s.year, s.installed ? "Already added" : ""].filter(Boolean).join(" · ") }))))));
  }, 250);
  input.addEventListener("input", run);
}

function pick(show, detail) {
  const notesOk = S.setup.anthropic_key;
  const notes = h("input", { type: "checkbox", id: "notes", checked: notesOk, disabled: !notesOk });
  const msg = h("div", { class: "msg", role: "status" });
  const go = h("button", { class: "primary", text: show.installed ? "Update this show" : "Add this show" });
  go.addEventListener("click", async () => {
    go.disabled = true; msg.className = "msg"; msg.textContent = "Starting…";
    const r = await api("/shows/setup", { method: "POST", body: {
      tmdb_id: show.tmdb_id, name: show.name, poster_path: show.poster_path,
      with_notes: notes.checked, update: !!show.installed } });
    if (!r.ok) { go.disabled = false; msg.className = "msg bad"; msg.textContent = r.data.message; return; }
    watchingJob = true; await refresh(); viewAdd();
  });
  detail.replaceChildren(h("div", { class: "card show" },
    poster(show.poster_url, show.name),
    h("div", { class: "stack" },
      h("div", {}, h("h2", { text: show.name + (show.year ? " (" + show.year + ")" : "") }), h("p", { class: "muted small", text: show.overview })),
      h("label", { class: "check", for: "notes" }, notes,
        h("span", {}, h("strong", { text: "Write character notes" }), h("div", { class: "help", text: notesOk ? "Who’s who, relationships and a spoiler-safe “right now” for every episode. Uses your Anthropic key and Claude Haiku (pennies)." : "Add an Anthropic key in Settings to turn this on. Faces work without it." }))),
      h("div", { class: "row" }, go),
      msg)));
  detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function viewJob() {
  const wrap = h("div", { class: "card stack" });
  app.replaceChildren(wrap);
  const draw = (job) => {
    if (!job) { watchingJob = false; viewAdd(); return; }
    const running = job.state === "running";
    wrap.replaceChildren(...[
      h("div", { class: "row spread" }, h("h2", { text: running ? "Setting up " + job.show_name : job.show_name }),
        h("span", { class: "pill " + ({ done: "ok", failed: "bad", cancelled: "warn" }[job.state] || ""), text: running ? job.percent + "%" : ({ done: "✓ Ready", failed: "Needs attention", cancelled: "Cancelled" }[job.state]) })),
      running && h("div", { class: "bar", role: "progressbar", "aria-valuenow": job.percent, "aria-valuemin": 0, "aria-valuemax": 100 }, h("div", { style: "width:" + job.percent + "%" })),
      h("p", { class: job.state === "failed" ? "msg bad" : "", text: job.message }),
      job.summary && h("p", { class: "muted", text: job.summary }),
      running && h("p", { class: "help", text: "You can close this window — setup keeps going in the background." }),
      h("details", {}, h("summary", { text: "Show details" }), h("pre", { class: "log", text: (job.details || []).join("\n") || "Nothing to show yet." })),
      h("div", { class: "row" },
        running && h("button", { onclick: async () => { await api("/shows/job/cancel", { method: "POST" }); }, text: "Cancel" }),
        job.state === "done" && h("button", { class: "primary", text: "Use this show now", onclick: async () => { await api("/shows/select", { method: "POST", body: { show_id: job.show_id } }); watchingJob = false; await refresh(); location.hash = "#/library"; toast("Ready — press ⌘⇧X while watching"); } }),
        !running && h("button", { onclick: () => { watchingJob = false; viewAdd(); }, text: job.state === "done" ? "Add another" : "Back" }))
    ].filter(Boolean));   // replaceChildren would print the string "null" for skipped parts
  };
  draw(S.job);
  every(1000, async () => {
    const r = await api("/shows/job");
    if (r.ok) { S.job = r.data.job; draw(S.job); if (S.job && S.job.state !== "running") { clearTimers(); await refresh(); } }
  });
}

// ── library ─────────────────────────────────────────────────────────────────
function viewLibrary() {
  clearTimers();   // a re-render must not stack another polling timer on top of the old one
  // Strictly a boolean: null !== false made the poll below redraw the page every 2 s (and stack timers).
  const running = !!(S.job && S.job.state === "running");
  const cards = S.shows.map(s => showCard(s));
  app.replaceChildren(h("div", { class: "stack" },
    h("div", { class: "row spread wrap" }, h("div", {}, h("h1", { text: "Your shows" }),
      h("p", { class: "muted", text: "Press ⌘⇧X while watching. Set where you are so spoilers stay hidden." })),
      h("a", { class: "btn primary", href: "#/add", style: "text-decoration:none", text: "Add a show" })),
    running && h("div", { class: "banner row spread" }, h("span", { text: "Setting up " + S.job.show_name + " — " + S.job.percent + "%" }), h("a", { href: "#/add", text: "View progress" })),
    cards.length ? cards : h("div", { class: "card" }, h("p", { class: "muted", text: "No shows yet." }), h("a", { href: "#/add", text: "Add your first show" }))));
  every(2000, async () => { const was = running; await refresh(); if (!!(S.job && S.job.state === "running") !== was) viewLibrary(); });
}

// The "N characters" list inside a library card: photo, actor, and whether story notes exist.
// Each tile opens an editor below the grid (the grid itself is never redrawn while editing).
// By default the server only lists characters the viewer has met; `showAll` re-requests with all=1 (spoilers).
function charList(data, showAll) {
  const spoilers = !!data.includes_spoilers;
  const missing = data.total - data.with_notes;
  const summary = h("p", { class: "chars-summary", text: data.with_notes + " with story notes" + (missing ? " · " + missing + " name only" : "") + (spoilers ? " · includes spoilers" : "") });
  const later = !spoilers && data.hidden_count > 0 && showAll
    ? h("p", { class: "chars-summary muted small" },
        data.hidden_count + (data.hidden_count === 1 ? " more character appears" : " more characters appear") + " later — ",
        h("button", { type: "button", class: "link", text: "Show them (spoilers)", onclick: showAll }))
    : null;
  const host = h("div", { class: "note-host" });
  let openTile = null;
  const grid = h("div", { class: "char-grid" }, data.characters.map(c => {
    const pill = h("span", { class: "pill " + (c.has_notes ? "ok" : ""), text: c.has_notes ? "story notes" : "name only" });
    const tile = h("button", { type: "button", class: "char", "aria-label": "Edit notes for " + c.name + (c.actor ? ", played by " + c.actor : ""), "aria-expanded": "false" },
      h("img", { class: "char-photo", src: c.photo_url, alt: "", loading: "lazy",
                 onerror: (e) => e.target.replaceWith(h("div", { class: "char-photo empty", "aria-hidden": "true", text: initial(c.name) })) }),
      h("div", { class: "char-name", text: c.name }),
      h("div", { class: "char-actor", text: c.actor || "" }),
      pill);
    tile.addEventListener("click", () => {
      if (openTile) { openTile.setAttribute("aria-expanded", "false"); openTile.classList.remove("open"); }
      openTile = tile; tile.setAttribute("aria-expanded", "true"); tile.classList.add("open");
      host.replaceChildren(noteEditor(spoilers, c, {
        saved: (hasNotes) => { pill.textContent = hasNotes ? "story notes" : "name only"; pill.className = "pill " + (hasNotes ? "ok" : ""); },
        close: () => { host.replaceChildren(); tile.setAttribute("aria-expanded", "false"); tile.classList.remove("open"); openTile = null; tile.focus(); },
      }));
      host.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    return tile;
  }));
  return [summary, later, grid, host].filter(Boolean);
}

const ARC_TYPES = ["other", "danger", "conflict", "alliance", "discovery", "deception", "loss", "turning_point"];
const STATUSES = ["unknown", "alive", "dead"];

// Editor for one character: relationships + "My notes" (user-written, per episode).
function noteEditor(spoilers, c, hooks) {
  const root = h("section", { class: "note-editor", "aria-label": "Notes for " + c.name });
  root.append(h("p", { class: "muted small", text: "Loading…" }));
  api("/characters/" + encodeURIComponent(c.character_id) + "/notes" + (spoilers ? "?all=1" : "")).then(r => {
    if (!r.ok) { root.replaceChildren(h("p", { class: "msg bad", text: r.data.message }), h("button", { type: "button", text: "Close", onclick: hooks.close })); return; }
    root.replaceChildren(...noteForm(c, r.data, hooks));
  });
  return root;
}

function noteForm(c, data, hooks) {
  const uid = "n" + Math.random().toString(36).slice(2, 8);
  const bio = h("textarea", { id: uid + "-bio", rows: 4, maxlength: 600, placeholder: "Who they are to the others, one per line" });
  bio.value = (data.relationship_summary || "").split(/;\s*|\n/).map(x => x.trim()).filter(Boolean).join("\n");
  const list = h("div", { class: "note-entries" });
  const msg = h("span", { class: "msg", role: "status", "aria-live": "polite" });
  const num = (label, value, min, max) => h("label", { class: "inline" }, label, h("input", { type: "number", min, max, value, "aria-label": label.trim() }));
  const select = (label, options, value) => {
    const el = h("select", { "aria-label": label }, options.map(o => h("option", { value: o, text: o.replace("_", " ") })));
    el.value = value; return el;
  };
  const areaField = (label, value) => {
    const id = uid + "-" + Math.random().toString(36).slice(2, 6);
    const ta = h("textarea", { id, rows: 2, maxlength: 300 }); ta.value = value || "";
    return { ta, node: h("div", { class: "field" }, h("label", { for: id, text: label }), ta) };
  };
  const rows = [];
  const addRow = (e) => {
    e = e || { season: 1, episode: 1, status: "unknown", arc_type: "other", current_arc: "", last_seen_description: "" };
    const season = num("S", e.season, 1, 50), episode = num("E", e.episode, 1, 500);
    const status = select("Status", STATUSES, e.status || "unknown");
    const arcType = select("Arc type", ARC_TYPES, e.arc_type || "other");
    const now = areaField("Right now", e.current_arc), scene = areaField("Last scene", e.last_seen_description);
    const row = { season, episode, status, arcType, now, scene };
    const node = h("div", { class: "note-entry" },
      h("div", { class: "row wrap note-top" }, h("span", { class: "lbl", text: "From" }), season, episode, status, arcType,
        h("button", { type: "button", class: "link", "aria-label": "Delete this note", text: "✕", onclick: () => { rows.splice(rows.indexOf(row), 1); node.remove(); } })),
      now.node, scene.node);
    row.node = node; rows.push(row); list.append(node);
    return row;
  };
  (data.entries || []).forEach(addRow);

  const saveBtn = h("button", { type: "button", class: "primary", text: "Save" });
  saveBtn.addEventListener("click", async () => {
    const entries = rows.map(r => ({ season: parseInt(r.season.querySelector("input").value, 10), episode: parseInt(r.episode.querySelector("input").value, 10),
      status: r.status.value, arc_type: r.arcType.value, current_arc: r.now.ta.value, last_seen_description: r.scene.ta.value }));
    const lines = bio.value.split("\n").map(x => x.trim()).filter(Boolean);
    saveBtn.disabled = true; msg.className = "msg"; msg.textContent = "Saving…";
    const res = await api("/characters/" + encodeURIComponent(c.character_id) + "/notes", { method: "PUT",
      body: { relationship_summary: lines.join("; "), entries } });
    saveBtn.disabled = false;
    if (res.ok && res.data.ok) {
      msg.className = "msg ok"; msg.textContent = "✓ Saved";
      hooks.saved(entries.some(e => e.current_arc.trim() || e.status !== "unknown" || e.last_seen_description.trim()) || data.ai_rows > 0);
    } else { msg.className = "msg bad"; msg.textContent = res.data.message || "Couldn't save."; }
  });

  return [
    h("h3", { class: "note-title" }, c.name, h("span", { class: "char-actor", text: c.actor ? "  played by " + c.actor : "" })),
    data.also_in && data.also_in.length ? h("p", { class: "also-in", text: "Also in: " + data.also_in.join(" · ") }) : null,
    h("div", { class: "field" }, h("label", { for: uid + "-bio", text: "Relationships (one per line)" }), bio),
    h("h4", { class: "note-sub", text: "My notes" }),
    data.ai_rows && !data.entries.length ? h("p", { class: "muted small", text: "AI notes: " + data.ai_rows + " episodes (read-only). Add a note for an episode to override it." }) : null,
    list,
    h("div", { class: "row wrap note-actions" },
      h("button", { type: "button", text: "+ Add a note", onclick: () => addRow().season.querySelector("input").focus() }),
      saveBtn, h("button", { type: "button", text: "Close", onclick: hooks.close }), msg),
  ];
}

function showCard(s) {
  const panel = h("div", { class: "chars", hidden: true });
  let loaded = false;
  const toggle = h("button", { class: "link chars-toggle", "aria-expanded": "false", text: s.total_characters + " characters ▸" });
  toggle.addEventListener("click", async () => {
    const opening = panel.hidden;
    panel.hidden = !opening;
    toggle.setAttribute("aria-expanded", String(opening));
    toggle.textContent = s.total_characters + " characters " + (opening ? "▾" : "▸");
    if (opening && !loaded) {
      loaded = true;
      const load = async (all) => {
        panel.replaceChildren(h("p", { class: "muted small", text: "Loading…" }));
        const r = await api("/shows/" + encodeURIComponent(s.show_id) + "/characters" + (all ? "?all=1" : ""));
        panel.replaceChildren(...(r.ok ? charList(r.data, () => load(true)) : [h("p", { class: "msg bad", text: r.data.message })]));
      };
      load(false);
    }
  });
  const save = debounce(async (season, episode) => {
    const r = await api("/position", { method: "POST", body: { show_id: s.show_id, season, episode } });
    toast(r.data.ok ? "Saved: S" + String(season).padStart(2, "0") + "E" + String(episode).padStart(2, "0") : r.data.message);
  }, 400);
  const mk = (label, key) => {
    const input = h("input", { type: "number", min: 1, value: s[key], "aria-label": label + " for " + s.show_name });
    const set = (v) => { s[key] = Math.max(1, v | 0 || 1); input.value = s[key]; save(s.season, s.episode); };
    input.addEventListener("change", () => set(parseInt(input.value, 10)));
    return h("div", { class: "stepper" }, h("span", { class: "lbl", text: label }),
      h("button", { "aria-label": "Previous " + label.toLowerCase(), onclick: () => set(s[key] - 1), text: "−" }), input,
      h("button", { "aria-label": "Next " + label.toLowerCase(), onclick: () => set(s[key] + 1), text: "+" }));
  };
  return h("div", { class: "card show" }, poster(s.poster_url, s.show_name),
    h("div", { class: "stack" },
      h("div", { class: "row spread" }, h("div", {}, h("h2", { text: s.show_name }), toggle),
        s.current ? h("span", { class: "pill ok", text: "● Watching" })
                  : h("button", { text: "Use this show", onclick: async () => { await api("/shows/select", { method: "POST", body: { show_id: s.show_id } }); await refresh(); viewLibrary(); } })),
      h("div", { class: "row wrap" }, mk("Season", "season"), mk("Episode", "episode"),
        h("a", { class: "btn map-link", href: "#/map/" + encodeURIComponent(s.show_id), text: "Relationship map" })),
      panel));
}

// ── relationship map ────────────────────────────────────────────────────────
const SVG_NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}

// Fruchterman-Reingold on a ring start: deterministic, no libraries. Returns [{x, y}] per node.
function layoutGraph(nodes, edges, W, H, r) {
  const n = nodes.length;
  const idx = new Map(nodes.map((nd, i) => [nd.id, i]));
  const links = edges.map(e => [idx.get(e.a), idx.get(e.b)]).filter(l => l[0] !== undefined && l[1] !== undefined);
  const cx = W / 2, cy = H / 2, padX = r + (W < 600 ? 44 : 56), padY = r + 30;
  const pos = nodes.map((_, i) => {
    const a = (2 * Math.PI * i) / Math.max(n, 1) - Math.PI / 2;
    return { x: cx + Math.cos(a) * (W / 2 - padX), y: cy + Math.sin(a) * (H / 2 - padY), dx: 0, dy: 0 };
  });
  if (n < 2) return pos;
  const k = Math.sqrt((W * H) / n) * 1.0;
  const steps = 320;
  for (let it = 0; it < steps; it++) {
    const temp = (1 - it / steps) * W * 0.08 + 0.5;
    pos.forEach(p => { p.dx = 0; p.dy = 0; });
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      let dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y;
      let d = Math.hypot(dx, dy);
      if (d < 0.01) { dx = 0.1 * ((i % 3) - 1); dy = 0.1 * ((j % 3) - 1); d = 0.15; }
      let f = (k * k) / d;
      if (d < r * 3.6) f += r * 60;                 // photos must not overlap
      pos[i].dx += (dx / d) * f; pos[i].dy += (dy / d) * f;
      pos[j].dx -= (dx / d) * f; pos[j].dy -= (dy / d) * f;
    }
    for (const [a, b] of links) {
      const dx = pos[a].x - pos[b].x, dy = pos[a].y - pos[b].y;
      const d = Math.max(Math.hypot(dx, dy), 0.01), f = (d * d) / k;
      pos[a].dx -= (dx / d) * f; pos[a].dy -= (dy / d) * f;
      pos[b].dx += (dx / d) * f; pos[b].dy += (dy / d) * f;
    }
    pos.forEach(p => {
      p.dx += (cx - p.x) * 0.25; p.dy += (cy - p.y) * 0.25;   // gentle gravity
      const d = Math.max(Math.hypot(p.dx, p.dy), 0.01), m = Math.min(d, temp);
      p.x = Math.min(W - padX, Math.max(padX, p.x + (p.dx / d) * m));
      p.y = Math.min(H - padY, Math.max(padY, p.y + (p.dy / d) * m));
    });
  }
  return pos;
}

async function viewMap() {
  clearTimers();
  const showId = decodeURIComponent(location.hash.split("/")[2] || "");
  const show = S.shows.find(x => x.show_id === showId);
  const back = h("a", { class: "btn", href: "#/library", text: "← Library" });
  if (!show) {
    app.replaceChildren(h("div", { class: "stack" }, back, h("div", { class: "card" }, h("p", { class: "muted", text: "That show isn't in your library." }))));
    return;
  }
  const code = "S" + String(show.season).padStart(2, "0") + "E" + String(show.episode).padStart(2, "0");
  const host = h("div", { class: "map-host" }, h("p", { class: "muted small", text: "Loading…" }));
  app.replaceChildren(h("div", { class: "stack" },
    h("div", { class: "row spread wrap" }, h("div", {}, h("h1", { text: "Relationship map" }),
      h("p", { class: "muted", text: show.show_name + " · only people you've met by " + code })), back),
    h("div", { class: "card map-card" }, host)));
  const r = await api("/shows/" + encodeURIComponent(showId) + "/relationships?season=" + show.season + "&episode=" + show.episode);
  if (!r.ok) { host.replaceChildren(h("p", { class: "msg bad", text: r.data.error || r.data.message })); return; }
  const { nodes, edges } = r.data;
  const linked = new Set(edges.flatMap(e => [e.a, e.b]));
  const shown = nodes.filter(nd => linked.has(nd.id));
  if (!edges.length) {
    host.replaceChildren(h("div", { class: "map-empty" },
      h("p", { class: "serif-lg", text: "No connections on file yet." }),
      h("p", { class: "muted", text: nodes.length
        ? "Notes for " + show.show_name + " don't mention how the " + nodes.length + " characters you've met are related. Add relationships in the character editor, or move your episode forward."
        : "Nobody has been introduced by " + code + " yet." })));
    return;
  }
  drawMap(host, shown, edges, nodes.filter(nd => !linked.has(nd.id)));
}

function drawMap(host, nodes, edges, loose) {
  const narrow = host.clientWidth < 560 || window.innerWidth < 560;
  const n = nodes.length;
  const W = narrow ? 420 : Math.max(480, Math.min(880, n * 62));
  const H = narrow ? Math.max(380, Math.min(n * 50, 900)) : Math.max(340, Math.min(n * 44, 820));
  const R = narrow ? 22 : 28;
  const pos = layoutGraph(nodes, edges, W, H, R);
  const at = new Map(nodes.map((nd, i) => [nd.id, pos[i]]));
  const root = svg("svg", { viewBox: "0 0 " + W + " " + H, class: "map-svg", style: "max-width:" + W + "px", role: "img",
    "aria-label": "Relationship map: " + nodes.length + " characters, " + edges.length + " connections" });
  const defs = svg("defs", {}, nodes.map(nd => svg("clipPath", { id: "clip-" + nd.id }, svg("circle", { r: R }))));
  const edgeEls = edges.map(e => {
    const a = at.get(e.a), b = at.get(e.b);
    const line = svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "map-edge" });
    const label = svg("text", { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, class: "map-label", "text-anchor": "middle", "dominant-baseline": "middle" }, e.label);
    return { e, line, label };
  });
  let selected = null;
  const nodeEls = new Map();
  function select(id) {
    selected = selected === id ? null : id;
    const ties = new Set([selected]);
    edgeEls.forEach(x => { if (x.e.a === selected || x.e.b === selected) { ties.add(x.e.a); ties.add(x.e.b); } });
    edgeEls.forEach(x => {
      const on = selected !== null && (x.e.a === selected || x.e.b === selected);
      x.line.classList.toggle("hot", on); x.line.classList.toggle("dim", selected !== null && !on);
      x.label.classList.toggle("hot", on); x.label.classList.toggle("dim", selected !== null && !on);
    });
    nodeEls.forEach((g, nid) => {
      g.classList.toggle("dim", selected !== null && !ties.has(nid));
      g.classList.toggle("sel", nid === selected);
      g.setAttribute("aria-pressed", String(nid === selected));
    });
    note.textContent = selected === null ? "Tap a face to see who they're connected to."
      : nodes.find(n => n.id === selected).name + ": " + edgeEls.filter(x => x.e.a === selected || x.e.b === selected)
          .map(x => x.e.label + " — " + nodes.find(n => n.id === (x.e.a === selected ? x.e.b : x.e.a)).name).join(" · ");
  }
  const nodeGroups = nodes.map(nd => {
    const p = at.get(nd.id);
    const img = svg("image", { href: nd.photo_url, x: -R, y: -R, width: 2 * R, height: 2 * R, "clip-path": "url(#clip-" + nd.id + ")", preserveAspectRatio: "xMidYMid slice" });
    img.addEventListener("error", () => img.remove());
    const g = svg("g", { class: "map-node", transform: "translate(" + p.x + " " + p.y + ")", tabindex: "0", role: "button", "aria-pressed": "false", "aria-label": nd.name },
      svg("circle", { r: R + 3, class: "map-ring" }),
      svg("circle", { r: R, class: "map-photo-bg" }),
      svg("text", { class: "map-initial", "text-anchor": "middle", "dominant-baseline": "central" }, initial(nd.name)),
      img,
      svg("text", { y: R + 15, class: "map-name", "text-anchor": "middle" }, nd.name));
    g.addEventListener("click", ev => { ev.stopPropagation(); select(nd.id); });
    g.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(nd.id); } });
    nodeEls.set(nd.id, g);
    return g;
  });
  root.addEventListener("click", () => { if (selected !== null) select(selected); });
  root.append(defs, ...edgeEls.map(x => x.line), ...edgeEls.map(x => x.label), ...nodeGroups);
  const note = h("p", { class: "map-note", "aria-live": "polite", text: "Tap a face to see who they're connected to." });
  host.replaceChildren(root, note);
  if (loose.length) host.append(h("p", { class: "map-loose", text: "Met, but no ties on file: " + loose.map(n => n.name).join(", ") }));
}

// ── settings ────────────────────────────────────────────────────────────────
async function viewSettings() {
  const r = await api("/buddy");
  const b = r.data.buddy, personalities = r.data.personalities;
  const f = {
    name: h("input", { type: "text", value: b.name, id: "b-name", maxlength: 24 }),
    personality: h("select", { id: "b-pers" }, personalities.map(p => h("option", { value: p, text: p.replace("_", " ") }))),
    voice: h("input", { type: "text", value: b.voice, id: "b-voice", list: "voices" }),
    cooldown: h("input", { type: "number", min: 0, step: 1, value: b.cooldown_minutes, id: "b-cool" }),
    mode: h("select", { id: "b-mode" }, h("option", { value: "audio", text: "At quiet moments (uses mic)" }), h("option", { value: "timer", text: "Every few minutes (no mic)" })),
    enabled: h("input", { type: "checkbox", id: "b-en", checked: b.enabled }),
    only: h("input", { type: "checkbox", id: "b-only", checked: b.share_only_window }),
    ack: h("input", { type: "checkbox", id: "b-ack", checked: b.disclosure_ack }),
  };
  f.personality.value = b.personality; f.mode.value = b.trigger_mode;
  const msg = h("div", { class: "msg", role: "status" });
  const save = h("button", { class: "primary", text: "Save", onclick: async () => {
    const res = await api("/buddy", { method: "POST", body: {
      name: f.name.value, personality: f.personality.value, voice: f.voice.value,
      cooldown_minutes: parseFloat(f.cooldown.value || "0"), trigger_mode: f.mode.value,
      enabled: f.enabled.checked, share_only_window: f.only.checked, disclosure_ack: f.ack.checked } });
    msg.className = "msg " + (res.data.ok ? "ok" : "bad"); msg.textContent = res.data.ok ? "✓ Saved" : res.data.message;
    if (res.data.ok) toast("Settings saved");
  } });
  const keyRow = (label, isSet, name, target) => h("div", { class: "row spread" },
    h("div", {}, h("strong", { text: label }), h("div", { class: "help", text: isSet ? "Saved in your Keychain" : "Not set" })),
    h("div", { class: "row" },
      h("span", { class: "pill " + (isSet ? "ok" : "warn"), text: isSet ? "✓ Set" : "Missing" }),
      h("button", { text: isSet ? "Replace" : "Add", onclick: () => keyEditor(name) })));
  const keysHost = h("div", { class: "card stack" });
  function drawKeys() {
    keysHost.replaceChildren(h("h2", { text: "Keys" }),
      keyRow("TMDB key", S.setup.tmdb_key, "TMDB_API_KEY"), keyRow("Anthropic (Claude) key", S.setup.anthropic_key, "ANTHROPIC_API_KEY"));
  }
  function keyEditor(name) {
    const cfg = name === "TMDB_API_KEY"
      ? { name, label: "TMDB key", helpText: "Free.", linkTarget: "tmdb_key_page", linkText: "Get a free key", isSet: S.setup.tmdb_key }
      : { name, label: "Anthropic (Claude) key", optional: true, helpText: "For character notes and Watch Buddy (Claude Haiku).", linkTarget: "anthropic_key_page", linkText: "Get a key", isSet: S.setup.anthropic_key };
    keyField.onSaved = () => drawKeys();
    keysHost.replaceChildren(h("h2", { text: "Keys" }), keyField(cfg), h("button", { class: "link", onclick: drawKeys, text: "Done" }));
  }
  drawKeys();
  const perm = (await api("/permissions")).data;
  app.replaceChildren(h("div", { class: "stack" }, h("h1", { text: "Settings" }),
    keysHost,
    h("div", { class: "card stack" }, h("h2", { text: "Permissions" }),
      h("div", { class: "row spread" }, h("div", {}, h("strong", { text: "Screen Recording" }), h("div", { class: "help", text: "Needed to identify characters. After turning it on, quit and reopen Goldfish Brain." })),
        perm.screen_recording === true ? h("span", { class: "pill ok", text: "✓ Allowed" })
          : h("button", { onclick: () => api("/open", { method: "POST", body: { target: "screen_recording" } }), text: "Open System Settings" }))),
    h("div", { class: "card stack" }, h("h2", { text: "Watch Buddy" }),
      !b.disclosure_ack && h("div", { class: "banner", text: "Watch Buddy is paused until you tick the privacy box below." }),
      h("div", {}, h("label", { class: "field", for: "b-name", text: "Name" }), f.name),
      h("div", {}, h("label", { class: "field", for: "b-pers", text: "Personality" }), f.personality),
      h("div", {}, h("label", { class: "field", for: "b-voice", text: "Voice" }), f.voice,
        h("datalist", { id: "voices" }, ["Samantha", "Alex", "Daniel", "Karen", "Moira", "Tessa", "Fred"].map(v => h("option", { value: v }))),
        h("div", { class: "help", text: "Any macOS voice name (System Settings → Accessibility → Spoken Content)." })),
      h("div", {}, h("label", { class: "field", for: "b-mode", text: "Chime in" }), f.mode, h("div", { class: "help", text: "“Quiet moments” listens to your Mac’s input device — use BlackHole to hear the show itself (see ONBOARDING.md)." })),
      h("div", {}, h("label", { class: "field", for: "b-cool", text: "At most once every (minutes)" }), f.cooldown, h("div", { class: "help", text: "0 = as often as allowed. Each reaction is one small, cheap Claude Haiku call." })),
      h("label", { class: "check", for: "b-en" }, f.enabled, h("span", { text: "Watch Buddy is on" })),
      h("label", { class: "check", for: "b-only" }, f.only, h("span", {}, h("strong", { text: "Share only the browser window" }), h("div", { class: "help", text: "Recommended. Otherwise the whole desktop is sent." }))),
      h("label", { class: "check", for: "b-ack" }, f.ack, h("span", {}, h("strong", { text: "I understand Buddy sends a screenshot to Anthropic (Claude)" }), h("div", { class: "help", text: "To react to the scene, Watch Buddy uploads a screenshot to Anthropic each time it speaks." }))),
      h("div", { class: "row" }, save), msg)));
}

render();
