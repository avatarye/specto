"""Seam 2 (#42): the video host on ports, headless (no mpv, no QML). It receives its
collection on ``data``, shows the selected (or first) video, and emits itself on
``selection`` and its time on ``time`` (frame steps, scrubs); a time arriving on its
``time`` port seeks the nearest frame without re-emitting. A-B loop, hardware decoding,
speed and the selected video are agent parameters in the document. A gallery follows a
time through the global clock and emits the time of a clicked frame. Sound (#216):
muted by default, ``muted`` / ``volume`` params remembered as navigation, one view with
sound per window, mpv muted outside normal playback. Worked values: the run-001 cam-a
video's sidecar and the cam-a / cam-b frame stamps (``tools/make_testdata.py``)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from app_doc_helpers import pin
from gallery_helpers import TESTDATA, FakeGallery, copy_frames, needs_testdata

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.timeline.readout import clip_time, timecode, volume_label
from specto_app.views.file_tree import FileTreeFactory
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_app.views.video_view import VIDEO_MANIFEST, VideoFactory, VideoHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection, Selection, TimeValue
from specto_core.timebase import OWN_TIMELINE, stamp_seconds

VIDEO_DIR = TESTDATA / "runs" / "run-001" / "video"
pytestmark = [
    needs_testdata,
    pytest.mark.skipif(not (VIDEO_DIR / "cam-a.mp4").exists(), reason="no cam-a.mp4"),
]

# sidecar stamps (seconds): video frames 4, 8 and 10 (31.25 ms apart, one stamp each)
FRAME_4 = stamp_seconds(1_700_000_000_125_000_000)
FRAME_8 = stamp_seconds(1_700_000_000_250_000_000)
FRAME_10 = stamp_seconds(1_700_000_000_312_500_000)
REAR_4 = stamp_seconds(1_700_000_000_300_000_000)  # cam-b/00004: between video 9 and 10, nearer 10


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    return Engine(project=None, jobs=InlineJobManager())


def video_items(folder: Path, *names: str) -> list[Item]:
    """Copies of cam-a.mp4 (+ sidecar) under ``names``, as single-part items."""
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for name in names:
        shutil.copy2(VIDEO_DIR / "cam-a.mp4", folder / f"{name}.mp4")
        shutil.copy2(VIDEO_DIR / "cam-a.timeline.json", folder / f"{name}.timeline.json")
        items.append(Item(key=name, parts={".mp4": str(folder / f"{name}.mp4")}))
    return items


def emitted(ctx: HostContext, view_id: str, port: str):
    return ctx.links.output_of(view_id, port)


def submit_video(engine: Engine, params: dict | None = None, *more: str) -> None:
    doc = engine.live.document()
    doc["root_view"] = "rena"
    doc["views"]["rena"] = {"type": "video", "label": "Cam-a video", "params": params or {}}
    pin(doc, "rena")
    for vid in more:
        doc["views"][vid] = {"type": "video", "label": f"Video {vid}", "params": {}}
        pin(doc, vid)
    engine.live.submit(doc, origin="agent:s1", label="video")


class FakeMpv:
    """Stands in for the MpvItem of a headless host: records what the host writes to
    it, in order, and answers the properties the host reads."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, object]] = []
        self.props: dict[str, object] = {
            "paused": True, "eof": False, "hasAudio": True,
            "renderFrames": 0, "mpvVersion": "fake", "backend": "none", "error": "",
        }  # fmt: skip

    def setProperty(self, name: str, value: object) -> None:
        self.writes.append((name, value))
        if name == "muted":
            self.props["mute"] = value

    def property(self, name: str) -> object:
        return self.props.get(name)

    def mpvProperty(self, name: str) -> object:
        return self.props.get(name, "")

    def setMpvProperty(self, name: str, value: object) -> None:
        self.writes.append((name, value))

    def pause(self) -> None:
        self.writes.append(("pause", True))

    def play(self) -> None:
        self.writes.append(("pause", False))

    def togglePause(self) -> None:
        self.writes.append(("pause", not self.props["paused"]))

    def seek(self, t: float, exact: bool, relative: bool) -> None:
        self.writes.append(("seek", t))

    def stop(self) -> None:
        self.writes.append(("stop", None))


