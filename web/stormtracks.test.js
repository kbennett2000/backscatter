"use strict";
// Unit tests for the storm-tracks GeoJSON builder. Run: `node --test web/stormtracks.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { trackFeatures, forward } = require("./stormtracks.js");

// A moving cell at 36 km/h (= 10 m/s) heading due east; and a stationary one.
const MOVING = {
  track_id: 12,
  lon: -104.5,
  lat: 39.8,
  max_dbz: 58.0,
  area_km2: 240.0,
  speed_kmh: 36.0,
  bearing_deg: 90.0,
};
const STATIONARY = {
  track_id: 13,
  lon: -103.9,
  lat: 39.6,
  max_dbz: 44.0,
  area_km2: 30.0,
  speed_kmh: 0.0,
  bearing_deg: null,
};

const M_PER_DEG_LAT = 111320;

// --- forward() geometry --------------------------------------------------------

test("forward due east moves lon by dist/(m_per_deg*cos lat), lat ~unchanged", () => {
  const [lon, lat] = forward(-104.5, 39.8, 90, 10000);
  const expDLon = 10000 / (M_PER_DEG_LAT * Math.cos((39.8 * Math.PI) / 180));
  assert.ok(Math.abs(lon - (-104.5 + expDLon)) < 1e-9);
  assert.ok(Math.abs(lat - 39.8) < 1e-9);
});

test("forward due north moves lat by dist/m_per_deg, lon unchanged", () => {
  const [lon, lat] = forward(-104.5, 39.8, 0, 11132);
  assert.ok(Math.abs(lat - (39.8 + 0.1)) < 1e-9); // 11132 m ≈ 0.1°
  assert.ok(Math.abs(lon - -104.5) < 1e-12);
});

test("forward handles a wrapped bearing (350° ≈ slightly west of north)", () => {
  const [lon, lat] = forward(-104.5, 39.8, 350, 10000);
  assert.ok(lat > 39.8); // mostly north
  assert.ok(lon < -104.5); // a little west
});

// --- feature inventory ---------------------------------------------------------

test("moving cell → Point + main line + 2 ticks + arrowhead (5 features)", () => {
  const fc = trackFeatures([MOVING]);
  assert.equal(fc.type, "FeatureCollection");
  assert.equal(fc.features.length, 5);
  assert.equal(fc.features.filter((f) => f.geometry.type === "Point").length, 1);
  assert.equal(fc.features.filter((f) => f.geometry.type === "LineString").length, 4);
  // The two ticks carry their forecast minute (30-min horizon = ticks at 15/30).
  const tickMins = fc.features
    .filter((f) => f.properties.tick_min != null)
    .map((f) => f.properties.tick_min)
    .sort((a, b) => a - b);
  assert.deepEqual(tickMins, [15, 30]);
});

test("main vector tip is the 30-min projection point (matches backend horizon)", () => {
  const fc = trackFeatures([MOVING]); // 10 m/s east → 18 km over 30 min
  const main = fc.features.find(
    (f) =>
      f.geometry.type === "LineString" &&
      f.geometry.coordinates.length === 2 &&
      f.properties.tick_min == null,
  );
  const [start, tip] = main.geometry.coordinates;
  const cosLat = Math.cos((39.8 * Math.PI) / 180);
  const expDLon = (10 * 30 * 60) / (M_PER_DEG_LAT * cosLat); // 18 km east in degrees
  assert.ok(Math.abs(start[0] - -104.5) < 1e-12);
  assert.ok(Math.abs(tip[0] - (-104.5 + expDLon)) < 1e-6);
  assert.ok(Math.abs(tip[1] - 39.8) < 1e-6); // due east → lat ~unchanged
});

test("stationary cell → marker only, no vector/ticks", () => {
  const fc = trackFeatures([STATIONARY]);
  assert.equal(fc.features.length, 1);
  assert.equal(fc.features[0].geometry.type, "Point");
});

// --- tick geometry: equal time → equal spacing, perpendicular orientation ------

test("tick centers are equally spaced along the heading (constant speed)", () => {
  const fc = trackFeatures([MOVING]); // 10 m/s east → 9 km per 15 min
  const ticks = fc.features.filter((f) => f.properties.tick_min != null);
  // Each tick's center is the midpoint of its perpendicular cross-line.
  const centersLon = ticks
    .sort((a, b) => a.properties.tick_min - b.properties.tick_min)
    .map((f) => {
      const [a, b] = f.geometry.coordinates;
      return (a[0] + b[0]) / 2;
    });
  const cosLat = Math.cos((39.8 * Math.PI) / 180);
  const stepDeg = (10 * 15 * 60) / (M_PER_DEG_LAT * cosLat); // 9 km east in degrees
  for (let i = 0; i < centersLon.length; i++) {
    assert.ok(Math.abs(centersLon[i] - (-104.5 + stepDeg * (i + 1))) < 1e-6);
  }
});

test("a tick is perpendicular to an eastward track (runs N–S)", () => {
  const fc = trackFeatures([MOVING]);
  const tick = fc.features.find((f) => f.properties.tick_min === 30);
  const [end1, end2] = tick.geometry.coordinates;
  // Eastward track → cross-line is north–south: ends differ in lat, ~equal lon.
  assert.ok(Math.abs(end1[0] - end2[0]) < 1e-6); // same lon
  assert.ok(Math.abs(end1[1] - end2[1]) > 0.05); // clearly different lat
});

test("arrowhead is a 3-point polyline meeting at the tip", () => {
  const fc = trackFeatures([MOVING]);
  // The main line's far end is the tip; the arrowhead is the 3-coordinate line.
  const arrow = fc.features.find(
    (f) => f.geometry.type === "LineString" && f.geometry.coordinates.length === 3,
  );
  assert.ok(arrow, "expected a 3-point arrowhead line");
  const main = fc.features.find(
    (f) =>
      f.geometry.type === "LineString" &&
      f.geometry.coordinates.length === 2 &&
      f.properties.tick_min == null,
  );
  const tip = main.geometry.coordinates[1];
  assert.deepEqual(arrow.geometry.coordinates[1], tip); // middle point is the tip
});

// --- empties -------------------------------------------------------------------

test("empty / missing input → empty collection", () => {
  assert.deepEqual(trackFeatures([]).features, []);
  assert.deepEqual(trackFeatures(undefined).features, []);
  assert.deepEqual(trackFeatures(null).features, []);
});
