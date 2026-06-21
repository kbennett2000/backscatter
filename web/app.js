"use strict";

// Basemap style URLs (keyless OpenFreeMap) + chrome come from theme.js `STYLES`.
const RADAR_SOURCE = "radar-frame";
const RADAR_LAYER = "radar-frame-layer";
const LOC_SOURCE = "locations"; // GeoJSON source feeding the location pins
const LOC_CIRCLE = "loc-circle";
const LOC_LABEL = "loc-label";
const PRELOAD_AHEAD = 3; // warm the next few PNGs so playback doesn't jank
const PAGE_SIZE = 20; // frames per request when paging an explicit window
const PAGE_FETCH_AHEAD = 3; // fetch the next page when this close to the end
const LS_KEY = "backscatter.location"; // last-selected location, across reloads
const LS_THEME = "backscatter.theme"; // Slice-21 light/dark pref (migrated → basemap)
const LS_BASEMAP = "backscatter.basemap"; // chosen map style key, across reloads
const LS_OPACITY = "backscatter.opacity"; // radar layer opacity, across reloads

const $ = (id) => document.getElementById(id);
const readout = $("readout");
const statusEl = $("status");
const statepanel = $("statepanel");
const spTitle = $("sp-title");
const spBody = $("sp-body");
const spAction = $("sp-action");
const spBackfill = $("sp-backfill");
const spProgress = $("sp-progress");
const spProgressText = $("sp-progress-text");
const spProgressFill = $("sp-progress-fill");
const spError = $("sp-error");
const rangebar = $("rangebar");
const timeline = $("timeline");
const playBtn = $("play");
const scrubber = $("scrubber");
const gaptrack = $("gaptrack");
const gapflag = $("gapflag");
const frametime = $("frametime");
const speed = $("speed");
const startInput = $("start");
const endInput = $("end");
const extentLabel = $("extent");
const locwrap = $("locwrap");
const locationSelect = $("location");
const manageBtn = $("manage");
const themeBtn = $("theme");
const winToggle = $("wintoggle");
const windowctl = $("windowctl");
const opacityInput = $("opacity");
const basemapSelect = $("basemap");
const locpanel = $("locpanel");
const loclist = $("loclist");
const locform = $("locform");
const lfName = $("lf-name");
const lfLat = $("lf-lat");
const lfLon = $("lf-lon");
const lfDefault = $("lf-default");
const lfSave = $("lf-save");
const formTitle = $("formtitle");
const locError = $("locerror");

const state = {
  map: null,
  locations: [], // [{name, lat, lon, default, site}] from /api/locations
  location: null, // active location name (runtime state)
  site: null, // active location's resolved radar
  frames: [],
  gaps: [], // [{afterIndex, seconds}] missing-data spans in the loaded window
  index: 0,
  playing: false,
  timer: null,
  preloaded: new Map(),
  editingId: null, // location id being edited, or null in add mode
  picking: false, // map-click sets the form's lat/lon
  layerReady: false,
  opacity: clampOpacity(localStorage.getItem(LS_OPACITY)), // radar layer opacity
  basemap: resolveInitialBasemap(
    localStorage.getItem(LS_BASEMAP) || localStorage.getItem(LS_THEME),
    window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches,
  ),
  extent: { min: null, max: null, count: 0 },
  view: "has-data", // 'has-data' | 'wrong-window' | 'empty' (first-run state)
  pollTimer: null, // setInterval handle for the new-frame poll
  backfillJobId: null, // id of the running one-click backfill job, or null
  backfillTimer: null, // setTimeout handle for the job-status poll
  backfillActive: false, // a backfill is in flight (suppresses the 30s frame poll)
  window: null, // {start, end} ISO when an explicit window is loaded; null = recent
  nextCursor: null,
  fetching: false,
};

const enc = encodeURIComponent;

function cornersFromBounds(b) {
  // Image-source corners, clockwise from top-left: TL, TR, BR, BL.
  return [
    [b.west, b.north],
    [b.east, b.north],
    [b.east, b.south],
    [b.west, b.south],
  ];
}

