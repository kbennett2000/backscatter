"use strict";
// Backfill button / progress logic (Slice 19). Pure — no DOM, no fetch — so it
// unit-tests under `node --test` and loads as a plain <script> in the browser
// (exposing globals). app.js does the DOM + polling wiring; the wording and the
// progress math live here, like gaps.js / firstrun.js.

const BACKFILL_POLL_MS = 2000;

/**
 * Plain-language line for the current job status.
 * @param {{state?:string,total?:number,fetched?:number,rendered?:number}|null} job
 */
function progressText(job) {
  if (!job || job.state === "queued") return "Starting…";
  if (job.state === "failed") return "Couldn't load history right now — try again.";
  if (job.state === "done") {
    const n = job.rendered || 0;
    return n === 1 ? "Loaded 1 frame." : `Loaded ${n} frames.`;
  }
  // running
  if (!job.total) return "Looking for recent radar…";
  return `Loading radar… ${job.fetched || 0} of ${job.total} frames`;
}

/** Progress as an integer percent 0–100 (0 when the total isn't known yet). */
function progressPercent(fetched, total) {
  if (!total || total <= 0) return 0;
  const pct = Math.round((fetched / total) * 100);
  return Math.max(0, Math.min(100, pct));
}

/** Whether a job state is final (stop polling). */
function isTerminal(stateName) {
  return stateName === "done" || stateName === "failed";
}

/** Poll interval for job status, ms. */
function pollDelayMs() {
  return BACKFILL_POLL_MS;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    progressText,
    progressPercent,
    isTerminal,
    pollDelayMs,
    BACKFILL_POLL_MS,
  };
}
