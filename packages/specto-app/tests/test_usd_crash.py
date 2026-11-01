"""Seam 2 (#155): a usd view whose renderer process dies, headless against the stub USD
source. However the source dies, the view says why in plain words (the exit code read
as a crash, the GPU's own words when it lost the device), shows the renderer's last
error lines from its stderr, and offers Retry and Open with Storm instead of a dead
picture."""

from __future__ import annotations

import signal
import sys
from pathlib import Path

import pytest
from app_doc_helpers import pin

from specto_app.views.usd_view import USD_MANIFEST, UsdHost, exit_reason, stub_launcher
from specto_core import Engine
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection
from specto_core.machine import MachineCapability

STAGE = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Z"
)
def Xform "World" {}
"""
ACCESS_VIOLATION = 0xC0000005
#: how the stub's ACCESS_VIOLATION crash reads here: an NTSTATUS on Windows, SIGSEGV on POSIX
CRASHED = (
    "the renderer crashed (access violation, 0xC0000005)"
    if sys.platform == "win32"
    else "the renderer crashed (segmentation fault, SIGSEGV)"
)
RTX_LOG = [
    "[Info] [omni.rtx] RTX ready",
    "[Error] [omni.rtx] Resource upload failed (error code: 2): a device lost, out of memory,"
    " or an unexpected bug",
    "[Error] [gpu.foundation] VkResult: ERROR_DEVICE_LOST",
    "[Fatal] Waiting on Semaphore 4 for longer than 60 sec: TextureStreaming copy engine",
    "[Info] shutting down",
]


def caps(storm: bool = True):
    return lambda: {
        "ovrtx": MachineCapability(name="ovrtx", present=True),
        "storm": MachineCapability(name="storm", present=storm),
    }


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    return Engine(project=None, jobs=InlineJobManager(), capabilities=caps())


def stage(folder: Path, name: str = "hillside") -> Collection:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.usda").write_text(STAGE, encoding="utf-8")
    return Collection(items=[Item(key=name, parts={".usda": str(folder / f"{name}.usda")})])


class Launches:
    """A source command that runs the stub as whichever renderer the view asks for, and
    records the renderer each launch was for."""

    def __init__(self) -> None:
        self.renderers: list[str] = []

    def __call__(self, view: dict, caps, width: int, height: int) -> list[str]:
        self.renderers.append((view.get("params") or {}).get("renderer") or "auto")
        argv = stub_launcher(width, height)
        return [*argv[:3], "--renderer", "rtx" if self.renderers[-1] != "storm" else "storm",
                *argv[3:]]  # fmt: skip


@pytest.fixture
def launches() -> Launches:
    return Launches()


@pytest.fixture
def host(engine: Engine, launches: Launches, qapp):
    from specto_app.hosts import HostContext

    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "usd", "label": "World", "params": {}}
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="usd")
    h = UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True, source=launches)
    yield h
    h.dispose()


def health(host: UsdHost) -> dict[str, str]:
    return host.ctx.health_of(host.view_id)


def crash(host: UsdHost, spin, code: int = ACCESS_VIOLATION, stderr: list[str] = RTX_LOG):
    host.source.send("crash", code=code, stderr=stderr)
    spin(until=lambda: host.failure is not None, timeout=10)


def test_a_renderer_crash_says_why_with_its_last_errors(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stage(tmp_path / "w"))
    spin(until=lambda: health(host)["status"] == "ok")

    crash(host, spin)

    assert host.source is None
    failure = host.failure
    assert failure["reason"] == CRASHED
    assert failure["cause"] == "the GPU device was lost: out of video memory, or a driver reset"
    assert failure["errors"] == RTX_LOG[1:4]  # its last error lines, the info left out
    assert failure["retry"] is True and failure["storm"] is True
    assert health(host) == {
        "status": "error",
        "message": f"{CRASHED}: the GPU device was lost: out of video memory, or a driver reset",
        "hint": "",
    }
    assert host.snapshot()["failure"] == failure
    bridge = host.bridge
    assert bridge.failed and bridge.failureReason == failure["reason"]
    assert bridge.failureErrors == failure["errors"] and bridge.canStorm


def test_retry_starts_the_renderer_again_on_the_same_stage(
    host: UsdHost, launches: Launches, tmp_path: Path, spin
):
    host.receive("data", stage(tmp_path / "w"))
    spin(until=lambda: health(host)["status"] == "ok")
    crash(host, spin)

    host.bridge.retry()

    assert host.failure is None and not host.bridge.failed
    spin(until=lambda: health(host)["status"] == "ok")
    assert host.snapshot()["source"]["loaded"].endswith("hillside.usda")
    assert launches.renderers == ["auto", "auto"]


def test_open_with_storm_keeps_the_choice_and_relaunches_with_storm(
    host: UsdHost, launches: Launches, tmp_path: Path, spin
):
    host.receive("data", stage(tmp_path / "w"))
    spin(until=lambda: health(host)["status"] == "ok")
    crash(host, spin)

    host.bridge.openWithStorm()

    assert host.ctx.live.document()["views"]["kobi"]["params"]["renderer"] == "storm"
    assert host.failure is None
    spin(until=lambda: health(host)["status"] == "ok")
    assert launches.renderers == ["auto", "storm"]

    crash(host, spin, code=1, stderr=["Traceback (most recent call last):", "RuntimeError: x"])
    assert host.failure["reason"] == "the renderer exited (code 1)"
    assert host.failure["cause"] == ""
    assert host.failure["errors"] == ["Traceback (most recent call last):", "RuntimeError: x"]
    assert host.failure["storm"] is False  # Storm is what died: Retry only


def test_without_storm_on_the_machine_only_retry_is_offered(
    engine: Engine, host: UsdHost, tmp_path: Path, spin
):
    engine._capabilities = caps(storm=False)()
    host.receive("data", stage(tmp_path / "w"))
    spin(until=lambda: health(host)["status"] == "ok")
    crash(host, spin, stderr=[])
    assert host.failure["errors"] == [] and host.failure["storm"] is False


def test_a_source_closed_on_purpose_is_no_failure(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stage(tmp_path / "w"))
    spin(until=lambda: health(host)["status"] == "ok")
    proc = host.source.proc
    host.receive("data", Collection(items=[]))
    assert proc.wait(timeout=10) == 0
    spin(ms=200)
    assert host.failure is None and health(host)["status"] == "paused"


def test_a_new_stage_clears_the_failure(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stage(tmp_path / "w"))
    spin(until=lambda: health(host)["status"] == "ok")
    crash(host, spin)
    host.receive("data", stage(tmp_path / "w2", "valley"))
    assert host.failure is None
    spin(until=lambda: health(host)["status"] == "ok")


def test_the_flows_crash_knob_crashes_the_first_stub_only(
    host: UsdHost, tmp_path: Path, spin, monkeypatch: pytest.MonkeyPatch
):
    """SPECTO_USD_STUB_CRASH_MS (the seam-3 flow's): the first stub this process starts
    dies like the #155 world, the one Retry starts lives."""
    import os
    import tempfile

    marker = Path(tempfile.gettempdir()) / f"specto-usd-stub-crashed-{os.getpid()}"
    marker.unlink(missing_ok=True)
    monkeypatch.setenv("SPECTO_USD_STUB_CRASH_MS", "100")
    try:
        host.receive("data", stage(tmp_path / "w"))
        spin(until=lambda: host.failure is not None, timeout=10)
        assert host.failure["reason"] == CRASHED
        assert host.failure["cause"].startswith("the GPU device was lost")

        host.bridge.retry()
        spin(until=lambda: health(host)["status"] == "ok", timeout=10)
        spin(ms=400)
        assert host.failure is None and host.source is not None
    finally:
        marker.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("code", "words"),
    [
        (ACCESS_VIOLATION, "the renderer crashed (access violation, 0xC0000005)"),
        (ACCESS_VIOLATION - (1 << 32), "the renderer crashed (access violation, 0xC0000005)"),
        (0xC0000409, "the renderer crashed (fail-fast abort, 0xC0000409)"),
        (0xC00000FD, "the renderer crashed (stack overflow, 0xC00000FD)"),
        (0xC0000017, "the renderer crashed (out of memory, 0xC0000017)"),
        (0xC0001234, "the renderer crashed (0xC0001234)"),
        (3, "the renderer aborted (code 3)"),
        (1, "the renderer exited (code 1)"),
        (0, "the renderer stopped"),
        (None, "the renderer exited"),
        (-signal.SIGSEGV, "the renderer crashed (segmentation fault, SIGSEGV)"),
        (-signal.SIGABRT, "the renderer crashed (abort, SIGABRT)"),
        (-9, "the renderer was killed (signal 9)"),  # SIGKILL; the name is not on Windows
    ],
)
def test_exit_codes_read_in_plain_words(code, words):
    assert exit_reason(code) == words
