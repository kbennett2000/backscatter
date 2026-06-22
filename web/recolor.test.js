"use strict";
// Unit tests for the client-side recolor pipeline. Run: `node --test web/recolor.test.js`.
// These lock the must-be-exact properties: lossless inversion, clear-air drops only the
// intended buckets, palette remap is faithful, and the source array is never mutated.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const {
  SOURCE_BUCKETS,
  CLEAR_AIR_MAX_BUCKET,
  PALETTES,
  buildLut,
  recolorRGBA,
} = require("./recolor.js");

const LUT = buildLut(SOURCE_BUCKETS);

// Build an RGBA buffer: one opaque pixel per bucket 0..14, then one fully transparent.
function sampleFrame() {
  const px = [...SOURCE_BUCKETS.map((c) => [...c, 255]), [0, 0, 0, 0]];
  return Uint8ClampedArray.from(px.flat());
}

test("source table matches the backend: 15 buckets, lut round-trips each color", () => {
  assert.equal(SOURCE_BUCKETS.length, 15);
  for (let i = 0; i < SOURCE_BUCKETS.length; i++) {
    const [r, g, b] = SOURCE_BUCKETS[i];
    assert.equal(LUT[(r << 16) | (g << 8) | b], i);
  }
});

test("lossless identity: nws remap, no hide → output equals input, input untouched", () => {
  const input = sampleFrame();
  const snapshot = Uint8ClampedArray.from(input);
  const out = recolorRGBA(input, {
    lut: LUT,
    target: PALETTES.nws,
    hideMaxBucket: -1,
  });
  assert.deepEqual(out, input); // identity remap reproduces the source exactly
  assert.deepEqual(input, snapshot); // source array never mutated
});

test("clear-air drops exactly buckets 0–1 and nothing else", () => {
  const input = sampleFrame();
  const out = recolorRGBA(input, {
    lut: LUT,
    target: PALETTES.nws,
    hideMaxBucket: CLEAR_AIR_MAX_BUCKET, // 1
  });
  let newlyTransparent = 0;
  for (let i = 0; i < input.length; i += 4) {
    const bucket = i / 4; // sampleFrame lays buckets out in order; last is transparent
    if (bucket <= CLEAR_AIR_MAX_BUCKET) {
      assert.equal(out[i + 3], 0, `bucket ${bucket} should be hidden`);
      newlyTransparent++;
    } else if (bucket < SOURCE_BUCKETS.length) {
      // untouched precip buckets keep their exact color + alpha
      assert.deepEqual(
        [out[i], out[i + 1], out[i + 2], out[i + 3]],
        [input[i], input[i + 1], input[i + 2], 255],
      );
    } else {
      assert.equal(out[i + 3], 0); // the already-transparent pixel stays transparent
    }
  }
  assert.equal(newlyTransparent, CLEAR_AIR_MAX_BUCKET + 1); // only buckets 0 and 1
});

test("palette remap: each opaque pixel → radarscope[idx], alpha preserved", () => {
  const input = sampleFrame();
  const out = recolorRGBA(input, {
    lut: LUT,
    target: PALETTES.radarscope,
    hideMaxBucket: -1,
  });
  for (let b = 0; b < SOURCE_BUCKETS.length; b++) {
    const i = b * 4;
    assert.deepEqual(
      [out[i], out[i + 1], out[i + 2], out[i + 3]],
      [...PALETTES.radarscope[b], 255],
    );
  }
  // transparent pixel unchanged
  const t = SOURCE_BUCKETS.length * 4;
  assert.equal(out[t + 3], 0);
});

test("nws palette is a pure identity remap (NWS unchanged when toggled back)", () => {
  const input = sampleFrame();
  const out = recolorRGBA(input, {
    lut: LUT,
    target: PALETTES.nws,
    hideMaxBucket: -1,
  });
  assert.deepEqual(out, input);
});

test("unknown opaque color passes through unchanged", () => {
  const input = Uint8ClampedArray.from([123, 45, 67, 255]);
  const out = recolorRGBA(input, {
    lut: LUT,
    target: PALETTES.radarscope,
    hideMaxBucket: 1,
  });
  assert.deepEqual([...out], [123, 45, 67, 255]);
});
