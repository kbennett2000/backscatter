"""Behavioral tests for the collection loop (moto S3, stubbed render)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from backscatter.collect.collect import (
    CycleResult,
    CycleStatus,
    collect_cycle,
    run_collect,
)
from backscatter.config import Config, Location, SeedLocation, resolve_location
from backscatter.ingest import naming, s3
from backscatter.render.render import RenderResult
from backscatter.sites.select import rank_sites
from backscatter.sites.table import site_by_icao
from backscatter.store import db
from backscatter.store import locations as locations_store

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
_SCAN = datetime(2026, 6, 20, 11, 50, tzinfo=UTC)


@pytest.fixture
def s3_client() -> Iterator[object]:
    with mock_aws():
        client = boto3.client("s3", region_name=s3.REGION)
        client.create_bucket(Bucket=s3.BUCKET)
        yield client


def _config(
    tmp_path: Path,
    *,
    override: str | None = None,
    seed: tuple[SeedLocation, ...] = (SeedLocation("Home", 39.3603, -104.5969, True),),
) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=0.0,
        site_override=override,
        seed_locations=seed,
    )


def _home(override: str | None = None) -> Location:
    return resolve_location(
        "Home", 39.3603, -104.5969, is_default=True, override=override
    )


def _loc(name: str, lat: float, lon: float) -> Location:
    return resolve_location(name, lat, lon, is_default=False, override=None)


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


def _open_db(config: Config):  # noqa: ANN201 - sqlite3.Connection
    conn = db.connect(config.db_path)
    db.init_db(conn)
    return conn


def test_cycle_stores_renders_and_indexes(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _put_volume(s3_client, _SCAN)
    conn = _open_db(config)

    results = collect_cycle(
        [_home()], config, conn, now=_NOW, client=s3_client, render_fn=_stub_render()
    )
    assert results == [
        CycleResult(CycleStatus.RENDERED, "KFTG", _SCAN, location="Home")
    ]
    row = db.latest_rendered_frame(conn)
    assert row is not None
    assert row["render_status"] == "rendered"
    assert row["image_path"] == "KFTG/KFTG20260620_115000_V06.png"
    assert row["width"] == 10 and row["height"] == 20
    assert (config.data_dir / "KFTG" / "20260620").is_dir()


def test_dedupe_across_cycles(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _put_volume(s3_client, _SCAN)
    calls: list[Path] = []

    run_collect(
        config, now_fn=lambda: _NOW, client=s3_client,
        render_fn=_stub_render(calls), max_cycles=2,
    )

    conn = _open_db(config)
    count = conn.execute("SELECT COUNT(*) AS n FROM volumes").fetchone()["n"]
    assert count == 1  # second cycle deduped
    assert len(calls) == 1  # render happened exactly once
    assert len(list((config.data_dir / "KFTG" / "20260620").iterdir())) == 1


def test_failover_to_next_site(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _put_volume(s3_client, _SCAN, site="KPUX")  # KFTG empty; KPUX (next) has data
    conn = _open_db(config)

    (res,) = collect_cycle(
        [_home()], config, conn, now=_NOW, client=s3_client, render_fn=_stub_render()
    )
    assert res.status is CycleStatus.RENDERED
    assert res.site == "KPUX"


def test_render_failure_recorded_and_loop_continues(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path)
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
        config, now_fn=lambda: next(nows), client=s3_client,
        render_fn=flaky_render, max_cycles=2,
    )

    conn = _open_db(config)
    rows = {
        r["scan_time"]: r["render_status"]
        for r in conn.execute("SELECT scan_time, render_status FROM volumes")
    }
    assert rows["2026-06-19T11:50:00+00:00"] == "failed"
    assert rows["2026-06-20T11:50:00+00:00"] == "rendered"
    assert calls["n"] == 2


class _FlakyDownloadClient:
    """Wraps a moto S3 client and raises mid-download on the first get_object."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.get_calls = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def get_object(self, **kwargs: object) -> object:
        self.get_calls += 1
        if self.get_calls == 1:
            raise RuntimeError("network blip mid-download")
        return self._inner.get_object(**kwargs)  # type: ignore[attr-defined]


def test_pull_error_survives_and_next_cycle_runs(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path)
    _put_volume(s3_client, datetime(2026, 6, 19, 11, 50, tzinfo=UTC))
    _put_volume(s3_client, datetime(2026, 6, 20, 11, 50, tzinfo=UTC))
    nows = iter(
        [
            datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
            datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        ]
    )
    flaky = _FlakyDownloadClient(s3_client)

    run_collect(
        config, now_fn=lambda: next(nows), client=flaky,
        render_fn=_stub_render(), max_cycles=2,
    )

    assert flaky.get_calls == 2
    conn = _open_db(config)
    rows = {
        r["scan_time"]: r["render_status"]
        for r in conn.execute("SELECT scan_time, render_status FROM volumes")
    }
    assert "2026-06-19T11:50:00+00:00" not in rows
    assert rows["2026-06-20T11:50:00+00:00"] == "rendered"


