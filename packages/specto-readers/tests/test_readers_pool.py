"""The worker pool: shared-memory results, progress, callbacks, cancel, crash isolation."""

from __future__ import annotations

import os
from collections.abc import Iterator

import numpy as np
import pytest
from readers_helpers import (
    CAM_A,
    add_one,
    crash,
    empty_array,
    first_jpg,
    make_array,
    make_list,
    needs_testdata,
    pid,
    qt_loaded,
    raise_error,
    sleep_then,
    until_cancelled,
)

from specto_core.jobs import InlineJobManager
from specto_core.kinds import BufferSpec, DataRef, DecodeRequest, TypedBuffer
from specto_readers import (
    PoolJobManager,
    SharedResult,
    release_result,
    submit_decode,
    submit_decode_batch,
)
from specto_readers.pool import _export

WAIT = 60.0


@pytest.fixture(scope="module")
def pool() -> Iterator[PoolJobManager]:
    with PoolJobManager(workers=2) as p:
        yield p


def test_plain_values_progress_and_callback(pool: PoolJobManager):
    seen: list[str] = []
    j = pool.submit("t", add_one, 41, on_done=lambda job: seen.append(job.id))
    assert j.state in ("queued", "running")
    assert pool.wait([j], WAIT)
    assert j.state == "done" and j.result == 42 and j.progress == 1.0 and j.message == "halfway"
    assert seen == [j.id]
    assert pool.status(j.id) is j and j in pool.list(active_only=False)


def test_array_results_come_back_in_shared_memory(pool: PoolJobManager):
    j = pool.submit("t", make_array, 12, dtype="int32")
    assert pool.wait([j], WAIT)
    res = j.result
    assert isinstance(res, SharedResult)
    assert res.spec.dtype == "int32" and res.spec.shape == [2, 6] and res.spec.shm
    assert np.array_equal(res.array, np.arange(12, dtype=np.int32).reshape(2, 6))
    arr = res.array  # a live view
    assert arr.base is not None
    del arr
    res.release()
    assert res.released
    with pytest.raises(ValueError):
        _ = res.array
    res.release()  # idempotent


def test_lists_and_empty_arrays(pool: PoolJobManager):
    j = pool.submit("t", make_list)
    assert pool.wait([j], WAIT)
    a, tag, b = j.result
    assert isinstance(a, SharedResult) and tag == "tag" and isinstance(b, SharedResult)
    assert a.spec.dtype == "float32" and b.array.tolist() == [1, 1, 1]
    release_result(j)
    assert a.released and b.released
    j = pool.submit("t", empty_array)
    assert pool.wait([j], WAIT)
    assert isinstance(j.result, TypedBuffer) and j.result.to_numpy().shape == (0, 4)


def test_export_passes_specs_through():
    spec = BufferSpec(dtype="uint8", shape=[3], shm="existing")
    assert _export(spec, {}) == ("spec", spec.model_dump())
    assert _export({"a": 1}, {}) == ("value", {"a": 1})


def test_error_in_job_fails_only_that_job(pool: PoolJobManager):
    bad = pool.submit("t", raise_error)
    good = pool.submit("t", add_one, 1)
    assert pool.wait([bad, good], WAIT)
    assert bad.state == "failed" and "ValueError: decoder exploded" in bad.error
    assert good.state == "done" and good.result == 2
    assert pool.respawns == 0


def test_worker_crash_fails_one_job_and_respawns(pool: PoolJobManager):
    before = list(pool.workers)
    respawns = pool.respawns
    dead = pool.submit("t", crash)
    after = pool.submit("t", add_one, 1)
    assert pool.wait([dead, after], WAIT)
    assert dead.state == "failed" and "died" in dead.error and dead.id in dead.error
    assert after.state == "done" and after.result == 2
    assert pool.respawns == respawns + 1
    assert len(pool.workers) == 2 and set(pool.workers) != set(before)
    # the pool still drives both workers afterwards
    jobs = [pool.submit("t", pid) for _ in range(6)]
    assert pool.wait(jobs, WAIT)
    assert set(j.result for j in jobs) == set(pool.workers)


