"""Tests for config resolution and precedence (CLI > env > default)."""

from __future__ import annotations

import json

import pytest

from backscatter.config import (
    DEFAULT_LAT,
    DEFAULT_LON,
    DEFAULT_POLL_INTERVAL_S,
    load_config,
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
    """Isolate every test from the ambient environment."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_default_resolves_elizabeth_to_kftg() -> None:
    config = load_config()
    assert (config.lat, config.lon) == (DEFAULT_LAT, DEFAULT_LON)
    assert config.site == "KFTG"
    assert config.poll_interval_s == DEFAULT_POLL_INTERVAL_S
    # Back-compat: the single form is one default location named "Home".
    assert len(config.locations) == 1
    home = config.locations[0]
    assert home.name == "Home" and home.is_default
    assert config.default_location is home


def test_poll_interval_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_POLL_INTERVAL", "30")
    assert load_config().poll_interval_s == 30.0


def test_explicit_site_env_overrides_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKSCATTER_SITE", "KTLX")
    config = load_config()
    # Override wins, but the location is untouched (still Elizabeth).
    assert config.site == "KTLX"
    assert (config.lat, config.lon) == (DEFAULT_LAT, DEFAULT_LON)


def test_cli_site_arg_wins_and_is_upcased() -> None:
    assert load_config(site="klot").site == "KLOT"


def test_cli_site_arg_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_SITE", "KTLX")
    assert load_config(site="klot").site == "KLOT"


def test_latlon_from_env_resolves_site(monkeypatch: pytest.MonkeyPatch) -> None:
    # Oklahoma City via env should resolve to KTLX.
    monkeypatch.setenv("BACKSCATTER_LAT", "35.4676")
    monkeypatch.setenv("BACKSCATTER_LON", "-97.5164")
    config = load_config()
    assert config.site == "KTLX"


def test_latlon_arg_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_LAT", "35.4676")
    monkeypatch.setenv("BACKSCATTER_LON", "-97.5164")
    # Seattle via args should win over OKC env → KATX.
    config = load_config(lat=47.6062, lon=-122.3321)
    assert config.site == "KATX"


# --- Slice 8: multiple locations ---------------------------------------------


def _set_locations(
    monkeypatch: pytest.MonkeyPatch, locs: list[dict[str, object]]
) -> None:
    monkeypatch.setenv("BACKSCATTER_LOCATIONS", json.dumps(locs))


def test_locations_json_resolves_each_site(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_locations(
        monkeypatch,
        [
            {"name": "Home", "lat": 39.3603, "lon": -104.5969, "default": True},
            {"name": "OKC", "lat": 35.4676, "lon": -97.5164},
        ],
    )
    config = load_config()
    by_name = {loc.name: loc for loc in config.locations}
    assert by_name["Home"].site == "KFTG" and by_name["Home"].is_default
    assert by_name["OKC"].site == "KTLX" and not by_name["OKC"].is_default
    # Home-facade properties delegate to the default location.
    assert config.site == "KFTG"


def test_override_pins_default_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_locations(
        monkeypatch,
        [
            {"name": "Home", "lat": 39.3603, "lon": -104.5969, "default": True},
            {"name": "OKC", "lat": 35.4676, "lon": -97.5164},
        ],
    )
    monkeypatch.setenv("BACKSCATTER_SITE", "KLOT")
    config = load_config()
    by_name = {loc.name: loc for loc in config.locations}
    assert by_name["Home"].site == "KLOT" and by_name["Home"].site_override
    # The override does NOT touch the other location.
    assert by_name["OKC"].site == "KTLX" and not by_name["OKC"].site_override


def test_location_by_name_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_locations(
        monkeypatch,
        [{"name": "Home", "lat": 39.36, "lon": -104.6, "default": True}],
    )
    config = load_config()
    assert config.location_by_name("home") is config.default_location
    assert config.location_by_name("nope") is None


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
def test_location_validation_errors(
    monkeypatch: pytest.MonkeyPatch, locs: list[dict[str, object]], match: str
) -> None:
    _set_locations(monkeypatch, locs)
    with pytest.raises(ValueError, match=match):
        load_config()


def test_bad_locations_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKSCATTER_LOCATIONS", "{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_config()
