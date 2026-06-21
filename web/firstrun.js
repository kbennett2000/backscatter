"use strict";
// First-run / view-state logic (Slice 18). Pure — no DOM, no fetch — so it unit-tests
// under `node --test` and loads as a plain <script> in the browser (exposing globals).
// app.js does the DOM/polling wiring; the decisions live here, like gaps.js / markers.js.

// How fresh the newest frame must be for the status cue to honestly claim active
// collection (radar cadence is ~5–10 min, so 15 min covers a normal gap between scans).
const FRESH_MS = 15 * 60 * 1000;

/**
 * Which of the three first-run states applies.
 * @param {number} extentCount - total rendered frames in the archive for this location.
 * @param {number} loadedCount - frames the current view actually loaded.
 * @returns {"has-data"|"wrong-window"|"empty"}
 */
function chooseView(extentCount, loadedCount) {
  if (loadedCount > 0) return "has-data";
  if (extentCount > 0) return "wrong-window"; // archive HAS data, this window doesn't
  return "empty"; // archive truly empty (new install)
}

/**
 * Poll for new frames only when it can matter: the empty state, or the live "latest"
 * view (no explicit historical window). Never while the tab is hidden.
 */
function shouldPoll(view, hasExplicitWindow, hidden) {
  if (hidden) return false;
  if (view === "empty") return true;
  return view === "has-data" && !hasExplicitWindow;
}

/** Whether the newest-frame timestamp advanced (ISO strings sort chronologically). */
function hasNewerFrame(prevMaxIso, currMaxIso) {
  if (!currMaxIso) return false;
  if (!prevMaxIso) return true;
  return currMaxIso > prevMaxIso;
}

/**
 * When a newer frame lands while on the latest/live view, auto-jump to it ONLY if the
 * user is parked on the newest frame. If they've scrubbed back, we refresh the list but
 * keep their position (handled by the caller) — never yank the view.
 */
function shouldAutoAdvance(onLatestView, onLastFrame) {
  return onLatestView && onLastFrame;
}

/** Whether the view is tracking the newest frame (drives the "● Live" badge). */
function isLiveView(view, hasExplicitWindow, onLastFrame) {
  return view === "has-data" && !hasExplicitWindow && onLastFrame;
}

/**
 * The live status cue. Shows the newest frame's age in plain language so the real
 * source lag (~5 min from the assembled bucket) reads honestly — not as a freeze. A
 * "● Live" badge means the view is tracking newest. `fmtAge(iso)` formats the relative
 * age (injected so this stays pure/testable).
 * @param {string|null} maxIso - the archive's newest scan_time, or null if empty.
 * @param {number} now - current time in epoch ms.
 * @param {boolean} live - whether the view is tracking the newest frame.
 * @param {(iso:string)=>string} fmtAge - e.g. iso => "6 min ago".
 */
function statusText(maxIso, now, live, fmtAge) {
  if (!maxIso) return "Collecting — waiting for the first frame…";
  const age = fmtAge(maxIso);
  if (now - Date.parse(maxIso) <= FRESH_MS) {
    return live ? `● Live · last frame ${age}` : `Last frame ${age}`;
  }
  // Older than ~15 min: collection may be behind — say so, don't claim "Live".
  return `Last frame ${age} · checking for new radar…`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    chooseView, shouldPoll, hasNewerFrame, shouldAutoAdvance, isLiveView,
    statusText, FRESH_MS,
  };
}
