"use strict";
// Responsive-layout logic (Slice 22). Pure — no DOM — so it unit-tests under
// `node --test` and loads as a plain <script> (exposing globals), like theme.js/gaps.js.
// The CSS media query owns the visual layout; this only decides "are we phone-width?" so
// app.js can auto-close the mobile window drawer when the viewport grows back to desktop.

// Must match the @media (max-width: 600px) breakpoint in style.css.
const BREAKPOINT = 600;

/** Whether the viewport is phone-width (the mobile treatment is active). */
function isMobile(width) {
  return width <= BREAKPOINT;
}

/** Toggle a drawer's open boolean. */
function nextOpen(current) {
  return !current;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { BREAKPOINT, isMobile, nextOpen };
}
