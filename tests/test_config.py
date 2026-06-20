"""Tests for config resolution and precedence (CLI > env > default)."""

from __future__ import annotations

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
