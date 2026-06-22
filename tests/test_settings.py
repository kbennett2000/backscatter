"""Tests for runtime retention settings (Slice 29 / ADR-0013).

Retention is DB-backed state: env seeds an empty store, then the DB wins and the policy
is read live. These exercise seeding, the GB↔bytes boundary, validation, and the
singleton invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backscatter.config import Config, SeedLocation
from backscatter.store import db, settings

_GIB = 1024**3


def _config(
    tmp_path: Path,
    *,
    age: float | None = 30.0,
    max_bytes: int | None = None,
) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=0.0,
        site_override=None,
        seed_locations=(SeedLocation("Home", 39.3603, -104.5969, True),),
        retention_max_age_days=age,
        retention_max_size_bytes=max_bytes,
    )


def _conn(config: Config) -> db.sqlite3.Connection:
    conn = db.connect(config.db_path)
    db.init_db(conn)
    return conn


def test_unseeded_store_reads_as_no_limits(tmp_path: Path) -> None:
    conn = _conn(_config(tmp_path))
    policy = settings.get_retention(conn)
    assert policy.max_age_days is None
    assert policy.max_size_bytes is None
    assert policy.active is False


def test_seed_from_config_then_db_wins(tmp_path: Path) -> None:
    config = _config(tmp_path, age=30.0, max_bytes=50 * _GIB)
    conn = _conn(config)
    settings.ensure_retention_seeded(conn, config)
    policy = settings.get_retention(conn)
    assert policy.max_age_days == 30.0
    assert policy.max_size_bytes == 50 * _GIB
    assert policy.active is True

    # Re-seeding with a different config is a no-op once a row exists (DB wins).
    other = _config(tmp_path, age=7.0, max_bytes=None)
    settings.ensure_retention_seeded(conn, other)
    assert settings.get_retention(conn).max_age_days == 30.0


def test_update_round_trips_and_stays_singleton(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conn = _conn(config)
    settings.ensure_retention_seeded(conn, config)

    settings.update_retention(conn, max_age_days=7.0, max_size_bytes=10 * _GIB)
    policy = settings.get_retention(conn)
    assert policy.max_age_days == 7.0
    assert policy.max_size_bytes == 10 * _GIB

    settings.update_retention(conn, max_age_days=14.0, max_size_bytes=None)
    assert settings.get_retention(conn).max_age_days == 14.0
    assert settings.get_retention(conn).max_size_bytes is None

    rows = conn.execute("SELECT COUNT(*) FROM retention_settings").fetchone()[0]
    assert rows == 1  # always exactly the id=1 singleton


def test_both_off_is_allowed_unlimited(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conn = _conn(config)
    policy = settings.update_retention(
        conn, max_age_days=None, max_size_bytes=None
    )
    assert policy.active is False  # unlimited archive — valid


def test_zero_age_disables_the_limit(tmp_path: Path) -> None:
    assert settings.validate_age_days(0) is None
    assert settings.validate_age_days(None) is None
    assert settings.validate_age_days(30) == 30.0


def test_negative_age_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_age_days"):
        settings.validate_age_days(-1)


def test_gb_to_bytes_conversion_and_validation(tmp_path: Path) -> None:
    assert settings.gb_to_bytes(None) is None
    assert settings.gb_to_bytes(2) == 2 * _GIB
    with pytest.raises(ValueError, match="max_size_gb"):
        settings.gb_to_bytes(0)
    with pytest.raises(ValueError, match="max_size_gb"):
        settings.gb_to_bytes(-5)


def test_bytes_to_gb_round_trip(tmp_path: Path) -> None:
    assert settings.bytes_to_gb(None) is None
    assert settings.bytes_to_gb(2 * _GIB) == 2.0


def test_update_rejects_nonpositive_bytes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conn = _conn(config)
    with pytest.raises(ValueError, match="max_size_bytes"):
        settings.update_retention(conn, max_age_days=None, max_size_bytes=0)
