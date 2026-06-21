"""FastAPI app: serves the MapLibre page, the rendered PNGs, and a small JSON API.

A single app per ADR-0004. ``create_app`` takes a :class:`Config` and bootstraps the
DB (schema + seeded location store). Locations are mutable, DB-backed state (ADR-0008)
— read live each request and managed via the CRUD endpoints. The app reads only
``config`` + the DB; no env access.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backscatter.api.frames import (
    DEFAULT_FRAMES_LIMIT,
    MAX_FRAMES_LIMIT,
    frames_extent,
    frames_in_range,
    frames_window,
    latest_frame,
    renders_dir,
)
from backscatter.config import Config, Location
from backscatter.store import db
from backscatter.store import locations as locations_store


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


def _resolve_site(
    conn: sqlite3.Connection, config: Config, site: str | None, location: str | None
) -> str:
    """Resolve which radar to serve: explicit site > location's site > default."""
    if site:
        return site.upper()
    if location:
        target = location.lower()
        for loc in locations_store.current_locations(conn, config.site_override):
            if loc.name.lower() == target:
                return loc.site
        raise HTTPException(status_code=400, detail=f"unknown location: {location!r}")
    return locations_store.default_location(conn, config.site_override).site


def _location_json(loc: Location) -> dict[str, object]:
    return {
        "id": loc.id,
        "name": loc.name,
        "lat": loc.lat,
        "lon": loc.lon,
        "default": loc.is_default,
        "site": loc.site,
    }


class LocationCreate(BaseModel):
    name: str
    lat: float
    lon: float
    default: bool = False


class LocationUpdate(BaseModel):
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    default: bool | None = None


# Frontend lives at the repo-level web/ dir (self-hosted from source). Resolve it
# relative to this file: src/backscatter/api/app.py -> repo root.
_DEFAULT_WEB_DIR = Path(__file__).resolve().parents[3] / "web"


def create_app(config: Config, *, web_dir: Path | None = None) -> FastAPI:
    """Build the FastAPI application for a given configuration."""
    web = web_dir or _DEFAULT_WEB_DIR
    renders = renders_dir(config.data_dir)

    # Bootstrap once: schema + seed the location store from env iff empty.
    boot = locations_store.connect_bootstrapped(config)
    boot.close()

    app = FastAPI(title="backscatter", docs_url=None, redoc_url=None)

    def _conn() -> sqlite3.Connection:
        return db.connect(config.db_path)

    @app.get("/api/config")
    def api_config() -> dict[str, object]:
        conn = _conn()
        try:
            d = locations_store.default_location(conn, config.site_override)
        finally:
            conn.close()
        return {"center": [d.lon, d.lat], "site": d.site}

    @app.get("/api/locations")
    def api_locations() -> dict[str, object]:
        conn = _conn()
        try:
            locs = locations_store.current_locations(conn, config.site_override)
        finally:
            conn.close()
        return {"locations": [_location_json(loc) for loc in locs]}

    @app.post("/api/locations", status_code=201)
    def create_location(body: LocationCreate) -> dict[str, object]:
        conn = _conn()
        try:
            loc = locations_store.create(
                conn, config.site_override,
                name=body.name, lat=body.lat, lon=body.lon, make_default=body.default,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            conn.close()
        return _location_json(loc)

    @app.put("/api/locations/{loc_id}")
    def update_location(loc_id: int, body: LocationUpdate) -> dict[str, object]:
        conn = _conn()
        try:
            loc = locations_store.update(
                conn, config.site_override, loc_id,
                name=body.name, lat=body.lat, lon=body.lon, make_default=body.default,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"no location {loc_id}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            conn.close()
        return _location_json(loc)

    @app.delete("/api/locations/{loc_id}", status_code=204)
    def delete_location(loc_id: int) -> None:
        conn = _conn()
        try:
            locations_store.delete(conn, loc_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"no location {loc_id}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            conn.close()

    @app.get("/api/latest")
    def api_latest(
        site: str | None = None, location: str | None = None
    ) -> dict[str, object]:
        conn = _conn()
        try:
            resolved_site = _resolve_site(conn, config, site, location)
            frame = latest_frame(conn, resolved_site)
        finally:
            conn.close()
        if frame is None:
            raise HTTPException(status_code=404, detail="no rendered frames yet")
        return frame.to_json()

    @app.get("/api/frames")
    def api_frames(
        site: str | None = None,
        location: str | None = None,
        start: str | None = None,
        end: str | None = None,
        cursor: str | None = None,
        limit: int = Query(DEFAULT_FRAMES_LIMIT, ge=1),
    ) -> dict[str, object]:
        """Rendered frames for a site/location, oldest-first (for the timeline).

        Two modes, both capped at MAX_FRAMES_LIMIT per request:
        - No ``start`` and no ``cursor`` → the most recent ``limit`` frames
          (the default rolling window). ``next_cursor`` is null.
        - With ``start`` and/or ``cursor`` → one ascending page of the
          ``[start, end]`` window. ``cursor`` is an exclusive lower bound
          (a prior frame's ``scan_time``); pass back ``next_cursor`` to page
          forward through a window deeper than the cap. Empty range → 200, [].
        ``location`` resolves to its site; ``site`` wins if both are given;
        otherwise defaults to the default location.
        """
        start_dt = _parse_ts(start, "start")
        end_dt = _parse_ts(end, "end")
        cursor_dt = _parse_ts(cursor, "cursor")
        capped = min(limit, MAX_FRAMES_LIMIT)

        conn = _conn()
        try:
            resolved_site = _resolve_site(conn, config, site, location)
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
    def api_frames_range(
        site: str | None = None, location: str | None = None
    ) -> dict[str, object]:
        """Earliest/latest rendered scan_time + count for a site (archive extent)."""
        conn = _conn()
        try:
            resolved_site = _resolve_site(conn, config, site, location)
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
