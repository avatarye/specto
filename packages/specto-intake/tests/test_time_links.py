"""Seam 1 (#42): a video root view and time map links on the link runtime. The video's
time, emitted on its timeline, reaches the galleries' ``time`` ports through ``time_map``
links (offset, two-way from the gallery end); the global clock (``links.global_clock``) keeps
the root view's current time and one key <-> time lookup per view (frames from their
file-name stamps, the video from its ``cam-a.timeline.json`` sidecar), so data at two
frame rates (the 32 fps video, the 16 Hz cam-a ticks, the 32 Hz cam-b frames) stays in
sync through it. Worked values: the first run-001 cam-a and cam-b frames and the
sidecar's stamps (``tools/make_testdata.py``: cam-a ticks every 62.5 ms with two frames
per tick, cam-b every 31.25 ms from 50 ms in)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from intake_helpers import RUN, copy_frames, needs_testdata, tree_files

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import FilesValue
from specto_core.timebase import TimeValue, stamp_seconds

VIDEO = RUN / "video"
pytestmark = [
    needs_testdata,
    pytest.mark.skipif(not (VIDEO / "cam-a.timeline.json").exists(), reason="no video sidecar"),
]

CAM_A_KEYS = r"(?P<cam>cam-a)/(?P<frame>\d{5})_\d+\.jpg$"
CAM_B_KEYS = r"(?P<cam>cam-b)/(?P<frame>\d{5})_\d+\.jpg$"

# stamps (seconds, as the clock reads the ns in the file names and the sidecar): cam-a
# and the video tick every 31.25 ms, cam-b every 62.5 ms from 50 ms in
FRONT_4 = stamp_seconds(1_700_000_000_125_000_000)  # cam-a/00004, video frame 4
FRONT_5 = stamp_seconds(1_700_000_000_156_250_000)
FRONT_6 = stamp_seconds(1_700_000_000_187_500_000)
FRONT_10 = stamp_seconds(1_700_000_000_312_500_000)
REAR_2 = stamp_seconds(1_700_000_000_175_000_000)  # 12.5 ms before cam-a/00006
REAR_6 = stamp_seconds(1_700_000_000_425_000_000)  # between cam-a/00013 and 00014, nearer 14


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "run-001")
    copy_frames(proj.root / "runs" / "run-001" / "cam-a", 12, "cam-a")
    copy_frames(proj.root / "runs" / "run-001" / "cam-b", 12, "cam-b")
    vdir = proj.root / "runs" / "run-001" / "video"
    vdir.mkdir(parents=True)
    shutil.copy2(VIDEO / "cam-a.timeline.json", vdir / "cam-a.timeline.json")
    (vdir / "cam-a.mp4").write_bytes(b"")  # the runtime reads the sidecar, never the video
    return proj


def _intaker(target: str, sources: list[str], screener: str, keys: str | None, group_by):
    params = {"sources": sources, "screener": [screener], "group_by": group_by}
    if keys:
        params["keys"] = keys
    return {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": target, "port": "data"},
        "params": params,
        "summary": {"sentence": f"The {target} collection: {sources[0]} ({screener})."},
    }


def _time_map(src: str, tgt: str, *, offset_ms: float = 0.0, two_way: bool = False) -> dict:
    return {
        "kind": "time_map",
        "two_way": two_way,
        "source": {"view": src, "port": "time"},
        "target": {"view": tgt, "port": "time"},
        "params": {"from_timeline": "stamp", "to_timeline": "stamp", "offset_ms": offset_ms},
        "summary": {"sentence": f"{tgt} follows the time of {src}."},
    }


def video_review(doc: dict) -> dict:
    """A cam-a video as the root view, cam-a and cam-b galleries time-mapped to it: the
    cam-a one two-way (a click seeks the video), the cam-b one shifted 100 ms."""
    doc["root_view"] = "rena"
    doc["views"]["kobi"] = {"type": "gallery", "label": "Cam A", "layout": {"position": [300, 0]}}
    doc["views"]["tamu"] = {"type": "gallery", "label": "Cam B", "layout": {"position": [960, 0]}}
    doc["views"]["rena"] = {"type": "video", "label": "Cam-a video", "layout": {}}
    doc["links"]["l2"] = _intaker("kobi", ["runs/run-001"], "@images", CAM_A_KEYS, ["cam", "frame"])
    doc["links"]["l3"] = _intaker("tamu", ["runs/run-001"], "@images", CAM_B_KEYS, ["cam", "frame"])
    doc["links"]["l4"] = _intaker("rena", ["runs/run-001/video"], "*.mp4", None, [])
    doc["links"]["l5"] = _time_map("rena", "kobi", two_way=True)
    doc["links"]["l6"] = _time_map("rena", "tamu", offset_ms=100)
    return doc


def all_files(engine: Engine, project: Project) -> FilesValue:
    """What the file tree emits for a pick of its root (#231)."""
    return tree_files(project.root)


def composed(project: Project) -> Engine:
    engine = Engine(project=project, jobs=InlineJobManager())
    engine.live.submit(video_review(engine.live.document()), origin="agent:s1", label="review")
    engine.links.emit("project", "all_files", all_files(engine, project))
    return engine


def test_the_video_time_reaches_time_mapped_galleries_and_back_from_the_two_way_end(
    project: Project,
):
    engine = composed(project)
    links = engine.links
    assert [it.key for it in links.input_of("rena", "data").items] == ["cam-a"]
    assert links.status("l5")["state"] == "waiting"  # nothing played yet

    links.emit("rena", "time", TimeValue(t=FRONT_4, timeline="stamp"))

    assert links.input_of("kobi", "time") == TimeValue(t=FRONT_4, timeline="stamp")
    assert links.input_of("tamu", "time") == TimeValue(t=FRONT_4 + 0.1, timeline="stamp")
    assert links.status("l5")["state"] == links.status("l6")["state"] == "resolved"
    assert links.global_clock.now == TimeValue(t=FRONT_4, timeline="stamp")  # the root view's time

    # a click in the cam-a gallery (two-way) seeks the video; the cam-b gallery (one-way,
    # fed from the video) hears nothing from it
    links.emit("kobi", "time", TimeValue(t=FRONT_10, timeline="stamp"))
    assert links.input_of("rena", "time") == TimeValue(t=FRONT_10, timeline="stamp")
    assert links.input_of("tamu", "time") == TimeValue(t=FRONT_4 + 0.1, timeline="stamp")
    # only the root view moves the clock
    assert links.global_clock.now == TimeValue(t=FRONT_4, timeline="stamp")

    # the shifted link maps back minus its offset from the far end
    doc = engine.live.document()
    doc["links"]["l6"]["two_way"] = True
    engine.live.submit(doc, origin="agent:s1", label="cam-b two-way")
    links.emit("tamu", "time", TimeValue(t=REAR_6, timeline="stamp"))
    assert links.input_of("rena", "time") == TimeValue(t=REAR_6 - 0.1, timeline="stamp")


def test_the_global_clock_keeps_two_frame_rates_in_sync(project: Project):
    engine = composed(project)
    clock = engine.links.global_clock

    # one key <-> time lookup per view: galleries from the stamps in their file names,
    # the video from its sidecar (240 frames, one stamp each)
    front, rear, video = clock.table_of("kobi"), clock.table_of("tamu"), clock.table_of("rena")
    assert front.keys[:3] == ["cam-a/00000", "cam-a/00001", "cam-a/00002"]
    assert front.time_of("cam-a/00004") == FRONT_4 and front.time_of("cam-a/00005") == FRONT_5
    assert rear.time_of("cam-b/00002") == REAR_2 and rear.timeline == "stamp"
    assert len(video.keys) == 240 and video.time_of("00006") == FRONT_6
    assert clock.table_of("fege") is None  # the file tree has no clock

    # the video plays (32 fps) and the galleries follow: at each video frame each
    # gallery's nearest frame is the one its own stamps say, never a shared index
    expect = {4: ("cam-a/00004", "cam-b/00001"), 6: ("cam-a/00006", "cam-b/00002")}
    expect |= {8: ("cam-a/00008", "cam-b/00003"), 10: ("cam-a/00010", "cam-b/00004")}
    for frame, (front_key, rear_key) in expect.items():
        t = video.time_of(f"{frame:05d}")
        engine.links.emit("rena", "time", TimeValue(t=t, timeline="stamp"))
        assert clock.now.t == t
        assert clock.nearest("kobi") == front_key
        assert clock.nearest("tamu", engine.links.input_of("tamu", "time")) != rear_key  # +100 ms
        assert clock.nearest("tamu") == rear_key
    assert clock.nearest("tamu", TimeValue(t=FRONT_4 + 0.1, timeline="stamp")) == "cam-b/00003"

    # a time on a timeline a view cannot answer maps to nothing
    assert clock.nearest("kobi", TimeValue(t=3.0, timeline="pts")) is None
    assert clock.nearest("fege") is None
    # the reverse lookup: a gallery click becomes a time on the gallery's timeline
    assert clock.time_of("tamu", "cam-b/00006") == TimeValue(t=REAR_6, timeline="stamp")
    assert clock.time_of("tamu", "cam-b/99999") is None


def test_a_time_on_the_wrong_timeline_fails_the_link_with_a_fixit(project: Project):
    engine = composed(project)
    links = engine.links
    links.emit("rena", "time", TimeValue(t=1.5, timeline="pts"))
    assert links.status("l6")["state"] == "failed"
    assert "pts" in links.status("l6")["message"] and "stamp" in links.status("l6")["message"]
    assert links.input_of("tamu", "time") is None
