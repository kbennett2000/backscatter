"""Slice 26b: the live-chunks frame wired into collect + archive reconciliation.

Hermetic: the 26a KEMX chunk fixture is split back into per-chunk objects served by
moto, so a collect cycle assembles the 0.5 deg cut from the chunks bucket exactly as
in production. The render is stubbed (Py-ART is exercised by test_chunks.py); these
tests are about the *wiring* — indexing source='live', the decode bound, and the
live -> assembled in-place upgrade.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

import boto3
import numpy as np
import pytest
from moto import mock_aws

from backscatter.collect import collect as collect_mod
from backscatter.collect.collect import collect_cycle
from backscatter.config import Config, Location, SeedLocation, resolve_location
from backscatter.decode.volume import Sweep, try_decode_lowest
from backscatter.ingest import naming, s3
from backscatter.ingest.chunks import CHUNKS_BUCKET, LiveCursor
from backscatter.render.render import RenderResult
from backscatter.store import db

_FIXTURE = Path(__file__).parent / "fixtures" / "chunks_KEMX.npz"
_SITE = "KEMX"
_DIR = "100"


def _load_fixture() -> tuple[bytes, list[int], datetime]:
    """The fixture's raw concat, chunk boundaries, and the authoritative scan_time."""
    fx = np.load(_FIXTURE)
    buf = fx["buf"].tobytes()
    offsets = [int(o) for o in fx["offsets"]]
    decoded = try_decode_lowest(buf)
    assert decoded is not None
    return buf, offsets, decoded.scan_time


@pytest.fixture
def s3_client() -> Iterator[object]:
    with mock_aws():
        client = boto3.client("s3", region_name=s3.REGION)
        client.create_bucket(Bucket=s3.BUCKET)
        client.create_bucket(Bucket=CHUNKS_BUCKET)
        yield client


def _seed_chunks(
    client: object,
    scan_time: datetime,
    buf: bytes,
    offsets: list[int],
    *,
    upto: int | None = None,
) -> None:
    """Split ``buf`` at ``offsets`` into per-chunk objects under ``_SITE/_DIR/``."""
    n = len(offsets) if upto is None else upto
    starts = [0, *offsets[:-1]]
    for i in range(n):
        kind = "S" if i == 0 else ("E" if i == len(offsets) - 1 else "I")
        key = f"{_SITE}/{_DIR}/{scan_time:%Y%m%d-%H%M%S}-{i + 1:03d}-{kind}"
        client.put_object(  # type: ignore[attr-defined]
            Bucket=CHUNKS_BUCKET, Key=key, Body=buf[starts[i] : offsets[i]]
        )


def _config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=0.0,
        site_override=_SITE,  # pin KEMX so it is the location's primary site
        seed_locations=(SeedLocation("Home", 39.3603, -104.5969, True),),
    )


def _home() -> Location:
    return resolve_location("Home", 39.3603, -104.5969, is_default=True, override=_SITE)


def _stub_render_sweep(
    calls: list[datetime] | None = None,
) -> Callable[..., RenderResult]:
    """A render_sweep_fn that fabricates a RenderResult without Py-ART."""

    def render(
        sweep: Sweep,
        config: Config,
        *,
        site_icao: str,
        scan_time: datetime,
        out_dir: Path | None = None,
    ) -> RenderResult:
        if calls is not None:
            calls.append(scan_time)
        name = f"{site_icao}{scan_time:%Y%m%d_%H%M%S}_V06"
        png = config.data_dir / "renders" / site_icao / f"{name}.png"
        return RenderResult(
            png_path=png,
            sidecar_path=png.with_suffix(".json"),
            site=site_icao,
            scan_time=scan_time,
            elevation_deg=0.48,
            width=10,
            height=20,
            bounds_wgs84=(-112.0, 30.0, -109.0, 33.0),
            bounds_3857=(0.0, 0.0, 1.0, 1.0),
        )

    return render


def _open_db(config: Config):  # noqa: ANN201 - sqlite3.Connection
    conn = db.connect(config.db_path)
    db.init_db(conn)
    return conn


def _run(
    config: Config,
    conn: object,
    *,
    now: datetime,
    client: object,
    cursors: dict[str, LiveCursor],
    render_calls: list[datetime] | None = None,
) -> None:
    collect_cycle(
        [_home()],
        config,
        conn,
        now=now,
        client=client,  # type: ignore[arg-type]
        render_fn=lambda *a, **k: pytest.fail("assembled render must not run"),
        live_cursors=cursors,
        render_sweep_fn=_stub_render_sweep(render_calls),
    )


