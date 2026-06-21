"""Runtime configuration — the single source of truth for locations, paths, cadence.

Configuration is a **list of named locations**, exactly one flagged the default
("Home"). The active radar ``site`` for each location is resolved from its lat/lon
against the bundled NEXRAD table (ADR-0005), unless an explicit site override is
given — which applies to the **default** location only. `collect` archives every
location; the API resolves a location to its site (frames are per-radar, ADR-0006).

Locations come from ``BACKSCATTER_LOCATIONS`` (a JSON list); absent that, the single
``BACKSCATTER_LAT``/``BACKSCATTER_LON`` form is treated as a one-entry "Home" list
(back-compat). No module reads the environment directly; this loader is the only
place, so a file-based loader (e.g. TOML) can drop in later (see ADR-0006).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from backscatter.sites.select import nearest_site

# Default location: Elizabeth, CO (operator's area; resolves to KFTG).
DEFAULT_LAT = 39.3603
DEFAULT_LON = -104.5969
DEFAULT_HOME_NAME = "Home"
DEFAULT_DATA_DIR = Path("data")
# Default DB filename, placed inside the resolved data dir unless overridden.
DEFAULT_DB_NAME = "backscatter.db"
# How often the collect loop polls S3. The radar cadence (4–10 min) is the real
# ceiling; ~60s with timestamp dedupe captures every volume without hammering S3.
DEFAULT_POLL_INTERVAL_S = 60.0


@dataclass(frozen=True)
class Location:
    """A named place + its resolved active radar."""

    name: str
    lat: float
    lon: float
    site: str  # resolved nearest radar, or the override (default location only)
    is_default: bool
    site_override: bool  # whether `site` was pinned explicitly (default loc only)


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    locations: tuple[Location, ...]
    data_dir: Path
    db_path: Path
    poll_interval_s: float

    @property
    def default_location(self) -> Location:
        """The Home location (validated to exist exactly once at load)."""
        return next(loc for loc in self.locations if loc.is_default)

    def location_by_name(self, name: str) -> Location | None:
        """Look up a configured location by name (case-insensitive)."""
        target = name.strip().lower()
        for loc in self.locations:
            if loc.name.lower() == target:
                return loc
        return None

    # Home-facade: existing single-location consumers (pull, /api/config, cli) read
    # these and transparently operate on the default location.
    @property
    def lat(self) -> float:
        return self.default_location.lat

    @property
    def lon(self) -> float:
        return self.default_location.lon

    @property
    def site(self) -> str:
        return self.default_location.site

    @property
    def site_override(self) -> bool:
        return self.default_location.site_override


def load_config(
    *,
    site: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> Config:
    """Resolve configuration with precedence CLI arg > env > default.

    Args:
        site: Explicit site override; applies to the default location only.
        lat: Latitude override for the (single-location) Home form.
        lon: Longitude override for the (single-location) Home form.

    Raises:
        ValueError: on invalid configuration (no locations, not exactly one default,
            duplicate names, or malformed ``BACKSCATTER_LOCATIONS`` JSON).
    """
    raw = _raw_locations(lat=lat, lon=lon)
    override = site or os.environ.get("BACKSCATTER_SITE")
    locations = tuple(_resolve_location(r, override) for r in raw)
    _validate_locations(locations)

    data_dir_env = os.environ.get("BACKSCATTER_DATA_DIR")
    data_dir = Path(data_dir_env) if data_dir_env else DEFAULT_DATA_DIR
    db_path_env = os.environ.get("BACKSCATTER_DB_PATH")
    db_path = Path(db_path_env) if db_path_env else data_dir / DEFAULT_DB_NAME
    poll_interval_s = _first_float(
        None, os.environ.get("BACKSCATTER_POLL_INTERVAL"), DEFAULT_POLL_INTERVAL_S
    )

    return Config(
        locations=locations,
        data_dir=data_dir,
        db_path=db_path,
        poll_interval_s=poll_interval_s,
    )


def _raw_locations(*, lat: float | None, lon: float | None) -> list[dict[str, object]]:
    """Raw location dicts from BACKSCATTER_LOCATIONS, or the single Home fallback."""
    blob = os.environ.get("BACKSCATTER_LOCATIONS")
    if blob:
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise ValueError(f"BACKSCATTER_LOCATIONS is not valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError("BACKSCATTER_LOCATIONS must be a JSON list of locations")
        return parsed
    # Back-compat: the single lat/lon form is one location named "Home".
    return [
        {
            "name": DEFAULT_HOME_NAME,
            "lat": _first_float(lat, os.environ.get("BACKSCATTER_LAT"), DEFAULT_LAT),
            "lon": _first_float(lon, os.environ.get("BACKSCATTER_LON"), DEFAULT_LON),
            "default": True,
        }
    ]


def _resolve_location(raw: dict[str, object], override: str | None) -> Location:
    """Turn a raw location dict into a resolved :class:`Location`."""
    if not isinstance(raw, dict):
        raise ValueError(f"each location must be a JSON object, got {raw!r}")
    try:
        name = str(raw["name"]).strip()
        lat = float(raw["lat"])  # type: ignore[arg-type]
        lon = float(raw["lon"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"location missing/invalid name/lat/lon: {raw!r}") from exc
    if not name:
        raise ValueError(f"location name must be non-empty: {raw!r}")
    is_default = bool(raw.get("default", False))

    # The site override pins the default location only; others resolve their nearest.
    if is_default and override:
        return Location(name, lat, lon, override.upper(), True, True)
    return Location(name, lat, lon, nearest_site(lat, lon).icao, is_default, False)


def _validate_locations(locations: tuple[Location, ...]) -> None:
    if not locations:
        raise ValueError("at least one location is required")
    defaults = [loc for loc in locations if loc.is_default]
    if len(defaults) != 1:
        raise ValueError(
            f"exactly one location must be the default, found {len(defaults)}"
        )
    names = [loc.name.lower() for loc in locations]
    if len(names) != len(set(names)):
        raise ValueError("location names must be unique (case-insensitive)")


def _first_float(arg: float | None, env: str | None, default: float) -> float:
    """Return the first present value (arg > env > default) as a float."""
    if arg is not None:
        return float(arg)
    if env is not None:
        return float(env)
    return default
