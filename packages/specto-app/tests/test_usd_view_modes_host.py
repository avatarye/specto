"""Seam 2 (#153): the usd view host's view modes against the stub USD source, headless.
The mode is the view param ``view_mode`` (one user submit from the footer's pill, or
the agent's); the host sends ``set_view_mode`` once the stage is loaded and the source
offers the mode, shows what the source answers, and asks again for each stage it loads
(each load starts lit). A mode the stage does not offer leaves the picture lit; snap
reports ``view_mode`` and ``view_modes`` as the 3d view does."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_usd_host import health, stages, submit_usd

from specto_app.hosts import HostContext
from specto_app.views import usd_view
from specto_app.views.usd_view import USD_MANIFEST, UsdHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine = Engine(project=None, jobs=InlineJobManager())
    submit_usd(engine)
    h = UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True)
    yield h
    h.dispose()


def source_mode(host: UsdHost) -> str | None:
    return host.snapshot()["source"].get("view_mode")


def loaded(host: UsdHost, tmp_path: Path, spin, *names: str) -> None:
    host.receive("data", stages(tmp_path / "w", *(names or ("valley",))))
    spin(until=lambda: health(host)["status"] == "ok" and bool(host.view_modes))


def test_the_menu_lists_the_modes_the_stage_offers(host: UsdHost, tmp_path, spin):
    assert host.bridge.viewModes == [{"mode": "lit", "label": "Lit"}]  # no stage yet
    loaded(host, tmp_path, spin)
    assert [r["mode"] for r in host.bridge.viewModes] == ["lit", "unlit", "wireframe", "collision"]
    assert host.bridge.viewModes[3] == {"mode": "collision", "label": "Collision"}
    assert host.bridge.viewMode == "lit" and host.bridge.viewModeLabel == "Lit"
    snap = host.snapshot()
    assert snap["view_mode"] == "lit"
    assert snap["view_modes"] == ["lit", "unlit", "wireframe", "collision"]


def test_picking_a_mode_is_one_submit_and_the_picture_follows(host: UsdHost, tmp_path, spin):
    loaded(host, tmp_path, spin)
    undo_depth = host.ctx.live.undo_info()["depth"]
    host.bridge.setViewMode("wireframe")
    assert host.params["view_mode"] == "wireframe"
    assert host.ctx.live.undo_info()["depth"] == undo_depth + 1
    spin(until=lambda: host.bridge.viewMode == "wireframe")
    assert source_mode(host) == "wireframe"
    assert host.bridge.viewModeLabel == "Wireframe"
    host.bridge.setViewMode("wireframe")  # the same mode again: nothing
    assert host.ctx.live.undo_info()["depth"] == undo_depth + 1


def test_the_agent_sets_the_mode_through_the_param(host: UsdHost, tmp_path, spin):
    loaded(host, tmp_path, spin)
    doc = host.ctx.live.document()
    doc["views"]["kobi"]["params"]["view_mode"] = "collision"
    host.ctx.live.submit(doc, origin="agent:s1", label="collision")
    host.on_patch([{"op": "add", "path": "/views/kobi/params/view_mode"}], {})
    spin(until=lambda: host.bridge.viewMode == "collision")
    assert host.bridge.viewModeNote == "3 colliders"
    snap = host.snapshot()
    assert (snap["view_mode"], snap["view_mode_note"]) == ("collision", "3 colliders")


def test_the_mode_is_kept_across_stages(host: UsdHost, tmp_path, spin):
    loaded(host, tmp_path, spin, "valley", "stage-a")
    host.set_view_mode("unlit")
    spin(until=lambda: source_mode(host) == "unlit")
    host.step_item(1)
    # the new stage loads lit, then the host asks for the mode again
    spin(until=lambda: (host.snapshot()["source"].get("loaded") or "").endswith("stage-a.usda"))
    spin(until=lambda: source_mode(host) == "unlit" and host.bridge.viewMode == "unlit")


def test_a_mode_the_stage_does_not_offer_leaves_it_lit(host: UsdHost, tmp_path, spin):
    loaded(host, tmp_path, spin)
    host.view_modes = [
        {"mode": "lit", "available": True, "reason": ""},
        {"mode": "collision", "available": False, "reason": "no prims with CollisionAPI"},
    ]
    host.set_view_mode("collision")
    spin(ms=300)
    assert host.params["view_mode"] == "collision"  # asked, and kept for the next stage
    assert source_mode(host) == "lit" and host.bridge.viewMode == "lit"
    assert [r["mode"] for r in host.bridge.viewModes] == ["lit"]  # collision is off the menu
    assert host.snapshot()["view_modes"] == ["lit"]


@pytest.fixture
def slow_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp):
    """A host whose stub source takes 1.5 s to build its collision overlay (SPEC-33)."""
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SPECTO_USD_STUB_COLLISION_S", "1.5")
    engine = Engine(project=None, jobs=InlineJobManager())
    submit_usd(engine)
    h = UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True)
    yield h
    h.dispose()


def test_a_collision_overlay_building_is_said_on_the_chip_and_in_health(
    slow_host: UsdHost, tmp_path, spin
):
    host = slow_host
    loaded(host, tmp_path, spin)
    host.set_view_mode("collision")
    # the chip names the mode on its way, not the one it leaves
    assert host.bridge.viewModeLabel == "Collision…"
    spin(until=lambda: health(host)["message"] == "building the collision overlay 3 colliders")
    assert health(host)["status"] == "loading" and host.bridge.viewMode == "lit"
    spin(until=lambda: host.bridge.viewMode == "collision")
    assert host.bridge.viewModeLabel == "Collision"
    spin(until=lambda: health(host)["status"] == "ok")


def test_the_chip_follows_the_source_after_the_host_stops_waiting(
    slow_host: UsdHost, tmp_path, spin, monkeypatch
):
    host = slow_host
    monkeypatch.setattr(usd_view, "MODE_TIMEOUT_S", 0.3)
    loaded(host, tmp_path, spin)
    host.set_view_mode("collision")
    spin(ms=600)  # the host's wait is over; the source still builds
    spin(until=lambda: source_mode(host) == "collision")
    # the chip and snap say what the picture shows
    spin(until=lambda: host.bridge.viewMode == "collision")
    assert host.bridge.viewModeLabel == "Collision"
    assert host.snapshot()["view_mode"] == "collision"
