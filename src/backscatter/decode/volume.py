"""Read a stored Level 2 volume and extract the lowest-tilt reflectivity sweep.

Py-ART is the reference reader. We take the **first sweep at the minimum elevation**
— the 0.5° reflectivity surveillance cut — at native super-resolution (0.5° azimuth
× 250 m gates). No resampling, no downsampling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyart
from numpy.typing import NDArray

REFLECTIVITY_FIELD = "reflectivity"


@dataclass(frozen=True)
class Sweep:
    """The lowest-tilt reflectivity sweep, in native polar coordinates."""

    site_id: str
    scan_time: datetime
    elevation_deg: float
    azimuths_deg: NDArray[np.float64]  # (nrays,), degrees cw from north, unsorted
    ranges_m: NDArray[np.float64]  # (ngates,), slant range to gate center
    reflectivity: np.ma.MaskedArray  # (nrays, ngates), dBZ


def read_lowest_reflectivity(path: str | Path) -> Sweep:
    """Decode ``path`` and return its lowest-elevation reflectivity sweep."""
    radar = pyart.io.read_nexrad_archive(str(path))

    # The lowest tilt; first occurrence if the VCP revisits it.
    fixed_angles = radar.fixed_angle["data"]
    sweep_index = int(np.argmin(fixed_angles))

    sweep_slice = radar.get_slice(sweep_index)
    azimuths = np.asarray(radar.azimuth["data"][sweep_slice], dtype=np.float64)
    ranges = np.asarray(radar.range["data"], dtype=np.float64)
    reflectivity = radar.fields[REFLECTIVITY_FIELD]["data"][sweep_slice]

    # datetime_from_radar returns a cftime datetime; rebuild a stdlib UTC one.
    t = pyart.util.datetime_from_radar(radar)
    scan_time = datetime(t.year, t.month, t.day, t.hour, t.minute, t.second, tzinfo=UTC)

    return Sweep(
        site_id=str(radar.metadata.get("instrument_name", "")).strip().upper(),
        scan_time=scan_time,
        elevation_deg=float(fixed_angles[sweep_index]),
        azimuths_deg=azimuths,
        ranges_m=ranges,
        reflectivity=np.ma.masked_invalid(reflectivity),
    )
