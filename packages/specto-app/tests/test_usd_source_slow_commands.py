"""A slow command never stops ``specto-usd-source`` (SPEC-33): while the render thread
works through one (a view mode switch, the collision overlay's build), the control
thread keeps reading, so camera moves collapse to the latest instead of filling the
pipe, queued view modes collapse to the last one asked, and the overlay builds beside
the render loop. In process: the source's own protocol over pipes, a stub renderer."""

from __future__ import annotations

import argparse
import os
import threading
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from specto_app.frames import control
from specto_app.usd import stage as stage_mod
from specto_app.usd.source import UsdSource

COLLIDERS = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Crate" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        double size = 2
    }
}
"""
SLOW_S = 2.0


class StubRenderer:
    """Records what the source asks; a view mode in ``slow`` takes ``SLOW_S``."""

    name = "stub"

    def __init__(self) -> None:
        self.slow: set[str] = set()
        self.modes: list[str] = []
        self.cameras: list[dict] = []
        self.submits = 0

    def load(self, path, up_axis, meters_per_unit, camera, width, height, times, lighting=None):
        self.size = (width, height)

    def camera_of(self, state, aspect):
        return state  # the source's CameraState as it is

    def set_camera(self, camera, cut=False) -> None:
        self.cameras.append(camera)

    def eyes(self) -> list[float]:
        """The x of each eye the source moved the camera to."""
        return [float(c.eye[0]) for c in self.cameras]

    def resize(self, width, height, camera) -> None:
        self.size = (width, height)

    def set_view_mode(self, mode, overlay=None) -> str:
        if mode in self.slow:
            time.sleep(SLOW_S)
        self.modes.append(mode)
        return ""

    def pick(self, x, y):
        return {"path": "/World/Crate", "position": [0.0, 0.0, 1.0], "normal": [0.0, 0.0, 1.0]}

    def submit(self, delta):
        self.submits += 1
        return self.submits

    def finish(self, op, publish) -> None:
        import numpy as np

        publish(np.zeros((self.size[1], self.size[0], 4), np.uint8))

    def close(self) -> None:
        pass


class InProcess:
    """A ``subprocess.Popen`` stand-in for :class:`control.FrameSourceProcess`: the
    source runs on a thread of this process, over pipes."""

    def __init__(self, source_of) -> None:
        to_source, self._host_out = os.pipe()
        self._host_in, from_source = os.pipe()
        self.stdin = os.fdopen(self._host_out, "wb", buffering=0)
        self.stdout = os.fdopen(self._host_in, "rb")
        self.stderr = None
        self.pid = os.getpid()
        self.returncode: int | None = None
        stdio = (os.fdopen(to_source, "rb", buffering=0), os.fdopen(from_source, "wb", buffering=0))
        self.source = source_of(stdio)
        self._out = stdio[1]
        self._thread = threading.Thread(target=self._run, name="usd-source", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.returncode = self.source.run()
        finally:
            self._out.close()  # the host's reader sees the source go

    def poll(self) -> int | None:
        return None if self._thread.is_alive() else self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        self._thread.join(timeout)
        return self.returncode

    def kill(self) -> None:
        self.source.stop()


@pytest.fixture
def renderer() -> StubRenderer:
    return StubRenderer()


@pytest.fixture
def source(renderer):
    args = argparse.Namespace(
        renderer="storm",
        max_pixels=64 * 36,
        width=64,
        height=36,
        settle_steps=4,
        fresh_renderer=False,
        name=f"specto-test-{uuid.uuid4().hex[:8]}",
    )
    proc = InProcess(lambda stdio: UsdSource(args, lambda: renderer, stdio))
    src = control.FrameSourceProcess(proc)  # type: ignore[arg-type]
    yield src
    src.close()


@pytest.fixture
def collider_stage(tmp_path: Path) -> Path:
    path = tmp_path / "crates.usda"
    path.write_text(COLLIDERS, encoding="utf-8", newline="\n")
    return path


def load(src: control.FrameSourceProcess, path: Path) -> None:
    src.wait_event("ready")
    src.send("load", path=str(path))
    while src.wait_event("health", timeout=20.0).get("state") != "ok":
        pass


def camera(x: float) -> dict:
    return {
        "position": [x, 0.0, 2.0],
        "target": [0.0, 0.0, 0.0],
        "up": [0.0, 0.0, 1.0],
        "fov_y": 0.9,
        "near": 0.1,
        "far": 1000.0,
    }


def in_background(fn):
    """Run ``fn`` on a thread; ``box`` gets its result (or exception), ``done`` is set."""
    box: list = []
    done = threading.Event()

    def run() -> None:
        try:
            box.append(fn())
        except Exception as e:
            box.append(e)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return box, done


def test_the_source_keeps_reading_while_a_slow_command_runs(source, renderer, collider_stage):
    load(source, collider_stage)
    renderer.slow.add("unlit")
    mode, mode_done = in_background(lambda: source.call("set_view_mode", mode="unlit"))
    time.sleep(0.2)  # the render thread is in the switch
    for i in range(2000):
        source.send("set_camera", **camera(float(i)), cut=False)
    t0 = time.monotonic()
    state = source.call("state", timeout=1.0)  # answered beside the switch
    assert time.monotonic() - t0 < 0.5
    assert not mode_done.is_set()
    assert state["view_mode"] == "lit"
    assert mode_done.wait(SLOW_S + 5)
    assert mode == [{"mode": "unlit", "note": ""}]
    # the moves made meanwhile come out as the latest one, not one each
    until(lambda: 1999.0 in renderer.eyes())
    assert len(renderer.cameras) < 20


def test_view_modes_asked_during_a_slow_switch_collapse_to_the_last(
    source, renderer, collider_stage
):
    load(source, collider_stage)
    renderer.slow.add("unlit")
    first, first_done = in_background(lambda: source.call("set_view_mode", mode="unlit"))
    time.sleep(0.2)  # the render thread is in the switch
    middle, middle_done = in_background(lambda: source.call("set_view_mode", mode="wireframe"))
    time.sleep(0.1)
    last, last_done = in_background(lambda: source.call("set_view_mode", mode="lit"))
    for done in (first_done, middle_done, last_done):
        assert done.wait(SLOW_S + 5)
    assert renderer.modes == ["unlit", "lit"]  # wireframe was never drawn
    assert first == [{"mode": "unlit", "note": ""}]
    # a mode given way answers with the one in effect
    assert middle == last == [{"mode": "lit", "note": ""}]
    assert source.call("state")["view_mode"] == "lit"


def test_the_collision_overlay_builds_beside_the_render_loop_once_a_stage(
    source, renderer, collider_stage, monkeypatch
):
    builds = []
    build = stage_mod.build_overlay

    def slow_build(*args, **kwargs):
        builds.append(threading.current_thread().name)
        time.sleep(SLOW_S)
        return build(*args, **kwargs)

    monkeypatch.setattr(stage_mod, "build_overlay", slow_build)
    load(source, collider_stage)
    answer, done = in_background(lambda: source.call("set_view_mode", mode="collision"))
    time.sleep(0.3)  # the build is under way
    before = renderer.submits
    for i in range(10):  # moves keep the loop stepping
        source.send("set_camera", **camera(float(i)), cut=False)
        time.sleep(0.05)
    assert not done.is_set()
    assert renderer.submits > before  # frames go on while the overlay builds
    assert 9.0 in renderer.eyes()
    assert done.wait(SLOW_S + 5)
    assert answer == [{"mode": "collision", "note": "1 collider"}]
    assert builds != ["usd-source"]  # not on the render thread
    # cached a stage: back to lit and to collision again builds nothing new
    source.call("set_view_mode", mode="lit")
    assert source.call("set_view_mode", mode="collision")["mode"] == "collision"
    assert renderer.modes == ["collision", "lit", "collision"]
    assert len(builds) == 1


def test_a_stage_opened_in_collision_renders_and_answers_while_the_overlay_builds(
    source, renderer, collider_stage, monkeypatch
):
    # a canvas saved in collision: the host asks for it right after the load, before the
    # stage's first frame; the overlay must not hold the first frame, a pick or a state
    build = stage_mod.build_overlay

    def slow_build(*args, **kwargs):
        time.sleep(SLOW_S)
        return build(*args, **kwargs)

    monkeypatch.setattr(stage_mod, "build_overlay", slow_build)
    source.wait_event("ready")
    source.send("load", path=str(collider_stage))
    mode, mode_done = in_background(lambda: source.call("set_view_mode", mode="collision"))
    t0 = time.monotonic()
    while source.wait_event("health", timeout=SLOW_S).get("state") != "ok":
        pass
    assert time.monotonic() - t0 < SLOW_S and not mode_done.is_set()  # first frame first
    assert source.call("pick", x=0.0, y=0.0, timeout=1.0)["path"] == "/World/Crate"
    assert source.call("state", timeout=1.0)["view_mode"] == "lit"
    before = renderer.submits
    source.send("set_camera", **camera(3.0), cut=False)
    until(lambda: renderer.submits > before)
    assert not mode_done.is_set()
    assert mode_done.wait(SLOW_S + 5)
    assert mode == [{"mode": "collision", "note": "1 collider"}]
    assert source.call("state", timeout=1.0)["view_mode"] == "collision"


def until(what, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if what():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")
