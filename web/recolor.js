"use strict";
// Client-side reflectivity recolor (Slice 24). Pure — `node --test`'d, loaded as a
// plain <script> exposing globals, like prefs.js/theme.js.
//
// The stored PNG uses 15 discrete dBZ buckets (see src/backscatter/render/colormap.py):
// every opaque pixel is EXACTLY one of the 15 NWS RGBs, and alpha is only ever 0 or 255
// (bilinear smoothing happens on dBZ before the discrete colormap, so there are no
// intermediate colors and no partial alpha). That makes RGB → bucket-index inversion
// lossless, so we can hide low buckets or remap to another palette purely on the client
// without re-rendering. The stored PNG is never written — this is a display transform.

// The 15 NWS reflectivity RGBs in bucket order 0..14 (dBZ 5,10,…,75+).
// MUST stay in sync with NWS_REFLECTIVITY in src/backscatter/render/colormap.py — a test
// asserts length 15 and index→color round-trips; the visual-check gate covers the rest.
const SOURCE_BUCKETS = [
  [4, 233, 231], // 0: [5,10)   clear-air (cyan)
  [1, 159, 244], // 1: [10,15)  clear-air (light blue)
  [3, 0, 244], // 2: [15,20)
  [2, 253, 2], // 3: [20,25)
  [1, 197, 1], // 4: [25,30)
  [0, 142, 0], // 5: [30,35)
  [253, 248, 2], // 6: [35,40)
  [229, 188, 0], // 7: [40,45)
  [253, 149, 0], // 8: [45,50)
  [253, 0, 0], // 9: [50,55)
  [212, 0, 0], // 10: [55,60)
  [188, 0, 0], // 11: [60,65)
  [248, 0, 253], // 12: [65,70)
  [152, 84, 198], // 13: [70,75)
  [255, 255, 255], // 14: [75,∞)
];

// Buckets ≤ this index are "clear-air" (< 15 dBZ): the bug/dust/gradient speckle the
// hide toggle drops. Single knob — bump to 0 (only <10) or 2 (<20) to retune.
const CLEAR_AIR_MAX_BUCKET = 1;

// Display palettes, keyed by the persisted choice. `nws` is the identity remap (each
// bucket → its own source color), so the default path is provably unchanged. `radarscope`
// is a RadarScope-*style* approximation — final shades are tuned at the visual-check gate.
const PALETTES = {
  nws: SOURCE_BUCKETS,
  radarscope: [
    [152, 215, 234], // 0  pale blue
    [90, 168, 220], // 1  light blue
    [32, 80, 200], // 2  blue
    [60, 200, 60], // 3  green
    [20, 160, 20], // 4  medium green
    [10, 110, 10], // 5  dark green
    [250, 240, 80], // 6  yellow
    [240, 190, 40], // 7  gold
    [245, 140, 20], // 8  orange
    [235, 30, 30], // 9  red
    [200, 20, 20], // 10 dark red
    [150, 10, 10], // 11 maroon
    [240, 60, 240], // 12 magenta
    [160, 90, 200], // 13 purple
    [255, 255, 255], // 14 white
  ],
};

const _NOT_FOUND = 255; // sentinel: no bucket index can be 255 (max is 14)

/**
 * Build an RGB → bucket-index lookup from a bucket list. Returns a Uint8Array indexed by
 * the packed color `r*65536 + g*256 + b`; entries are 255 (not a bucket) where unmapped.
 * Built once and reused across frames/pixels.
 */
function buildLut(buckets) {
  const lut = new Uint8Array(0x1000000).fill(_NOT_FOUND);
  for (let i = 0; i < buckets.length; i++) {
    const [r, g, b] = buckets[i];
    lut[(r << 16) | (g << 8) | b] = i;
  }
  return lut;
}

/**
 * Recolor RGBA pixel data. Returns a NEW Uint8ClampedArray — the input is never mutated,
 * so the source frame stays the source of truth.
 *
 * - alpha 0 → copied through (transparent stays transparent).
 * - opaque + color in `lut` → bucket idx. If `hideMaxBucket >= 0 && idx <= hideMaxBucket`,
 *   the pixel becomes fully transparent; otherwise it takes `target[idx]` at full alpha.
 * - opaque + color not in `lut` (shouldn't happen) → copied through unchanged, so we never
 *   corrupt unexpected data.
 *
 * @param {Uint8ClampedArray|Uint8Array} data   RGBA bytes (length = w*h*4)
 * @param {Uint8Array} opts.lut                 from buildLut(SOURCE_BUCKETS)
 * @param {number[][]} opts.target              the display palette (15 RGB triples)
 * @param {number} opts.hideMaxBucket           hide buckets ≤ this; -1 hides nothing
 */
function recolorRGBA(data, { lut, target, hideMaxBucket }) {
  const out = new Uint8ClampedArray(data.length);
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3];
    if (a === 0) {
      // transparent: alpha already 0; rgb left 0 (invisible anyway)
      continue;
    }
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const idx = lut[(r << 16) | (g << 8) | b];
    if (idx === _NOT_FOUND) {
      // unrecognized color: pass through untouched
      out[i] = r;
      out[i + 1] = g;
      out[i + 2] = b;
      out[i + 3] = a;
      continue;
    }
    if (hideMaxBucket >= 0 && idx <= hideMaxBucket) {
      continue; // drop clear-air: leave fully transparent
    }
    const c = target[idx];
    out[i] = c[0];
    out[i + 1] = c[1];
    out[i + 2] = c[2];
    out[i + 3] = 255;
  }
  return out;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SOURCE_BUCKETS,
    CLEAR_AIR_MAX_BUCKET,
    PALETTES,
    buildLut,
    recolorRGBA,
  };
}
