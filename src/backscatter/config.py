"""Runtime configuration — infra settings + the seed for the location store.

Infra config (data dir, DB path, poll interval, the global ``SITE`` override) is
env-sourced and immutable. The **location list** is mutable, user-managed runtime
data persisted in the SQLite store (`store/locations.py`, ADR-0008) — not here. This
module owns the env *seed* for an empty store and the reusable location logic
(`Location`, `resolve_location`, `validate_locations`) that the store imports.

No module reads the environment directly; this loader is the only place. A
``BACKSCATTER_LOCATIONS`` JSON list seeds the store; absent it, the single
``BACKSCATTER_LAT``/``BACKSCATTER_LON`` form seeds a one-entry "Home" (back-compat).
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
class SeedLocation:
    """A raw location used only to seed an empty store (site not yet resolved)."""

    name: str
    lat: float
    lon: float
    is_default: bool


@dataclass(frozen=True)
class Location:
    """A named place + its resolved active radar."""

    name: str
    lat: float
    lon: float
    site: str  # resolved nearest radar, or the override (default location only)
    is_default: bool
    site_override: bool  # whether `site` was pinned explicitly (default loc only)
    id: int | None = None  # store row id (None for unsaved/seed-derived)


@dataclass(frozen=True)
class Config:
    """Resolved runtime infra configuration (locations live in the store)."""

    data_dir: Path
    db_path: Path
    poll_interval_s: float
    site_override: str | None  # global SITE pin (applies to the default location)
    seed_locations: tuple[SeedLocation, ...]  # used only to bootstrap an empty store


def resolve_location(
    name: str,
    lat: float,
    lon: float,
    *,
    is_default: bool,
    override: str | None,
) -> Location:
    """Resolve a location's active radar (override pins the default only)."""
    if is_default and override:
        return Location(name, lat, lon, override.upper(), True, True)
    return Location(name, lat, lon, nearest_site(lat, lon).icao, is_default, False)


def validate_locations(names: list[str], default_count: int) -> None:
    """Enforce the location invariants; raise ValueError on violation."""
    if not names:
        raise ValueError("at least one location is required")
    if default_count != 1:
        raise ValueError(
            f"exactly one location must be the default, found {default_count}"
        )
    lowered = [n.lower() for n in names]
    if len(lowered) != len(set(lowered)):
        raise ValueError("location names must be unique (case-insensitive)")


def load_config(
    *,
    site: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> Config:
    """Resolve infra config + the location seed. Precedence CLI arg > env > default.

    Raises:
        ValueError: on malformed ``BACKSCATTER_LOCATIONS`` JSON or an invalid seed
            (no locations, not exactly one default, duplicate names).
    """
    seed = _seed_locations(lat=lat, lon=lon)
    _validate_seed(seed)

    data_dir_env = os.environ.get("BACKSCATTER_DATA_DIR")
    data_dir = Path(data_dir_env) if data_dir_env else DEFAULT_DATA_DIR
    db_path_env = os.environ.get("BACKSCATTER_DB_PATH")
    db_path = Path(db_path_env) if db_path_env else data_dir / DEFAULT_DB_NAME
    poll_interval_s = _first_float(
        None, os.environ.get("BACKSCATTER_POLL_INTERVAL"), DEFAULT_POLL_INTERVAL_S
    )

    return Config(
        data_dir=data_dir,
        db_path=db_path,
        poll_interval_s=poll_interval_s,
        site_override=site or os.environ.get("BACKSCATTER_SITE"),
        seed_locations=seed,
    )


def _seed_locations(
    *, lat: float | None, lon: float | None
) -> tuple[SeedLocation, ...]:
    """Seed locations from BACKSCATTER_LOCATIONS, or the single Home fallback."""
    blob = os.environ.get("BACKSCATTER_LOCATIONS")
    if blob:
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise ValueError(f"BACKSCATTER_LOCATIONS is not valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError("BACKSCATTER_LOCATIONS must be a JSON list of locations")
        return tuple(_seed_from_dict(raw) for raw in parsed)
    # Back-compat: the single lat/lon form is one location named "Home".
    return (
        SeedLocation(
            name=DEFAULT_HOME_NAME,
            lat=_first_float(lat, os.environ.get("BACKSCATTER_LAT"), DEFAULT_LAT),
            lon=_first_float(lon, os.environ.get("BACKSCATTER_LON"), DEFAULT_LON),
            is_default=True,
        ),
    )


def _seed_from_dict(raw: object) -> SeedLocation:
    if not isinstance(raw, dict):
        raise ValueError(f"each location must be a JSON object, got {raw!r}")
    try:
        name = str(raw["name"]).strip()
        lat = float(raw["lat"])
        lon = float(raw["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"location missing/invalid name/lat/lon: {raw!r}") from exc
    if not name:
        raise ValueError(f"location name must be non-empty: {raw!r}")
    return SeedLocation(name, lat, lon, bool(raw.get("default", False)))


def _validate_seed(seed: tuple[SeedLocation, ...]) -> None:
    validate_locations(
        [s.name for s in seed], sum(1 for s in seed if s.is_default)
    )


def _first_float(arg: float | None, env: str | None, default: float) -> float:
    """Return the first present value (arg > env > default) as a float."""
    if arg is not None:
        return float(arg)
    if env is not None:
        return float(env)
    return default
