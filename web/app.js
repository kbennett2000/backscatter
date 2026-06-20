"use strict";

// Keyless OpenFreeMap style (vector basemap with admin boundaries). No token.
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const RADAR_SOURCE = "radar-frame";
const RADAR_LAYER = "radar-frame-layer";
const PRELOAD_AHEAD = 3; // warm the next few PNGs so playback doesn't jank

const readout = document.getElementById("readout");
const timeline = document.getElementById("timeline");
const playBtn = document.getElementById("play");
const scrubber = document.getElementById("scrubber");
const frametime = document.getElementById("frametime");
const speed = document.getElementById("speed");

const state = {
  map: null,
  frames: [],
  index: 0,
  playing: false,
  timer: null,
  preloaded: new Map(), // url -> Image
};

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
  // Frames are UTC; show unambiguously without locale surprises.
  return iso.replace("T", " ").replace(/(\+00:00|Z)$/, "Z");
}

async function main() {
  const config = await fetch("/api/config").then((r) => r.json());
  state.map = new maplibregl.Map({
    container: "map",
    style: BASEMAP_STYLE,
    center: config.center, // [lon, lat] from server Config
    zoom: 7,
  });
  state.map.addControl(new maplibregl.NavigationControl(), "top-right");
  state.map.on("load", () => initTimeline(config));
}

async function initTimeline(config) {
  const data = await fetch(
    `/api/frames?site=${encodeURIComponent(config.site)}`,
  ).then((r) => r.json());
  state.frames = data.frames || [];

  if (state.frames.length === 0) {
    readout.textContent = "No frames yet — run `backscatter collect`.";
    return;
  }

  // Seed the image source/layer from the first frame, then jump to the latest.
  const first = state.frames[0];
  state.map.addSource(RADAR_SOURCE, {
    type: "image",
    url: first.image_url,
    coordinates: cornersFromBounds(first.bounds),
  });
  state.map.addLayer({
    id: RADAR_LAYER,
    type: "raster",
    source: RADAR_SOURCE,
    paint: { "raster-opacity": 0.8 },
  });

  timeline.hidden = false;
  scrubber.max = String(state.frames.length - 1);
  const single = state.frames.length < 2;
  scrubber.disabled = single;
  playBtn.disabled = single;

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

  goTo(state.frames.length - 1); // start on the most recent frame
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
  readout.textContent =
    `${f.site} · ${fmtTime(f.scan_time)} · ` +
    `${f.elevation_deg.toFixed(1)}° · ${state.index + 1}/${state.frames.length}`;
  preloadAround(state.index);
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

function play() {
  if (state.frames.length < 2) return;
  state.playing = true;
  playBtn.textContent = "❚❚";
  state.timer = setInterval(() => {
    goTo((state.index + 1) % state.frames.length); // loop continuously
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

main();
