"""Joint tracks from tables (SPEC-43.3): the ``joint_track`` decoder reads a CSV or
Parquet table into cached columns; ``JointTrack`` answers the nearest sample at a time
on the global clock and counts the joint names the URDF does not have."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from specto_core.kinds import DataRef, DecodeRequest
from specto_core.timebase import TimeValue
from specto_readers import default_readers
from specto_readers.decoders import DecodeError
from specto_readers.joint_track import JointTrack

T0_NS = 1_700_000_000_000_000_000  # an epoch-ns stamp


def _decode(path: Path, **options) -> JointTrack:
    ref = DataRef(kind="joint_track", uri=str(path))
    buf = default_readers().decode(ref, DecodeRequest(options=options))
    return JointTrack.from_buffer(buf)


def _csv(tmp_path: Path, text: str, name: str = "track.csv") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_csv_with_epoch_ns_stamps_reads_onto_the_stamp_clock(tmp_path: Path):
    p = _csv(
        tmp_path,
        "stamp_ns,shoulder,elbow\n"
        f"{T0_NS},0.0,1.0\n"
        f"{T0_NS + 100_000_000},0.5,1.5\n"
        f"{T0_NS + 200_000_000},1.0,2.0\n",
    )
    track = _decode(p)
    assert track.names == ["shoulder", "elbow"]
    assert track.timeline == "stamp"
    assert len(track) == 3
    lo, hi = track.range()
    assert lo == pytest.approx(1_700_000_000.0) and hi == pytest.approx(1_700_000_000.2)


def test_the_nearest_sample_shows_never_an_interpolated_one(tmp_path: Path):
    p = _csv(
        tmp_path,
        "stamp_ns,shoulder,elbow\n"
        f"{T0_NS},0.0,1.0\n"
        f"{T0_NS + 100_000_000},0.5,1.5\n"
        f"{T0_NS + 200_000_000},1.0,2.0\n",
    )
    track = _decode(p)
    t0 = 1_700_000_000.0
    assert track.sample(TimeValue(t=t0 + 0.04)) == {"shoulder": 0.0, "elbow": 1.0}
    assert track.sample(TimeValue(t=t0 + 0.06)) == {"shoulder": 0.5, "elbow": 1.5}
    # before the first and after the last sample: the end samples
    assert track.sample(TimeValue(t=t0 - 10)) == {"shoulder": 0.0, "elbow": 1.0}
    assert track.sample(TimeValue(t=t0 + 10)) == {"shoulder": 1.0, "elbow": 2.0}


def test_a_tie_between_two_samples_picks_the_earlier(tmp_path: Path):
    p = _csv(tmp_path, "time,j\n0.0,1.0\n1.0,2.0\n")
    track = _decode(p)
    assert track.sample(TimeValue(t=0.5)) == {"j": 1.0}


def test_seconds_column_and_unsorted_rows(tmp_path: Path):
    p = _csv(tmp_path, "t,j1,j2\n2.0,20,200\n0.0,0,0\n1.0,10,100\n")
    track = _decode(p)
    assert track.range() == (0.0, 2.0)
    assert track.sample(TimeValue(t=1.1)) == {"j1": 10.0, "j2": 100.0}


def test_time_column_is_found_by_name_anywhere_or_given(tmp_path: Path):
    p = _csv(tmp_path, "j1,timestamp,j2\n5,0.0,6\n7,1.0,8\n")
    track = _decode(p)
    assert track.names == ["j1", "j2"]
    assert track.sample(TimeValue(t=0.9)) == {"j1": 7.0, "j2": 8.0}
    p2 = _csv(tmp_path, "when,j\n0,1\n1,2\n", name="given.csv")
    assert _decode(p2, time_column="when").sample(TimeValue(t=1.0)) == {"j": 2.0}
    # no time-like name: the first column is the time
    assert _decode(p2).names == ["j"]


def test_unit_suffix_beats_magnitude(tmp_path: Path):
    # 1500 ms is 1.5 s, though as plain numbers it would read as seconds
    p = _csv(tmp_path, "time_ms,j\n0,1\n1500,2\n")
    assert _decode(p).range() == (0.0, 1.5)
    # bare integers of epoch magnitude read as ns / us / ms by size
    p2 = _csv(tmp_path, f"stamp,j\n{T0_NS},1\n", name="ns.csv")
    assert _decode(p2).range()[0] == pytest.approx(1_700_000_000.0)
    p3 = _csv(tmp_path, "stamp,j\n1700000000000,1\n", name="ms.csv")
    assert _decode(p3).range()[0] == pytest.approx(1_700_000_000.0)


def test_empty_cells_skip_that_joint_at_that_row(tmp_path: Path):
    # the elbow is reported at 0.0 and 0.2 only: at 0.1 it shows its own nearest sample
    p = _csv(tmp_path, "time,shoulder,elbow\n0.0,0,10\n0.1,1,\n0.2,2,12\n")
    track = _decode(p)
    assert track.sample(TimeValue(t=0.1)) == {"shoulder": 1.0, "elbow": 10.0}
    assert track.sample(TimeValue(t=0.16)) == {"shoulder": 2.0, "elbow": 12.0}


def test_non_numeric_columns_are_skipped_and_reported(tmp_path: Path):
    p = _csv(tmp_path, "time,frame_id,j\n0,base,1\n1,base,2\n")
    track = _decode(p)
    assert track.names == ["j"]
    assert track.skipped == ["frame_id"]


def test_parquet_reads_like_csv(tmp_path: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "stamp_ns": pa.array([T0_NS, T0_NS + 50_000_000], pa.int64()),
            "wrist": pa.array([0.25, None], pa.float32()),
            "gripper": pa.array([0.01, 0.02], pa.float64()),
        }
    )
    p = tmp_path / "track.parquet"
    pq.write_table(table, p)
    track = _decode(p)
    assert track.names == ["wrist", "gripper"]
    t1 = 1_700_000_000.05
    assert track.sample(TimeValue(t=t1)) == {"wrist": 0.25, "gripper": 0.02}


def test_unmatched_counts_joint_names_the_urdf_does_not_have(tmp_path: Path):
    p = _csv(tmp_path, "time,arm/shoulder,elbow,wrist\n0,1,2,3\n")
    track = _decode(p)
    assert track.unmatched(["shoulder", "elbow", "wrist", "gripper"]) == ["arm/shoulder"]


def test_a_table_without_joint_columns_or_rows_is_an_error(tmp_path: Path):
    with pytest.raises(DecodeError, match="no joint columns"):
        _decode(_csv(tmp_path, "time,label\n0,a\n"))
    with pytest.raises(DecodeError, match="no rows"):
        _decode(_csv(tmp_path, "time,j\n", name="empty.csv"))
    with pytest.raises(DecodeError, match="time column 'when'"):
        _decode(_csv(tmp_path, "time,j\n0,1\n", name="x.csv"), time_column="when")
    with pytest.raises(DecodeError, match="cannot read"):
        _decode(tmp_path / "missing.csv")


def test_a_track_on_another_timeline_answers_only_that_one(tmp_path: Path):
    p = _csv(tmp_path, "time,j\n0,1\n1,2\n")
    track = _decode(p, timeline="sim")
    assert track.timeline == "sim"
    assert track.sample(TimeValue(t=1.0, timeline="sim")) == {"j": 2.0}
    assert track.sample(TimeValue(t=1.0, timeline="stamp")) is None
    # a bare time (the receiver's own timeline) is taken on the track's
    assert track.sample(TimeValue(t=1.0, timeline="")) == {"j": 2.0}


def test_the_buffer_is_one_float64_table_with_its_columns(tmp_path: Path):
    p = _csv(tmp_path, "time,a,b\n0,1,2\n1,3,4\n")
    ref = DataRef(kind="joint_track", uri=str(p))
    buf = default_readers().decode(ref)
    arr = buf.to_numpy()
    assert arr.dtype == np.float64 and arr.shape == (2, 3)
    assert buf.spec.layout == "table"
    assert buf.spec.meta["columns"] == ["time", "a", "b"]
    assert buf.spec.meta["timeline"] == "stamp"


def test_decoded_on_the_pool_and_cached_after_the_shared_buffer_is_released(tmp_path: Path):
    from specto_readers import PoolJobManager, SharedResult, submit_decode

    p = _csv(tmp_path, "time,j\n0,1\n1,2\n2,3\n")
    with PoolJobManager(workers=1) as pool:
        job = submit_decode(pool, DataRef(kind="joint_track", uri=str(p)), DecodeRequest())
        assert pool.wait([job], 60.0), job.error
        res = job.result
        assert isinstance(res, SharedResult)
        track = JointTrack.from_buffer(res.buffer)
        res.release()
    assert track.sample(TimeValue(t=1.4)) == {"j": 2.0}
