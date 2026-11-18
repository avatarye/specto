"""``tools/bench_gallery.py``'s frame statistics (SPEC-1): the frames over 33 ms beside
the GUI thread's gen-2 collections, and the gate that tells a regression from a
collection pause (SPEC-54 tracks those)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "tools" / "bench_gallery.py"


def _bench():
    spec = importlib.util.spec_from_file_location("bench_gallery_under_test", BENCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frames(long_at: int, n: int = 20) -> list:
    """``n`` 16.7 ms frames (swap gap, sync to swap, sync to end of render, as
    ``GalleryItem.frameTimes`` gives them) with one 60 ms frame at ``long_at``."""
    bench = _bench()
    return [bench.Frame(60.0 if i == long_at else 16.7, 9.0, 2.0) for i in range(n)]


def test_a_long_frame_during_a_gen2_collection_is_reported_but_passes_the_gate():
    bench = _bench()
    # the long frame (index 10) runs from 167 ms to 227 ms; the collection overlaps it
    fm = bench.frame_stats(_frames(10), [{"t": 0.17, "gen": 2, "ms": 55.0}])
    assert fm["frames"] == 20 and fm["worst"] == 60.0
    assert fm["over_33ms"] == 1 and fm["over_33ms_with_gen2_gc"] == 1
    assert fm["long"][0]["gen2_gc_ms"] == 55.0
    assert bench.frames_ok(fm)


def test_a_long_frame_no_collection_explains_fails_the_gate():
    bench = _bench()
    # a gen-2 collection elsewhere in the run, and a gen-1 one over the long frame
    gcs = [{"t": 0.02, "gen": 2, "ms": 10.0}, {"t": 0.17, "gen": 1, "ms": 40.0}]
    fm = bench.frame_stats(_frames(10), gcs)
    assert fm["over_33ms"] == 1 and fm["over_33ms_with_gen2_gc"] == 0
    assert fm["gen2_gc"] == 1
    assert not bench.frames_ok(fm)
