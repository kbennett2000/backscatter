"use strict";
// Timeline gap detection (Slice 13). Pure logic over a frame window's scan_times —
// no DOM, no fetch — so it unit-tests cleanly under `node --test` and loads as a
// plain <script> in the browser (exposing globals).
//
// The scrubber is index-spaced (frame i -> step i), so a large *time* hole between
// consecutive frames is invisible without this. A "gap" is a consecutive interval
// noticeably larger than the window's normal cadence — derived, not hardcoded, so a
// normal clear-air spacing isn't flagged but a real collection hole is.

// A gap is any interval longer than this multiple of the window's median interval.
// Median (not mean) so the gaps themselves don't inflate the baseline; ×3 leaves a
// missed scan or two unflagged but catches real holes (clear-air ~10min -> ~30min
// threshold; precip ~5min -> ~15min). Tunable in one place.
const GAP_FACTOR = 3;

function _median(sorted) {
  // `sorted` is an ascending copy. Even length -> mean of the middle two.
  const n = sorted.length;
  if (n === 0) return 0;
  const mid = Math.floor(n / 2);
  return n % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Detect gaps in an ascending list of ISO-8601 scan_times.
 * @param {string[]} scanTimes - ascending ISO-8601 timestamps (as /api/frames returns).
 * @param {number} [factor=GAP_FACTOR] - interval > factor*median => a gap.
 * @returns {{afterIndex:number, seconds:number}[]} gaps, one per offending interval;
 *   a gap sits between frame `afterIndex` and `afterIndex + 1`.
 */
function detectGaps(scanTimes, factor) {
  const f = factor === undefined ? GAP_FACTOR : factor;
  // Need >=3 frames (>=2 intervals) before a median means anything.
  if (!Array.isArray(scanTimes) || scanTimes.length < 3) return [];

  const intervals = [];
  for (let i = 1; i < scanTimes.length; i++) {
    const a = Date.parse(scanTimes[i - 1]);
    const b = Date.parse(scanTimes[i]);
    if (Number.isNaN(a) || Number.isNaN(b)) return []; // bail on unparseable input
    intervals.push((b - a) / 1000);
  }

  const med = _median([...intervals].sort((x, y) => x - y));
  if (med <= 0) return [];
  const threshold = f * med;

  const gaps = [];
  for (let i = 0; i < intervals.length; i++) {
    if (intervals[i] > threshold) {
      gaps.push({ afterIndex: i, seconds: intervals[i] });
    }
  }
  return gaps;
}

/** Compact human duration, e.g. 5520 -> "1h 32m", 1800 -> "30m", 45 -> "45s". */
function fmtDuration(seconds) {
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

// Browser: globals. Node (`node --test`): module.exports. The guard is false in the
// browser (no `module`), so this file is a plain classic script there.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { detectGaps, fmtDuration, GAP_FACTOR };
}
