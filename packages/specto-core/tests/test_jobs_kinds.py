from __future__ import annotations

import numpy as np
import pytest

from specto_core import Engine
from specto_core.errors import NotFound
from specto_core.events import EventStream
from specto_core.jobs import InlineJobManager
from specto_core.kinds import (
    BufferSpec,
    DataRef,
    Decoder,
    DecodeRequest,
    Item,
    Locator,
    PluginManifest,
    TypedBuffer,
    ViewManifest,
    default_plugins,
)


def test_inline_job_manager_runs_and_emits():
    events = EventStream()
    jm = InlineJobManager(events)

    def work(n, *, progress):
        for i in range(n):
            progress((i + 1) / n, f"step {i}")
        return n * 2

    job = jm.submit("scan", work, 3, canvas="cv1")
    assert job.id == "j1" and job.state == "done" and job.result == 6 and job.progress == 1.0
    kinds = [e.kind for e in events.since(0)]
    assert kinds == ["job.progress"] * 3 + ["job.done"]

    def boom(*, progress):
        raise RuntimeError("bad file")

    job2 = jm.submit("decode", boom)
    assert job2.state == "failed" and "bad file" in job2.error
    assert [j.id for j in jm.list(canvas="cv1")] == ["j1"]
    with pytest.raises(NotFound):
        jm.status("j9")
    assert jm.cancel("j2").state == "failed"  # already finished: unchanged


def test_job_commands(engine: Engine):
    job = engine.jobs.submit("thumbs", lambda *, progress: 1)
    assert engine.call("job.status", {"job": job.id})["job"]["state"] == "done"
    assert engine.call("job.list", {})["jobs"][0]["kind"] == "thumbs"
    assert engine.call("job.cancel", {"job": job.id})["job"]["id"] == job.id


def test_typed_buffer_roundtrip():
    arr = np.arange(12, dtype=np.uint8).reshape(3, 4)
    buf = TypedBuffer.from_numpy(arr, layout="HW")
    assert buf.spec.nbytes == 12 and buf.spec.shape == [3, 4]
    raw = buf.to_numpy().tobytes()
    again = TypedBuffer.attach(BufferSpec(dtype="uint8", shape=[3, 4]), raw)
    assert np.array_equal(again.to_numpy(), arr)
    with pytest.raises(ValueError):
        TypedBuffer(buf.spec).to_numpy()


def test_protocols_are_structural():
    class JpegLocator:
        name = "jpeg"

        def can_handle(self, path: str) -> int:
            return 10 if path.endswith(".jpg") else 0

        def locate(self, item: Item) -> DataRef:
            return DataRef(kind="image2d", uri=item.primary)

    class JpegDecoder:
        name = "jpeg"

        def decode(self, ref: DataRef, request: DecodeRequest) -> TypedBuffer:
            return TypedBuffer.from_numpy(np.zeros((2, 2, 3), np.uint8), layout="HWC")

    assert isinstance(JpegLocator(), Locator) and isinstance(JpegDecoder(), Decoder)
    item = Item(key="00001", parts={".jpg": "a/00001.jpg", ".json": "a/00001.json"})
    ref = JpegLocator().locate(item)
    assert ref.kind == "image2d" and ref.uri == "a/00001.jpg"
    out = JpegDecoder().decode(ref, DecodeRequest(purpose="thumbnail", max_size=256))
    assert out.spec.layout == "HWC"


def test_plugin_registry_and_aliases():
    reg = default_plugins()
    assert reg.view("gallery").host == "quick" and reg.view("json").cost.web_views == 1
    assert reg.kinds_for_path("x/00001.jpg") == ["image2d"]
    assert reg.kinds_for_path("x/a.geojson") == ["geojson", "json"]
    assert ".jpg" in reg.aliases()["@images"] and "@json" in reg.aliases()
    reg.register(
        PluginManifest(
            name="ext", views=[ViewManifest(type="map", host="web", accepts=["geojson"])]
        )
    )
    assert reg.view("map").plugin == "ext"
    with pytest.raises(ValueError):
        reg.register(PluginManifest(name="ext"))
    reg.disable("ext", "crashed 3 times")
    assert reg.disabled() == {"ext": "crashed 3 times"}


def test_job_result_serialises_shared_memory_and_unknown_objects():
    from specto_core.jobs import JobInfo
    from specto_core.kinds import BufferSpec

    class Shared:  # what the readers pool stores: a spec plus an open shm handle
        def __init__(self) -> None:
            self.spec = BufferSpec(dtype="uint8", shape=[2, 2, 4], shm="psm_x", layout="HWC")
            self.handle = object()

    info = JobInfo(id="j1", kind="decode", state="done", result=[Shared(), object(), {"n": 1}])
    out = info.model_dump(mode="json")["result"]
    assert out[0]["buffer"]["shm"] == "psm_x"
    assert out[1] == {"type": "object"} and out[2] == {"n": 1}


def _until_cancelled(progress) -> str:
    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if progress.cancelled():
            return "stopped"
        time.sleep(0.01)
    return "never told"


def test_a_thread_job_hears_its_cancel_through_progress():
    """SPEC-17: ``progress.cancelled()`` turns True once the job is cancelled, so a long
    job stops; an inline job is never cancelled mid-run."""
    import time

    from specto_core.jobs import ThreadJobManager

    jobs = ThreadJobManager()
    j = jobs.submit("t", _until_cancelled)
    time.sleep(0.1)
    jobs.cancel(j.id)
    t0 = time.monotonic()
    while jobs.list() and any(t.is_alive() for t in list(jobs._threads.values())):
        if time.monotonic() - t0 > 5:
            break
        time.sleep(0.01)
    assert time.monotonic() - t0 < 5 and j.state == "cancelled"
    jobs.shutdown()
    seen: list[bool] = []
    InlineJobManager().submit("t", lambda progress: seen.append(progress.cancelled()))
    assert seen == [False]
