"""Tests for historical backfill (Slice 12) — moto S3, stubbed render."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from backscatter.backfill.backfill import list_range, plan_backfill, run_backfill
from backscatter.config import Config, SeedLocation
from backscatter.ingest import naming, s3
from backscatter.render.render import RenderResult
from backscatter.store import db

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
_BODY = b"vol-bytes"  # 9 bytes — exact for byte-estimate assertions


@pytest.fixture
def s3_client() -> Iterator[object]:
    with mock_aws():
        client = boto3.client("s3", region_name=s3.REGION)
        client.create_bucket(Bucket=s3.BUCKET)
        yield client


def _config(tmp_path: Path, *, age: float | None = None) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=0.0,
        site_override=None,
        seed_locations=(SeedLocation("Home", 39.3603, -104.5969, True),),
        retention_max_age_days=age,
    )


def _conn(config: Config) -> db.sqlite3.Connection:
    conn = db.connect(config.db_path)
    db.init_db(conn)
    return conn


def _put(
    client: object, scan: datetime, *, site: str = "KFTG", body: bytes = _BODY
) -> str:
    key = f"{scan:%Y/%m/%d}/{site}/{site}{scan:%Y%m%d_%H%M%S}_V06"
    client.put_object(Bucket=s3.BUCKET, Key=key, Body=body)  # type: ignore[attr-defined]
    return key


def _stub_render(calls: list[Path] | None = None) -> Callable[..., RenderResult]:
    def render(volume_path: Path, config: Config) -> RenderResult:
        if calls is not None:
            calls.append(Path(volume_path))
        name = Path(volume_path).name
        site = naming.parse_site(name)
        scan = naming.parse_scan_time(name)
        png = config.data_dir / "renders" / site / f"{name}.png"
        return RenderResult(
            png_path=png, sidecar_path=png.with_suffix(".json"),
            site=site, scan_time=scan, elevation_deg=0.5, width=10, height=20,
            bounds_wgs84=(-107.0, 37.0, -101.0, 42.0), bounds_3857=(0.0, 0.0, 1.0, 1.0),
        )

    return render


def _scan_times(config: Config) -> set[str]:
    conn = _conn(config)
    rows = conn.execute("SELECT scan_time FROM volumes").fetchall()
    conn.close()
    return {r["scan_time"] for r in rows}


# --- range selection ---------------------------------------------------------


def test_list_range_selects_only_in_range_sorted(s3_client: object) -> None:
    # Volumes spanning four UTC days; only days 18–19 are in [start, end].
    _put(s3_client, datetime(2026, 6, 17, 10, 0, tzinfo=UTC))  # before
    a = _put(s3_client, datetime(2026, 6, 18, 12, 5, tzinfo=UTC))
    b = _put(s3_client, datetime(2026, 6, 18, 12, 0, tzinfo=UTC))
    c = _put(s3_client, datetime(2026, 6, 19, 23, 59, 0, tzinfo=UTC))  # == end
    _put(s3_client, datetime(2026, 6, 20, 6, 0, tzinfo=UTC))  # after

    start = datetime(2026, 6, 18, 0, 0, tzinfo=UTC)
    end = datetime(2026, 6, 19, 23, 59, 0, tzinfo=UTC)
    objs = list_range(s3_client, "KFTG", start, end)

    # Boundary-inclusive, oldest-first across days.
    assert [k for k, _ in objs] == [b, a, c]


def test_list_range_far_past_historical(s3_client: object) -> None:
    # A range entirely in the past (nothing "latest") still resolves.
    k = _put(s3_client, datetime(2025, 1, 2, 3, 4, tzinfo=UTC))
    objs = list_range(
        s3_client,
        "KFTG",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 3, tzinfo=UTC),
    )
    assert [key for key, _ in objs] == [k]


# --- full run ----------------------------------------------------------------


def _seed_three(client: object) -> list[datetime]:
    scans = [
        datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        datetime(2026, 6, 18, 13, 0, tzinfo=UTC),
    ]
    for scan in scans:
        _put(client, scan)
    return scans


def test_full_run_indexes_and_renders_each(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    scans = _seed_three(s3_client)
    conn = _conn(config)

    report = run_backfill(
        config, conn, "KFTG",
        datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 19, tzinfo=UTC),
        now=_NOW, client=s3_client, render_fn=_stub_render(),
    )
    conn.close()

    assert (report.fetched, report.rendered, report.render_failed) == (3, 3, 0)
    assert report.already_have == 0
    assert report.oldest == scans[0] and report.newest == scans[-1]
    assert _scan_times(config) == {s.isoformat() for s in scans}
    conn = _conn(config)
    frames = db.rendered_frames(conn, site="KFTG", start=None, end=None, limit=99)
    conn.close()
    assert len(frames) == 3


def test_progress_cb_invoked_with_live_counts(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path)
    _seed_three(s3_client)
    conn = _conn(config)
    calls: list[tuple[int, int, int]] = []

    def progress(processed: int, total: int, report: object) -> None:
        calls.append((processed, total, report.fetched))  # type: ignore[attr-defined]

    report = run_backfill(
        config, conn, "KFTG",
        datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 19, tzinfo=UTC),
        now=_NOW, client=s3_client, render_fn=_stub_render(), progress_cb=progress,
    )
    conn.close()

    # Fires once per volume; the last tick reports the full count.
    assert calls == [(1, 3, 1), (2, 3, 2), (3, 3, 3)]
    assert report.fetched == 3


# --- dedupe / idempotent -----------------------------------------------------


def test_rerun_is_idempotent(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _seed_three(s3_client)
    conn = _conn(config)
    calls: list[Path] = []
    rng = (datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 19, tzinfo=UTC))

    first = run_backfill(
        config, conn, "KFTG", *rng, now=_NOW, client=s3_client,
        render_fn=_stub_render(calls),
    )
    second = run_backfill(
        config, conn, "KFTG", *rng, now=_NOW, client=s3_client,
        render_fn=_stub_render(calls),
    )
    conn.close()

    assert first.fetched == 3 and second.fetched == 0
    assert second.already_have == 3
    assert len(calls) == 3  # rendered exactly once each, never re-rendered
    assert len(_scan_times(config)) == 3  # no duplicate rows


def test_backfill_skips_preexisting_frame(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    scans = _seed_three(s3_client)
    # Pre-index the middle scan as if collect already had it.
    conn = _conn(config)
    db.record_volume(
        conn, site="KFTG", scan_time=scans[1], s3_key="k", path=Path("x"),
        size_bytes=1, downloaded_at=scans[1],
    )

    plan = plan_backfill(
        config, conn, "KFTG",
        datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 19, tzinfo=UTC),
        now=_NOW, client=s3_client,
    )
    conn.close()

    assert plan.total == 3
    assert plan.already_have == 1
    assert plan.to_fetch == 2
    assert plan.bytes_estimate == 2 * len(_BODY)


# --- resilience --------------------------------------------------------------


def test_bad_render_skipped_volume_kept(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _put(s3_client, datetime(2026, 6, 18, 12, 0, tzinfo=UTC))
    _put(s3_client, datetime(2026, 6, 18, 12, 30, tzinfo=UTC))
    conn = _conn(config)
    good = _stub_render()

    def render(volume_path: Path, config: Config) -> RenderResult:
        if "120000" in Path(volume_path).name:
            raise RuntimeError("decode blew up")
        return good(volume_path, config)

    report = run_backfill(
        config, conn, "KFTG",
        datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 19, tzinfo=UTC),
        now=_NOW, client=s3_client, render_fn=render,
    )
    rows = {
        r["scan_time"]: r["render_status"]
        for r in conn.execute("SELECT scan_time, render_status FROM volumes")
    }
    conn.close()

    assert (report.fetched, report.rendered, report.render_failed) == (2, 1, 1)
    assert rows["2026-06-18T12:00:00+00:00"] == "failed"  # volume kept, marked failed
    assert rows["2026-06-18T12:30:00+00:00"] == "rendered"


class _FlakyGet:
    """Wrap a moto client; raise on get_object for keys containing ``bad``."""

    def __init__(self, inner: object, bad: str) -> None:
        self._inner = inner
        self._bad = bad

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def get_object(self, **kwargs: object) -> object:
        if self._bad in str(kwargs.get("Key", "")):
            raise RuntimeError("network blip mid-download")
        return self._inner.get_object(**kwargs)  # type: ignore[attr-defined]


def test_fetch_error_skipped_and_run_continues(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path)
    _put(s3_client, datetime(2026, 6, 18, 12, 0, tzinfo=UTC))
    _put(s3_client, datetime(2026, 6, 18, 12, 30, tzinfo=UTC))
    conn = _conn(config)

    report = run_backfill(
        config, conn, "KFTG",
        datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 19, tzinfo=UTC),
        now=_NOW, client=_FlakyGet(s3_client, "120000"), render_fn=_stub_render(),
    )
    conn.close()

    assert report.skipped == 1
    assert report.fetched == 1
    # The errored volume was never stored; the other one was.
    assert _scan_times(config) == {"2026-06-18T12:30:00+00:00"}


# --- dry-run (plan only) -----------------------------------------------------


def test_plan_writes_nothing(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _seed_three(s3_client)
    conn = _conn(config)

    plan = plan_backfill(
        config, conn, "KFTG",
        datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 19, tzinfo=UTC),
        now=_NOW, client=s3_client,
    )
    conn.close()

    assert plan.to_fetch == 3
    assert plan.bytes_estimate == 3 * len(_BODY)
    assert _scan_times(config) == set()  # nothing indexed
    assert not (config.data_dir / "KFTG").exists()  # nothing downloaded


# --- retention warning -------------------------------------------------------


def test_retention_warning_counts_old_volumes(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path, age=30)  # cutoff = _NOW - 30d = 2026-05-21
    _put(s3_client, datetime(2026, 5, 1, 12, 0, tzinfo=UTC))  # 50d → older
    _put(s3_client, datetime(2026, 6, 19, 12, 0, tzinfo=UTC))  # within window
    conn = _conn(config)

    plan = plan_backfill(
        config, conn, "KFTG",
        datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 20, tzinfo=UTC),
        now=_NOW, client=s3_client,
    )
    conn.close()

    assert plan.older_than_retention == 1
    assert plan.retention_cutoff == _NOW - timedelta(days=30)


def test_no_retention_warning_within_window(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path, age=30)
    _put(s3_client, datetime(2026, 6, 19, 12, 0, tzinfo=UTC))
    conn = _conn(config)
    plan = plan_backfill(
        config, conn, "KFTG",
        datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 20, tzinfo=UTC),
        now=_NOW, client=s3_client,
    )
    conn.close()
    assert plan.older_than_retention == 0


def test_retention_off_no_cutoff(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path, age=None)  # retention disabled
    _put(s3_client, datetime(2025, 1, 1, 12, 0, tzinfo=UTC))  # ancient
    conn = _conn(config)
    plan = plan_backfill(
        config, conn, "KFTG",
        datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC),
        now=_NOW, client=s3_client,
    )
    conn.close()
    assert plan.older_than_retention == 0
    assert plan.retention_cutoff is None