def test_no_candidate_logs_warning(
    tmp_path: Path, s3_client: object, caplog: pytest.LogCaptureFixture
) -> None:
    config = _config(tmp_path)
    conn = _open_db(config)
    with caplog.at_level("WARNING", logger="backscatter.collect"):
        (res,) = collect_cycle(
            [_home()], config, conn, now=_NOW, client=s3_client,
            render_fn=_stub_render(),
        )
    assert res.status is CycleStatus.NOTHING
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_override_pins_primary_site(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path, override="KTLX")
    _put_volume(s3_client, _SCAN, site="KTLX")
    conn = _open_db(config)

    (res,) = collect_cycle(
        [_home(override="KTLX")], config, conn, now=_NOW, client=s3_client,
        render_fn=_stub_render(),
    )
    # KTLX isn't Elizabeth's nearest — collecting it proves we ranked from the pin.
    assert res.status is CycleStatus.RENDERED
    assert res.site == "KTLX"


def test_override_failover_walks_pinned_neighbors(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path, override="KTLX")
    ktlx = site_by_icao("KTLX")
    assert ktlx is not None
    neighbor = rank_sites(ktlx.lat, ktlx.lon)[1].site.icao
    _put_volume(s3_client, _SCAN, site=neighbor)
    conn = _open_db(config)

    (res,) = collect_cycle(
        [_home(override="KTLX")], config, conn, now=_NOW, client=s3_client,
        render_fn=_stub_render(),
    )
    assert res.status is CycleStatus.RENDERED
    assert res.site == neighbor


# --- multiple locations ------------------------------------------------------


