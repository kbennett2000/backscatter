"""Tests for retention / pruning (ADR-0009).

Frames are seeded as **real** files on disk (raw volume + optional PNG/sidecar) plus
their index rows, so deletion, size accounting, and orphan checks are exercised end
to end. The clock is injected (``now=``) — no real time involved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backscatter.config import Config, SeedLocation
from backscatter.prune.prune import (
    PruneReason,
    human_bytes,
    run_prune,
    select_candidates,
)
from backscatter.store import db

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
_GIB = 1024**3


def _config(
    tmp_path: Path,
    *,
    age: float | None = None,
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


def _seed(
    config: Config,
    *,
    site: str = "KFTG",
    age_days: float,
    raw_bytes: bytes = b"x",
    png_bytes: bytes | None = None,
    downloaded: datetime | None = None,
) -> dict[str, Path]:
    """Index a volume + write its files; ``age_days`` back from _NOW is the scan time.

    Returns the on-disk paths so tests can assert presence/absence directly.
    """
    scan = _NOW - timedelta(days=age_days)
    name = f"{site}{scan:%Y%m%d_%H%M%S}_V06"
    raw = config.data_dir / site / f"{scan:%Y%m%d}" / name
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(raw_bytes)

    conn = _conn(config)
    db.record_volume(
        conn,
        site=site,
        scan_time=scan,
        s3_key=f"k/{name}",
        path=raw,
        size_bytes=len(raw_bytes),
        downloaded_at=downloaded or scan,
    )
    paths = {"raw": raw}
    if png_bytes is not None:
        rel = f"{site}/{name}.png"
        png = config.data_dir / "renders" / rel
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(png_bytes)
        sidecar = png.with_suffix(".json")
        sidecar.write_text("{}")
        db.record_render(
            conn,
            site=site,
            scan_time=scan,
            image_path=rel,
            elevation_deg=0.5,
            width=2,
            height=2,
            bounds=(-1.0, -1.0, 1.0, 1.0),
            rendered_at=scan,
        )
        paths["png"] = png
        paths["sidecar"] = sidecar
    conn.close()
    return paths


def _scan_times(config: Config) -> set[str]:
    conn = _conn(config)
    rows = conn.execute("SELECT scan_time FROM volumes").fetchall()
    conn.close()
    return {r["scan_time"] for r in rows}


# --- age limit ---------------------------------------------------------------


def test_age_prunes_only_old(tmp_path: Path) -> None:
    config = _config(tmp_path, age=30)
    old = _seed(config, age_days=40)
    new = _seed(config, age_days=1)

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert report.deleted == 1
    assert not old["raw"].exists()
    assert new["raw"].exists()
    assert _scan_times(config) == {(_NOW - timedelta(days=1)).isoformat()}
    assert report.by_reason == {"age": 1}


def test_age_uses_scan_time_not_download_time(tmp_path: Path) -> None:
    # Old scan, fetched just now (a future backfill shape): age is by scan_time, so
    # it is still eligible. ADR-0009 flags this for the later backfill slice.
    config = _config(tmp_path, age=30)
    old = _seed(config, age_days=40, downloaded=_NOW)

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert report.deleted == 1
    assert not old["raw"].exists()


def test_age_disabled_keeps_everything(tmp_path: Path) -> None:
    config = _config(tmp_path, age=None)  # no policy at all
    _seed(config, age_days=400)

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert report.deleted == 0
    assert len(_scan_times(config)) == 1


# --- size cap ----------------------------------------------------------------


def test_size_cap_deletes_oldest_first_until_under(tmp_path: Path) -> None:
    config = _config(tmp_path, max_bytes=250)
    # Three unrendered frames, 100 bytes raw each (exact accounting).
    f1 = _seed(config, age_days=3, raw_bytes=b"a" * 100)
    f2 = _seed(config, age_days=2, raw_bytes=b"b" * 100)
    f3 = _seed(config, age_days=1, raw_bytes=b"c" * 100)

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    # 300 > 250 → drop the single oldest (200 <= 250); stop. No more.
    assert report.deleted == 1
    assert report.bytes_reclaimed == 100
    assert not f1["raw"].exists()
    assert f2["raw"].exists() and f3["raw"].exists()
    assert report.by_reason == {"size": 1}


def test_size_accounting_includes_render_bytes(tmp_path: Path) -> None:
    # One frame whose raw is tiny but whose PNG pushes it over the cap — proves the
    # cap reflects real footprint (raw + png + sidecar), not just the raw column.
    config = _config(tmp_path, max_bytes=500)
    f = _seed(config, age_days=1, raw_bytes=b"x" * 10, png_bytes=b"p" * 1000)

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert report.deleted == 1
    assert report.bytes_reclaimed >= 1010  # raw + png + sidecar
    assert not f["raw"].exists() and not f["png"].exists()


def test_multi_radar_size_cap_is_global_oldest_first(tmp_path: Path) -> None:
    config = _config(tmp_path, max_bytes=1500)
    # Interleaved sites; deletion must follow global scan_time order, not site.
    d1 = _seed(config, site="KFTG", age_days=3, raw_bytes=b"a" * 1000)
    d2 = _seed(config, site="KTLX", age_days=2, raw_bytes=b"b" * 1000)
    d3 = _seed(config, site="KFTG", age_days=1, raw_bytes=b"c" * 1000)

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    # 3000 > 1500 → drop oldest two globally (KFTG d1, KTLX d2); keep newest (d3).
    assert report.deleted == 2
    assert not d1["raw"].exists() and not d2["raw"].exists()
    assert d3["raw"].exists()


# --- both active -------------------------------------------------------------


def test_both_active_size_prunes_a_young_frame(tmp_path: Path) -> None:
    # All frames within the age window, but the archive is over cap: the oldest
    # (still "young") is size-pruned — either condition prunes.
    config = _config(tmp_path, age=30, max_bytes=2500)
    young_oldest = _seed(config, age_days=10, raw_bytes=b"a" * 1000)
    _seed(config, age_days=5, raw_bytes=b"b" * 1000)
    _seed(config, age_days=1, raw_bytes=b"c" * 1000)

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert report.deleted == 1
    assert not young_oldest["raw"].exists()
    assert report.by_reason == {"size": 1}


def test_both_active_age_prunes_under_cap(tmp_path: Path) -> None:
    # Size cap effectively inactive (huge); age still prunes the old frame.
    config = _config(tmp_path, age=30, max_bytes=10 * _GIB)
    old = _seed(config, age_days=40)
    new = _seed(config, age_days=1)

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert report.deleted == 1
    assert not old["raw"].exists() and new["raw"].exists()
    assert report.by_reason == {"age": 1}


def test_reason_both_when_old_and_overflow(tmp_path: Path) -> None:
    config = _config(tmp_path, age=30, max_bytes=500)
    _seed(config, age_days=40, raw_bytes=b"x" * 1000)  # old AND over the cap

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert report.deleted == 1
    assert report.by_reason == {"both": 1}


# --- atomic delete / no orphans ----------------------------------------------


def test_delete_removes_raw_png_sidecar_and_row(tmp_path: Path) -> None:
    config = _config(tmp_path, age=30)
    f = _seed(config, age_days=40, png_bytes=b"png")

    conn = _conn(config)
    run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert not f["raw"].exists()
    assert not f["png"].exists()
    assert not f["sidecar"].exists()
    assert _scan_times(config) == set()  # no dangling row


# --- dry-run -----------------------------------------------------------------


def test_dry_run_deletes_nothing_but_reports(tmp_path: Path) -> None:
    config = _config(tmp_path, age=30)
    old = _seed(config, age_days=40, png_bytes=b"png")
    _seed(config, age_days=1, png_bytes=b"png")

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=True)
    conn.close()

    assert report.dry_run is True
    assert report.deleted == 1  # would delete
    assert report.bytes_reclaimed > 0
    assert report.oldest == (_NOW - timedelta(days=40)).isoformat()
    assert report.newest == report.oldest  # only one candidate
    # Nothing actually removed.
    assert old["raw"].exists() and old["png"].exists() and old["sidecar"].exists()
    assert len(_scan_times(config)) == 2


# --- resilience / self-heal --------------------------------------------------


def test_file_error_skips_frame_and_keeps_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, age=30)
    good = _seed(config, site="KFTG", age_days=40)
    poison = _seed(config, site="KTLX", age_days=41)

    real_unlink = Path.unlink

    def flaky_unlink(self: Path, *a: object, **k: object) -> None:
        if "KTLX" in str(self):
            raise PermissionError("locked")
        real_unlink(self)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert report.deleted == 1
    assert report.skipped == 1
    assert report.errors  # carries a sample message
    # Good frame gone; poisoned frame left fully intact (file + row) — no orphan.
    assert not good["raw"].exists()
    assert poison["raw"].exists()
    assert (_NOW - timedelta(days=41)).isoformat() in _scan_times(config)


def test_self_heals_already_missing_files(tmp_path: Path) -> None:
    config = _config(tmp_path, age=30)
    f = _seed(config, age_days=40, png_bytes=b"png")
    # Simulate a crash mid-prune: files already gone, row still present.
    f["raw"].unlink()
    f["png"].unlink()

    conn = _conn(config)
    report = run_prune(conn, config, now=_NOW, dry_run=False)
    conn.close()

    assert report.deleted == 1  # missing files treated as success → row removed
    assert _scan_times(config) == set()


# --- selection purity --------------------------------------------------------


def test_select_candidates_does_not_delete(tmp_path: Path) -> None:
    config = _config(tmp_path, age=30)
    old = _seed(config, age_days=40)

    conn = _conn(config)
    cands = select_candidates(conn, config, now=_NOW)
    conn.close()

    assert len(cands) == 1
    assert cands[0].reason is PruneReason.AGE
    assert old["raw"].exists()  # selection is pure — nothing deleted
    assert len(_scan_times(config)) == 1


def test_human_bytes() -> None:
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(2 * 1024**3) == "2.0 GB"