def test_live_frame_assembled_indexed_and_rendered(
    tmp_path: Path, s3_client: object
) -> None:
    buf, offsets, scan = _load_fixture()
    _seed_chunks(s3_client, scan, buf, offsets)  # full, complete volume
    config = _config(tmp_path)
    conn = _open_db(config)

    _run(config, conn, now=scan + timedelta(seconds=80), client=s3_client, cursors={})

    row = db.latest_rendered_frame(conn, _SITE)
    assert row is not None
    assert row["source"] == "live"
    assert row["render_status"] == "rendered"
    assert row["scan_time"] == scan.isoformat()
    # The live partial was written to the canonical path (where assembled will land).
    dest = (
        tmp_path
        / "data"
        / _SITE
        / f"{scan:%Y%m%d}"
        / (f"{_SITE}{scan:%Y%m%d_%H%M%S}_V06")
    )
    assert dest.exists() and dest.stat().st_size == len(buf)
    assert conn.execute("SELECT COUNT(*) AS n FROM volumes").fetchone()["n"] == 1


def test_decode_only_when_new_chunks_arrive(
    tmp_path: Path, s3_client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    buf, offsets, scan = _load_fixture()
    config = _config(tmp_path)
    conn = _open_db(config)
    cursors: dict[str, LiveCursor] = {}
    render_calls: list[datetime] = []

    # Count real decode attempts (ride_volume only decodes when chunks advanced).
    decode_calls = {"n": 0}
    real_decode = collect_mod.chunks.try_decode_all_lowest

    def counting_decode(data: bytes):  # noqa: ANN202
        decode_calls["n"] += 1
        return real_decode(data)

    monkeypatch.setattr(collect_mod.chunks, "try_decode_all_lowest", counting_decode)

    # Cycle A: 3 chunks present (incomplete) → one decode attempt, returns None.
    _seed_chunks(s3_client, scan, buf, offsets, upto=3)
    _run(
        config,
        conn,
        now=scan + timedelta(seconds=40),
        client=s3_client,
        cursors=cursors,
        render_calls=render_calls,
    )
    assert decode_calls["n"] == 1
    assert db.volume_source(conn, _SITE, scan) is None  # nothing indexed yet

    # Cycle B: no new chunks since last poll → the count gate skips the decode.
    _run(
        config,
        conn,
        now=scan + timedelta(seconds=70),
        client=s3_client,
        cursors=cursors,
        render_calls=render_calls,
    )
    assert decode_calls["n"] == 1  # unchanged — no redundant decode

    # Cycle C: remaining chunks arrive → decode runs, frame indexed + rendered once.
    _seed_chunks(s3_client, scan, buf, offsets)
    _run(
        config,
        conn,
        now=scan + timedelta(seconds=100),
        client=s3_client,
        cursors=cursors,
        render_calls=render_calls,
    )
    assert decode_calls["n"] == 2
    assert db.volume_source(conn, _SITE, scan) == "live"
    assert len(render_calls) == 1

    # Cycle D: volume done → no decode, no re-render on a steady poll.
    _run(
        config,
        conn,
        now=scan + timedelta(seconds=130),
        client=s3_client,
        cursors=cursors,
        render_calls=render_calls,
    )
    assert decode_calls["n"] == 2 and len(render_calls) == 1


def test_live_row_upgraded_to_assembled_in_place(
    tmp_path: Path, s3_client: object
) -> None:
    buf, offsets, scan = _load_fixture()
    _seed_chunks(s3_client, scan, buf, offsets)
    config = _config(tmp_path)
    conn = _open_db(config)
    cursors: dict[str, LiveCursor] = {}
    render_calls: list[datetime] = []

    # Cycle 1: live frame indexed.
    _run(
        config,
        conn,
        now=scan + timedelta(seconds=80),
        client=s3_client,
        cursors=cursors,
        render_calls=render_calls,
    )
    live_row = db.latest_rendered_frame(conn, _SITE)
    assert live_row is not None and live_row["source"] == "live"
    png_before = live_row["image_path"]

    # The complete assembled volume lands in S3 at its deterministic key.
    key = naming.archive_key(_SITE, scan)
    complete = b"COMPLETE-ASSEMBLED-VOLUME-BYTES" * 64
    s3_client.put_object(Bucket=s3.BUCKET, Key=key, Body=complete)  # type: ignore[attr-defined]

    # Cycle 2: now past the reconcile delay → the row is upgraded in place.
    _run(
        config,
        conn,
        now=scan + timedelta(minutes=7),
        client=s3_client,
        cursors=cursors,
        render_calls=render_calls,
    )

    rows = conn.execute("SELECT * FROM volumes").fetchall()
    assert len(rows) == 1  # upgraded in place — never a second row
    row = rows[0]
    assert row["source"] == "assembled"
    # No re-render: the PNG + render metadata are exactly as the live frame left them.
    assert row["image_path"] == png_before
    assert row["render_status"] == "rendered"
    assert len(render_calls) == 1
    # The raw artifact on disk is now the complete assembled volume (partial replaced).
    dest = Path(row["path"])
    assert dest.read_bytes() == complete
    assert row["size_bytes"] == len(complete)


def test_reconcile_waits_when_assembled_not_yet_present(
    tmp_path: Path, s3_client: object
) -> None:
    buf, offsets, scan = _load_fixture()
    _seed_chunks(s3_client, scan, buf, offsets)
    config = _config(tmp_path)
    conn = _open_db(config)
    cursors: dict[str, LiveCursor] = {}

    _run(
        config,
        conn,
        now=scan + timedelta(seconds=80),
        client=s3_client,
        cursors=cursors,
    )
    # Past the reconcile delay, but the assembled volume has NOT landed → row stays
    # 'live', untouched and retry-safe (no corruption, no spurious upgrade).
    _run(
        config, conn, now=scan + timedelta(minutes=7), client=s3_client, cursors=cursors
    )
    assert db.volume_source(conn, _SITE, scan) == "live"


def _two_cut_decoder(
    base_time: datetime, sails_time: datetime
) -> Callable[[bytes], list[Sweep]]:
    """A try_decode_all_lowest stub yielding a base + a SAILS surveillance cut.

    The real multi-cut decode is proven on real data in test_surveillance.py; here we
    drive the *wiring* (which cut becomes 'live' vs 'live-sails') deterministically."""

    def fake(_data: bytes) -> list[Sweep]:
        empty = np.ma.array(np.zeros((0, 0)))
        return [
            Sweep(_SITE, t, 0.48, np.array([]), np.array([]), empty)
            for t in (base_time, sails_time)
        ]

    return fake


def test_sails_cut_indexed_as_live_sails(
    tmp_path: Path, s3_client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A volume with a SAILS re-scan yields TWO frames: base 'live' + 'live-sails'.

    Both are rendered, at distinct scan_times; the base keeps the volume-start time (so
    it reconciles) and the SAILS cut lands ~2.4 min later."""
    buf, offsets, scan = _load_fixture()
    _seed_chunks(s3_client, scan, buf, offsets)  # full, complete volume
    sails = scan + timedelta(seconds=144)
    monkeypatch.setattr(
        collect_mod.chunks, "try_decode_all_lowest", _two_cut_decoder(scan, sails)
    )
    config = _config(tmp_path)
    conn = _open_db(config)
    render_calls: list[datetime] = []

    _run(
        config,
        conn,
        now=scan + timedelta(seconds=170),
        client=s3_client,
        cursors={},
        render_calls=render_calls,
    )

    assert db.volume_source(conn, _SITE, scan) == "live"
    assert db.volume_source(conn, _SITE, sails) == "live-sails"
    rows = conn.execute(
        "SELECT scan_time FROM volumes WHERE render_status='rendered' "
        "ORDER BY scan_time"
    ).fetchall()
    assert [r["scan_time"] for r in rows] == [scan.isoformat(), sails.isoformat()]
    assert sorted(render_calls) == [scan, sails]


def test_sails_cut_is_not_reconciled(
    tmp_path: Path, s3_client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconcile upgrades the base 'live' row but leaves the SAILS 'live-sails' row.

    The archive has one object per volume (at the volume start), so there is no
    assembled object at a SAILS cut's timestamp; the reconcile worklist is source='live'
    only, so SAILS frames stay live permanently (ADR-0012)."""
    buf, offsets, scan = _load_fixture()
    _seed_chunks(s3_client, scan, buf, offsets)
    sails = scan + timedelta(seconds=144)
    monkeypatch.setattr(
        collect_mod.chunks, "try_decode_all_lowest", _two_cut_decoder(scan, sails)
    )
    config = _config(tmp_path)
    conn = _open_db(config)

    _run(config, conn, now=scan + timedelta(seconds=170), client=s3_client, cursors={})

    # The complete assembled volume lands at the BASE scan's deterministic key only.
    s3_client.put_object(  # type: ignore[attr-defined]
        Bucket=s3.BUCKET, Key=naming.archive_key(_SITE, scan), Body=b"COMPLETE" * 64
    )
    _run(config, conn, now=scan + timedelta(minutes=7), client=s3_client, cursors={})

    assert db.volume_source(conn, _SITE, scan) == "assembled"  # base upgraded
    assert db.volume_source(conn, _SITE, sails) == "live-sails"  # SAILS untouched


def test_live_disabled_is_assembled_only(tmp_path: Path, s3_client: object) -> None:
    buf, offsets, scan = _load_fixture()
    _seed_chunks(s3_client, scan, buf, offsets)
    from dataclasses import replace

    config = replace(_config(tmp_path), live_chunks=False)
    conn = _open_db(config)

    collect_cycle(
        [_home()],
        config,
        conn,
        now=scan + timedelta(seconds=80),
        client=s3_client,  # type: ignore[arg-type]
        render_sweep_fn=lambda *a, **k: pytest.fail("live render must not run"),
    )
    # Live off → the chunks path never runs; no frame indexed (no assembled seeded).
    assert db.latest_rendered_frame(conn, _SITE) is None
    assert conn.execute("SELECT COUNT(*) AS n FROM volumes").fetchone()["n"] == 0