def test_collect_iterates_all_locations(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _put_volume(s3_client, _SCAN, site="KFTG")
    _put_volume(s3_client, _SCAN, site="KTLX")
    conn = _open_db(config)

    results = collect_cycle(
        [_home(), _loc("OKC", 35.4676, -97.5164)], config, conn,
        now=_NOW, client=s3_client, render_fn=_stub_render(),
    )
    assert {r.location for r in results} == {"Home", "OKC"}
    assert all(r.status is CycleStatus.RENDERED for r in results)
    assert {r["site"] for r in conn.execute("SELECT site FROM volumes")} == {
        "KFTG", "KTLX"
    }


def test_co_located_locations_dedupe_to_one_frame(
    tmp_path: Path, s3_client: object
) -> None:
    # Home (Elizabeth) and Parker both have KFTG as their nearest radar.
    config = _config(tmp_path)
    _put_volume(s3_client, _SCAN)  # a single KFTG volume
    conn = _open_db(config)
    calls: list[Path] = []

    results = collect_cycle(
        [_home(), _loc("Parker", 39.5186, -104.7614)], config, conn,
        now=_NOW, client=s3_client, render_fn=_stub_render(calls),
    )
    assert [(r.location, r.status, r.site) for r in results] == [
        ("Home", CycleStatus.RENDERED, "KFTG"),
        ("Parker", CycleStatus.ALREADY_HAVE, "KFTG"),
    ]
    assert len(calls) == 1  # rendered exactly once
    assert conn.execute("SELECT COUNT(*) AS n FROM volumes").fetchone()["n"] == 1
    assert len(list((config.data_dir / "KFTG" / "20260620").iterdir())) == 1


def test_one_location_error_does_not_stop_others(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path)
    _put_volume(s3_client, _SCAN, site="KFTG")
    _put_volume(s3_client, _SCAN, site="KTLX")
    conn = _open_db(config)
    good = _stub_render()

    def render(volume_path: Path, config: Config) -> RenderResult:
        if "KFTG" in Path(volume_path).name:
            raise RuntimeError("decode blew up for KFTG")
        return good(volume_path, config)

    results = collect_cycle(
        [_home(), _loc("OKC", 35.4676, -97.5164)], config, conn,
        now=_NOW, client=s3_client, render_fn=render,
    )
    by_loc = {r.location: r for r in results}
    assert by_loc["Home"].status is CycleStatus.RENDER_FAILED
    assert by_loc["OKC"].status is CycleStatus.RENDERED
    assert db.latest_rendered_frame(conn, "KTLX") is not None


# --- live-reload: collector re-reads the store each cycle ---------------------


def test_run_collect_picks_up_added_location_next_cycle(
    tmp_path: Path, s3_client: object
) -> None:
    # Seeded with Home (KFTG) only. A KTLX volume exists, but until OKC is added at
    # runtime nothing collects it — proving the loop re-reads the store each cycle.
    config = _config(tmp_path)
    _put_volume(s3_client, _SCAN, site="KFTG")
    _put_volume(s3_client, _SCAN, site="KTLX")
    added = {"done": False}

    def now_fn() -> datetime:
        # Fires after cycle 1 reads locations; adds OKC for cycle 2 to pick up.
        if not added["done"]:
            added["done"] = True
            conn = locations_store.connect_bootstrapped(config)
            try:
                locations_store.create(
                    conn, None, name="OKC", lat=35.4676, lon=-97.5164,
                    make_default=False,
                )
            finally:
                conn.close()
        return _NOW

    run_collect(
        config, now_fn=now_fn, client=s3_client, render_fn=_stub_render(),
        max_cycles=2,
    )
    conn = _open_db(config)
    # KTLX got collected only because OKC was added between cycles.
    assert db.latest_rendered_frame(conn, "KTLX") is not None


def test_run_collect_drops_deleted_location_next_cycle(
    tmp_path: Path, s3_client: object
) -> None:
    # Seeded Home + OKC. KTLX only appears on day 2; OKC is deleted after cycle 1,
    # so day-2 KTLX must NOT be collected — proving deletions take effect live.
    config = _config(
        tmp_path,
        seed=(
            SeedLocation("Home", 39.3603, -104.5969, True),
            SeedLocation("OKC", 35.4676, -97.5164, False),
        ),
    )
    _put_volume(s3_client, datetime(2026, 6, 19, 11, 50, tzinfo=UTC), site="KFTG")
    _put_volume(s3_client, datetime(2026, 6, 20, 11, 50, tzinfo=UTC), site="KFTG")
    _put_volume(s3_client, datetime(2026, 6, 20, 11, 50, tzinfo=UTC), site="KTLX")
    nows = iter(
        [
            datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
            datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        ]
    )
    deleted = {"done": False}

    def now_fn() -> datetime:
        n = next(nows)
        if not deleted["done"]:  # after cycle 1, drop OKC
            deleted["done"] = True
            conn = locations_store.connect_bootstrapped(config)
            try:
                okc = next(
                    loc for loc in locations_store.current_locations(conn, None)
                    if loc.name == "OKC"
                )
                assert okc.id is not None
                locations_store.delete(conn, okc.id)
            finally:
                conn.close()
        return n

    run_collect(
        config, now_fn=now_fn, client=s3_client, render_fn=_stub_render(),
        max_cycles=2,
    )
    conn = _open_db(config)
    # OKC was deleted before cycle 2, so the day-2 KTLX volume was never collected.
    assert db.latest_rendered_frame(conn, "KTLX") is None


# --- retention: throttled prune in the loop ----------------------------------


def _prune_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> list[datetime]:
    """Replace run_prune in the loop with a spy recording the ``now`` it saw."""
    seen: list[datetime] = []

    def spy(conn: object, config: Config, *, now: datetime, dry_run: bool) -> None:
        seen.append(now)

    monkeypatch.setattr("backscatter.collect.collect.run_prune", spy)
    return seen


def test_loop_prunes_first_cycle_then_throttled(
    tmp_path: Path, s3_client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)  # age default ON → retention active; interval 3600s
    seen = _prune_spy(monkeypatch)
    base = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    nows = iter([base, base + timedelta(seconds=60), base + timedelta(seconds=120)])

    run_collect(
        config, now_fn=lambda: next(nows), client=s3_client,
        render_fn=_stub_render(), max_cycles=3,
    )
    # Three cycles within 120s of each other and a 3600s interval → pruned once.
    assert seen == [base]


def test_loop_prunes_each_cycle_once_interval_elapses(
    tmp_path: Path, s3_client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)  # interval 3600s
    seen = _prune_spy(monkeypatch)
    base = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    nows = iter([base, base + timedelta(hours=2), base + timedelta(hours=4)])

    run_collect(
        config, now_fn=lambda: next(nows), client=s3_client,
        render_fn=_stub_render(), max_cycles=3,
    )
    # Each cycle is >1h after the last prune → prunes every cycle.
    assert len(seen) == 3


def test_loop_skips_prune_when_retention_off(
    tmp_path: Path, s3_client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(_config(tmp_path), retention_max_age_days=None)  # no policy
    seen = _prune_spy(monkeypatch)

    run_collect(
        config, now_fn=lambda: _NOW, client=s3_client,
        render_fn=_stub_render(), max_cycles=2,
    )
    assert seen == []  # no policy active → prune never runs
