"""Seam 2 (#141): the usd view as a player, against the stub USD source, headless. The
stage's time codes are a ``usd`` timeline in seconds (timecode / timeCodesPerSecond); a
time in moves the source's timecode and the landed time goes out once; the footer's
scrubber emits timecodes; a root usd view's play drives the global clock; a static stage
has no transport and ignores time."""

from __future__ import annotations

from pathlib import Path

import pytest
from app_doc_helpers import pin

from specto_app.views.usd_view import USD_MANIFEST, UsdHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection, TimeValue
from specto_core.timebase import OWN_TIMELINE

#: a box that crosses the ground in two seconds: 48 timecodes at 24 per second
ANIMATED = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Z"
    startTimeCode = 0
    endTimeCode = 48
    timeCodesPerSecond = 24
)
def Xform "World"
{
    def Cube "Box"
    {
        double3 xformOp:translate.timeSamples = { 0: (-6, 0, 1), 48: (6, 0, 1) }
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""
#: a Kit world's header: a range of -1 .. 0 and nothing time-sampled
STATIC = """#usda 1.0
(
    defaultPrim = "World"
    endTimeCode = 0
    startTimeCode = -1
    upAxis = "Z"
)
def Xform "World" {}
"""


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    return Engine(project=None, jobs=InlineJobManager())


def stage(folder: Path, name: str, text: str) -> Collection:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.usda"
    path.write_text(text, encoding="utf-8")
    return Collection(items=[Item(key=name, parts={".usda": str(path)})])


def submit_usd(engine: Engine, *, root: bool = False) -> None:
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "usd", "label": "World", "params": {}}
    if root:
        doc["root_view"] = "kobi"
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="usd")


def make_host(engine: Engine) -> UsdHost:
    from specto_app.hosts import HostContext

    return UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True)


@pytest.fixture
def host(engine: Engine, qapp):
    submit_usd(engine)
    h = make_host(engine)
    yield h
    h.dispose()


def emitted(host: UsdHost):
    return host.ctx.links.output_of(host.view_id, "time")


def source_timecode(host: UsdHost):
    return host.snapshot()["source"].get("timecode")


def loaded(host: UsdHost, spin) -> None:
    spin(until=lambda: host.ctx.health_of(host.view_id)["status"] == "ok" and host.stage)


def test_a_received_time_moves_the_stage_and_the_landed_time_goes_out_once(
    host: UsdHost, tmp_path: Path, spin
):
    host.receive("data", stage(tmp_path / "w", "drive", ANIMATED))
    loaded(host, spin)
    assert host.snapshot()["time"] == {
        "animated": True,
        "timeline": "usd",
        "range": [0.0, 48.0],
        "tcps": 24.0,
        "timecode": 0.0,
        "t": 0.0,
        "playing": False,
    }
    assert emitted(host) is None  # nothing moved yet

    host.receive("time", TimeValue(t=1.5, timeline="usd"))  # 1.5 s at 24 tc/s
    spin(until=lambda: source_timecode(host) == 36.0)
    assert emitted(host) == TimeValue(t=1.5, timeline="usd")
    assert host.snapshot()["time"]["timecode"] == 36.0

    host.receive("time", TimeValue(t=10.0, timeline="usd"))  # past the end: lands on it
    spin(until=lambda: source_timecode(host) == 48.0)
    assert emitted(host) == TimeValue(t=2.0, timeline="usd")

    host.receive("time", TimeValue(t=0.5, timeline="video"))  # not the stage's clock
    spin(60)
    assert source_timecode(host) == 48.0 and emitted(host) == TimeValue(t=2.0, timeline="usd")


def test_the_scrubber_emits_whole_timecodes_and_moves_the_stage(
    host: UsdHost, tmp_path: Path, spin
):
    host.receive("data", stage(tmp_path / "w", "drive", ANIMATED))
    loaded(host, spin)
    tl = host.tl
    assert (tl.tMin, tl.tMax, tl.timeline) == (0.0, 2.0, "usd")  # seconds on the usd timeline
    assert tl.label == "0 / 0-48 @ 24 tc/s"

    tl.scrubStart(0.52)  # 12.48 timecodes: the drag lands on whole ones
    assert emitted(host) == TimeValue(t=0.5, timeline="usd")
    tl.scrubTo(1.01)
    assert emitted(host) == TimeValue(t=24 / 24, timeline="usd")
    assert tl.label == "24 / 0-48 @ 24 tc/s"
    tl.scrubEnd(1.27)  # 30.48: 30
    assert emitted(host) == TimeValue(t=30 / 24, timeline="usd") and tl.t == 30 / 24
    spin(until=lambda: source_timecode(host) == 30.0)

    tl.step(1)  # Right: one timecode on
    assert emitted(host) == TimeValue(t=31 / 24, timeline="usd")
    tl.step(-40)  # clamped at the start
    assert emitted(host) == TimeValue(t=0.0, timeline="usd")
    spin(until=lambda: source_timecode(host) == 0.0)