def test_the_video_emits_itself_and_its_time_and_follows_a_received_time(
    engine: Engine, tmp_path: Path
):
    submit_video(engine)
    ctx = HostContext(engine, None)
    host = VideoHost(ctx, "rena", VIDEO_MANIFEST, headless=True)
    assert host.key is None and ctx.health_of("rena")["status"] == "paused"
    assert emitted(ctx, "rena", "selection") is None and emitted(ctx, "rena", "time") is None

    host.receive("data", Collection(items=video_items(tmp_path / "video", "cam-a")))

    assert host.key == "cam-a" and host.path.endswith("cam-a.mp4")
    assert len(host.timemap) == 240 and host.timeline == "stamp"  # the sidecar's clock
    assert emitted(ctx, "rena", "selection") == Selection(keys=["cam-a"], last="cam-a")
    assert emitted(ctx, "rena", "time") is None  # nothing played or stepped yet
    assert host.frame == 0 and ctx.health_of("rena")["status"] == "ok"

    host.step(4)  # headless: a step lands at once and emits the frame's time
    assert host.frame == 4 and host.playback_paused
    assert emitted(ctx, "rena", "time") == TimeValue(t=FRAME_4, timeline="stamp")
    host.scrub(FRAME_10, final=True)
    assert host.frame == 10
    assert emitted(ctx, "rena", "time") == TimeValue(t=FRAME_10, timeline="stamp")

    # a time arriving on the input port seeks the nearest frame; the frame it lands on
    # is emitted once (the player's time stays the one source), the runtime's echo
    # guard keeps it from going back over a two-way link
    host.receive("time", TimeValue(t=REAR_4, timeline="stamp"))
    assert host.frame == 10 and host.position == host.timemap.pts[10]
    assert emitted(ctx, "rena", "time") == TimeValue(t=FRAME_10, timeline="stamp")
    assert ctx.links.global_clock.now == TimeValue(t=FRAME_10, timeline="stamp")  # rena is the root
    host.receive("time", TimeValue(t=3.0, timeline="pts"))  # not this video's clock
    assert host.frame == 10

    snap = host.snapshot()
    assert snap["frame"] == 10 and snap["frame_count"] == 240 and snap["timeline"] == "stamp"
    assert snap["time"] == {"t": FRAME_10, "timeline": "stamp"} and snap["paused"] is True
    assert snap["speed"] == 1.0 and snap["loop"] is None and snap["hwdec"] is False
    assert snap["selected"] == {"index": 0, "key": "cam-a"} and snap["backend"] == "none"

    host.receive("data", None)
    assert host.key is None and ctx.health_of("rena")["status"] == "paused"


def test_the_footer_reads_the_frame_from_one_over_the_count_at_the_source_frame_rate(
    engine: Engine, tmp_path: Path
):
    """#128: "12 / 240 @ 32 fps" at rest, frames counted from 1; the rate is the
    source's own (cam-a.mp4 runs at 32 fps, the sidecar says so too while mpv has not
    reported it)."""
    submit_video(engine)
    host = VideoHost(HostContext(engine, None), "rena", VIDEO_MANIFEST, headless=True)
    assert host.tl.label == ""  # nothing to play

    host.receive("data", Collection(items=video_items(tmp_path / "video", "cam-a")))
    assert host.tl.label == "1 / 240 @ 32 fps"
    host.step(11)
    assert host.tl.label == "12 / 240 @ 32 fps"
    host.step(1000)  # clamped to the last frame
    assert host.tl.label == "240 / 240 @ 32 fps"