def test_cancel_queued_and_running(pool: PoolJobManager):
    slow = pool.submit("t", sleep_then, 0.4)
    slow2 = pool.submit("t", sleep_then, 0.4)
    queued = pool.submit("t", add_one, 1)
    assert pool.queued >= 1
    c = pool.cancel(queued.id)
    assert c.state == "cancelled" and pool.queued == 0
    r = pool.cancel(slow.id)
    assert r.state == "cancelled"
    assert pool.wait([slow2], WAIT) and slow2.result == 0.4
    pool.poll(0.6)  # the cancelled job's late result is dropped
    assert slow.state == "cancelled" and slow.result is None
    assert pool.cancel(slow2.id).state == "done"


def test_unpicklable_function_fails_cleanly(pool: PoolJobManager):
    j = pool.submit("t", lambda progress: 1)
    assert pool.wait([j], WAIT)
    assert j.state == "failed" and "Error" in j.error


def test_workers_never_import_qt(pool: PoolJobManager):
    j = pool.submit("t", qt_loaded)
    assert pool.wait([j], WAIT) and j.result is False


def test_a_cancelled_running_job_hears_it_through_progress(pool: PoolJobManager, tmp_path):
    """SPEC-17: cancel reaches a running job (``progress.cancelled()``), so a long one
    (an archived video's copy) stops instead of running on to a dropped result."""
    import time

    marker = tmp_path / "heard"
    j = pool.submit("t", until_cancelled, str(marker))
    deadline = time.monotonic() + 20
    while j.state != "running" and time.monotonic() < deadline:
        pool.poll(0.05)
    time.sleep(0.2)
    pool.cancel(j.id)
    while not marker.exists() and time.monotonic() < deadline:
        pool.poll(0.05)
    assert marker.read_text(encoding="utf-8") == "cancelled"
    assert j.state == "cancelled"


def test_events_and_default_size():
    from specto_core.events import EventStream

    ev = EventStream()
    with PoolJobManager(workers=1, events=ev) as p:
        j = p.submit("t", add_one, 1)
        assert p.wait([j], WAIT)
    kinds = [e.kind for e in ev.since(0)]
    assert kinds[-1] == "job.done" and "job.progress" in kinds
    assert PoolJobManager(start=False).size == max(1, (os.cpu_count() or 4) - 2)
    with pytest.raises(RuntimeError):
        p.submit("t", add_one, 1)


def test_shutdown_cancels_pending():
    p = PoolJobManager(workers=1)
    a = p.submit("t", sleep_then, 0.2)
    b = p.submit("t", add_one, 1)
    p.shutdown(timeout=3.0)
    assert b.state == "cancelled" and a.done
    assert p.workers == []


# -- decode through the pool -------------------------------------------------------------


@needs_testdata
def test_decode_thumbnails_through_pool_and_inline(pool: PoolJobManager):
    ref = DataRef(kind="image2d", uri=str(first_jpg()))
    req = DecodeRequest(purpose="thumbnail", max_size=96)
    j = submit_decode(pool, ref, req, canvas="cv1")
    assert pool.wait([j], WAIT)
    assert j.canvas == "cv1" and j.kind == "decode"
    res = j.result
    assert isinstance(res, SharedResult) and res.spec.shape == [72, 96, 4]
    assert res.spec.layout == "HWC" and res.array.dtype == np.uint8
    inline = InlineJobManager()
    k = submit_decode(inline, ref, req)
    assert k.state == "done" and np.array_equal(k.result.to_numpy(), res.array)
    res.release()


@needs_testdata
def test_decode_batch_isolates_bad_items(pool: PoolJobManager):
    refs = [DataRef(kind="image2d", uri=str(p)) for p in sorted(CAM_A.glob("*.jpg"))[:5]]
    refs.insert(2, DataRef(kind="image2d", uri=str(CAM_A / "missing.jpg")))
    j = submit_decode_batch(pool, refs, DecodeRequest(purpose="thumbnail", max_size=64))
    assert pool.wait([j], WAIT)
    out = j.result
    assert len(out) == 6 and isinstance(out[0], SharedResult) and out[0].spec.shape == [48, 64, 4]
    assert isinstance(out[2], dict) and "missing.jpg" in out[2]["uri"] and "error" in out[2]
    release_result(j)
    with pytest.raises(ValueError):
        submit_decode_batch(pool, [], DecodeRequest())
