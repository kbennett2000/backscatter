#!/usr/bin/env python
"""Capture real screenshots + GIFs of the running backscatter app for the docs.

Repeatable (not hand-grabbed): drives the LIVE app with Playwright against the local
archive, writes PNGs and GIFs under docs/assets/. GIFs are built from screenshot
sequences (which reliably capture the WebGL map) assembled with ffmpeg.

Prereqs: the `docs` dependency group + a Chromium for Playwright, and ffmpeg:
    uv sync --group docs
    uv run playwright install chromium      # (a system Chrome also works)
    # ffmpeg must be on PATH

Usage (starts its own server against ./data, captures, tears down):
    uv run --group docs python scripts/capture_docs.py
Or point at an already-running app:
    uv run --group docs python scripts/capture_docs.py --url http://127.0.0.1:8000/

The app needs a populated archive (run `backscatter collect`/`backfill` first). This
script only *drives* the app read-only; it changes no app behaviour.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

GL_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--no-sandbox",
    "--hide-scrollbars",
]


def _wait_api(port: int, tries: int = 80) -> bool:
    for _ in range(tries):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _ready(page: Page) -> None:
    """Wait for frames to load, then let the basemap tiles + radar paint."""
    for _ in range(60):
        n = page.evaluate(
            "(typeof state!=='undefined' && state.frames && state.frames.length) || 0"
        )
        if n and n > 1:
            break
        page.wait_for_timeout(500)
    page.wait_for_timeout(4500)


def _seq_to_gif(frames: list[Path], out_gif: Path, fps: int, width: int) -> None:
    """Assemble a PNG sequence into an optimized palette GIF via ffmpeg."""
    listing = out_gif.with_suffix(".txt")
    listing.write_text("".join(f"file '{f}'\nduration {1/fps:.4f}\n" for f in frames))
    vf = (
        f"scale={width}:-1:flags=lanczos,"
        "split[s0][s1];[s0]palettegen=stats_mode=diff[p];"
        "[s1][p]paletteuse=dither=bayer:bayer_scale=3"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-vf", vf, "-loop", "0", str(out_gif)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    listing.unlink(missing_ok=True)


def _record(
    page: Page, steps: list[Callable[[Page], None]], out_gif: Path,
    *, fps: int = 10, width: int = 960,
) -> None:
    """Run each step, screenshot after it, and stitch the frames into a GIF."""
    with tempfile.TemporaryDirectory() as td:
        frames: list[Path] = []
        for i, step in enumerate(steps):
            step(page)
            fp = Path(td) / f"{i:04d}.png"
            page.screenshot(path=fp)
            frames.append(fp)
        _seq_to_gif(frames, out_gif, fps, width)
    print("  gif:", out_gif.name, f"({out_gif.stat().st_size // 1024} KB)")


def _hold(ms: int) -> Callable[[Page], None]:
    return lambda pg: pg.wait_for_timeout(ms)


def _goto_frame(i: int) -> Callable[[Page], None]:
    """Jump to frame ``i`` and wait for its radar image to actually paint.

    The radar layer updates via an async ``updateImage`` (a fetch + decode), so the old
    fixed 70 ms wait routinely screenshotted a half-loaded or blank map — that put the
    "missing radar" frames in the GIFs. We instead register for the map's next ``idle``
    (which fires once the new image has loaded and repainted), trigger the jump, and
    resolve on that idle — capped by a safety timeout — then add a short settle.
    """
    def step(pg: Page) -> None:
        pg.evaluate(
            """(i) => new Promise((resolve) => {
                if (typeof goTo !== 'function') return resolve();
                const m = state.map;
                let settled = false;
                const done = () => { if (!settled) { settled = true; resolve(); } };
                m.once('idle', done);   // the idle after the image reload + repaint
                goTo(i);                // triggers the radar updateImage
                setTimeout(done, 2500); // safety cap if no further render happens
            })""",
            i,
        )
        pg.wait_for_timeout(120)
    return step


def capture(url: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=GL_ARGS)

        # ---- stills (crisp 2x) ----
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 760}, device_scale_factor=2
        )
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        _ready(page)
        # The hero/readout shots open on the latest frame by default. If that frame is
        # quiet, set BACKSCATTER_DOCS_HERO_AT (ISO scan_time) to land on a livelier one
        # (e.g. the archive's peak-storm frame) for a screenshot that actually shines.
        hero_at = os.environ.get("BACKSCATTER_DOCS_HERO_AT")
        if hero_at:
            page.evaluate(
                """(t) => {
                    const a = Date.parse(t);
                    let best = -1, bd = Infinity;
                    state.frames.forEach((f, i) => {
                        const d = Math.abs(Date.parse(f.scan_time) - a);
                        if (d < bd) { bd = d; best = i; }
                    });
                    if (best >= 0 && typeof goTo === 'function') {
                        if (typeof pause === 'function') pause();
                        goTo(best);
                    }
                }""",
                hero_at,
            )
            page.wait_for_timeout(3500)  # let the radar image fetch + paint
        page.screenshot(path=out / "app-overview.png")
        print("  png: app-overview.png")
        # Dark mode (Slice 21): toggle, wait for the dark basemap + re-added radar
        # layer, shoot the same view, then toggle back to light for the rest.
        page.click("#theme")
        page.wait_for_function(
            "() => document.documentElement.dataset.theme === 'dark'"
            " && !!state.map.getLayer('radar-frame-layer')",
            timeout=15000,
        )
        page.wait_for_timeout(4500)
        page.screenshot(path=out / "app-overview-dark.png")
        print("  png: app-overview-dark.png")
        page.click("#theme")
        page.wait_for_function(
            "() => document.documentElement.dataset.theme === 'light'"
            " && !!state.map.getLayer('radar-frame-layer')",
            timeout=15000,
        )
        page.wait_for_timeout(1500)
        # land just after the biggest gap so the markers + flag show
        page.evaluate(
            "(function(){var g=state.gaps&&state.gaps.slice()"
            ".sort((a,b)=>b.seconds-a.seconds)[0];"
            "if(g){pause();goTo(g.afterIndex+1);}})()"
        )
        page.wait_for_timeout(800)
        page.locator("#timeline").screenshot(path=out / "timeline-gaps.png")
        page.locator("#readout").screenshot(path=out / "readout.png")
        page.locator("#rangebar").screenshot(path=out / "window-picker.png")
        page.click("#manage")
        page.wait_for_timeout(500)
        page.locator("#locpanel").screenshot(path=out / "location-panel.png")
        for name in ("timeline-gaps", "readout", "window-picker", "location-panel"):
            print(f"  png: {name}.png")
        ctx.close()

        # ---- GIFs (1x, modest width to stay lean) ----
        gif_vp = {"width": 1100, "height": 650}

        def fresh() -> Page:
            c = browser.new_context(viewport=gif_vp, device_scale_factor=1)
            pg = c.new_page()
            pg.goto(url, wait_until="load")
            _ready(pg)
            return pg

        # playback: loop a smooth, gap-free, weather-rich stretch.
        #
        # Two failure modes to avoid: (1) crossing a collection gap, so the storm
        # teleports (a time-jump, not blank frames — that's what reads as "missing
        # radar"); (2) landing on a sparse/clear stretch with nothing to watch. By
        # default we segment on state.gaps and loop the LONGEST contiguous run (its
        # latest ~18 frames). For a punchier loop, set BACKSCATTER_DOCS_PLAYBACK_FROM/
        # _TO (ISO scan_times) to pin the window to a specific active stretch — pick it
        # with: analyse data/renders/<site>/*.png for peak high-dBZ coverage.
        pg = fresh()
        pb_from = os.environ.get("BACKSCATTER_DOCS_PLAYBACK_FROM")
        pb_to = os.environ.get("BACKSCATTER_DOCS_PLAYBACK_TO")
        if pb_from and pb_to:
            win = pg.evaluate(
                """([from_, to_]) => {
                    const a = Date.parse(from_), b = Date.parse(to_);
                    let s = -1, e = -1;
                    state.frames.forEach((f, i) => {
                        const t = Date.parse(f.scan_time);
                        if (t >= a && t <= b) { if (s < 0) s = i; e = i; }
                    });
                    if (s < 0) return [Math.max(0, state.frames.length - 18),
                                       state.frames.length];
                    return [s, e + 1];
                }""",
                [pb_from, pb_to],
            )
        else:
            win = pg.evaluate(
                """() => {
                    const n = state.frames.length;
                    const bounds = (state.gaps || []).map(g => g.afterIndex)
                        .sort((a, b) => a - b);
                    const starts = [0, ...bounds.map(b => b + 1)];
                    const ends = [...bounds, n - 1];        // inclusive segment ends
                    let best = [0, n - 1], bestLen = -1;
                    for (let i = 0; i < starts.length; i++) {
                        const len = ends[i] - starts[i] + 1;
                        if (len > bestLen) {
                            bestLen = len; best = [starts[i], ends[i]];
                        }
                    }
                    let [s, e] = best;
                    if (e - s + 1 > 18) s = e - 17;         // cap, keep the latest part
                    return [s, e + 1];                      // end-exclusive
                }"""
            )
        start, end = int(win[0]), int(win[1])
        _record(pg, [_goto_frame(i) for i in range(start, end)],
                out / "playback.gif", fps=8, width=960)
        pg.context.close()

        # scrub: sweep the whole loaded archive once (handle traverses, radar changes)
        pg = fresh()
        n = pg.evaluate("state.frames.length")
        steps = [_goto_frame(i) for i in range(0, n, max(1, n // 28))]
        _record(pg, steps, out / "scrub.gif", fps=10, width=960)
        pg.context.close()

        # switch-location: Home (KFTG) -> OKC (KTLX); map flies + readout changes
        pg = fresh()

        def pick_okc(p2: Page) -> None:
            p2.select_option("#location", "OKC")

        switch_steps = (
            [_hold(250)] * 4
            + [pick_okc]
            + [_hold(300)] * 14
        )
        _record(pg, switch_steps, out / "switch-location.gif", fps=8, width=960)
        pg.context.close()

        # manage-locations: open the panel
        pg = fresh()

        def open_manage(p2: Page) -> None:
            p2.click("#manage")

        manage_steps = [_hold(400)] * 2 + [open_manage] + [_hold(350)] * 8
        _record(pg, manage_steps, out / "manage-locations.gif", fps=6, width=900)
        pg.context.close()

        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=None, help="Running app URL (else start one).")
    ap.add_argument("--port", type=int, default=8020, help="Port if starting a server.")
    ap.add_argument("--out", default="docs/assets", help="Output dir for assets.")
    args = ap.parse_args()

    proc = None
    url = args.url
    if not url:
        print(f"starting app on :{args.port} …")
        proc = subprocess.Popen(
            ["uv", "run", "backscatter", "serve", "--port", str(args.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ),
        )
        if not _wait_api(args.port):
            proc.terminate()
            raise SystemExit("server did not come up")
        url = f"http://127.0.0.1:{args.port}/"

    try:
        print("capturing from", url)
        capture(url, Path(args.out))
        print("done.")
        return 0
    finally:
        if proc is not None:
            proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
