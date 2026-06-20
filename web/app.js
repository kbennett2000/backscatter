"use strict";

// Keyless OpenFreeMap style (vector basemap with admin boundaries). No token.
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const RADAR_SOURCE = "radar-frame";
const RADAR_LAYER = "radar-frame-layer";

const readout = document.getElementById("readout");

async function main() {
  const config = await fetch("/api/config").then((r) => r.json());

  const map = new maplibregl.Map({
    container: "map",
    style: BASEMAP_STYLE,
    center: config.center, // [lon, lat] from server Config
    zoom: 7,
  });
  map.addControl(new maplibregl.NavigationControl(), "top-right");

  map.on("load", () => loadLatestFrame(map));
}

async function loadLatestFrame(map) {
  const res = await fetch("/api/latest");
  if (!res.ok) {
    readout.textContent = "No rendered frame yet — run `backscatter render`.";
    return;
  }
  const frame = await res.json();
  const b = frame.bounds;

  // Image-source corners, clockwise from top-left: TL, TR, BR, BL.
  map.addSource(RADAR_SOURCE, {
    type: "image",
    url: frame.image_url,
    coordinates: [
      [b.west, b.north],
      [b.east, b.north],
      [b.east, b.south],
      [b.west, b.south],
    ],
  });
  map.addLayer({
    id: RADAR_LAYER,
    type: "raster",
    source: RADAR_SOURCE,
    paint: { "raster-opacity": 0.8 },
  });

  readout.textContent = `${frame.site} · ${frame.scan_time} · ${frame.elevation_deg.toFixed(1)}°`;
}

main();
