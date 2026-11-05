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
from specto_app.usd import source as source_mod
from specto_app.usd import stage as stage_mod
from specto_app.usd.collision import BuildCancelled
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
BARRELS = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Z"
)

def Xform "World"
{
    def Cylinder "Barrel" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
    }

    def Cylinder "Keg" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
    }
}
"""


class StubRenderer:
    """Records what the source asks; a view mode in ``slow`` waits for ``release``."""

    name = "stub"

    def __init__(self) -> None:
        self.slow: set[str] = set()
        self.entered = threading.Event()  # a slow switch began
        self.release = threading.Event()  # it may end
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
            self.entered.set()
            self.release.wait(10.0)
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


@pytest.fixture
def barrel_stage(tmp_path: Path) -> Path:
    path = tmp_path / "barrels.usda"
    path.write_text(BARRELS, encoding="utf-8", newline="\n")
    return path


class Builds:
    """``stage.build_overlay`` under the test's hand: ``hold`` keeps a build going until
    ``release`` (or until it is cancelled, which it notices between colliders and, with
    ``linger``, takes that long to act on); ``fail`` builds raise. It records each build's
    thread and stage file, how many were cancelled and how many ran at once."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.hold = False
        self.release = threading.Event()
        self.started = threading.Event()
        self.ended = threading.Event()
        self.linger = 0.0
        self.fail = 0
        self.made: list[tuple[str, str]] = []
        self.cancelled = 0
        self.most_at_once = 0
        self._running = 0
        self._lock = threading.Lock()
        build = stage_mod.build_overlay

        def held_build(stage, up_axis="Y", t=None, cancelled=None):
            with self._lock:
                self._running += 1
                self.most_at_once = max(self.most_at_once, self._running)
                self.made.append(
                    (threading.current_thread().name, Path(stage.GetRootLayer().realPath).name)
                )
            self.started.set()
            try:
                while self.hold and not self.release.wait(0.005):
                    if cancelled is not None and cancelled():
                        time.sleep(self.linger)  # the collider in hand comes first
                        self.cancelled += 1
                        raise BuildCancelled("cancelled")
                if self.fail:
                    self.fail -= 1
                    raise RuntimeError("the hull blew up")
                return build(stage, up_axis, t, cancelled=cancelled)
            finally:
                with self._lock:
                    self._running -= 1
                self.ended.set()

        monkeypatch.setattr(stage_mod, "build_overlay", held_build)


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> Builds:
    return Builds(monkeypatch)


def load(src: control.FrameSourceProcess, path: Path, first: bool = True) -> None:
    if first:
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


