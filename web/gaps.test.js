"use strict";
// Unit tests for the timeline gap-detection rule. Run: `node --test web/gaps.test.js`.
// Node's built-in test runner — no npm, no package.json, no dependencies.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { detectGaps, fmtDuration, GAP_FACTOR } = require("./gaps.js");

// Build ascending ISO scan_times from a list of inter-frame gaps in minutes.
// `mins[i]` is the spacing from frame i to frame i+1, so N frames need N-1 entries.
function times(startIso, ...mins) {
  const out = [new Date(startIso).toISOString()];
  let t = new Date(startIso).getTime();
  for (const m of mins) {
    t += m * 60_000;
    out.push(new Date(t).toISOString());
  }
  return out;
}

const START = "2026-06-20T00:00:00Z";

test("GAP_FACTOR default is 3", () => {
  assert.equal(GAP_FACTOR, 3);
});

test("steady precip cadence (~5 min) has no gaps", () => {
  const t = times(START, 5, 5, 5, 5, 5, 5, 5, 5);
  assert.deepEqual(detectGaps(t), []);
});

test("steady clear-air cadence (~10 min) has no gaps", () => {
  const t = times(START, 10, 10, 10, 10, 10, 10);
  assert.deepEqual(detectGaps(t), []);
});

test("one missed clear-air scan (~20 min) is NOT flagged", () => {
  // median stays ~10 -> threshold ~30; a single 20-min interval is under it.
  const t = times(START, 10, 10, 20, 10, 10, 10);
  assert.deepEqual(detectGaps(t), []);
});

test("a real 90-min hole in 5-min cadence IS flagged", () => {
  // intervals: many 5s + one 90 -> median 5, threshold 15, 90 > 15.
  const t = times(START, 5, 5, 5, 90, 5, 5, 5);
  const gaps = detectGaps(t);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].afterIndex, 3); // between frame 3 and 4
  assert.equal(gaps[0].seconds, 90 * 60);
});

test("mixed VCP (precip then clear-air) yields no false gaps", () => {
  const t = times(START, 5, 5, 5, 10, 10, 10);
  assert.deepEqual(detectGaps(t), []);
});

test("gap at the first interval (afterIndex 0)", () => {
  const t = times(START, 120, 5, 5, 5, 5, 5);
  const gaps = detectGaps(t);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].afterIndex, 0);
});

test("gap at the last interval (afterIndex N-2)", () => {
  const t = times(START, 5, 5, 5, 5, 5, 120);
  const gaps = detectGaps(t);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].afterIndex, t.length - 2);
});

test("multiple gaps in one window", () => {
  const t = times(START, 5, 60, 5, 5, 90, 5);
  const gaps = detectGaps(t).map((g) => g.afterIndex);
  assert.deepEqual(gaps, [1, 4]);
});

test("edge cases: empty, single, two frames, all-continuous", () => {
  assert.deepEqual(detectGaps([]), []);
  assert.deepEqual(detectGaps([START]), []);
  assert.deepEqual(detectGaps(times(START, 90)), []); // 2 frames -> no median
  assert.deepEqual(detectGaps(times(START, 5, 5, 5, 5)), []);
});

test("unparseable input bails to no gaps", () => {
  assert.deepEqual(detectGaps(["nope", "also-nope", "still-nope"]), []);
});

test("fmtDuration formats compactly", () => {
  assert.equal(fmtDuration(45), "45s");
  assert.equal(fmtDuration(1800), "30m");
  assert.equal(fmtDuration(5520), "1h 32m");
  assert.equal(fmtDuration(3600), "1h");
});
