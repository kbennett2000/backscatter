"""Tests for config: infra resolution + the location seed + resolution helper."""

from __future__ import annotations

import json

import pytest

from backscatter.config import (
    DEFAULT_LAT,
    DEFAULT_LON,
    DEFAULT_POLL_INTERVAL_S,
    load_config,
    resolve_location,
)

_ENV_VARS = (
    "BACKSCATTER_SITE",
    "BACKSCATTER_LAT",
    "BACKSCATTER_LON",
    "BACKSCATTER_LOCATIONS",
    "BACKSCATTER_DATA_DIR",
    "BACKSCATTER_DB_PATH",
    "BACKSCATTER_POLL_INTERVAL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _set_locations(
    monkeypatch: pytest.MonkeyPatch, locs: list[dict[str, object]]
) -> None:
    monkeypatch.setenv("BACKSCATTER_LOCATIONS", json.dumps(locs))


# --- infra + seed defaults ---------------------------------------------------


def test_default_seed_is_home_elizabeth() -> None:
    config = load_config()
    assert config.poll_interval_s == DEFAULT_POLL_INTERVAL_S
    assert config.site_override is None
    assert len(config.seed_locations) == 1
    home = config.seed_locations[0]
    assert (home.name, home.lat, home.lon, home.is_default) == (
        "Home", DEFAULT_LAT, DEFAULT_LON, True
    )


def test_poll_interval_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_POLL_INTERVAL", "30")
    assert load_config().poll_interval_s == 30.0


def test_single_form_latlon_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_LAT", "35.4676")
    monkeypatch.setenv("BACKSCATTER_LON", "-97.5164")
    home = load_config().seed_locations[0]
    assert (home.lat, home.lon) == (35.4676, -97.5164)


def test_latlon_arg_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_LAT", "35.4676")
    config = load_config(lat=47.6062, lon=-122.3321)
    assert (config.seed_locations[0].lat, config.seed_locations[0].lon) == (
        47.6062, -122.3321
    )


# --- site override capture ---------------------------------------------------


def test_site_override_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_SITE", "KTLX")
    assert load_config().site_override == "KTLX"


def test_site_override_arg_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_SITE", "KTLX")
    assert load_config(site="KLOT").site_override == "KLOT"


# --- multi-location seed -----------------------------------------------------


def test_locations_json_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_locations(
        monkeypatch,
        [
            {"name": "Home", "lat": 39.3603, "lon": -104.5969, "default": True},
            {"name": "OKC", "lat": 35.4676, "lon": -97.5164},
        ],
    )
    seed = load_config().seed_locations
    assert [s.name for s in seed] == ["Home", "OKC"]
    assert [s.is_default for s in seed] == [True, False]


@pytest.mark.parametrize(
    ("locs", "match"),
    [
        ([], "at least one location"),
        (
            [{"name": "A", "lat": 1, "lon": 2}, {"name": "B", "lat": 3, "lon": 4}],
            "exactly one location must be the default",
        ),
        (
            [
                {"name": "A", "lat": 1, "lon": 2, "default": True},
                {"name": "B", "lat": 3, "lon": 4, "default": True},
            ],
            "exactly one location must be the default",
        ),
        (
            [
                {"name": "Home", "lat": 1, "lon": 2, "default": True},
                {"name": "home", "lat": 3, "lon": 4},
            ],
            "unique",
        ),
    ],
)
def test_seed_validation_errors(
    monkeypatch: pytest.MonkeyPatch, locs: list[dict[str, object]], match: str
) -> None:
    _set_locations(monkeypatch, locs)
    with pytest.raises(ValueError, match=match):
        load_config()


def test_bad_locations_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_LOCATIONS", "{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_config()


# --- resolve_location --------------------------------------------------------


def test_resolve_location_nearest() -> None:
    loc = resolve_location("Home", 39.3603, -104.5969, is_default=True, override=None)
    assert loc.site == "KFTG" and not loc.site_override


def test_resolve_location_override_pins_default_only() -> None:
    pinned = resolve_location(
        "Home", 39.3603, -104.5969, is_default=True, override="klot"
    )
    assert pinned.site == "KLOT" and pinned.site_override
    # Override is ignored for a non-default location.
    other = resolve_location(
        "OKC", 35.4676, -97.5164, is_default=False, override="klot"
    )
    assert other.site == "KTLX" and not other.site_override