// Time helpers (isoToLocalInput / localInputToIso / fmtLocalTime / fmtLocalDateTime) are
// pure functions in timefmt.js, loaded before this script. Everything shown/entered is
// local; storage/queries stay UTC.

function escapeHtml(s) {
  return String(s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function isoMinusHours(iso, hours) {
  const t = new Date(iso).getTime() - hours * 3600_000;
  return new Date(t).toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function fetchFrames({ start, end, cursor, limit }) {
  // Server resolves the active location to its radar (Slice 8 `location` param).
  const p = new URLSearchParams({ location: state.location });
  if (start) p.set("start", start);
  if (end) p.set("end", end);
  if (cursor) p.set("cursor", cursor);
  if (limit) p.set("limit", String(limit));
  return fetch(`/api/frames?${p.toString()}`).then((r) => r.json());
}

// --- location management (CRUD) ---------------------------------------------

async function refreshLocations() {
  const data = await fetch("/api/locations").then((r) => r.json());
  state.locations = data.locations || [];
  // If the active location was removed, fall back to the default.
  if (!state.locations.some((l) => l.name === state.location)) {
    const fallback =
      state.locations.find((l) => l.default) || state.locations[0];
    if (fallback) await switchLocation(fallback.name);
  }
  populateSelector(state.location);
  refreshLocationMarkers(); // add/move/remove pins to match the edited list
  renderLocList();
}

function renderLocList() {
  loclist.innerHTML = "";
  for (const loc of state.locations) {
    const row = document.createElement("div");
    row.className = "locrow";
    const label = document.createElement("span");
    label.className = "grow";
    label.textContent =
      `${loc.default ? "★ " : ""}${loc.name} · ${loc.site} · ` +
      `${loc.lat.toFixed(3)}, ${loc.lon.toFixed(3)}`;
    row.appendChild(label);
    if (!loc.default) {
      row.appendChild(_btn("default", "ghost", () => setDefault(loc.id)));
    }
    row.appendChild(_btn("edit", "ghost", () => startEdit(loc)));
    row.appendChild(_btn("delete", "ghost", () => deleteLocation(loc.id)));
    loclist.appendChild(row);
  }
}

function _btn(text, cls, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = cls;
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

function startAdd() {
  state.editingId = null;
  formTitle.textContent = "Add location";
  lfSave.textContent = "Add";
  lfName.value = "";
  lfLat.value = "";
  lfLon.value = "";
  lfDefault.checked = false;
  locError.textContent = "";
}

function startEdit(loc) {
  state.editingId = loc.id;
  formTitle.textContent = `Edit ${loc.name}`;
  lfSave.textContent = "Save";
  lfName.value = loc.name;
  lfLat.value = loc.lat;
  lfLon.value = loc.lon;
  lfDefault.checked = loc.default;
  locError.textContent = "";
}

async function submitForm(ev) {
  ev.preventDefault();
  locError.textContent = "";
  const body = {
    name: lfName.value.trim(),
    lat: Number(lfLat.value),
    lon: Number(lfLon.value),
    default: lfDefault.checked,
  };
  const editing = state.editingId !== null;
  const url = editing ? `/api/locations/${state.editingId}` : "/api/locations";
  const resp = await fetch(url, {
    method: editing ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    locError.textContent = data.detail || `error ${resp.status}`;
    return;
  }
  startAdd();
  await refreshLocations();
}

async function deleteLocation(id) {
  const resp = await fetch(`/api/locations/${id}`, { method: "DELETE" });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    locError.textContent = data.detail || `error ${resp.status}`;
    return;
  }
  await refreshLocations();
}

async function setDefault(id) {
  const resp = await fetch(`/api/locations/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ default: true }),
  });
  if (resp.ok) await refreshLocations();
}

async function main() {
  const [, locData] = await Promise.all([
    fetch("/api/config").then((r) => r.json()),
    fetch("/api/locations").then((r) => r.json()),
  ]);
  state.locations = locData.locations || [];
  const active = pickInitialLocation();
  state.location = active.name;
  state.site = active.site;
  populateSelector(active.name);

  // The <head> shim already set data-theme to match; start the map on the chosen basemap.
  state.map = new maplibregl.Map({
    container: "map",
    style: basemapUrl(state.basemap),
    center: [active.lon, active.lat],
    zoom: 7,
  });
  state.map.addControl(new maplibregl.NavigationControl(), "top-right");
  state.map.on("load", init);
  state.map.on("click", (e) => {
    if (!state.picking) return;
    lfLat.value = e.lngLat.lat.toFixed(4);
    lfLon.value = e.lngLat.lng.toFixed(4);
    state.picking = false;
  });
}

function pickInitialLocation() {
  const saved = localStorage.getItem(LS_KEY);
  return (
    state.locations.find((l) => l.name === saved) ||
    state.locations.find((l) => l.default) ||
    state.locations[0]
  );
}

function populateSelector(activeName) {
  locationSelect.innerHTML = "";
  for (const loc of state.locations) {
    const opt = document.createElement("option");
    opt.value = loc.name;
    opt.textContent = `${loc.name} · ${loc.site}`;
    locationSelect.appendChild(opt);
  }
  locationSelect.value = activeName;
  // A single location renders exactly as before — no point offering a switcher.
  locwrap.hidden = state.locations.length < 2;
}

async function switchLocation(name) {
  const loc = state.locations.find((l) => l.name === name);
  if (!loc) return;
  pause();
  state.location = loc.name;
  state.site = loc.site;
  localStorage.setItem(LS_KEY, loc.name);
  refreshLocationMarkers(); // restyle which pin is highlighted as active
  state.map.flyTo({ center: [loc.lon, loc.lat], zoom: 7 });
  await refreshExtent();
  await loadDefault(); // re-point the timeline at this location's recent window
}

async function init() {
  ensureLocationLayers(); // pins for every configured location, above the radar
  refreshLocationMarkers();
  updateThemeButton(chromeFor(state.basemap));
  populateBasemapSelect();
  await refreshExtent();
  wireControls();
  await loadDefault(); // recent rolling window (unchanged default UX)
}

// --- basemap + chrome theme -------------------------------------------------

function updateThemeButton(chrome) {
  // Show the glyph for the mode you'd switch TO.
  themeBtn.textContent = chrome === "dark" ? "☀" : "☾";
}

function populateBasemapSelect() {
  basemapSelect.innerHTML = "";
  for (const [key, s] of Object.entries(STYLES)) {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = s.label;
    basemapSelect.appendChild(opt);
  }
  basemapSelect.value = state.basemap;
}

// Switch the keyless basemap and derive the UI chrome (light/dark) from it. The radar
// dBZ palette is untouched (it's baked into the PNGs).
function applyBasemap(key) {
  if (!isValidBasemap(key)) return;
  state.basemap = key;
  const chrome = chromeFor(key);
  document.documentElement.dataset.theme = chrome;
  try {
    localStorage.setItem(LS_BASEMAP, key);
  } catch (e) {
    /* private mode / storage disabled — applies for this session */
  }
  updateThemeButton(chrome);
  basemapSelect.value = key;
  // setStyle() wipes every custom source/layer, so rebuild ours after the new basemap
  // settles. Re-add on the next `idle` (fires once the style is applied and tile/sprite
  // requests have settled — even if a basemap sprite 404s, unlike isStyleLoaded()).
  state.map.once("idle", reapplyMapLayers);
  // diff:false forces a clean reload (a style diff can leave our custom layers in a
  // half-state), so reapplyMapLayers always rebuilds from scratch.
  state.map.setStyle(basemapUrl(key), { diff: false });
}

function reapplyMapLayers() {
  state.layerReady = false;
  ensureLocationLayers();
  refreshLocationMarkers();
  if (state.frames.length) {
    ensureLayer(state.frames[state.index]); // re-add the radar image for the current frame
    setRadarVisible(true);
  }
}

// A pin per configured location: a circle + a name label. The active location is
// amber and larger; the others are muted. Built once; data refreshed via setData.
function ensureLocationLayers() {
  if (state.map.getSource(LOC_SOURCE)) return;
  state.map.addSource(LOC_SOURCE, {
    type: "geojson",
    data: locationFeatures([], null),
  });
  state.map.addLayer({
    id: LOC_CIRCLE,
    type: "circle",
    source: LOC_SOURCE,
    layout: { "circle-sort-key": ["case", ["get", "active"], 1, 0] },
    paint: {
      "circle-radius": ["case", ["get", "active"], 8, 5.5],
      "circle-color": ["case", ["get", "active"], "#ffb020", "#ffffff"],
      "circle-stroke-color": ["case", ["get", "active"], "#3a2600", "#0b1018"],
      "circle-stroke-width": 2,
    },
  });
  state.map.addLayer({
    id: LOC_LABEL,
    type: "symbol",
    source: LOC_SOURCE,
    layout: {
      "text-field": ["get", "name"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 12,
      "text-offset": [0, 1.1],
      "text-anchor": "top",
      // Let close labels collide-hide rather than smash into an unreadable mess.
      "text-allow-overlap": false,
    },
    paint: {
      "text-color": ["case", ["get", "active"], "#ffb020", "#ffffff"],
      "text-halo-color": "#0b1018",
      "text-halo-width": 1.6,
    },
  });
}

function refreshLocationMarkers() {
  const src = state.map.getSource(LOC_SOURCE);
  if (src) src.setData(locationFeatures(state.locations, state.location));
}

async function refreshExtent() {
  const r = await fetch(`/api/frames/range?location=${enc(state.location)}`).then(
    (x) => x.json(),
  );
  applyExtent(r);
}

// Record the archive extent and refresh the picker bounds + the live status cue.
function applyExtent(r) {
  state.extent = { min: r.min, max: r.max, count: r.count };
  if (r.min && r.max) {
    startInput.min = endInput.min = isoToLocalInput(r.min);
    startInput.max = endInput.max = isoToLocalInput(r.max);
    extentLabel.textContent =
      `archive: ${fmtLocalDateTime(r.min)} – ${fmtLocalDateTime(r.max)} (${r.count})`;
  }
  updateStatus();
}

// The always-visible freshness cue: the newest frame's age in plain language, with a
// "● Live" badge when the view is tracking newest. Tapping it jumps to latest (wired in
// wireControls) — the reachable "go live" affordance on mobile, where Latest is otherwise
// inside the Window drawer.
function updateStatus() {
  const now = Date.now();
  const onLast =
    state.frames.length > 0 && state.index === state.frames.length - 1;
  const live = isLiveView(state.view, state.window !== null, onLast);
  statusEl.textContent = statusText(state.extent.max, now, live, (iso) =>
    relativeAge(iso, now),
  );
  statusEl.classList.toggle("live", live); // brighter when tracking newest
  statusEl.hidden = false;
}

// --- first-run state messaging ----------------------------------------------

function showStatePanel(view) {
  // Don't stomp a backfill that's already running in this same card.
  if (state.backfillActive) {
    statepanel.hidden = false;
    return;
  }
  resetBackfillUI();
  if (view === "empty") {
    spTitle.textContent = "Collecting radar now";
    spBody.innerHTML =
      `backscatter is collecting radar for <strong>${escapeHtml(state.location)}</strong> ` +
      "now. Your first frame should appear within about 5 minutes — this page updates on " +
      "its own. Don't want to wait? Load the last few hours of radar right now. " +
      '<a href="https://kbennett2000.github.io/backscatter/help/' +
      '#i-dont-want-to-wait-can-i-load-past-radar" target="_blank" rel="noopener">' +
      "What's this?</a>";
    spAction.hidden = true;
    spBackfill.textContent = "Load recent radar now";
    spBackfill.classList.remove("secondary"); // primary CTA on the first-run card
    spBackfill.hidden = false;
  } else {
    // wrong-window: the archive HAS data, just not for the picked range.
    const from = state.extent.min ? fmtLocalDateTime(state.extent.min) : "—";
    const to = state.extent.max ? fmtLocalDateTime(state.extent.max) : "—";
    spTitle.textContent = "No radar in this time window";
    spBody.innerHTML =
      "There's no radar saved for the time range you picked. You have data from " +
      `<strong>${escapeHtml(from)}</strong> to <strong>${escapeHtml(to)}</strong> ` +
      "(your local time).";
    spAction.textContent = "Jump to latest";
    spAction.hidden = false;
    spBackfill.textContent = "Load recent radar";
    spBackfill.classList.add("secondary"); // "Jump to latest" is the primary action here
    spBackfill.hidden = false;
  }
  statepanel.hidden = false;
}

function hideStatePanel() {
  statepanel.hidden = true;
}

// Reset the in-card backfill button/progress to its idle look (button enabled,
// progress + error hidden). Called whenever the panel is (re)shown.
function resetBackfillUI() {
  spBackfill.disabled = false;
  spProgress.hidden = true;
  spError.hidden = true;
  spProgressFill.style.width = "0%";
}

// --- one-click backfill (Slice 19) ------------------------------------------
// Kick off a server-side backfill of recent radar, then poll its status and show
// plain-language progress in the same card. On success the timeline auto-populates;
// on failure we say so plainly and let the user retry.

const BACKFILL_HOURS = 6; // a single click loads the last 6 hours

async function startBackfill() {
  if (state.backfillActive) return; // guard double-click
  state.backfillActive = true;
  spBackfill.disabled = true;
  spError.hidden = true;
  spProgress.hidden = false;
  showBackfillProgress(null); // "Starting…"
  maybePoll(); // pause the 30s frame poll while the backfill drives refreshes

  let resp;
  try {
    resp = await fetch("/api/backfill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ location: state.location, hours: BACKFILL_HOURS }),
    });
  } catch {
    return failBackfill("Couldn't reach the server — check your connection and try again.");
  }

  if (resp.status === 409) {
    // Already running (e.g. another tab) — attach to that job instead of erroring.
    const data = await resp.json().catch(() => ({}));
    const running = data.detail && data.detail.job;
    if (running && running.id) {
      state.backfillJobId = running.id;
      showBackfillProgress(running);
      scheduleBackfillPoll();
      return;
    }
    return failBackfill("A backfill is already running. Give it a moment.");
  }
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    return failBackfill(data.detail || `Couldn't start backfill (error ${resp.status}).`);
  }

  const job = await resp.json();
  state.backfillJobId = job.id;
  showBackfillProgress(job);
  scheduleBackfillPoll();
}

function showBackfillProgress(job) {
  spProgressText.textContent = progressText(job);
  const pct = job ? progressPercent(job.fetched, job.total) : 0;
  spProgressFill.style.width = `${pct}%`;
}

function scheduleBackfillPoll() {
  state.backfillTimer = setTimeout(pollBackfill, pollDelayMs());
}

async function pollBackfill() {
  if (!state.backfillJobId) return;
  let job;
  try {
    const r = await fetch(`/api/backfill/${enc(state.backfillJobId)}`);
    if (r.status === 404) {
      // The job was lost (server restart) — it's idempotent, so just let them retry.
      return failBackfill("Lost track of the backfill — please try again.");
    }
    job = await r.json();
  } catch {
    scheduleBackfillPoll(); // transient; keep polling
    return;
  }
  showBackfillProgress(job);
  if (!isTerminal(job.state)) {
    scheduleBackfillPoll();
    return;
  }
  // Terminal.
  state.backfillActive = false;
  state.backfillJobId = null;
  if (job.state === "failed") {
    return failBackfill("Couldn't load history right now — please try again.");
  }
  await finishBackfill();
}

// Success: refresh the archive extent and jump to the latest frames. applyFrames
// hides the panel when frames arrive; if nothing loaded, the empty card returns.
async function finishBackfill() {
  await refreshExtent();
  await loadDefault();
  maybePoll(); // resume the normal frame poll
}

function failBackfill(message) {
  state.backfillActive = false;
  state.backfillJobId = null;
  if (state.backfillTimer !== null) {
    clearTimeout(state.backfillTimer);
    state.backfillTimer = null;
  }
  spProgress.hidden = true;
  spError.textContent = message;
  spError.hidden = false;
  spBackfill.disabled = false;
  maybePoll(); // resume the normal frame poll
}

// --- auto-update poll (surface newly-landed frames without a reload) ---------

const POLL_MS = 10000; // 10s; pairs with the 30s collect interval to cut felt lag

function maybePoll() {
  // While a backfill is in flight it drives its own refresh, so suppress the
  // frame poll to avoid two refreshers fighting over the same card.
  const want =
    !state.backfillActive &&
    shouldPoll(state.view, state.window !== null, document.hidden);
  if (want && state.pollTimer === null) {
    state.pollTimer = setInterval(pollTick, POLL_MS);
  } else if (!want && state.pollTimer !== null) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function pollTick() {
  let r;
  try {
    r = await fetch(`/api/frames/range?location=${enc(state.location)}`).then((x) =>
      x.json(),
    );
  } catch {
    return; // transient; try again next tick
  }
  const isNew = hasNewerFrame(state.extent.max, r.max);
  const wasEmpty = state.view === "empty";
  applyExtent(r);
  if (!isNew) return;
  if (wasEmpty) {
    await loadDefault(); // first frame landed → show it automatically
    return;
  }
  // On the latest/live view a new frame just shows — no "click to view" nudge. If the
  // user has scrubbed back, refresh the list but keep their frame (never yank).
  const onLatest = state.view === "has-data" && state.window === null;
  if (!onLatest) return;
  const onLast = state.index === state.frames.length - 1;
  const prevScan = state.frames[state.index] && state.frames[state.index].scan_time;
  const data = await fetchFrames({});
  state.window = null;
  applyFrames(data, { replace: true, jumpTo: "last" });
  prefillPickerFromLoaded();
  if (!shouldAutoAdvance(onLatest, onLast) && prevScan) {
    const i = state.frames.findIndex((f) => f.scan_time === prevScan);
    if (i >= 0) goTo(i); // restore the user's position instead of jumping to newest
  }
}

async function loadDefault() {
  // No start/cursor -> server returns the most recent frames.
  const data = await fetchFrames({});
  state.window = null;
  applyFrames(data, { replace: true, jumpTo: "last" });
  prefillPickerFromLoaded();
}

async function loadWindow(startIso, endIso) {
  state.window = { start: startIso, end: endIso };
  const data = await fetchFrames({
    start: startIso,
    end: endIso,
    limit: PAGE_SIZE,
  });
  applyFrames(data, { replace: true, jumpTo: "first" });
}

async function fetchNextPage() {
  if (state.fetching || !state.nextCursor || !state.window) return;
  state.fetching = true;
  try {
    const data = await fetchFrames({
      start: state.window.start,
      end: state.window.end,
      cursor: state.nextCursor,
      limit: PAGE_SIZE,
    });
    applyFrames(data, { replace: false });
  } finally {
    state.fetching = false;
  }
}

function applyFrames(data, { replace, jumpTo }) {
  const incoming = data.frames || [];
  state.frames = replace ? incoming : state.frames.concat(incoming);
  state.nextCursor = data.next_cursor || null;

  if (state.frames.length === 0) {
    timeline.hidden = true;
    setRadarVisible(false); // don't leave a stale frame from the previous location
    state.gaps = [];
    renderGapTrack();
    gapflag.hidden = true;
    // Distinguish "new install, nothing yet" from "this window has no data" — they're
    // different problems with different actions.
    state.view = chooseView(state.extent.count, 0);
    readout.textContent = `${state.location} · ${state.site}`;
    showStatePanel(state.view);
    updateStatus();
    maybePoll();
    return;
  }

  state.view = "has-data";
  hideStatePanel();
  ensureLayer(state.frames[0]);
  setRadarVisible(true);
  timeline.hidden = false;
  scrubber.max = String(state.frames.length - 1);
  const single = state.frames.length < 2;
  scrubber.disabled = single;
  playBtn.disabled = single;
  // Recompute gaps over the whole loaded window (replace or paged-in append) so the
  // track marks where the archive is holey vs continuous.
  state.gaps = detectGaps(state.frames.map((f) => f.scan_time));
  renderGapTrack();

  if (replace) {
    const i = jumpTo === "first" ? 0 : state.frames.length - 1;
    goTo(i);
  } else {
    // Appended a page: keep position, just reflect the new length.
    readoutFor(state.index);
  }
  maybePoll(); // start/stop the new-frame poll for the view we just landed in
}

function setRadarVisible(visible) {
  if (!state.layerReady) return;
  state.map.setLayoutProperty(
    RADAR_LAYER,
    "visibility",
    visible ? "visible" : "none",
  );
}

function ensureLayer(frame) {
  if (state.layerReady) return;
  state.map.addSource(RADAR_SOURCE, {
    type: "image",
    url: frame.image_url,
    coordinates: cornersFromBounds(frame.bounds),
  });
  // Insert the radar below the location pins so the markers stay visible on top.
  const below = state.map.getLayer(LOC_CIRCLE) ? LOC_CIRCLE : undefined;
  state.map.addLayer(
    {
      id: RADAR_LAYER,
      type: "raster",
      source: RADAR_SOURCE,
      paint: { "raster-opacity": state.opacity },
    },
    below,
  );
  state.layerReady = true;
}

function goTo(i) {
  if (state.frames.length === 0) return;
  state.index = Math.max(0, Math.min(i, state.frames.length - 1));
  const f = state.frames[state.index];
  state.map.getSource(RADAR_SOURCE).updateImage({
    url: f.image_url,
    coordinates: cornersFromBounds(f.bounds), // per-frame (failover-safe)
  });
  scrubber.value = String(state.index);
  frametime.textContent = fmtLocalTime(f.scan_time);
  readoutFor(state.index);
  updateGapFlag(state.index);
  preloadAround(state.index);
  if (state.index >= state.frames.length - PAGE_FETCH_AHEAD) fetchNextPage();
}

// Mark each missing-data span on the scrubber track. Markers are %-based, so they
// stay aligned on resize without JS. A gap between frame i and i+1 sits at step i.
function renderGapTrack() {
  gaptrack.innerHTML = "";
  const span = state.frames.length - 1;
  if (span < 1) return;
  for (const g of state.gaps) {
    const seg = document.createElement("div");
    seg.className = "gapseg";
    seg.style.left = `${(g.afterIndex / span) * 100}%`;
    seg.style.width = `${(1 / span) * 100}%`;
    seg.title = `missing data · ${fmtDuration(g.seconds)}`;
    gaptrack.appendChild(seg);
  }
}

// Trailing-edge indicator: when the current frame sits just after a gap, say so — the
// jump you scrubbed/played across wasn't continuous.
function updateGapFlag(i) {
  const g = state.gaps.find((x) => x.afterIndex === i - 1);
  if (g) {
    gapflag.textContent = `⚠ gap before · ${fmtDuration(g.seconds)}`;
    gapflag.hidden = false;
  } else {
    gapflag.hidden = true;
  }
}

function readoutFor(i) {
  const f = state.frames[i];
  readout.textContent =
    `${state.location} · ${f.site} · ${fmtLocalDateTime(f.scan_time)} · ` +
    `${f.elevation_deg.toFixed(1)}° · ${i + 1}/${state.frames.length}`;
  updateStatus();
}

function preloadAround(i) {
  for (let k = 0; k <= PRELOAD_AHEAD; k++) {
    const f = state.frames[(i + k) % state.frames.length];
    if (!state.preloaded.has(f.image_url)) {
      const img = new Image();
      img.src = f.image_url;
      state.preloaded.set(f.image_url, img);
    }
  }
}

function prefillPickerFromLoaded() {
  if (state.frames.length === 0) return;
  startInput.value = isoToLocalInput(state.frames[0].scan_time);
  endInput.value = isoToLocalInput(state.frames[state.frames.length - 1].scan_time);
}

function play() {
  if (state.frames.length < 2) return;
  state.playing = true;
  playBtn.textContent = "❚❚";
  state.timer = setInterval(() => {
    goTo((state.index + 1) % state.frames.length);
  }, Number(speed.value));
}

function pause() {
  state.playing = false;
  playBtn.textContent = "▶";
  if (state.timer !== null) {
    clearInterval(state.timer);
    state.timer = null;
  }
}

function wireControls() {
  rangebar.hidden = false;
  locationSelect.addEventListener("change", () =>
    switchLocation(locationSelect.value),
  );
  manageBtn.addEventListener("click", () => {
    locpanel.hidden = !locpanel.hidden;
    if (!locpanel.hidden) {
      windowctl.classList.remove("open"); // mobile: one drawer at a time
      startAdd();
      renderLocList();
    }
  });
  themeBtn.addEventListener("click", () =>
    applyBasemap(nextChromeToggle(state.basemap)),
  );
  basemapSelect.addEventListener("change", () => applyBasemap(basemapSelect.value));
  // Radar opacity: live setPaintProperty + persist.
  opacityInput.value = String(state.opacity);
  opacityInput.addEventListener("input", () => {
    state.opacity = clampOpacity(opacityInput.value);
    if (state.layerReady) {
      state.map.setPaintProperty(RADAR_LAYER, "raster-opacity", state.opacity);
    }
    try {
      localStorage.setItem(LS_OPACITY, String(state.opacity));
    } catch (e) {
      /* storage disabled — applies for this session */
    }
  });
  // Mobile-only: the window/time controls live in a drawer toggled by this button.
  winToggle.addEventListener("click", () => {
    const open = nextOpen(windowctl.classList.contains("open"));
    windowctl.classList.toggle("open", open);
    if (open) locpanel.hidden = true; // mutual-exclude with the Locations panel
  });
  // Crossing back to desktop width: the drawer is always-inline there, so drop the class.
  window.addEventListener("resize", () => {
    if (!isMobile(window.innerWidth)) windowctl.classList.remove("open");
  });
  locform.addEventListener("submit", submitForm);
  $("lf-reset").addEventListener("click", startAdd);
  $("lf-pick").addEventListener("click", () => {
    state.picking = true;
    locError.textContent = "click the map to set lat/lon…";
  });
  scrubber.addEventListener("input", () => {
    pause();
    goTo(Number(scrubber.value));
  });
  playBtn.addEventListener("click", () => (state.playing ? pause() : play()));
  speed.addEventListener("change", () => {
    if (state.playing) {
      pause();
      play();
    }
  });
  $("load").addEventListener("click", () => {
    pause();
    const s = localInputToIso(startInput.value);
    const e = localInputToIso(endInput.value);
    if (s && e) loadWindow(s, e);
  });
  $("latest").addEventListener("click", () => {
    pause();
    loadDefault();
  });
  for (const btn of document.querySelectorAll(".preset")) {
    btn.addEventListener("click", () => {
      if (!state.extent.max) return;
      pause();
      const end = state.extent.max;
      const start = isoMinusHours(end, Number(btn.dataset.hours));
      startInput.value = isoToLocalInput(start);
      endInput.value = isoToLocalInput(end);
      loadWindow(start, end);
    });
  }
  // Tapping the freshness cue jumps to the latest frame — the reachable "go live"
  // control on mobile (where the Latest button sits inside the Window drawer).
  const goLive = () => {
    pause();
    loadDefault();
  };
  statusEl.addEventListener("click", goLive);
  statusEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      goLive();
    }
  });
  // The wrong-window card's "Jump to latest" snaps back to the live latest view.
  spAction.addEventListener("click", () => {
    pause();
    loadDefault();
  });
  spBackfill.addEventListener("click", startBackfill);
  // Pause polling when the tab is backgrounded; catch up the moment it returns.
  document.addEventListener("visibilitychange", () => {
    maybePoll();
    if (!document.hidden && shouldPoll(state.view, state.window !== null, false)) {
      pollTick();
    }
  });
}

main();
