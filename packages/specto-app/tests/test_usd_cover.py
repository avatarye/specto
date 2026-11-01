"""Seam 2 (#156): while a usd view loads a stage it shows a loading label (the file's name
and the current phase: starting the renderer, compiling shaders, opening the stage, the
first frame, streaming textures), never the previous stage's picture or a partial first
frame. The label stays from the open or reload until the first complete frame of the
new stage: after ``loaded``, once the viewport shows the frame the source's ``ok``
names (#166: never counted from the frame on screen then, which a settled renderer may
never pass). Headless against the stub USD source; the viewport's frames come in
through the bridge as the FrameItem reports them."""

from __future__ import annotations

from pathlib import Path

import pytest
from app_doc_helpers import pin

from specto_app.frames.ring import RingReader
from specto_app.views.usd_view import USD_MANIFEST, UsdHost, cover_words, stub_launcher
from specto_core import Engine
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection

STAGE = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Z"
)
def Xform "World" {}
"""


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    return Engine(project=None, jobs=InlineJobManager(), capabilities=dict)


def stages(folder: Path, *names: str) -> Collection:
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for name in names:
        (folder / f"{name}.usda").write_text(STAGE, encoding="utf-8")
        items.append(Item(key=name, parts={".usda": str(folder / f"{name}.usda")}))
    return Collection(items=items)


def slow_rtx_stub(view: dict, caps, width: int, height: int) -> list[str]:
    """The stub standing in for rtx (its start phase is compiling shaders), its load
    phases 150 ms apart so each one can be seen."""
    return [*stub_launcher(width, height), "--phase-ms", "150", "--renderer", "rtx"]


def settled_stub(view: dict, caps, width: int, height: int) -> list[str]:
    """The stub as Storm draws (#166): two frames after a load, then nothing until the
    next change, as a renderer that has settled."""
    return [*stub_launcher(width, height), "--renderer", "storm", "--settle-frames", "2"]


class Viewport:
    """The FrameItem's part, headless: shows the ring's newest frame as soon as it is
    out, as the native item does when the source wakes it."""

    def __init__(self, host: UsdHost) -> None:
        from PySide6.QtCore import QTimer

        self.host = host
        self.reader: RingReader | None = None
        self.seq: int | None = None
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll)
        self.timer.start(1)

    def poll(self) -> None:
        if self.reader is None and self.host.ready is not None:
            self.reader = RingReader.open(self.host.ready["ring"])
        frame = self.reader.latest() if self.reader is not None else None
        if frame is not None and frame.seq != self.seq:
            self.seq = frame.seq
            self.host.bridge.frameShown(frame.seq)

    def close(self) -> None:
        self.timer.stop()
        if self.reader is not None:
            self.reader.close()


def make_host(engine: Engine, **kw) -> UsdHost:
    from specto_app.hosts import HostContext

    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "usd", "label": "World", "params": {}}
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="usd")
    kw.setdefault("source", slow_rtx_stub)
    return UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True, **kw)


@pytest.fixture
def host(engine: Engine, qapp):
    h = make_host(engine)
    yield h
    h.dispose()


def health(host: UsdHost) -> str:
    return host.ctx.health_of(host.view_id)["status"]


def labels(host: UsdHost) -> list[tuple[str, str] | None]:
    """Every label the view shows from now on, as (file, phase); None: the picture."""
    seen: list[tuple[str, str] | None] = []

    def record() -> None:
        b = host.bridge
        label = (b.coverFile, b.coverPhase) if b.covered else None
        if not seen or seen[-1] != label:
            seen.append(label)

    host.bridge.coverChanged.connect(record)
    return seen


def test_the_label_names_the_file_and_walks_the_phases_to_the_picture(
    host: UsdHost, tmp_path: Path, spin
):
    seen = labels(host)
    host.receive("data", stages(tmp_path / "w", "hillside"))
    assert host.cover == {"file": "hillside.usda", "phase": "starting renderer"}

    spin(until=lambda: host.cover is None, timeout=10)

    phases = [label[1] for label in seen if label is not None]
    assert {label[0] for label in seen if label is not None} == {"hillside.usda"}
    assert phases[0] == "starting renderer"
    for phase in ("compiling shaders (cached)", "opening stage", "rendering the first frame"):
        assert phase in phases
    assert phases.index("opening stage") < phases.index("rendering the first frame")
    assert seen[-1] is None and health(host) == "ok"  # headless: no frames to wait for
    assert host.snapshot()["cover"] is None


def test_the_label_stays_until_the_frame_ok_names_is_shown(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stages(tmp_path / "w", "hillside"))
    host.bridge.frameShown(1)  # the viewport shows frames (the previous stage's)
    spin(until=lambda: health(host) == "ok", timeout=10)

    # the first complete frame is out: the label stays until the viewport shows it
    first = host.cover_frame
    assert first is not None and first > 1
    assert host.cover == {"file": "hillside.usda", "phase": "streaming textures"}
    host.bridge.frameShown(first - 1)
    assert host.cover is not None and host.bridge.covered
    host.bridge.frameShown(first)
    assert host.cover is None and not host.bridge.covered


def test_a_settled_renderer_lifts_the_label(engine: Engine, tmp_path: Path, qapp, spin):
    """#166: Storm draws a few frames after a load and then idles; the viewport has
    shown the newest of them by the time ``ok`` is taken. The label lifts on that frame,
    not on frames that never come."""
    host = make_host(engine, source=settled_stub)
    viewport = Viewport(host)
    try:
        host.receive("data", stages(tmp_path / "w", "valley", "hillside"))
        spin(until=lambda: host.cover is None, timeout=10)
        assert health(host) == "ok" and viewport.seq is not None

        host.step_item(1)  # a second stage, the source already settled
        assert host.cover is not None
        spin(until=lambda: host.cover is None, timeout=10)
        assert host.snapshot()["source"]["loaded"].endswith("hillside.usda")
    finally:
        viewport.close()
        host.dispose()


def test_a_frame_before_the_stage_loaded_does_not_lift_the_label(
    host: UsdHost, tmp_path: Path, spin
):
    host.receive("data", stages(tmp_path / "w", "hillside"))
    spin(until=lambda: host.ready is not None, timeout=10)
    reader = RingReader.open(host.ready["ring"])
    try:
        shown: list[int] = []
        # the frames drawn before the stage loaded keep coming while it opens (to the
        # first frame's phase: the stub loads it a phase later)
        while host.cover["phase"] != "rendering the first frame":
            frame = reader.latest()
            if frame is not None:
                host.bridge.frameShown(frame.seq)
                shown.append(frame.seq)
            assert host.cover is not None
            spin(10)
        spin(until=lambda: health(host) == "ok", timeout=10)
        assert shown and host.cover is not None and host.cover_frame > max(shown)
    finally:
        reader.close()


def test_stepping_covers_the_previous_stage_at_once(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stages(tmp_path / "w", "valley", "hillside"))
    spin(until=lambda: host.cover is None, timeout=10)

    host.step_item(1)

    assert host.cover == {"file": "hillside.usda", "phase": "opening stage"}
    spin(until=lambda: host.cover is None, timeout=10)
    assert host.snapshot()["source"]["loaded"].endswith("hillside.usda")


def test_a_relaunched_source_covers_its_reload(engine: Engine, tmp_path: Path, qapp, spin):
    host = make_host(engine, idle_kill_s=0.3)
    try:
        host.receive("data", stages(tmp_path / "w", "hillside"))
        spin(until=lambda: host.cover is None, timeout=10)
        host.pause()
        spin(until=lambda: host.source is None, timeout=5)

        host.resume()
        spin(until=lambda: host.source is not None)
        assert host.cover == {"file": "hillside.usda", "phase": "starting renderer"}
        spin(until=lambda: host.cover is None, timeout=10)
    finally:
        host.dispose()


def test_a_renderer_that_dies_while_loading_drops_the_label(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stages(tmp_path / "w", "hillside"))
    spin(until=lambda: host.ready is not None)
    host.source.send("crash", code=0xC0000005, stderr=[])
    spin(until=lambda: host.failure is not None, timeout=10)
    assert host.cover is None


def test_no_stage_no_label(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stages(tmp_path / "w", "hillside"))
    host.receive("data", Collection(items=[]))
    assert host.cover is None


@pytest.mark.parametrize(
    ("state", "message", "words"),
    [
        ("starting", "", "starting renderer"),
        ("compiling shaders", "(first run, about 3 min)", "compiling shaders (first run, about 3 min)"),  # noqa: E501
        ("compiling shaders", "", "compiling shaders (cached)"),
        ("composing", "world.usd", "opening stage"),
        ("first frame", "", "rendering the first frame"),
    ],
)  # fmt: skip
def test_the_phases_in_words(state, message, words):
    assert cover_words(state, message) == words