def until(what, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if what():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def mode_event(src: control.FrameSourceProcess, mode: str) -> dict:
    """The next ``view_mode`` event that says ``mode``."""
    while True:
        event = src.wait_event("view_mode", timeout=10.0)
        if event["mode"] == mode:
            return event


def test_the_source_keeps_reading_while_a_slow_command_runs(source, renderer, collider_stage):
    load(source, collider_stage)
    renderer.slow.add("unlit")
    mode, mode_done = in_background(lambda: source.call("set_view_mode", mode="unlit"))
    assert renderer.entered.wait(5.0)  # the render thread is in the switch
    for i in range(2000):
        source.send("set_camera", **camera(float(i)), cut=False)
    t0 = time.monotonic()
    state = source.call("state", timeout=1.0)  # answered beside the switch
    assert time.monotonic() - t0 < 0.5
    assert not mode_done.is_set()
    assert state["view_mode"] == "lit"
    renderer.release.set()
    assert mode_done.wait(5.0)
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
    assert renderer.entered.wait(5.0)
    source.send("set_view_mode", mode="wireframe")
    source.send("set_view_mode", mode="lit")
    source.call("state", timeout=1.0)  # both read: they wait behind the switch together
    renderer.release.set()
    assert first_done.wait(5.0) and first == [{"mode": "unlit", "note": ""}]
    until(lambda: renderer.modes == ["unlit", "lit"])  # wireframe was never drawn
    assert source.call("state")["view_mode"] == "lit"


def test_the_collision_overlay_builds_beside_the_render_loop_once_a_stage(
    source, renderer, builds, collider_stage
):
    load(source, collider_stage)
    builds.hold = True
    answer, done = in_background(lambda: source.call("set_view_mode", mode="collision"))
    assert builds.started.wait(5.0)
    for i in range(10):  # each move is drawn while the overlay builds
        drawn = renderer.submits
        source.send("set_camera", **camera(float(i)), cut=False)
        until(lambda drawn=drawn: renderer.submits > drawn)
    assert not done.is_set()
    assert 9.0 in renderer.eyes()
    builds.release.set()
    assert done.wait(5.0)
    assert answer == [{"mode": "collision", "note": "1 collider"}]
    assert builds.made[0][0] != "usd-source"  # not on the render thread
    # kept for the stage: back to lit and to collision again builds nothing new
    source.call("set_view_mode", mode="lit")
    assert source.call("set_view_mode", mode="collision")["mode"] == "collision"
    assert renderer.modes == ["collision", "lit", "collision"]
    assert len(builds.made) == 1


def test_a_stage_opened_in_collision_renders_and_answers_while_the_overlay_builds(
    source, renderer, builds, collider_stage
):
    # a canvas saved in collision: the host asks for it right after the load, before the
    # stage's first frame; the overlay must not hold the first frame, a pick or a state
    builds.hold = True
    source.wait_event("ready")
    source.send("load", path=str(collider_stage))
    mode, mode_done = in_background(lambda: source.call("set_view_mode", mode="collision"))
    while source.wait_event("health", timeout=5.0).get("state") != "ok":
        pass  # the first frame comes while the overlay builds
    assert not mode_done.is_set()
    assert source.call("pick", x=0.0, y=0.0, timeout=1.0)["path"] == "/World/Crate"
    assert source.call("state", timeout=1.0)["view_mode"] == "lit"
    drawn = renderer.submits
    source.send("set_camera", **camera(3.0), cut=False)
    until(lambda: renderer.submits > drawn)
    assert not mode_done.is_set()
    builds.release.set()
    assert mode_done.wait(5.0)
    assert mode == [{"mode": "collision", "note": "1 collider"}]
    assert source.call("state", timeout=1.0)["view_mode"] == "collision"


def test_collision_asked_with_a_load_builds_the_new_stage_off_the_render_thread(
    source, renderer, builds, collider_stage, barrel_stage
):
    load(source, collider_stage)
    source.call("set_view_mode", mode="collision")  # the first stage's overlay is built
    source.call("set_view_mode", mode="lit")
    source.poll()  # the events so far
    renderer.slow.add("unlit")
    source.send("set_view_mode", mode="unlit")
    assert renderer.entered.wait(5.0)
    source.send("load", path=str(barrel_stage))
    source.send("set_view_mode", mode="collision")
    source.call("state", timeout=1.0)  # both read: the render thread takes them in one turn
    renderer.release.set()
    assert mode_event(source, "collision") == {"mode": "collision", "note": "2 colliders"}
    barrel_builds = [thread for thread, stage in builds.made if stage == "barrels.usda"]
    assert barrel_builds and "usd-source" not in barrel_builds


def test_a_load_mid_build_answers_the_old_ask_stops_its_build_and_stays_lit(
    source, renderer, builds, collider_stage, barrel_stage
):
    load(source, collider_stage)
    builds.hold = True
    answer, done = in_background(lambda: source.call("set_view_mode", mode="collision"))
    assert builds.started.wait(5.0)
    load(source, barrel_stage, first=False)
    assert done.wait(1.0), "the ask for the old stage is left waiting"
    assert isinstance(answer[0], control.SourceError) and "new stage" in str(answer[0])
    assert builds.ended.wait(2.0) and builds.cancelled == 1  # the old stage's build stopped
    assert source.call("state", timeout=1.0)["view_mode"] == "lit"
    assert "collision" not in renderer.modes
    assert [stage for _, stage in builds.made] == ["crates.usda"]


def test_one_build_runs_at_a_time_and_the_newest_wins(source, builds, collider_stage, barrel_stage):
    load(source, collider_stage)
    builds.hold, builds.linger = True, 0.3  # the old build takes a while to stop
    in_background(lambda: source.call("set_view_mode", mode="collision"))
    assert builds.started.wait(5.0)
    load(source, barrel_stage, first=False)
    builds.hold = False
    answer = source.call("set_view_mode", mode="collision", timeout=5.0)
    assert answer == {"mode": "collision", "note": "2 colliders"}
    assert builds.cancelled == 1 and builds.most_at_once == 1
    assert [stage for _, stage in builds.made] == ["crates.usda", "barrels.usda"]


def test_switching_away_mid_build_and_back_reuses_the_running_build(
    source, renderer, builds, collider_stage
):
    load(source, collider_stage)
    builds.hold = True
    first, first_done = in_background(lambda: source.call("set_view_mode", mode="collision"))
    building = source.wait_event("health", timeout=2.0)
    assert building == {"state": "building collision", "message": "1 collider"}
    t0 = time.monotonic()
    assert source.call("set_view_mode", mode="lit", timeout=1.0)["mode"] == "lit"
    assert time.monotonic() - t0 < 0.5  # lit does not wait on the build
    assert first_done.wait(1.0) and first == [{"mode": "lit", "note": ""}]
    assert source.wait_event("health", timeout=1.0)["state"] == "ok"  # the label is off
    again, again_done = in_background(lambda: source.call("set_view_mode", mode="collision"))
    builds.release.set()
    assert again_done.wait(5.0) and again[0]["mode"] == "collision"
    assert len(builds.made) == 1
    assert renderer.modes == ["lit", "collision"]


def test_a_build_that_fails_is_said_once_and_a_later_ask_builds_again(
    source, builds, collider_stage
):
    load(source, collider_stage)
    builds.fail = 1
    with pytest.raises(control.SourceError, match="the hull blew up"):
        source.call("set_view_mode", mode="collision", timeout=5.0)
    warning = source.wait_event("warning", timeout=1.0)
    assert "collision overlay" in warning["message"] and "the hull blew up" in warning["message"]
    assert source.call("set_view_mode", mode="collision", timeout=5.0) == {
        "mode": "collision",
        "note": "1 collider",
    }
    assert len(builds.made) == 2
    assert source.call("state")["view_mode"] == "collision"


def test_a_build_that_never_ends_is_answered_at_the_bound_and_stopped(
    source, builds, collider_stage, monkeypatch
):
    monkeypatch.setattr(source_mod, "OVERLAY_TIMEOUT_S", 0.5)
    load(source, collider_stage)
    builds.hold = True  # never released
    t0 = time.monotonic()
    with pytest.raises(control.SourceError, match=r"collision overlay took longer than 0\.5 s"):
        source.call("set_view_mode", mode="collision", timeout=2.5)
    assert time.monotonic() - t0 < 1.5
    assert builds.ended.wait(2.0) and builds.cancelled == 1
    assert source.call("state", timeout=1.0)["view_mode"] == "lit"


def test_the_source_ends_with_no_build_left_running(source, builds, collider_stage):
    load(source, collider_stage)
    builds.hold = True  # never released
    in_background(lambda: source.call("set_view_mode", mode="collision"))
    assert builds.started.wait(5.0)
    t0 = time.monotonic()
    source.close()
    assert time.monotonic() - t0 < 3.0
    assert builds.ended.is_set() and builds.cancelled == 1
    assert not [t for t in threading.enumerate() if t.name == "usd-collision" and t.is_alive()]
