"""FastAPI app: serves the MapLibre page, the rendered PNGs, and a small JSON API.

A single app per ADR-0004. ``create_app`` is a factory taking a :class:`Config`, so
tests inject a config pointing at a temp data dir. The app reads only
``config.lat/lon`` (map center) and ``config.data_dir`` (renders) — no env access.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backscatter.api.frames import (
    DEFAULT_FRAMES_LIMIT,
    MAX_FRAMES_LIMIT,
    frames_extent,
    frames_in_range,
    frames_window,
    latest_frame,
    renders_dir,
)
from backscatter.config import Config
from backscatter.store import db


def _parse_ts(value: str | None, label: str) -> datetime | None:
    """Parse an optional ISO-8601 timestamp, or raise HTTP 400."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid {label} timestamp: {value!r}"
        ) from exc

# Frontend lives at the repo-level web/ dir (self-hosted from source). Resolve it
# relative to this file: src/backscatter/api/app.py -> repo root.
_DEFAULT_WEB_DIR = Path(__file__).resolve().parents[3] / "web"


def create_app(config: Config, *, web_dir: Path | None = None) -> FastAPI:
    """Build the FastAPI application for a given configuration."""
    web = web_dir or _DEFAULT_WEB_DIR
    renders = renders_dir(config.data_dir)

    app = FastAPI(title="backscatter", docs_url=None, redoc_url=None)

    @app.get("/api/config")
    def api_config() -> dict[str, object]:
        return {"center": [config.lon, config.lat], "site": config.site}

    @app.get("/api/latest")
    def api_latest() -> dict[str, object]:
        conn = db.connect(config.db_path)
        try:
            frame = latest_frame(conn)
        finally:
            conn.close()
        if frame is None:
            raise HTTPException(status_code=404, detail="no rendered frames yet")
        return frame.to_json()

    @app.get("/api/frames")
    def api_frames(
        site: str | None = None,
        start: str | None = None,
        end: str | None = None,
        cursor: str | None = None,
        limit: int = Query(DEFAULT_FRAMES_LIMIT, ge=1),
    ) -> dict[str, object]:
        """Rendered frames for a site, oldest-first (for the timeline).

        Two modes, both capped at MAX_FRAMES_LIMIT per request:
        - No ``start`` and no ``cursor`` → the most recent ``limit`` frames
          (the default rolling window). ``next_cursor`` is null.
        - With ``start`` and/or ``cursor`` → one ascending page of the
          ``[start, end]`` window. ``cursor`` is an exclusive lower bound
          (a prior frame's ``scan_time``); pass back ``next_cursor`` to page
          forward through a window deeper than the cap. Empty range → 200, [].
        """
        resolved_site = (site or config.site).upper()
        start_dt = _parse_ts(start, "start")
        end_dt = _parse_ts(end, "end")
        cursor_dt = _parse_ts(cursor, "cursor")
        capped = min(limit, MAX_FRAMES_LIMIT)

        conn = db.connect(config.db_path)
        try:
            if start_dt is None and cursor_dt is None:
                frames = frames_in_range(
                    conn, site=resolved_site, start=None, end=end_dt, limit=capped
                )
                next_cursor: str | None = None
            else:
                # Fetch one extra to detect whether another page exists.
                page = frames_window(
                    conn, site=resolved_site, start=start_dt, end=end_dt,
                    after=cursor_dt, limit=capped + 1,
                )
                has_more = len(page) > capped
                frames = page[:capped]
                next_cursor = frames[-1].scan_time if has_more else None
        finally:
            conn.close()
        return {
            "site": resolved_site,
            "count": len(frames),
            "limit": capped,
            "next_cursor": next_cursor,
            "frames": [f.to_json() for f in frames],
        }

    @app.get("/api/frames/range")
    def api_frames_range(site: str | None = None) -> dict[str, object]:
        """Earliest/latest rendered scan_time + count for a site (archive extent)."""
        resolved_site = (site or config.site).upper()
        conn = db.connect(config.db_path)
        try:
            mn, mx, count = frames_extent(conn, site=resolved_site)
        finally:
            conn.close()
        return {"site": resolved_site, "min": mn, "max": mx, "count": count}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(web / "index.html")

    # Rendered PNGs (may not exist yet — don't fail app construction).
    app.mount(
        "/renders",
        StaticFiles(directory=renders, check_dir=False),
        name="renders",
    )
    app.mount("/static", StaticFiles(directory=web, check_dir=False), name="static")

    return app
