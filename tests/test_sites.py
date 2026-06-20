"""Tests for the bundled site table, haversine, and nearest-site selection.

Coordinates are load-bearing — a wrong value silently resolves the wrong radar —
so haversine is checked against known great-circle distances and selection against
known points across CONUS.
"""

from __future__ import annotations

import pytest

from backscatter.sites.select import (
    COVERAGE_RANGE_KM,
    haversine,
    nearest_site,
    rank_sites,
)
from backscatter.sites.table import load_sites

# --- haversine: value tests against known distances --------------------------


def test_haversine_one_degree_latitude() -> None:
    # One degree of latitude is ~111.19 km anywhere.
    assert haversine(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.19, abs=0.5)


def test_haversine_one_degree_longitude_at_equator() -> None:
    # One degree of longitude at the equator is ~111.32 km.
    assert haversine(0.0, 0.0, 0.0, 1.0) == pytest.approx(111.32, abs=0.5)


def test_haversine_known_city_pair() -> None:
    # London (51.5074, -0.1278) to Paris (48.8566, 2.3522): ~343 km.
    assert haversine(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(343, abs=5)


def test_haversine_symmetric_and_zero() -> None:
    assert haversine(40.0, -105.0, 40.0, -105.0) == pytest.approx(0.0, abs=1e-9)
    a = haversine(39.0, -104.0, 35.0, -97.0)
    b = haversine(35.0, -97.0, 39.0, -104.0)
    assert a == pytest.approx(b)


# --- nearest-site resolution: known points across CONUS ----------------------


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (39.3603, -104.5969, "KFTG"),  # Elizabeth, CO (anchor)
        (35.4676, -97.5164, "KTLX"),  # Oklahoma City, OK
        (41.8781, -87.6298, "KLOT"),  # Chicago, IL
        (25.7617, -80.1918, "KAMX"),  # Miami, FL
        (47.6062, -122.3321, "KATX"),  # Seattle, WA
    ],
)
def test_nearest_site_known_points(lat: float, lon: float, expected: str) -> None:
    assert nearest_site(lat, lon).icao == expected


def test_research_radars_excluded_from_table() -> None:
    # KCRI (ROC) and KOUN (NSSL) are non-operational Norman, OK research/test
    # radars. They are intentionally excluded from the bundled table; a future CSV
    # regeneration must not silently re-add them. See nexrad_sites.csv header.
    icaos = {s.icao for s in load_sites()}
    assert "KCRI" not in icaos
    assert "KOUN" not in icaos


def test_norman_area_resolves_to_operational_ktlx() -> None:
    # A coordinate essentially on top of the excluded KCRI/KOUN must resolve to the
    # nearest *operational* radar, KTLX — not a re-added research radar. Before the
    # exclusion this point resolved to KCRI, so this is the regression guard.
    assert nearest_site(35.2226, -97.4395).icao == "KTLX"


def test_rank_sites_is_distance_ordered() -> None:
    ranked = rank_sites(39.3603, -104.5969)
    distances = [r.distance_km for r in ranked]
    assert distances == sorted(distances)
    assert len(ranked) == len(load_sites())


def test_rank_sites_covers_flag() -> None:
    ranked = rank_sites(39.3603, -104.5969)
    # Nearest (KFTG, ~50 km) covers; farthest site does not.
    assert ranked[0].covers is True
    assert ranked[0].distance_km <= COVERAGE_RANGE_KM
    assert ranked[-1].covers is False


# --- table sanity ------------------------------------------------------------


def test_table_loaded_and_well_formed() -> None:
    sites = load_sites()
    assert len(sites) > 150  # full WSR-88D network is ~160
    icaos = [s.icao for s in sites]
    assert len(icaos) == len(set(icaos)), "ICAOs must be unique"
    for s in sites:
        assert len(s.icao) == 4 and s.icao.isupper()
        assert -90.0 <= s.lat <= 90.0
        assert -180.0 <= s.lon <= 180.0


def test_table_spot_check_known_coordinates() -> None:
    by_icao = {s.icao: s for s in load_sites()}
    kftg = by_icao["KFTG"]
    assert kftg.lat == pytest.approx(39.7866, abs=0.01)
    assert kftg.lon == pytest.approx(-104.5458, abs=0.01)
    ktlx = by_icao["KTLX"]
    assert ktlx.lat == pytest.approx(35.3334, abs=0.01)
    assert ktlx.lon == pytest.approx(-97.2778, abs=0.01)