def test_the_progress_bar_tip_shows_the_hovered_frame_as_clip_time_and_smpte_timecode(
    engine: Engine, tmp_path: Path
):
    """#128: hovering the bar names the frame under the pointer by its clip time (pts,
    to the hundredth) and SMPTE timecode (the frame within its second: a 32 fps second
    holds frames 0..31, frame 32 opens the next one)."""
    submit_video(engine)
    host = VideoHost(HostContext(engine, None), "rena", VIDEO_MANIFEST, headless=True)
    host.receive("data", Collection(items=video_items(tmp_path / "video", "cam-a")))

    def tip_at(frame: int) -> tuple[str, str]:
        host.tl.hover(host.timemap.time_of(frame, "stamp"))
        return host.tl.hoverClip, host.tl.hoverTimecode

    assert tip_at(12) == ("0:00.38", "00:00:00:12")  # pts 0.375
    host.step(12)  # the readout counts from 1, the timecode's frames from 0
    assert host.tl.label.startswith("13 / 240") and tip_at(12)[1].endswith(":12")
    assert tip_at(31) == ("0:00.97", "00:00:00:31")  # pts 0.96875
    assert tip_at(34) == ("0:01.06", "00:00:01:02")  # pts 1.0625: frame 32 was 01:00
    host.tl.hoverLeave()
    assert (host.tl.hoverClip, host.tl.hoverTimecode) == ("", "")
    assert host.tl.label == "13 / 240 @ 32 fps"  # hovering does not move the player


def test_speed_is_a_vertical_slider_with_five_stops_and_a_button_naming_the_speed(
    engine: Engine, tmp_path: Path
):
    """#128: the footer's speed button reads the speed ("1x"); its popover is a vertical
    slider, 4x at the top to 0.25x at the foot, stops evenly spaced (a log2 scale). A
    pick snaps to the nearest stop and is live, never a submit; an agent's speed between
    stops (1.5x) sits between them."""
    submit_video(engine, {"speed": 1.5})
    host = VideoHost(HostContext(engine, None), "rena", VIDEO_MANIFEST, headless=True)
    bridge = host.bridge
    assert [(s["label"], s["fraction"]) for s in bridge.speedStops] == [
        ("4x", 1.0),
        ("2x", 0.75),
        ("1x", 0.5),
        ("0.5x", 0.25),
        ("0.25x", 0.0),
    ]
    assert bridge.speedLabel == "1.5x" and 0.64 < bridge.speedFraction < 0.65  # log2(6) / 4

    heard = []
    bridge.speedChanged.connect(lambda: heard.append(bridge.speedLabel))
    bridge.pickSpeed(0.7)  # nearest stop: 2x
    assert host.speed == 2.0 and bridge.speedLabel == "2x" and bridge.speedFraction == 0.75
    bridge.pickSpeed(-0.3)
    assert host.speed == 0.25 and bridge.speedLabel == "0.25x"
    bridge.pickSpeed(0.45)
    assert host.speed == 1.0 and bridge.speedLabel == "1x"
    assert heard == ["2x", "0.25x", "1x"]
    bridge.stepSpeed(1)  # the + key
    assert bridge.speedLabel == "2x"
    assert engine.live.document()["views"]["rena"]["params"]["speed"] == 1.5
    assert engine.live.undo_info()["depth"] == 1  # the agent's submit only


def test_clip_time_and_timecode_carry_hours():
    assert clip_time(3725.5) == "1:02:05.50" and timecode(3725.5, 25.0) == "01:02:05:12"
    assert clip_time(59.996) == "1:00.00" and timecode(0.0, 0.0) == "00:00:00:00"