def test_a_static_stage_has_no_transport_and_ignores_time(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stage(tmp_path / "w", "valley", STATIC))
    loaded(host, spin)
    assert host.snapshot()["time"]["animated"] is False and host.bridge.animated is False
    assert host.tl.label == ""

    host.receive("time", TimeValue(t=1.0, timeline="usd"))
    host.tl.scrubEnd(0.5)
    host.tl.step(1)
    host.tl.togglePlay()
    spin(60)
    assert emitted(host) is None and source_timecode(host) is None
    assert host.snapshot()["time"]["playing"] is False

    # stepping on to an animated stage brings the transport
    host.receive(
        "data",
        Collection(
            items=[
                *stage(tmp_path / "w", "valley", STATIC).items,
                *stage(tmp_path / "w", "drive", ANIMATED).items,
            ]
        ),
    )
    host.step_item(1)
    spin(until=lambda: host.time is not None and host.time.animated)
    assert host.bridge.animated is True and host.tl.label == "0 / 0-48 @ 24 tc/s"


def test_play_on_the_root_usd_view_drives_the_global_clock(
    engine: Engine, qapp, tmp_path: Path, spin
):
    submit_usd(engine, root=True)
    host = make_host(engine)
    try:
        clock = engine.links.global_clock
        host.receive("data", stage(tmp_path / "w", "drive", ANIMATED))
        loaded(host, spin)

        host.tl.togglePlay()
        assert host.tl.playing and host.snapshot()["time"]["playing"] is True
        spin(until=lambda: clock.now is not None and clock.now.t >= 0.25)  # 6 timecodes in
        assert clock.now.timeline == "usd" and (clock.now.t * 24).is_integer()
        host.tl.togglePlay()
        assert not host.tl.playing
        landed = host.timecode
        assert clock.now == TimeValue(t=landed / 24, timeline="usd") == emitted(host)
        spin(until=lambda: source_timecode(host) == landed)
        spin(100)
        assert host.timecode == landed  # paused: the clock stays

        host.tl.scrubEnd(1.9)  # near the end, play runs out and stops on the last timecode
        host.tl.togglePlay()
        spin(until=lambda: not host.tl.playing)
        assert clock.now == TimeValue(t=2.0, timeline="usd") and host.timecode == 48.0
        host.tl.togglePlay()  # at the end, play starts over
        spin(until=lambda: 0 < host.timecode < 48)
        host.tl.togglePlay()
    finally:
        host.dispose()


def test_a_time_map_link_drives_the_stage_from_a_video(engine: Engine, qapp, tmp_path: Path, spin):
    """The manifest's example: a video's ``video`` timeline onto the stage's ``usd`` one,
    offset 0.5 s; the video is the root view, so the stage follows the global clock."""
    from specto_app.hosts import HostContext, HostRegistry
    from specto_app.views.usd_view import UsdFactory

    class Headless(UsdFactory):
        def create(self, ctx, view_id, manifest):
            return UsdHost(ctx, view_id, manifest, headless=True)

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(Headless())
    doc = engine.live.document()
    doc["root_view"] = "tamu"
    doc["views"]["tamu"] = {"type": "video", "label": "Drive video", "params": {}}
    doc["views"]["kobi"] = {"type": "usd", "label": "World", "params": {}}
    pin(doc, "tamu"), pin(doc, "kobi")
    doc["links"]["l9"] = {
        "kind": "time_map",
        "source": {"view": "tamu", "port": "time"},
        "target": {"view": "kobi", "port": "time"},
        "params": {"from_timeline": "video", "to_timeline": "usd", "offset_ms": 500},
        "summary": {"sentence": "Scrubbing the video tamu moves the stage's time."},
    }
    engine.live.submit(doc, origin="agent:s1", label="video drives the stage")
    host = reg.host("kobi")
    try:
        host.receive("data", stage(tmp_path / "w", "drive", ANIMATED))
        loaded(host, spin)

        ctx.emit("tamu", "time", TimeValue(t=0.25, timeline="video"))  # a scrub of the video
        spin(until=lambda: source_timecode(host) == 18.0)  # (0.25 + 0.5) s * 24
        assert host.tl.label == "18 / 0-48 @ 24 tc/s"
        assert emitted(host) == TimeValue(t=0.75, timeline="usd")
        assert engine.links.global_clock.now == TimeValue(t=0.25, timeline="video")

        ctx.emit("tamu", "time", TimeValue(t=1.0, timeline="video"))
        spin(until=lambda: source_timecode(host) == 36.0)
    finally:
        reg.dispose_all()


def test_a_time_on_no_named_timeline_is_the_stages_own(host: UsdHost, tmp_path: Path, spin):
    # a Python link's bare seconds (#176) name no timeline: the stage takes them as its own
    host.receive("data", stage(tmp_path / "w", "drive", ANIMATED))
    loaded(host, spin)
    host.receive("time", TimeValue(t=1.5, timeline=OWN_TIMELINE))
    spin(until=lambda: source_timecode(host) == 36.0)
    assert emitted(host) == TimeValue(t=1.5, timeline="usd")
