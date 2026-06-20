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
    frames_in_range,
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
        limit: int = Query(DEFAULT_FRAMES_LIMIT, ge=1),
    ) -> dict[str, object]:
        """Rendered frames for a site over a range, oldest-first (for the timeline).

        Defaults to the configured site and the most recent ``limit`` frames
        (capped at MAX_FRAMES_LIMIT). An empty range returns 200 with no frames.
        """
        resolved_site = (site or config.site).upper()
        start_dt = _parse_ts(start, "start")
        end_dt = _parse_ts(end, "end")
        capped = min(limit, MAX_FRAMES_LIMIT)

        conn = db.connect(config.db_path)
        try:
            frames = frames_in_range(
                conn, site=resolved_site, start=start_dt, end=end_dt, limit=capped
            )
        finally:
            conn.close()
        return {
            "site": resolved_site,
            "count": len(frames),
            "limit": capped,
            "frames": [f.to_json() for f in frames],
        }

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