def test_loop_hwdec_speed_and_the_selected_video_are_agent_parameters(
    engine: Engine, tmp_path: Path
):
    clear_pending()
    submit_video(engine, {"speed": 2.0, "loop": [0.5, 1.0], "hwdec": True, "selected": "take-b"})
    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)

    class Headless(VideoFactory):
        def create(self, ctx, view_id, manifest):
            return VideoHost(ctx, view_id, manifest, headless=True)

    reg.add_factory(Headless())
    host = reg.host("rena")
    host.receive("data", Collection(items=video_items(tmp_path / "video", "take-a", "take-b")))

    assert host.key == "take-b" and host.speed == 2.0 and host.loop == [0.5, 1.0] and host.hwdec
    assert emitted(ctx, "rena", "selection") == Selection(keys=["take-b"], last="take-b")

    # the agent changes the parameters: the host follows the patch
    doc = engine.live.document()
    doc["views"]["rena"]["params"] = {
        "speed": 0.5,
        "loop": None,
        "hwdec": False,
        "selected": "take-a",
    }
    engine.live.submit(doc, origin="agent:s1", label="slower, no loop, take-a")
    assert host.key == "take-a" and host.speed == 0.5 and host.loop is None and not host.hwdec
    assert emitted(ctx, "rena", "selection") == Selection(keys=["take-a"], last="take-a")

    # the user steps to the next video (PgDn) and the speed keys: live, not a submit
    assert host.step_item(1) == 1 and host.key == "take-b"
    assert emitted(ctx, "rena", "selection").last == "take-b"
    host.step_speed(1)
    assert host.speed == 1.0
    assert engine.live.document()["views"]["rena"]["params"]["speed"] == 0.5
    assert engine.live.undo_info()["depth"] == 2  # the two submits, nothing from the user

    # a loop the agent writes shows on the bar; the selected video survives a reload
    assert host.tl.snapshot()["loop"] == [-1.0, -1.0]
    doc = engine.live.document()
    doc["views"]["rena"]["params"]["loop"] = [0.2, 0.9]
    engine.live.submit(doc, origin="agent:s1", label="loop")
    assert host.tl.snapshot()["loop"] == [0.2, 0.9] and host.snapshot()["loop"] == [0.2, 0.9]


