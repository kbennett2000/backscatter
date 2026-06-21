"""Chunks assembler tests (Slice 26a) — the make-or-break correctness gate.

Hermetic: a committed fixture (the minimal real chunk bytes that complete the 0.5 deg
cut for a clear-air KEMX volume) + the assembled lowest sweep as an int16 (0.5-dBZ,
lossless) reference. The decoded-from-chunks sweep MUST equal the assembled tilt.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from backscatter.decode.volume import try_decode_lowest
from backscatter.ingest.chunks import order_chunks, parse_chunk_key

FIXTURE = Path(__file__).parent / "fixtures" / "chunks_KEMX.npz"


def _reference(fx: np.lib.npyio.NpzFile) -> np.ma.MaskedArray:
    """Rebuild the assembled lowest sweep from the int16 (dBZ×2) reference."""
    q = fx["ref_q2"]
    return np.ma.masked_equal(q, -32768).astype(np.float64) / 2.0


def test_chunk_decode_matches_assembled() -> None:
    """0.5 deg sweep decoded from partial chunks == the assembled volume's tilt."""
    fx = np.load(FIXTURE)
    sweep = try_decode_lowest(fx["buf"].tobytes())
    assert sweep is not None
    ref = _reference(fx)
    assert sweep.reflectivity.shape == ref.shape == (720, 1832)
    # Same no-data footprint, and every gate value identical (no fresher-but-wrong).
    assert np.array_equal(
        np.ma.getmaskarray(sweep.reflectivity), np.ma.getmaskarray(ref)
    )
    assert float(np.ma.abs(sweep.reflectivity - ref).max()) == 0.0


def test_completeness_rule_waits_for_the_second_cut() -> None:
    """Never a half-swept frame: incomplete (1 cut) -> None; complete (2) -> Sweep."""
    fx = np.load(FIXTURE)
    buf = fx["buf"].tobytes()
    offsets = fx["offsets"]
    # Everything but the last chunk (which begins the 2nd cut) → 0.5° not yet frozen.
    assert try_decode_lowest(buf[: int(offsets[-2])]) is None
    assert try_decode_lowest(buf) is not None


def test_parse_and_order_chunk_keys() -> None:
    c = parse_chunk_key("KEMX/927/20260621-215213-001-S")
    assert (c.num, c.kind) == (1, "S")
    assert c.start == datetime(2026, 6, 21, 21, 52, 13, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_chunk_key("not-a-chunk-key")
    keys = [f"K/9/20260621-215213-{n:03d}-I" for n in (3, 1, 2)]
    got = order_chunks([parse_chunk_key(k) for k in keys])
    assert [c.num for c in got] == [1, 2, 3]
