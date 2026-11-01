"""``specto-usd-source`` rtx flavour against the fake ovrtx (#155): the two levers on a
stage switch. ``--fresh-renderer`` gives each stage after the first its own renderer
(the running one is closed, never handed a second stage); ``--texture-streaming`` sets
ovrtx's texture streaming mode at renderer creation."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from specto_app.frames import control

FAKE = Path(__file__).resolve().parent / "fake_ovrtx"
STAGE = '#usda 1.0\n(\n    defaultPrim = "World"\n    upAxis = "Z"\n)\ndef Xform "World" {}\n'


def calls(log: Path, name: str) -> list[dict]:
    if not log.exists():
        return []
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["call"] == name]


def spawn(log: Path, *args: str) -> control.FrameSourceProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(FAKE), env.get("PYTHONPATH", "")])
    env["FAKE_OVRTX_LOG"] = str(log)
    argv = [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx"]
    return control.FrameSourceProcess.spawn(
        [*argv, "--width", "64", "--height", "36", *args], env=env
    )


def load_ok(src: control.FrameSourceProcess, path: Path) -> None:
    src.send("load", path=str(path))
    deadline = time.monotonic() + 20
    while True:
        params = src.wait_event("health", timeout=max(0.1, deadline - time.monotonic()))
        if params.get("state") == "error":
            raise AssertionError(params)
        if params.get("state") == "ok":
            return


@pytest.fixture
def worlds(tmp_path: Path) -> list[Path]:
    paths = []
    for name in ("valley", "hillside", "valley_again"):
        path = tmp_path / f"{name}.usda"
        path.write_text(STAGE, encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


def test_a_fresh_renderer_takes_each_new_stage(tmp_path: Path, worlds: list[Path]):
    log = tmp_path / "ovrtx.log"
    src = spawn(log, "--fresh-renderer")
    try:
        src.wait_event("ready", timeout=20)
        for path in worlds:
            load_ok(src, path)
        assert src.call("state")["loaded"] == str(worlds[-1])
    finally:
        assert src.close() == 0
    assert len(calls(log, "Renderer")) == 3  # one per stage
    assert len(calls(log, "attach_ovstage")) == 3


def test_by_default_one_renderer_takes_the_stages_in_turn(tmp_path: Path, worlds: list[Path]):
    log = tmp_path / "ovrtx.log"
    src = spawn(log)
    try:
        src.wait_event("ready", timeout=20)
        for path in worlds:
            load_ok(src, path)
    finally:
        src.close()
    assert len(calls(log, "Renderer")) == 1 and len(calls(log, "attach_ovstage")) == 3


@pytest.mark.parametrize(("flag", "mode"), [("sync", 1), ("off", 0), ("async", None)])
def test_the_texture_streaming_mode_is_set_at_renderer_creation(
    tmp_path: Path, worlds: list[Path], flag: str, mode: int | None
):
    log = tmp_path / "ovrtx.log"
    src = spawn(log, "--texture-streaming", flag)
    try:
        src.wait_event("ready", timeout=20)
        load_ok(src, worlds[0])
    finally:
        src.close()
    (renderer,) = calls(log, "Renderer")
    assert renderer["config"].get("texture_streaming_mode") == mode
