"use strict";

// Keyless OpenFreeMap style (vector basemap with admin boundaries). No token.
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const RADAR_SOURCE = "radar-frame";
const RADAR_LAYER = "radar-frame-layer";
const PRELOAD_AHEAD = 3; // warm the next few PNGs so playback doesn't jank
const PAGE_SIZE = 20; // frames per request when paging an explicit window
const PAGE_FETCH_AHEAD = 3; // fetch the next page when this close to the end
const LS_KEY = "backscatter.location"; // last-selected location, across reloads

const $ = (id) => document.getElementById(id);
const readout = $("readout");
const rangebar = $("rangebar");
const timeline = $("timeline");
const playBtn = $("play");
const scrubber = $("scrubber");
const frametime = $("frametime");
const speed = $("speed");
const startInput = $("start");
const endInput = $("end");
const extentLabel = $("extent");
const locwrap = $("locwrap");
const locationSelect = $("location");

const state = {
  map: null,
  locations: [], // [{name, lat, lon, default, site}] from /api/locations
  location: null, // active location name (runtime state)
  site: null, // active location's resolved radar
  frames: [],
  index: 0,
  playing: false,
  timer: null,
  preloaded: new Map(),
  layerReady: false,
  extent: { min: null, max: null, count: 0 },
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

function fmtTime(iso) {
  return iso.replace("T", " ").replace(/(\+00:00|Z)$/, "Z");
}

// "2026-06-20T23:06:51+00:00" -> "2026-06-20T23:06" for datetime-local inputs.
function toLocalInput(iso) {
  return iso.slice(0, 16);
}

// datetime-local value (UTC) -> ISO with explicit Z.
function inputToIso(value) {
  if (!value) return null;
  return value.length === 16 ? `${value}:00Z` : `${value}Z`;
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

  state.map = new maplibregl.Map({
    container: "map",
    style: BASEMAP_STYLE,
    center: [active.lon, active.lat],
    zoom: 7,
  });
  state.map.addControl(new maplibregl.NavigationControl(), "top-right");
  state.map.on("load", init);
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
  state.map.flyTo({ center: [loc.lon, loc.lat], zoom: 7 });
  await refreshExtent();
  await loadDefault(); // re-point the timeline at this location's recent window
}

async function init() {
  await refreshExtent();
  wireControls();
  await loadDefault(); // recent rolling window (unchanged default UX)
}

async function refreshExtent() {
  const r = await fetch(`/api/frames/range?location=${enc(state.location)}`).then(
    (x) => x.json(),
  );
  state.extent = { min: r.min, max: r.max, count: r.count };
  if (r.min && r.max) {
    startInput.min = endInput.min = toLocalInput(r.min);
    startInput.max = endInput.max = toLocalInput(r.max);
    extentLabel.textContent = `archive: ${fmtTime(r.min)} – ${fmtTime(r.max)} (${r.count})`;
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
    readout.textContent = replace
      ? `${state.location} · no frames in this range.`
      : readout.textContent;
    return;
  }

  ensureLayer(state.frames[0]);
  setRadarVisible(true);
  timeline.hidden = false;
  scrubber.max = String(state.frames.length - 1);
  const single = state.frames.length < 2;
  scrubber.disabled = single;
  playBtn.disabled = single;

  if (replace) {
    const i = jumpTo === "first" ? 0 : state.frames.length - 1;
    goTo(i);
  } else {
    // Appended a page: keep position, just reflect the new length.
    readoutFor(state.index);
  }
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
  state.map.addLayer({
    id: RADAR_LAYER,
    type: "raster",
    source: RADAR_SOURCE,
    paint: { "raster-opacity": 0.8 },
  });
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
  frametime.textContent = fmtTime(f.scan_time);
  readoutFor(state.index);
  preloadAround(state.index);
  if (state.index >= state.frames.length - PAGE_FETCH_AHEAD) fetchNextPage();
}

function readoutFor(i) {
  const f = state.frames[i];
  readout.textContent =
    `${state.location} · ${f.site} · ${fmtTime(f.scan_time)} · ` +
    `${f.elevation_deg.toFixed(1)}° · ${i + 1}/${state.frames.length}`;
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
  startInput.value = toLocalInput(state.frames[0].scan_time);
  endInput.value = toLocalInput(state.frames[state.frames.length - 1].scan_time);
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
    const s = inputToIso(startInput.value);
    const e = inputToIso(endInput.value);
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
      startInput.value = toLocalInput(start);
      endInput.value = toLocalInput(end);
      loadWindow(start, end);
    });
  }
}

main();
