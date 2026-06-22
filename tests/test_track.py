"""Value-based tests for storm-cell identification (Slice 28a).

A synthetic dBZ grid with blobs at *known* pixel locations must yield cells at the
matching lon/lat, with the right peak dBZ and ground area, and must honor the dBZ
threshold + minimum-area filters. The orientation guard (top rows → north, right
cols → east) is where a flipped axis would hide, exactly as in test_raster.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from backscatter.render.geometry import lonlat_to_mercator, mercator_to_lonlat
from backscatter.store import db
from backscatter.track.associate import TrackedCell
from backscatter.track.detect import (
    DEFAULT_DBZ_THRESHOLD,
    Cell,
    detect_cells,
)

PIXEL = 250.0
N = 100  # 100 x 100 grid


def _grid_centered_on(lon: float, lat: float) -> tuple[np.ndarray, tuple[float, ...]]:
    """An all-NaN N×N dBZ grid whose Mercator extent is centered on (lon, lat)."""
    xc, yc = (float(v) for v in lonlat_to_mercator(lon, lat))
    x_min = xc - (N / 2) * PIXEL
    y_max = yc + (N / 2) * PIXEL
    bounds = (x_min, y_max - N * PIXEL, x_min + N * PIXEL, y_max)
    grid = np.full((N, N), np.nan, dtype=np.float64)
    return grid, bounds


def _expected_lonlat(
    bounds: tuple[float, ...], row: float, col: float
) -> tuple[float, float]:
    x_min, _y_min, _x_max, y_max = bounds
    x = x_min + (col + 0.5) * PIXEL
    y = y_max - (row + 0.5) * PIXEL
    lon, lat = mercator_to_lonlat(x, y)
    return float(lon), float(lat)


def test_single_blob_centroid_and_area() -> None:
    grid, bounds = _grid_centered_on(-104.5, 39.8)
    # A 21×21 uniform 50 dBZ blob centered at (row=20, col=70): north-east of center.
    # (21×21 ≈ 16 km² on the ground, comfortably over the 10 km² floor.)
    grid[10:31, 60:81] = 50.0

    cells = detect_cells(grid, bounds)
    assert len(cells) == 1
    cell = cells[0]

    exp_lon, exp_lat = _expected_lonlat(bounds, 20.0, 70.0)
    assert cell.centroid_lon == pytest.approx(exp_lon, abs=1e-6)
    assert cell.centroid_lat == pytest.approx(exp_lat, abs=1e-6)
    assert cell.max_dbz == pytest.approx(50.0)

    # 441 pixels, each 250 m * cos(lat) on the ground (Web-Mercator correction).
    ground_px = PIXEL * math.cos(math.radians(cell.centroid_lat))
    expected_area = 21 * 21 * ground_px * ground_px / 1_000_000.0
    assert cell.area_km2 == pytest.approx(expected_area, rel=1e-9)


def test_orientation_top_is_north_right_is_east() -> None:
    """A flip guard: a blob in the top-right quadrant must read north & east."""
    center_lon, center_lat = -104.5, 39.8
    grid, bounds = _grid_centered_on(center_lon, center_lat)
    grid[0:21, 75:96] = 55.0  # top (north) and right (east) of center

    (cell,) = detect_cells(grid, bounds)
    assert cell.centroid_lat > center_lat  # top rows → north
    assert cell.centroid_lon > center_lon  # right cols → east


def test_below_threshold_is_ignored() -> None:
    grid, bounds = _grid_centered_on(-104.5, 39.8)
    grid[40:60, 40:60] = DEFAULT_DBZ_THRESHOLD - 5.0  # large but sub-threshold
    assert detect_cells(grid, bounds) == []


def test_tiny_cell_below_min_area_is_dropped() -> None:
    grid, bounds = _grid_centered_on(-104.5, 39.8)
    grid[50, 50] = 60.0  # one 250 m pixel ≈ 0.04 km², well under the 10 km² floor
    assert detect_cells(grid, bounds) == []
    # ...but it survives if we lower the floor, proving it was the area filter.
    assert len(detect_cells(grid, bounds, min_area_km2=0.0)) == 1


def test_cells_sorted_strongest_first() -> None:
    grid, bounds = _grid_centered_on(-104.5, 39.8)
    grid[5:26, 5:26] = 45.0  # weaker
    grid[60:81, 60:81] = 62.0  # stronger
    cells = detect_cells(grid, bounds)
    assert [round(c.max_dbz) for c in cells] == [62, 45]


def test_all_nodata_returns_empty() -> None:
    grid, bounds = _grid_centered_on(-104.5, 39.8)
    assert detect_cells(grid, bounds) == []


# --- storage round-trip --------------------------------------------------------


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "backscatter.db")
    db.init_db(conn)
    return conn


def _cells() -> list[TrackedCell]:
    return [
        TrackedCell(
            Cell(centroid_lon=-104.5, centroid_lat=39.8, max_dbz=60.0, area_km2=30.0),
            track_id=1,
            u_ms=5.0,
            v_ms=-3.0,
        ),
        TrackedCell(
            Cell(centroid_lon=-104.2, centroid_lat=39.9, max_dbz=48.0, area_km2=12.0),
            track_id=2,
            u_ms=0.0,
            v_ms=0.0,
        ),
    ]


def test_record_cells_round_trip(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    scan = datetime(2026, 6, 20, 21, 51, 7, tzinfo=UTC)
    db.record_cells(conn, site="KFTG", scan_time=scan, cells=_cells())

    rows = conn.execute(
        "SELECT centroid_lon, centroid_lat, max_dbz, area_km2, track_id, u_ms, v_ms "
        "FROM cells WHERE site = ? AND scan_time = ? ORDER BY max_dbz DESC",
        ("KFTG", scan.isoformat()),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["max_dbz"] == pytest.approx(60.0)
    assert rows[0]["track_id"] == 1
    assert rows[0]["u_ms"] == pytest.approx(5.0)
    assert rows[0]["v_ms"] == pytest.approx(-3.0)


def test_record_cells_round_trips_n_obs(tmp_path: Path) -> None:
    """The per-track observation count (Slice 28f) survives store → read-back."""
    conn = _conn(tmp_path)
    scan = datetime(2026, 6, 20, 21, 51, 7, tzinfo=UTC)
    cell = Cell(centroid_lon=-104.5, centroid_lat=39.8, max_dbz=60.0, area_km2=30.0)
    db.record_cells(
        conn,
        site="KFTG",
        scan_time=scan,
        cells=[TrackedCell(cell, track_id=1, u_ms=5.0, v_ms=0.0, n_obs=4)],
    )

    (row,) = db.cells_for_frame(conn, site="KFTG", scan_time=scan)
    assert row["n_obs"] == 4


def test_active_tracks_for_coast_feeds_back_n_obs(tmp_path: Path) -> None:
    """A candidate offered for the next frame carries its stored n_obs (so the count
    keeps climbing across collect cycles instead of resetting to 1)."""
    conn = _conn(tmp_path)
    f1 = datetime(2026, 6, 20, 21, 50, tzinfo=UTC)
    f2 = datetime(2026, 6, 20, 21, 55, tzinfo=UTC)
    cell = Cell(centroid_lon=-104.5, centroid_lat=39.8, max_dbz=50.0, area_km2=20.0)
    db.record_cells(
        conn,
        site="KFTG",
        scan_time=f1,
        cells=[TrackedCell(cell, track_id=1, u_ms=10.0, v_ms=0.0, n_obs=3)],
    )
    ((tc, _seen),) = db.active_tracks_for_coast(
        conn, site="KFTG", scan_time=f2, max_frames=2
    )
    assert tc.n_obs == 3


def test_record_cells_replaces_not_appends(tmp_path: Path) -> None:
    """A re-render of the same frame must replace its cells, not duplicate them."""
    conn = _conn(tmp_path)
    scan = datetime(2026, 6, 20, 21, 51, 7, tzinfo=UTC)
    db.record_cells(conn, site="KFTG", scan_time=scan, cells=_cells())
    db.record_cells(conn, site="KFTG", scan_time=scan, cells=_cells()[:1])

    (count,) = conn.execute(
        "SELECT COUNT(*) FROM cells WHERE site = ? AND scan_time = ?",
        ("KFTG", scan.isoformat()),
    ).fetchone()
    assert count == 1


def test_delete_frame_cascades_to_cells(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    scan = datetime(2026, 6, 20, 21, 51, 7, tzinfo=UTC)
    db.record_cells(conn, site="KFTG", scan_time=scan, cells=_cells())
    db.delete_frame(conn, site="KFTG", scan_time=scan.isoformat())

    (count,) = conn.execute("SELECT COUNT(*) FROM cells").fetchone()
    assert count == 0


def test_allocate_track_id_is_monotonic(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    when = datetime(2026, 6, 20, 21, 51, 7, tzinfo=UTC)
    a = db.allocate_track_id(conn, site="KFTG", created_at=when)
    b = db.allocate_track_id(conn, site="KFTG", created_at=when)
    assert b > a  # AUTOINCREMENT never reuses an id


def _tc(track_id: int, *, u: float = 10.0, lon: float = -104.5) -> TrackedCell:
    return TrackedCell(
        Cell(centroid_lon=lon, centroid_lat=39.8, max_dbz=50.0, area_km2=20.0),
        track_id=track_id,
        u_ms=u,
        v_ms=0.0,
    )


def test_active_tracks_for_coast_offers_a_track_that_missed_a_frame(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    f1 = datetime(2026, 6, 20, 21, 50, tzinfo=UTC)
    f2 = datetime(2026, 6, 20, 21, 55, tzinfo=UTC)
    f3 = datetime(2026, 6, 20, 22, 0, tzinfo=UTC)
    db.record_cells(conn, site="KFTG", scan_time=f1, cells=[_tc(1, u=12.0)])
    # track 1 absent at f2 (only track 9 detected)
    db.record_cells(conn, site="KFTG", scan_time=f2, cells=[_tc(9, lon=-103.0)])

    # At f3, coasting (max_frames≥1) still offers track 1 from f1 (it missed f2),
    # carrying its last-seen time + motion; track 9 comes from f2.
    got = db.active_tracks_for_coast(conn, site="KFTG", scan_time=f3, max_frames=2)
    by_id = {tc.track_id: (tc, seen) for tc, seen in got}
    assert set(by_id) == {1, 9}
    assert by_id[1][1] == f1  # last_seen = f1 (the missed-f2 coast)
    assert by_id[1][0].u_ms == pytest.approx(12.0)
    assert by_id[9][1] == f2


def test_active_tracks_for_coast_window_and_latest_per_track(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    f1 = datetime(2026, 6, 20, 21, 50, tzinfo=UTC)
    f2 = datetime(2026, 6, 20, 21, 55, tzinfo=UTC)
    f3 = datetime(2026, 6, 20, 22, 0, tzinfo=UTC)
    f4 = datetime(2026, 6, 20, 22, 5, tzinfo=UTC)
    db.record_cells(conn, site="KFTG", scan_time=f1, cells=[_tc(1)])
    db.record_cells(conn, site="KFTG", scan_time=f2, cells=[_tc(9, lon=-103.0)])
    db.record_cells(conn, site="KFTG", scan_time=f3, cells=[_tc(9, lon=-102.9)])

    # max_frames=1 → window is the last 2 frames {f3,f2}: track 1 (last at f1) drops;
    # track 9 returns its LATEST row (f3), once.
    ids1 = {tc.track_id: seen for tc, seen in
            db.active_tracks_for_coast(conn, site="KFTG", scan_time=f4, max_frames=1)}
    assert ids1 == {9: f3}

    # max_frames=2 → window {f3,f2,f1}: track 1 is back in range.
    ids2 = {tc.track_id for tc, _ in
            db.active_tracks_for_coast(conn, site="KFTG", scan_time=f4, max_frames=2)}
    assert ids2 == {1, 9}

    # max_frames=0 → previous frame only {f3}: no coasting.
    ids0 = {tc.track_id for tc, _ in
            db.active_tracks_for_coast(conn, site="KFTG", scan_time=f4, max_frames=0)}
    assert ids0 == {9}
