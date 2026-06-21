"""Tests for the mutable location store (ADR-0008)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backscatter.config import Config, SeedLocation
from backscatter.store import locations as loc


def _config(tmp_path: Path, *seed: SeedLocation) -> Config:
    rows = seed or (SeedLocation("Home", 39.3603, -104.5969, True),)
    return Config(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=60.0,
        site_override=None,
        seed_locations=rows,
    )


def _conn(tmp_path: Path, *seed: SeedLocation) -> sqlite3.Connection:
    return loc.connect_bootstrapped(_config(tmp_path, *seed))


# --- seeding -----------------------------------------------------------------


def test_seed_when_empty_then_resolves(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    locs = loc.current_locations(conn, None)
    assert [(x.name, x.site, x.is_default) for x in locs] == [("Home", "KFTG", True)]
    assert loc.default_location(conn, None).site == "KFTG"


def test_store_wins_when_populated(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    loc.create(conn, None, name="OKC", lat=35.4676, lon=-97.5164, make_default=False)
    # A re-bootstrap with a different seed must not change the populated store.
    loc.ensure_seeded(
        conn, _config(tmp_path, SeedLocation("Other", 47.6, -122.3, True))
    )
    assert [x.name for x in loc.current_locations(conn, None)] == ["Home", "OKC"]


def test_override_applies_to_default_only(tmp_path: Path) -> None:
    conn = _conn(
        tmp_path,
        SeedLocation("Home", 39.3603, -104.5969, True),
        SeedLocation("OKC", 35.4676, -97.5164, False),
    )
    by_name = {x.name: x for x in loc.current_locations(conn, "KLOT")}
    assert by_name["Home"].site == "KLOT" and by_name["Home"].site_override
    assert by_name["OKC"].site == "KTLX" and not by_name["OKC"].site_override


# --- create / update / delete ------------------------------------------------


def test_create_and_unique_name(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    created = loc.create(conn, None, name="OKC", lat=35.4676, lon=-97.5164,
                         make_default=False)
    assert created.id == 2 and created.site == "KTLX"
    with pytest.raises(ValueError, match="already exists"):
        loc.create(conn, None, name="okc", lat=1, lon=2, make_default=False)


def test_create_default_is_sole_default(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    loc.create(conn, None, name="OKC", lat=35.4676, lon=-97.5164, make_default=True)
    defaults = [x.name for x in loc.current_locations(conn, None) if x.is_default]
    assert defaults == ["OKC"]


def test_update_rename_collision(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    loc.create(conn, None, name="OKC", lat=35.4676, lon=-97.5164, make_default=False)
    with pytest.raises(ValueError, match="already exists"):
        loc.update(conn, None, 2, name="Home")  # collides with the default


def test_update_set_default_demotes(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    okc = loc.create(conn, None, name="OKC", lat=35.4676, lon=-97.5164,
                     make_default=False)
    assert okc.id is not None
    loc.update(conn, None, okc.id, make_default=True)
    by_name = {x.name: x.is_default for x in loc.current_locations(conn, None)}
    assert by_name == {"OKC": True, "Home": False}


def test_update_missing_raises_keyerror(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    with pytest.raises(KeyError):
        loc.update(conn, None, 999, lat=1.0)


def test_delete_last_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    with pytest.raises(ValueError, match="only location"):
        loc.delete(conn, 1)


def test_delete_default_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    loc.create(conn, None, name="OKC", lat=35.4676, lon=-97.5164, make_default=False)
    with pytest.raises(ValueError, match="default"):
        loc.delete(conn, 1)  # Home is the default


def test_delete_non_default(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    loc.create(conn, None, name="OKC", lat=35.4676, lon=-97.5164, make_default=False)
    loc.delete(conn, 2)
    assert [x.name for x in loc.current_locations(conn, None)] == ["Home"]
