"""Read a stored Level 2 volume and extract the lowest-tilt reflectivity sweep.

Py-ART is the reference reader. We take the **first sweep at the minimum elevation**
— the 0.5° reflectivity surveillance cut — at native super-resolution (0.5° azimuth
× 250 m gates). No resampling, no downsampling.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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


def _extract_sweep(
    radar: pyart.core.Radar, sweep_index: int, scan_time: datetime
) -> Sweep:
    """Build a :class:`Sweep` for one sweep of a decoded radar at a given scan time."""
    sweep_slice = radar.get_slice(sweep_index)
    azimuths = np.asarray(radar.azimuth["data"][sweep_slice], dtype=np.float64)
    ranges = np.asarray(radar.range["data"], dtype=np.float64)
    reflectivity = radar.fields[REFLECTIVITY_FIELD]["data"][sweep_slice]
    return Sweep(
        site_id=str(radar.metadata.get("instrument_name", "")).strip().upper(),
        scan_time=scan_time,
        elevation_deg=float(radar.fixed_angle["data"][sweep_index]),
        azimuths_deg=azimuths,
        ranges_m=ranges,
        reflectivity=np.ma.masked_invalid(reflectivity),
    )


def _volume_start(radar: pyart.core.Radar) -> datetime:
    """The volume's start time as a stdlib UTC datetime (whole seconds)."""
    # datetime_from_radar returns a cftime datetime; rebuild a stdlib UTC one.
    t = pyart.util.datetime_from_radar(radar)
    return datetime(t.year, t.month, t.day, t.hour, t.minute, t.second, tzinfo=UTC)


def sweep_from_radar(radar: pyart.core.Radar) -> Sweep:
    """Extract the lowest-tilt reflectivity sweep from a decoded Py-ART radar."""
    # The lowest tilt; first occurrence if the VCP revisits it.
    sweep_index = int(np.argmin(radar.fixed_angle["data"]))
    return _extract_sweep(radar, sweep_index, _volume_start(radar))


def surveillance_indices(fixed_angle: NDArray[np.float64]) -> list[int]:
    """Indices of the lowest-tilt **surveillance** cuts in a volume's sweep list.

    A WSR-88D split cut scans the lowest tilt twice — surveillance (reflectivity)
    first, then Doppler (velocity) — and both carry reflectivity in super-res, so the
    presence of reflectivity does NOT distinguish them. The robust rule is *the first
    sweep of each visit to the minimum elevation*: that selects the surveillance half
    and drops its Doppler twin. SAILS/MRLE re-scan the lowest tilt mid-volume, so a
    precip volume yields several such visits (one base + one per SAILS cut); a clear-air
    or legacy volume yields exactly one. Matches the proven ``argmin`` base selection
    for the first cut (see ADR-0012)."""
    angles = np.asarray(fixed_angle, dtype=np.float64)
    at_min = np.isclose(angles, angles.min())
    return [
        i for i in range(len(angles)) if at_min[i] and (i == 0 or not at_min[i - 1])
    ]


def sweep_start_time(
    time_units: str,
    time_data: NDArray[np.float64],
    sweep_start_ray_index: NDArray[np.int64],
    sweep_index: int,
) -> datetime:
    """Start time of one sweep from a radar's time arrays — pure, value-testable.

    ``time_units`` is ``"seconds since <ISO epoch>"`` and ``time_data`` holds each ray's
    offset from it; the sweep's start is its first ray's offset. Whole seconds
    (truncated), tz-aware UTC — same shape as :func:`_volume_start`, so the base cut's
    time equals the assembled ``_V06`` name (reconcile-safe)."""
    iso = time_units.split("since", 1)[1].strip().replace("Z", "+00:00")
    epoch = datetime.fromisoformat(iso)
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=UTC)
    ray0 = int(sweep_start_ray_index[sweep_index])
    offset = float(time_data[ray0])
    return (epoch + timedelta(seconds=offset)).replace(microsecond=0)


def _sweep_start_time(radar: pyart.core.Radar, sweep_index: int) -> datetime:
    """Start time of one sweep of a decoded radar (see :func:`sweep_start_time`)."""
    return sweep_start_time(
        str(radar.time["units"]),
        radar.time["data"],
        radar.sweep_start_ray_index["data"],
        sweep_index,
    )


def surveillance_sweeps(radar: pyart.core.Radar) -> list[Sweep]:
    """Every lowest-tilt 0.5° surveillance cut in the volume, each at its own time.

    One sweep for a normal volume; more when SAILS/MRLE re-scan the lowest tilt
    (each extra cut is a distinct frame ~minutes later). Ordered earliest-first."""
    return [
        _extract_sweep(radar, i, _sweep_start_time(radar, i))
        for i in surveillance_indices(radar.fixed_angle["data"])
    ]


def read_lowest_reflectivity(path: str | Path) -> Sweep:
    """Decode a complete stored volume → its lowest-elevation reflectivity sweep."""
    return sweep_from_radar(pyart.io.read_nexrad_archive(str(path)))


def try_decode_lowest(data: bytes, *, min_sweeps: int = 2) -> Sweep | None:
    """Decode a possibly-PARTIAL Level 2 stream (concatenated real-time chunks) → the
    lowest-tilt reflectivity sweep, but ONLY once >= ``min_sweeps`` cuts are present.

    That completeness rule is the crux: the 0.5° reflectivity surveillance cut is the
    FIRST sweep, so a second sweep appearing means the lowest cut is fully scanned and
    frozen. We never render a half-swept frame; ``None`` means "not complete yet, wait".
    Resolution-/split-cut-agnostic (super-res 720-ray and legacy 360-ray both work)."""
    try:
        radar = pyart.io.read_nexrad_archive(io.BytesIO(data))
    except Exception:
        return None  # truncated mid-record / not enough bytes yet
    if radar.nsweeps < min_sweeps:
        return None
    return sweep_from_radar(radar)


def try_decode_all_lowest(data: bytes) -> list[Sweep]:
    """Decode a possibly-PARTIAL stream → every **frozen** 0.5° surveillance cut.

    The multi-cut analogue of :func:`try_decode_lowest`: a surveillance cut is frozen
    (fully swept) once a later sweep has begun — generalizing the base cut's
    ``nsweeps >= 2`` rule to "a sweep exists past this cut's index". So a partial volume
    surfaces its base cut as soon as the next sweep starts, then each SAILS cut as the
    volume continues to arrive. Returns ``[]`` if the stream doesn't decode yet or no
    cut is frozen. Used by the live chunks assembler (Slice 27b)."""
    try:
        radar = pyart.io.read_nexrad_archive(io.BytesIO(data))
    except Exception:
        return []  # truncated mid-record / not enough bytes yet
    frozen = [
        i
        for i in surveillance_indices(radar.fixed_angle["data"])
        if i < radar.nsweeps - 1  # a later sweep exists → this cut is fully swept
    ]
    return [_extract_sweep(radar, i, _sweep_start_time(radar, i)) for i in frozen]
