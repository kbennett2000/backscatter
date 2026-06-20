"""Runtime configuration — the single source of truth for location, site, paths.

Every module takes a :class:`Config`; no module reads the environment directly.
The primary location input is a lat/lon; the active radar ``site`` is resolved from
it against the bundled NEXRAD table (ADR-0005) unless an explicit site override is
given. Precedence is **CLI argument > environment variable > built-in default**.
The loader is intentionally small so a file-based loader (e.g. TOML) can drop in
later without changing call sites (see ADR-0006).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from backscatter.sites.select import nearest_site

# Default location: Elizabeth, CO (operator's area; resolves to KFTG).
DEFAULT_LAT = 39.3603
DEFAULT_LON = -104.5969
DEFAULT_DATA_DIR = Path("data")
# Default DB filename, placed inside the resolved data dir unless overridden.
DEFAULT_DB_NAME = "backscatter.db"


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    lat: float
    lon: float
    site: str
    data_dir: Path
    db_path: Path


def load_config(
    *,
    site: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> Config:
    """Resolve configuration with precedence CLI arg > env > default.

    The active ``site`` is the nearest radar to the resolved lat/lon, unless an
    explicit site (``site`` arg or ``BACKSCATTER_SITE``) is given — that always
    wins.

    Args:
        site: Explicit site override (e.g. the ``pull`` positional).
        lat: Latitude override.
        lon: Longitude override.
    """
    resolved_lat = _first_float(lat, os.environ.get("BACKSCATTER_LAT"), DEFAULT_LAT)
    resolved_lon = _first_float(lon, os.environ.get("BACKSCATTER_LON"), DEFAULT_LON)

    explicit_site = site or os.environ.get("BACKSCATTER_SITE")
    resolved_site = (
        explicit_site.upper()
        if explicit_site
        else nearest_site(resolved_lat, resolved_lon).icao
    )

    data_dir_env = os.environ.get("BACKSCATTER_DATA_DIR")
    data_dir = Path(data_dir_env) if data_dir_env else DEFAULT_DATA_DIR

    db_path_env = os.environ.get("BACKSCATTER_DB_PATH")
    db_path = Path(db_path_env) if db_path_env else data_dir / DEFAULT_DB_NAME

    return Config(
        lat=resolved_lat,
        lon=resolved_lon,
        site=resolved_site,
        data_dir=data_dir,
        db_path=db_path,
    )


def _first_float(arg: float | None, env: str | None, default: float) -> float:
    """Return the first present value (arg > env > default) as a float."""
    if arg is not None:
        return float(arg)
    if env is not None:
        return float(env)
    return default
