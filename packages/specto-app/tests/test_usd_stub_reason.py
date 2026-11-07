"""SPEC-26: a usd view that falls back to the stub USD source because this machine has no
USD renderer says why. The stub's test pattern used to show with health ``ok`` and
nothing else. Now health stays ``ok``, but carries a hint naming what is missing, from
the capability detection (ovrtx, OpenUSD imaging). The user gets one notice, and ``snap``
and the agent see the hint too. A fallback a test or flow asked for (a capability
disabled or faked) says nothing, and neither does a real renderer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from app_doc_helpers import pin

from specto_app.views.usd_view import USD_MANIFEST, UsdHost, stub_reason
from specto_core import Engine
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection
from specto_core.machine import MachineCapability

NO_OVRTX = "ovrtx is not installed: install the specto-app[rtx] extra (needs pypi.nvidia.com)"
NO_STORM = "no OpenUSD imaging build at C:/nowhere: build OpenUSD with imaging"
STAGE = '#usda 1.0\n(\n    defaultPrim = "World"\n)\ndef Xform "World" {}\n'


def caps(ovrtx: bool = False, storm: bool = False) -> dict[str, MachineCapability]:
    return {
        "ovrtx": MachineCapability(name="ovrtx", present=ovrtx, reason="" if ovrtx else NO_OVRTX),
        "storm": MachineCapability(name="storm", present=storm, reason="" if storm else NO_STORM),
    }


def view(renderer: str = "auto") -> dict:
    return {"type": "usd", "params": {"renderer": renderer}}


@pytest.fixture
def detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine whose capabilities were detected, none disabled or faked (the test
    conftest disables both)."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SPECTO_DISABLE_CAPABILITIES", "")
    monkeypatch.setenv("SPECTO_FAKE_CAPABILITIES", "")


def test_the_reason_names_every_renderer_that_is_missing(detected):
    reason = stub_reason(view("auto"), lambda: caps())
    assert reason.startswith("no USD renderer")
    assert NO_OVRTX in reason and NO_STORM in reason
    # asked for Storm: only Storm's reason
    reason = stub_reason(view("storm"), lambda: caps(ovrtx=True))
    assert NO_STORM in reason and NO_OVRTX not in reason


def test_no_reason_when_a_real_renderer_runs_or_the_stub_was_asked_for(detected, monkeypatch):
    assert stub_reason(view("auto"), lambda: caps(ovrtx=True)) == ""
    assert stub_reason(view("auto"), lambda: caps(storm=True)) == ""
    assert stub_reason(view("storm"), lambda: caps(storm=True)) == ""

    def never() -> dict:
        raise AssertionError("an explicit rtx needs no capabilities")

    # an explicit rtx never falls back: its health says why it cannot start
    assert stub_reason(view("rtx"), never) == ""
    monkeypatch.setenv("SPECTO_DISABLE_CAPABILITIES", "ovrtx,storm")  # a test or flow
    assert stub_reason(view("auto"), lambda: caps()) == ""
    monkeypatch.setenv("SPECTO_DISABLE_CAPABILITIES", "")
    monkeypatch.setenv("SPECTO_FAKE_CAPABILITIES", "storm")
    assert stub_reason(view("auto"), lambda: caps(storm=True)) == ""


def host_on(engine: Engine, tmp_path: Path, informed: list[str]) -> UsdHost:
    from specto_app.hosts import HostContext

    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "usd", "label": "World", "params": {}}
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="usd")
    ctx = HostContext(engine, None)
    ctx.inform = informed.append  # type: ignore[method-assign]
    host = UsdHost(ctx, "kobi", USD_MANIFEST, headless=True)
    folder = tmp_path / "w"
    folder.mkdir()
    for name in ("valley", "ridge"):
        (folder / f"{name}.usda").write_text(STAGE, encoding="utf-8")
    items = [Item(key=n, parts={".usda": str(folder / f"{n}.usda")}) for n in ("valley", "ridge")]
    host.receive("data", Collection(items=items))
    return host


def test_a_view_without_a_renderer_says_why_in_its_health_and_once_in_a_notice(
    detected, tmp_path: Path, monkeypatch, qapp, spin
):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine = Engine(project=None, jobs=InlineJobManager())
    engine._capabilities = caps()  # detected: neither renderer here
    informed: list[str] = []
    host = host_on(engine, tmp_path, informed)
    try:
        spin(until=lambda: host.ctx.health_of("kobi")["status"] == "ok")
        health = host.ctx.health_of("kobi")
        assert health["message"] == "valley.usda"  # health stays ok, the file named
        assert NO_OVRTX in health["hint"] and NO_STORM in health["hint"]
        assert host.snapshot()["fallback"] == health["hint"]
        assert len(informed) == 1 and NO_OVRTX in informed[0]
        host.step_item(1)  # another stage, same source: the hint stays, no second notice
        spin(until=lambda: host.ctx.health_of("kobi")["message"] == "ridge.usda")
        assert NO_OVRTX in host.ctx.health_of("kobi")["hint"] and len(informed) == 1
    finally:
        host.dispose()


def test_a_view_whose_renderer_is_faked_says_nothing(
    detected, tmp_path: Path, monkeypatch, qapp, spin
):
    """A scripted flow's machine with ovrtx (faked): the stub plays it, silently."""
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SPECTO_FAKE_CAPABILITIES", "ovrtx")
    engine = Engine(project=None, jobs=InlineJobManager())
    engine._capabilities = caps(ovrtx=True)
    informed: list[str] = []
    host = host_on(engine, tmp_path, informed)
    try:
        spin(until=lambda: host.ctx.health_of("kobi")["status"] == "ok")
        assert host.ctx.health_of("kobi")["hint"] == "" and not informed
        assert host.snapshot()["fallback"] == ""
    finally:
        host.dispose()
