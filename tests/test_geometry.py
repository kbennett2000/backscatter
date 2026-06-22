"""Value tests for radar polar geometry — the load-bearing correctness piece.

These prove the azimuth convention (0 = north, clockwise), that range increases
outward, and that our ground-range model agrees with Py-ART's reference.
"""

from __future__ import annotations

import numpy as np
import pytest
from pyart.core import antenna_to_cartesian

from backscatter.render.geometry import (
    beam_ground_range,
    geodesic_between,
    geodesic_inverse,
    ground_destination,
    polar_to_lonlat,
)

SITE_LAT, SITE_LON = 39.7866, -104.5458
ELEV = 0.5


def test_azimuth_zero_is_north() -> None:
    lon, lat = polar_to_lonlat(SITE_LAT, SITE_LON, 50_000, 0.0, ELEV)
    assert lat > SITE_LAT  # moved north
    assert abs(lon - SITE_LON) < 1e-3  # barely moved east/west


def test_azimuth_ninety_is_east() -> None:
    lon, lat = polar_to_lonlat(SITE_LAT, SITE_LON, 50_000, 90.0, ELEV)
    assert lon > SITE_LON  # moved east
    assert abs(lat - SITE_LAT) < 1e-2


def test_azimuth_one_eighty_is_south() -> None:
    _lon, lat = polar_to_lonlat(SITE_LAT, SITE_LON, 50_000, 180.0, ELEV)
    assert lat < SITE_LAT


def test_azimuth_two_seventy_is_west() -> None:
    lon, _lat = polar_to_lonlat(SITE_LAT, SITE_LON, 50_000, 270.0, ELEV)
    assert lon < SITE_LON


def test_range_increases_outward() -> None:
    lons_lats = [
        polar_to_lonlat(SITE_LAT, SITE_LON, r, 45.0, ELEV)
        for r in (10_000, 50_000, 150_000)
    ]
    lon_arr = np.array([p[0] for p in lons_lats])
    lat_arr = np.array([p[1] for p in lons_lats])
    az, dist = geodesic_inverse(SITE_LAT, SITE_LON, lon_arr, lat_arr)
    assert dist[0] < dist[1] < dist[2]
    # Azimuth of a 45° gate stays ~45° (NE).
    assert np.allclose(az % 360.0, 45.0, atol=0.5)


@pytest.mark.parametrize("range_km", [10.0, 100.0, 200.0])
def test_ground_range_matches_pyart(range_km: float) -> None:
    # Py-ART is the reference beam model; our ground range must agree.
    x, y, _z = antenna_to_cartesian(range_km, 90.0, ELEV)
    pyart_ground = float(np.hypot(x, y))
    ours = float(beam_ground_range(np.float64(range_km * 1000.0), ELEV))
    assert ours == pytest.approx(pyart_ground, rel=1e-4)


def test_pyart_convention_matches_ours() -> None:
    # az 0 -> +north (y), az 90 -> +east (x): confirms shared convention.
    x_n, y_n, _ = antenna_to_cartesian(50.0, 0.0, ELEV)
    assert y_n > 0 and abs(x_n) < 1.0
    x_e, y_e, _ = antenna_to_cartesian(50.0, 90.0, ELEV)
    assert x_e > 0 and abs(y_e) < 1.0


def test_beam_ground_range_zero_and_monotonic() -> None:
    assert float(beam_ground_range(np.float64(0.0), ELEV)) == pytest.approx(0.0)
    r = np.array([1_000.0, 50_000.0, 100_000.0, 200_000.0])
    s = beam_ground_range(r, ELEV)
    assert np.all(np.diff(s) > 0)
    assert np.all(s <= r)  # ground range never exceeds slant range


def test_geodesic_between_inverts_ground_destination() -> None:
    # Place a point a known azimuth/distance away, then recover them. This is the
    # round-trip the storm-cell tracker relies on for displacement → motion (28b).
    for az_deg, dist_m in ((45.0, 10_000.0), (210.0, 35_000.0), (350.0, 2_500.0)):
        lon, lat = ground_destination(SITE_LAT, SITE_LON, az_deg, dist_m)
        az_back, dist_back = geodesic_between(SITE_LON, SITE_LAT, lon, lat)
        assert az_back == pytest.approx(az_deg, abs=1e-6)
        assert dist_back == pytest.approx(dist_m, rel=1e-9)
