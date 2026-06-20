"""Behavioral tests for the collection loop (moto S3, stubbed render)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from backscatter.collect import collect as collect_mod
from backscatter.collect.collect import (
    CycleResult,
    CycleStatus,
    collect_cycle,
    run_collect,
)
from backscatter.config import Config
from backscatter.ingest import naming, s3
from backscatter.render.render import RenderResult
from backscatter.store import db


@pytest.fixture
def s3_client() -> Iterator[object]:
    with mock_aws():
        client = boto3.client("s3", region_name=s3.REGION)
        client.create_bucket(Bucket=s3.BUCKET)
        yield client


def _config(tmp_path: Path) -> Config:
    # Elizabeth, CO -> nearest KFTG, then KPUX (failover candidate).
    return Config(
        lat=39.3603,
        lon=-104.5969,
        site="KFTG",
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=0.0,
    )


def _put_volume(client: object, scan: datetime, *, site: str = "KFTG") -> None:
    key = f"{scan:%Y/%m/%d}/{site}/{site}{scan:%Y%m%d_%H%M%S}_V06"
    client.put_object(Bucket=s3.BUCKET, Key=key, Body=b"bytes")  # type: ignore[attr-defined]


def _stub_render(calls: list[Path] | None = None) -> Callable[..., RenderResult]:
    """A render_fn that fabricates a RenderResult without invoking Py-ART."""

    def render(volume_path: Path, config: Config) -> RenderResult:
        if calls is not None:
            calls.append(Path(volume_path))
        name = Path(volume_path).name
        site = naming.parse_site(name)
        scan = naming.parse_scan_time(name)
        png = config.data_dir / "renders" / site / f"{name}.png"
        return RenderResult(
            png_path=png,
            sidecar_path=png.with_suffix(".json"),
            site=site,
            scan_time=scan,
            elevation_deg=0.5,
            width=10,
            height=20,
            bounds_wgs84=(-107.0, 37.0, -101.0, 42.0),
            bounds_3857=(0.0, 0.0, 1.0, 1.0),
        )

    return render


def _open_db(config: Config):
    conn = db.connect(config.db_path)
    db.init_db(conn)
    return conn


def test_cycle_stores_renders_and_indexes(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    _put_volume(s3_client, datetime(2026, 6, 20, 11, 50, tzinfo=UTC))
    conn = _open_db(config)

    res = collect_cycle(
        config, conn, now=now, client=s3_client, render_fn=_stub_render()
    )
    assert res == CycleResult(
        CycleStatus.RENDERED, "KFTG", datetime(2026, 6, 20, 11, 50, tzinfo=UTC)
    )
    row = db.latest_rendered_frame(conn)
    assert row is not None
    assert row["render_status"] == "rendered"
    assert row["image_path"] == "KFTG/KFTG20260620_115000_V06.png"
    assert row["width"] == 10 and row["height"] == 20
    assert row["elevation_deg"] == 0.5
    # Raw volume landed on disk.
    assert (config.data_dir / "KFTG" / "20260620").is_dir()


def test_dedupe_across_cycles(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    _put_volume(s3_client, datetime(2026, 6, 20, 11, 50, tzinfo=UTC))
    calls: list[Path] = []

    run_collect(
        config,
        now_fn=lambda: now,
        client=s3_client,
        render_fn=_stub_render(calls),
        max_cycles=2,
    )

    conn = _open_db(config)
    count = conn.execute("SELECT COUNT(*) AS n FROM volumes").fetchone()["n"]
    assert count == 1  # second cycle deduped
    assert len(calls) == 1  # render happened exactly once
    stored = list((config.data_dir / "KFTG" / "20260620").iterdir())
    assert len(stored) == 1


def test_failover_to_next_site(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    # Nearest (KFTG) has nothing; the next ranked covering site (KPUX) does.
    _put_volume(s3_client, datetime(2026, 6, 20, 11, 50, tzinfo=UTC), site="KPUX")
    conn = _open_db(config)

    res = collect_cycle(
        config, conn, now=now, client=s3_client, render_fn=_stub_render()
    )
    assert res.status is CycleStatus.RENDERED
    assert res.site == "KPUX"
    assert (config.data_dir / "KPUX" / "20260620").is_dir()


def test_render_failure_recorded_and_loop_continues(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path)
    # Two different days so each cycle finds a distinct newest volume.
    _put_volume(s3_client, datetime(2026, 6, 19, 11, 50, tzinfo=UTC))
    _put_volume(s3_client, datetime(2026, 6, 20, 11, 50, tzinfo=UTC))
    nows = iter(
        [
            datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
            datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        ]
    )

    good = _stub_render()
    calls = {"n": 0}

    def flaky_render(volume_path: Path, config: Config) -> RenderResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("decode blew up")
        return good(volume_path, config)

    run_collect(
        config,
        now_fn=lambda: next(nows),
        client=s3_client,
        render_fn=flaky_render,
        max_cycles=2,
    )

    conn = _open_db(config)
    rows = {
        r["scan_time"]: r["render_status"]
        for r in conn.execute("SELECT scan_time, render_status FROM volumes")
    }
    assert rows["2026-06-19T11:50:00+00:00"] == "failed"  # first cycle render raised
    assert rows["2026-06-20T11:50:00+00:00"] == "rendered"  # loop recovered
    assert calls["n"] == 2


def test_unexpected_cycle_error_does_not_kill_loop(
    tmp_path: Path, s3_client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    seen = {"n": 0}

    def flaky_cycle(*args: object, **kwargs: object) -> CycleResult:
        seen["n"] += 1
        if seen["n"] == 1:
            raise RuntimeError("transient S3 blip")
        return CycleResult(CycleStatus.NOTHING)

    monkeypatch.setattr(collect_mod, "collect_cycle", flaky_cycle)
    # Should not raise despite the first cycle blowing up.
    run_collect(config, client=s3_client, render_fn=_stub_render(), max_cycles=2)
    assert seen["n"] == 2  # loop continued past the error