@needs_testdata
def test_a_gallery_follows_time_through_the_clock_and_emits_the_time_of_a_click(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two galleries at different frame phases (cam-a, cam-b) time-mapped two-way: a click
    in one marks the frame nearest in time in the other as its current item, which says
    nothing back. Following is not selecting (SPEC-6): the selection stays the user's own
    pick, and the current item is marked apart from it."""
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "run-001" / "cam-a", 12, "cam-a")
    copy_frames(proj.root / "runs" / "run-001" / "cam-b", 12, "cam-b")
    engine = Engine(project=proj, jobs=InlineJobManager())
    clear_pending()
    fakes: dict[str, FakeGallery] = {}

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes[view_id] = FakeGallery()
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[view_id])

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(FileTreeFactory())
    reg.add_factory(Factory())
    reg.host("fege")

    doc = engine.live.document()
    for vid, lid, cam, x in (("kobi", "l2", "cam-a", 300), ("tamu", "l3", "cam-b", 960)):
        doc["views"][vid] = {"type": "gallery", "label": cam, "layout": {"position": [x, 0]}}
        doc["links"][lid] = {
            "kind": "intaker",
            "scope": "pinned",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": vid, "port": "data"},
            "params": {
                "sources": ["runs/run-001"],
                "screener": ["@images"],
                "keys": rf"(?P<cam>{cam})/(?P<frame>\d{{5}})_\d+\.jpg$",
                "group_by": ["cam", "frame"],
            },
            "summary": {"sentence": f"The {cam} gallery shows the {cam} frames of run-001."},
        }
    doc["links"]["l7"] = {
        "kind": "time_map",
        "two_way": True,
        "source": {"view": "kobi", "port": "time"},
        "target": {"view": "tamu", "port": "time"},
        "params": {"from_timeline": "stamp", "to_timeline": "stamp"},
        "summary": {"sentence": "Each gallery highlights the frame nearest the other's time."},
    }
    engine.live.submit(doc, origin="agent:s1", label="cam-a and cam-b linked on time")
    front, rear = reg.host("kobi"), reg.host("tamu")
    front.pump(), rear.pump()

    fakes["kobi"].user_click(4)  # cam-a/00004 at FRAME_4

    assert emitted(ctx, "kobi", "time") == TimeValue(t=FRAME_4, timeline="stamp")
    assert fakes["kobi"].current() == 4  # the clicked frame is now the clock's frame here
    assert (
        fakes["tamu"].current() == 1 and fakes["tamu"].revealed[-1] == 1
    )  # cam-b/00001, 12.5 ms before
    assert fakes["tamu"].selected() == -1  # followed, not picked
    assert rear.snapshot()["current"] == {"index": 1, "key": "cam-b/00001"}
    assert emitted(ctx, "tamu", "time") is None and emitted(ctx, "tamu", "selection") is None

    fakes["tamu"].user_click(4)  # cam-b/00004: the cam-a frame nearest is 00010
    assert emitted(ctx, "tamu", "time") == TimeValue(t=REAR_4, timeline="stamp")
    assert fakes["tamu"].current() == 4
    assert fakes["kobi"].current() == 10 and fakes["kobi"].selected() == 4
    assert emitted(ctx, "kobi", "time") == TimeValue(t=FRAME_4, timeline="stamp")  # no echo


def test_a_time_on_no_named_timeline_is_the_videos_own(engine: Engine, tmp_path: Path):
    # a Python link's bare seconds (#176) name no timeline: the video takes them as its own
    submit_video(engine)
    host = VideoHost(HostContext(engine, None), "rena", VIDEO_MANIFEST, headless=True)
    host.receive("data", Collection(items=video_items(tmp_path / "video", "cam-a")))
    host.receive("time", TimeValue(t=FRAME_4, timeline=OWN_TIMELINE))
    assert host.frame == 4


def test_sound_is_muted_by_default_one_view_at_a_time_and_remembered_as_navigation(
    engine: Engine, tmp_path: Path
):
    """#216: two video views in one window (one HostContext) and one in another. The
    speaker unmutes a view and mutes the window's other one, each change written to its
    params as navigation (no undo step); the slider's volume likewise; the agent's params
    do the same and obey the same rule."""
    clear_pending()
    submit_video(engine, {}, "siba")
    ctx = HostContext(engine, None)
    other_window = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)

    class Headless(VideoFactory):
        def create(self, ctx, view_id, manifest):
            return VideoHost(ctx, view_id, manifest, headless=True)

    reg.add_factory(Headless())
    rena, siba = reg.host("rena"), reg.host("siba")
    far = VideoHost(other_window, "rena", VIDEO_MANIFEST, headless=True)
    items = video_items(tmp_path / "video", "cam-a")
    for h in (rena, siba, far):
        h.receive("data", Collection(items=items))
    assert rena.muted and siba.muted and far.muted and rena.volume == 1.0
    snap = rena.snapshot()
    assert snap["muted"] is True and snap["volume"] == 1.0 and snap["has_audio"] is False
    assert snap["mpv_mute"] is True and snap["audio_output"] == ""
    depth = engine.live.undo_info()["depth"]

    # the speaker: rena gets sound; remembered in its params, not an undo step
    rena.bridge.toggleMute()
    assert not rena.muted and siba.muted
    assert engine.live.document()["views"]["rena"]["params"]["muted"] is False
    assert engine.live.undo_info()["depth"] == depth
    assert engine.live.journal.tail(1)[0].kind == "navigate"

    # siba's speaker takes it: rena is muted again and its params say so; the other
    # window's view is not this window's business
    far.set_muted(False, remember=True)
    siba.bridge.toggleMute()
    assert not siba.muted and rena.muted and not far.muted
    params = {vid: engine.live.document()["views"][vid]["params"] for vid in ("rena", "siba")}
    assert params["rena"]["muted"] is True and params["siba"]["muted"] is False
    assert engine.live.undo_info()["depth"] == depth

    # the volume slider: a press snaps to the 5 % grid and is remembered on release only;
    # the wheel steps the grid; the value is clamped
    siba.bridge.pickVolume(0.52, False)
    assert siba.volume == 0.5 and "volume" not in engine.live.document()["views"]["siba"]["params"]
    siba.bridge.pickVolume(0.52, True)
    assert engine.live.document()["views"]["siba"]["params"]["volume"] == 0.5
    siba.bridge.stepVolume(2)
    assert siba.volume == 0.6 and siba.bridge.volumeLabel == "60 %"
    assert engine.live.document()["views"]["siba"]["params"]["volume"] == 0.6
    siba.set_volume(1.7)
    assert siba.volume == 1.0 and volume_label(-0.2) == "0 %"
    assert engine.live.undo_info()["depth"] == depth

    # the agent: unmuting rena in the document mutes siba (remembered), a volume lands
    doc = engine.live.document()
    doc["views"]["rena"]["params"].update({"muted": False, "volume": 0.25})
    engine.live.submit(doc, origin="agent:s1", label="sound on A at a quarter")
    assert not rena.muted and rena.volume == 0.25 and siba.muted
    assert engine.live.document()["views"]["siba"]["params"]["muted"] is True
    assert engine.live.undo_info()["depth"] == depth + 1  # the agent's submit, nothing else

    # a disposed view leaves the window's rule
    siba.dispose()
    assert siba not in VideoHost._live
    far.dispose()
    rena.dispose()


def test_sound_plays_only_during_normal_playback(engine: Engine, tmp_path: Path):
    """#216: mpv's mute is the view's mute or any state that is not plain playback:
    paused, scrubbing, a seek or step in flight. The host writes it ahead of every
    seek, so a scrub or a received time never blips, and lifts it when playback runs."""
    submit_video(engine, {"muted": False})
    ctx = HostContext(engine, None)
    host = VideoHost(ctx, "rena", VIDEO_MANIFEST, headless=True)
    mpv = FakeMpv()
    host.mpv = mpv  # type: ignore[assignment]
    host.receive("data", Collection(items=video_items(tmp_path / "video", "cam-a")))
    host._on_loaded()
    assert not host.muted and host.playback_paused and host.effective_muted()
    assert ("muted", True) in mpv.writes and ("volume", 100.0) in mpv.writes

    # playing: the pause report lifts the mute
    mpv.props["paused"] = False
    host._on_paused()
    assert not host.effective_muted() and mpv.writes[-2:] == [("muted", False), ("volume", 100.0)]

    # a received time while playing: mute is queued before the pause and the seek
    mpv.writes.clear()
    host.receive("time", TimeValue(t=FRAME_8, timeline="stamp"))
    assert [w[0] for w in mpv.writes] == ["muted", "volume", "pause", "seek"]
    assert mpv.writes[0] == ("muted", True) and host.effective_muted()
    # ... and the landing (paused) keeps it
    mpv.props["paused"] = True
    host._on_paused()
    host._on_restart()
    assert host.effective_muted() and mpv.props["mute"] is True

    # a scrub while paused and a step: muted throughout; the view's own state unchanged
    host.tl.scrubStart(FRAME_4)
    assert host.tl.scrubbing and host.effective_muted()
    host.tl.scrubEnd(FRAME_4)
    host.step(1)
    assert host.effective_muted() and not host.muted
    host._on_restart()
    assert host.effective_muted()  # still paused

    # play again: sound; the user's mute wins over playback
    mpv.props["paused"] = False
    host._on_paused()
    assert not host.effective_muted() and mpv.props["mute"] is False
    host.set_muted(True, remember=True)
    assert host.effective_muted() and mpv.props["mute"] is True
    assert engine.live.document()["views"]["rena"]["params"]["muted"] is True
    assert host.snapshot()["mpv_mute"] is True and host.snapshot()["has_audio"] is False
    host._on_audio_track()
    assert host.has_audio and host.bridge.hasAudio
    host.dispose()


def released_video(engine: Engine, tmp_path: Path) -> tuple[VideoHost, FakeMpv, list]:
    """A loaded headless video on a fake mpv, with every time it emits recorded."""
    submit_video(engine)
    ctx = HostContext(engine, None)
    host = VideoHost(ctx, "rena", VIDEO_MANIFEST, headless=True)
    mpv = FakeMpv()
    host.mpv = mpv  # type: ignore[assignment]
    host.receive("data", Collection(items=video_items(tmp_path / "video", "cam-a")))
    host._on_loaded()
    times: list = []
    emit = host.emit

    def recording(port: str, value: object) -> None:
        if port == "time":
            times.append(value)
        emit(port, value)

    host.emit = recording  # type: ignore[method-assign]
    return host, mpv, times


def at(host: VideoHost, mpv: FakeMpv, frame: int | float) -> None:
    """mpv reports a position: a frame index, or seconds when a float."""
    mpv.props["position"] = frame if isinstance(frame, float) else host.timemap.pts[frame]
    host._on_position()


def test_a_released_video_shows_the_same_frame_after_its_resume(engine: Engine, tmp_path: Path):
    """SPEC-3: released, the video's node frees mpv's render context and the item reopens
    the file for the next one. Position changes nobody asked for in between (the old
    context's frame going, the reopening) are not emitted; once reopened the video seeks
    back to the frame it showed, emitted once on landing."""
    host, mpv, times = released_video(engine, tmp_path)
    assert host.release_when_paused
    at(host, mpv, 4)
    host.pause()
    host.on_release()
    at(host, mpv, 5)  # the old context's frame went
    host.on_restore()
    host.resume()
    at(host, mpv, 0.0)  # the file reopening for the new context
    mpv.writes.clear()
    host._on_loaded()
    assert mpv.writes[-1] == ("seek", host.timemap.pts[4])
    host._on_restart()
    at(host, mpv, 4)
    assert host.frame == 4
    assert times == [TimeValue(t=FRAME_4, timeline="stamp")] * 2  # before, and on landing


def test_a_video_playing_when_released_plays_on_from_the_same_frame(engine: Engine, tmp_path: Path):
    """Released while playing: no play before the file has reopened and landed back on
    the frame it rested on, so the clock never jumps toward frame 0."""
    host, mpv, times = released_video(engine, tmp_path)
    mpv.props["paused"] = False
    host._on_paused()
    at(host, mpv, 6)
    host.pause()  # off-screen: playback stops on frame 6
    mpv.props["paused"] = True
    host._on_paused()
    at(host, mpv, 6)
    host.on_release()
    at(host, mpv, 7)  # the old context's frame went
    mpv.writes.clear()
    host.on_restore()
    host.resume()
    assert ("pause", False) not in mpv.writes  # held until the landing
    at(host, mpv, 0.0)  # reopening
    host._on_loaded()
    assert mpv.writes[-1] == ("seek", host.timemap.pts[6])
    host._on_restart()
    at(host, mpv, 6)
    assert mpv.writes[-1] == ("pause", False)  # plays on from the frame it showed
    stamps = {v.t for v in times}
    assert stamps <= {host.time_value(6).t, host.time_value(None).t}
    assert times[-1] == host.time_value(6)


def test_a_time_received_while_released_is_where_the_video_comes_back(
    engine: Engine, tmp_path: Path
):
    host, mpv, _ = released_video(engine, tmp_path)
    host.pause()
    host.on_release()
    host.receive("time", TimeValue(t=FRAME_8, timeline="stamp"))
    host.on_restore()
    host.resume()
    mpv.writes.clear()
    host._on_loaded()
    assert mpv.writes[-1] == ("seek", host.timemap.pts[8])


def test_a_video_playing_when_paused_plays_on_when_resumed(engine: Engine, tmp_path: Path):
    host, mpv, _ = released_video(engine, tmp_path)
    mpv.props["paused"] = False
    host._on_paused()
    host.pause()  # not released (the grace did not run out): plays on at once
    assert mpv.writes[-1] == ("pause", True)
    mpv.writes.clear()
    host.resume()
    assert mpv.writes == [("pause", False)]
