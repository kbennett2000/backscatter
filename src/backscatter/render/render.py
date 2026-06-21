"""Render one stored volume to a georeferenced PNG + bounds sidecar.

Pipeline: decode lowest-tilt reflectivity → rasterize to Web Mercator (origin from
the bundled site table) → color with the NWS dBZ table → write PNG and a JSON
sidecar carrying explicit bounds (both EPSG:3857 and WGS84) so the map layer
(Slice 4) can place the image without guessing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from backscatter.config import Config
from backscatter.decode.volume import (
    REFLECTIVITY_FIELD,
    Sweep,
    read_lowest_reflectivity,
)
from backscatter.ingest import naming
from backscatter.render.colormap import dbz_to_rgba
from backscatter.render.raster import rasterize
from backscatter.sites.table import Site, site_by_icao


@dataclass(frozen=True)
class RenderResult:
    """Outcome of a render: the written files and their georeferencing."""

    png_path: Path
    sidecar_path: Path
    site: str
    scan_time: datetime
    elevation_deg: float
    width: int
    height: int
    bounds_wgs84: tuple[float, float, float, float]  # west, south, east, north
    bounds_3857: tuple[float, float, float, float]


def _lookup_site(icao: str) -> Site:
    site = site_by_icao(icao)
    if site is None:
        raise ValueError(f"site {icao!r} is not in the bundled NEXRAD table")
    return site


def render_volume(
    volume_path: str | Path,
    config: Config,
    *,
    out_dir: Path | None = None,
) -> RenderResult:
    """Decode a stored volume and render its frame. Thin wrapper over render_sweep."""
    volume_path = Path(volume_path)
    # Site origin and canonical timestamp come from the filename (authoritative,
    # consistent with the index), not the file's internal metadata.
    icao = naming.parse_site(volume_path.name)
    scan_time = naming.parse_scan_time(volume_path.name)
    sweep = read_lowest_reflectivity(volume_path)
    return render_sweep(
        sweep, config, site_icao=icao, scan_time=scan_time, out_dir=out_dir
    )


def render_sweep(
    sweep: Sweep,
    config: Config,
    *,
    site_icao: str,
    scan_time: datetime,
    out_dir: Path | None = None,
) -> RenderResult:
    """Georeference, color, and write a frame from an already-decoded ``Sweep``.

    ``site_icao`` + ``scan_time`` are the authoritative identity (from the volume
    filename for assembled volumes, or the chunk-dir start time for the live path), so a
    live frame and the later assembled frame for the same scan write the same PNG name.
    """
    site = _lookup_site(site_icao)
    raster = rasterize(sweep, site.lat, site.lon)
    rgba = dbz_to_rgba(raster.dbz)

    name = f"{site_icao}{scan_time:%Y%m%d_%H%M%S}_V06"
    out_dir = out_dir or (config.data_dir / "renders" / site_icao)
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{name}.png"
    sidecar_path = out_dir / f"{name}.json"

    Image.fromarray(rgba, mode="RGBA").save(png_path)

    west, south, east, north = raster.bounds_wgs84
    sidecar = {
        "site": site_icao,
        "scan_time": scan_time.isoformat(),
        "elevation_deg": round(sweep.elevation_deg, 3),
        "field": REFLECTIVITY_FIELD,
        "crs": "EPSG:3857",
        "bounds_3857": list(raster.bounds_3857),
        "bounds_wgs84": {"west": west, "south": south, "east": east, "north": north},
        "width": raster.width,
        "height": raster.height,
        "max_range_km": round(raster.max_range_m / 1000.0, 1),
        "source_volume": name,
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    return RenderResult(
        png_path=png_path,
        sidecar_path=sidecar_path,
        site=site_icao,
        scan_time=scan_time,
        elevation_deg=sweep.elevation_deg,
        width=raster.width,
        height=raster.height,
        bounds_wgs84=raster.bounds_wgs84,
        bounds_3857=raster.bounds_3857,
    )
