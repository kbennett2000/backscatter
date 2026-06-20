"""Radar polar geometry → geographic / web-mercator coordinates.

The azimuth convention here **is** the radar convention and the geodesic
convention: 0° = north, increasing clockwise (90° = east). Gate placement uses the
great-circle (``pyproj.Geod``) from the site, after correcting slant range to
ground range with the standard 4/3-earth beam model. This module is the load-bearing
geometry; it is proven by value tests and cross-checked against Py-ART, not by
eyeballing an image.
"""

from __future__ import annotations

from typing import cast

import numpy as np
from numpy.typing import NDArray
from pyproj import Geod, Transformer

# WGS84 geodesic for great-circle gate placement.
_GEOD = Geod(ellps="WGS84")
# WGS84 (lon/lat) <-> Web Mercator (EPSG:3857), always_xy => (lon, lat) / (x, y).
_TO_MERCATOR = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_TO_WGS84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

# Effective earth radius for beam propagation: 4/3 * mean earth radius (m).
_EFFECTIVE_EARTH_RADIUS_M = 4.0 / 3.0 * 6371000.0


def beam_ground_range(
    slant_range_m: NDArray[np.float64] | float, elev_deg: float
) -> NDArray[np.float64] | float:
    """Great-circle distance along the ground to a gate at a given slant range.

    Standard 4/3-earth beam model (Doviak & Zrnić): accounts for the beam climbing
    above the curved earth. Monotonic in slant range; 0 at the radar.
    """
    elev = np.radians(elev_deg)
    ke_a = _EFFECTIVE_EARTH_RADIUS_M
    height = (
        np.sqrt(
            slant_range_m**2 + ke_a**2 + 2.0 * slant_range_m * ke_a * np.sin(elev)
        )
        - ke_a
    )
    ground = ke_a * np.arcsin(slant_range_m * np.cos(elev) / (ke_a + height))
    return cast("NDArray[np.float64] | float", ground)


def polar_to_lonlat(
    site_lat: float,
    site_lon: float,
    range_m: float,
    az_deg: float,
    elev_deg: float,
) -> tuple[float, float]:
    """Map a single (range, azimuth) gate to (lon, lat).

    Azimuth is degrees clockwise from north. Returns ``(lon, lat)`` in WGS84.
    """
    ground = float(beam_ground_range(np.float64(range_m), elev_deg))
    lon, lat, _back_az = _GEOD.fwd(site_lon, site_lat, az_deg, ground)
    return float(lon), float(lat)


def ground_destination(
    site_lat: float, site_lon: float, az_deg: float, ground_m: float
) -> tuple[float, float]:
    """(lon, lat) reached by travelling ``ground_m`` along ``az_deg`` from the site.

    Pure geodesic (no beam correction) — used to bound the coverage circle.
    """
    lon, lat, _back = _GEOD.fwd(site_lon, site_lat, az_deg, ground_m)
    return float(lon), float(lat)


def lonlat_to_mercator(
    lon: NDArray[np.float64] | float, lat: NDArray[np.float64] | float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """WGS84 (lon, lat) → Web Mercator (x, y) in meters. Vectorized."""
    x, y = _TO_MERCATOR.transform(lon, lat)
    return x, y


def mercator_to_lonlat(
    x: NDArray[np.float64] | float, y: NDArray[np.float64] | float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Web Mercator (x, y) → WGS84 (lon, lat). Vectorized."""
    lon, lat = _TO_WGS84.transform(x, y)
    return lon, lat


def geodesic_inverse(
    site_lat: float,
    site_lon: float,
    lon: NDArray[np.float64],
    lat: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """For each point, azimuth (deg cw from north) and ground distance (m) from site.

    Vectorized inverse geodesic used to map output pixels back to polar coordinates.
    """
    site_lons = np.full(lon.shape, site_lon)
    site_lats = np.full(lat.shape, site_lat)
    az, _back_az, dist = _GEOD.inv(site_lons, site_lats, lon, lat)
    return np.asarray(az, dtype=np.float64), np.asarray(dist, dtype=np.float64)
