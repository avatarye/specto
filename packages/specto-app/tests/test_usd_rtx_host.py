"""Seam 2 (#137): the usd view's host starts ``specto-usd-source`` with the RTX renderer
when this machine has the ovrtx capability (``params.renderer`` auto or rtx), and the
stub USD source otherwise until Storm lands (#143). The source renders with the fake
ovrtx (``tests/fake_ovrtx``); usd-core is real."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from test_usd_host import stages, submit_usd

from specto_app.hosts import HostContext
from specto_app.views.usd_view import UsdFactory
from specto_core import Engine
from specto_core.jobs import InlineJobManager
from specto_core.machine import MachineCapability

FAKE = Path(__file__).resolve().parent / "fake_ovrtx"


def engine_with(ovrtx: bool) -> Engine:
    cap = MachineCapability(name="ovrtx", present=ovrtx, version="0.6.0.fake" if ovrtx else None)
    return Engine(project=None, jobs=InlineJobManager(), capabilities=lambda: {"ovrtx": cap})


@pytest.fixture(autouse=True)
def fake_ovrtx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(FAKE), os.environ.get("PYTHONPATH", "")]))
    monkeypatch.setenv("FAKE_OVRTX_LOG", str(tmp_path / "ovrtx.log"))


def open_view(engine: Engine, params: dict | None = None):
    submit_usd(engine, params)
    ctx = HostContext(engine, None)
    host = UsdFactory().create(ctx, "kobi", engine.plugins.view("usd"))
    return host


@pytest.mark.parametrize("renderer", ["auto", "rtx"])
def test_with_ovrtx_the_view_renders_with_rtx(renderer, tmp_path: Path, qapp, spin):
    host = open_view(engine_with(ovrtx=True), {"renderer": renderer})
    try:
        host.receive("data", stages(tmp_path / "w", "valley"))
        spin(until=lambda: host.ctx.health_of("kobi")["status"] == "ok", timeout=30)
        snap = host.snapshot()
        assert snap["renderer"] == "rtx"
        assert snap["up_axis"] == "Z"
        assert snap["source"]["renderer"] == "rtx" and snap["source"]["steps"] > 0
    finally:
        host.dispose()


def test_without_ovrtx_auto_keeps_the_stub_source(tmp_path: Path, qapp, spin):
    host = open_view(engine_with(ovrtx=False))
    try:
        host.receive("data", stages(tmp_path / "w", "valley"))
        spin(until=lambda: host.ctx.health_of("kobi")["status"] == "ok", timeout=30)
        assert host.snapshot()["renderer"] == "stub"
    finally:
        host.dispose()


def test_the_view_navigates_with_the_3d_views_camera_in_the_stages_frame(
    tmp_path: Path, qapp, spin
):
    # the stub source remembers the last set_camera; its stage is Z-up
    host = open_view(engine_with(ovrtx=False))
    try:
        host.receive("data", stages(tmp_path / "w", "valley"))
        spin(until=lambda: host.ctx.health_of("kobi")["status"] == "ok", timeout=30)
        spin(until=lambda: host.snapshot()["source"].get("camera"))
        first = host.snapshot()["source"]["camera"]
        # the 3d view's origin look, turned into the Z-up stage: Z is up, the eye is
        # above the ground and the look goes to the world origin
        assert first["up"] == pytest.approx([0, 0, 1])
        assert first["target"] == pytest.approx([0, 0, 0])
        assert first["position"][2] > 0
        assert first["cut"] is True
        host.camera.press("right", False)  # a right-drag looks around from the eye
        host.camera.drag(120.0, 0.0)
        host.camera.release("right")
        spin(until=lambda: host.snapshot()["source"]["camera"]["target"] != first["target"])
        moved = host.snapshot()["source"]["camera"]
        assert moved["cut"] is False
        assert moved["position"] == pytest.approx(first["position"], abs=1e-6)
    finally:
        host.dispose()


def _held_step(tmp_path: Path) -> bool:
    log = tmp_path / "ovrtx.log"
    return log.exists() and '"hold_step"' in log.read_text(encoding="utf-8")


def _gone(pid: int) -> bool:
    import subprocess
    import sys

    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) not in out
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


@pytest.mark.parametrize(("hold", "within"), [(3, 15), (60, 20)], ids=["finishes", "killed"])
def test_closing_mid_first_frame_never_blocks_and_leaves_no_process(
    tmp_path: Path, qapp, spin, monkeypatch: pytest.MonkeyPatch, hold: int, within: int
):
    """SPEC-87: dispose with the source inside a long first-frame step returns at once;
    the source still ends, by itself after the step (3 s) or killed after the close
    timeout (a 60 s step)."""
    import time

    monkeypatch.setenv("FAKE_OVRTX_HOLD_STEP_S", str(hold))
    host = open_view(engine_with(ovrtx=True), {"renderer": "rtx"})
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: _held_step(tmp_path), timeout=30)
    pid = host.source.proc.pid
    started = time.perf_counter()
    host.dispose()
    assert time.perf_counter() - started < 0.1
    spin(until=lambda: _gone(pid), timeout=within)
