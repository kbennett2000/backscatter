"""Discover the newest rendered frame from the on-disk render sidecars.

Renders are not yet tracked in the SQLite index (that arrives with the Slice 5
collection loop), so the newest frame is found by scanning the render sidecars
under ``data/renders/<SITE>/<name>.json``. Each sidecar already carries the bounds
and metadata the map needs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

RENDERS_SUBDIR = "renders"


@dataclass(frozen=True)
class FrameMeta:
    """Everything the frontend needs to place one frame on the map."""

    site: str
    scan_time: str  # ISO-8601 UTC, as written by the renderer
    elevation_deg: float
    width: int
    height: int
    bounds: dict[str, float]  # west, south, east, north (WGS84)
    image_url: str  # served path, e.g. /renders/KFTG/KFTG..._V06.png

    def to_json(self) -> dict[str, object]:
        return {
            "site": self.site,
            "scan_time": self.scan_time,
            "elevation_deg": self.elevation_deg,
            "width": self.width,
            "height": self.height,
            "bounds": self.bounds,
            "image_url": self.image_url,
        }


def renders_dir(data_dir: Path) -> Path:
    """Directory holding rendered frames for a given data dir."""
    return data_dir / RENDERS_SUBDIR


def _frame_from_sidecar(sidecar: Path, root: Path) -> FrameMeta | None:
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        site = meta["site"]
        volume = meta["source_volume"]
        bounds = meta["bounds_wgs84"]
    except (OSError, ValueError, KeyError):
        return None
    # image_url mirrors the sidecar location under the /renders mount.
    rel = sidecar.parent.relative_to(root)
    return FrameMeta(
        site=site,
        scan_time=meta["scan_time"],
        elevation_deg=meta["elevation_deg"],
        width=meta["width"],
        height=meta["height"],
        bounds={k: bounds[k] for k in ("west", "south", "east", "north")},
        image_url=f"/renders/{rel.as_posix()}/{volume}.png",
    )


def latest_frame(data_dir: Path) -> FrameMeta | None:
    """Return the newest rendered frame (by scan_time), or None if there are none."""
    root = renders_dir(data_dir)
    if not root.is_dir():
        return None
    frames = [
        frame
        for sidecar in root.glob("**/*.json")
        if (frame := _frame_from_sidecar(sidecar, root)) is not None
    ]
    if not frames:
        return None
    return max(frames, key=lambda f: f.scan_time)
