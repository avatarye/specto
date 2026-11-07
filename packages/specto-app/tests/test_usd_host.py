"""Seam 2 (#139): the usd view host against the stub USD source, headless (no QML, no
FrameItem). The host spawns one frame-source process per view, loads the stage it
shows (the picked item of its collection, else the first) and steps through the rest
one stage at a time; health walks the source's start phases; the PRIMS outliner is fed
from ``query_tree``; the VIEW panel's speed and World origin drive the camera seam; the
view pauses its source when culled and kills it after 60 s paused, relaunching on
resume."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from app_doc_helpers import pin

from specto_app.views.usd_view import USD_MANIFEST, UsdHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection, Selection

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
    return Engine(project=None, jobs=InlineJobManager())


def stages(folder: Path, *names: str) -> Collection:
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for name in names:
        (folder / f"{name}.usda").write_text(STAGE, encoding="utf-8")
        items.append(Item(key=name, parts={".usda": str(folder / f"{name}.usda")}))
    return Collection(items=items)


def submit_usd(engine: Engine, params: dict | None = None) -> None:
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "usd", "label": "World", "params": params or {}}
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="usd")


@pytest.fixture
def host(engine: Engine, qapp):
    from specto_app.hosts import HostContext

    submit_usd(engine)
    h = UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True)
    yield h
    h.dispose()


def health(host: UsdHost) -> dict[str, str]:
    return host.ctx.health_of(host.view_id)


def test_a_picked_stage_starts_one_source_and_loads_it(host: UsdHost, tmp_path: Path, spin):
    assert host.source is None and health(host)["status"] == "paused"

    host.receive("data", stages(tmp_path / "w", "valley", "stage-a"))

    assert host.source is not None and host.key == "valley"
    spin(until=lambda: health(host)["status"] == "ok")
    snap = host.snapshot()
    assert snap["path"].endswith("valley.usda") and snap["renderer"] == "stub"
    assert snap["selected"] == {"index": 0, "key": "valley"}
    assert snap["source"]["loaded"].endswith("valley.usda")


def test_health_walks_the_start_phases_to_ok(host: UsdHost, tmp_path: Path, spin):
    seen: list[tuple[str, str]] = []
    host.ctx.health_listeners.append(lambda vid: seen.append(tuple(health(host).values())[:2]))

    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok")

    assert seen[0] == ("loading", "starting renderer")
    assert ("loading", "composing valley.usda") in seen
    assert ("loading", "first frame") in seen
    assert seen[-1] == ("ok", "valley.usda")


def test_stepping_loads_one_stage_at_a_time_in_the_same_source(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stages(tmp_path / "w", "valley", "stage-a", "stage-b"))
    spin(until=lambda: health(host)["status"] == "ok")
    pid = host.source.proc.pid

    assert host.step_item(1) == 1
    spin(until=lambda: (host.snapshot()["source"].get("loaded") or "").endswith("stage-a.usda"))
    assert host.source.proc.pid == pid and host.step_state() == {"index": 1, "count": 3}
    assert host.step_item(5) == 2 and host.step_item(1) == 2  # stops at the end, no wrap

    # a pick names the stage (a prim key carries its item key before the '#')
    host.receive("selection", Selection(keys=["valley#/World/Sky"], last="valley#/World/Sky"))
    assert host.key == "valley"
    spin(until=lambda: (host.snapshot()["source"].get("loaded") or "").endswith("valley.usda"))
    assert host.ctx.links.output_of("kobi", "selection") is None  # steps are not prim picks


def test_a_collection_without_stages_closes_the_source(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok")
    proc = host.source.proc

    host.receive("data", Collection(items=[Item(key="notes", parts={".md": "notes.md"})]))

    assert host.source is None and health(host)["status"] == "paused"
    assert proc.wait(timeout=10) == 0


def names(rows: list[dict]) -> list[str]:
    return [
        ("  " * r["depth"]) + (r["name"] if r["kind"] == "prim" else f"{r['count']} more")
        for r in rows
    ]


def test_the_prims_outliner_shows_the_stage_tree_lazily_and_paged(
    host: UsdHost, tmp_path: Path, spin
):
    host.receive("data", stages(tmp_path / "w", "valley"))
    # on load: the root prims, the default prim open on its children
    spin(until=lambda: len(host.outliner_rows()) > 1)
    assert names(host.outliner_rows()) == [
        "World", "  Blocks", "  Sky", "  Cameras", "  Vehicles",
    ]  # fmt: skip
    sky = host.outliner_rows()[2]
    assert sky == {
        "kind": "prim", "path": "/World/Sky", "name": "Sky", "type": "DomeLight",
        "depth": 1, "count": 0, "expanded": False,
    }  # fmt: skip

    host.toggle_row("/World/Vehicles")  # 250 vans: the first page, then "150 more"
    spin(until=lambda: len(host.outliner_rows()) > 5)
    rows = names(host.outliner_rows())
    assert rows[5:7] == ["    Van_000", "    Van_001"] and rows[-1] == "    150 more"
    assert len(rows) == 5 + 100 + 1
    host.more_rows("/World/Vehicles")
    spin(until=lambda: names(host.outliner_rows())[-1] == "    50 more")
    host.toggle_row("/World/Vehicles")
    assert names(host.outliner_rows()) == [
        "World", "  Blocks", "  Sky", "  Cameras", "  Vehicles",
    ]  # fmt: skip

    host.set_tree_filter("block_0")  # the source's search, flat
    spin(until=lambda: len(host.outliner_rows()) == 3)
    assert [r["path"] for r in host.outliner_rows()] == [
        f"/World/Blocks/Block_0{i}" for i in (1, 2, 3)
    ]
    host.set_tree_filter("")
    assert len(host.outliner_rows()) == 5


def test_the_view_panel_and_the_viewport_drive_the_camera_seam(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok")
    cam = host.camera
    # the stage's bounds and up axis went in; the first look went to the source
    assert ("load", [[-60.0, -40.0, 0.0], [60.0, 40.0, 12.0]], "Z") in cam.calls
    spin(until=lambda: host.snapshot()["source"].get("camera"))
    first = host.snapshot()["source"]["camera"]
    assert first["target"] == [0.0, 0.0, 0.0] and first["cut"] is True

    b = host.bridge
    b.press(1, False, 0.0, 0.0)
    b.drag(5, -2)
    b.release(1)
    b.press(4, False, 0.0, 0.0)
    b.release(4)
    b.press(8, False, 0.0, 0.0)  # a button the camera does not use
    b.wheel(120)
    b.key("w", True)
    b.key("w", False)
    b.focusLost()
    b.frame()  # F (or the footer's target) with nothing selected
    b.worldOrigin()
    assert cam.calls[-11:] == [
        ("press", "left", False), ("drag", 5.0, -2.0), ("release", "left"),
        ("press", "middle", False), ("release", "middle"),
        ("wheel", 120.0), ("key", "w", True), ("key", "w", False), ("release_keys",),
        ("frame_all",), ("world_origin",),
    ]  # fmt: skip

    b.setSpeedLog2(1.0, False)  # live while the slider moves: no submit
    assert cam.camera_speed == 2.0 and "camera_speed" not in host.params
    b.setSpeedLog2(2.0, True)  # released: one user submit
    assert cam.camera_speed == 4.0 and host.params["camera_speed"] == 4.0
    b.setSpeedLog2(9.0, True)
    assert host.camera_speed == 8.0  # clamped to 1/8 .. 8


def test_the_panels_keep_their_state_in_the_view_params(host: UsdHost, spin):
    assert host.panel_state("prims") == {"collapsed": False, "width": 240}
    host.bridge.setPanelCollapsed("prims", True)
    host.bridge.setPanelWidth("view", 999)
    assert host.params["panels"] == {
        "prims": {"collapsed": True, "width": 240},
        "view": {"collapsed": False, "width": 480},
    }
    # the agent's (or an undo's) params come back in
    doc = host.ctx.live.document()
    doc["views"]["kobi"]["params"]["panels"] = {"view": {"collapsed": True, "width": 180}}
    doc["views"]["kobi"]["params"]["camera_speed"] = 0.5
    host.ctx.live.submit(doc, origin="agent:s1", label="panels")
    host.on_patch([{"op": "replace", "path": "/views/kobi/params"}], {})
    assert host.panel_state("view") == {"collapsed": True, "width": 180}
    assert host.panel_state("prims") == {"collapsed": False, "width": 240}
    assert host.camera_speed == 0.5 and host.camera.camera_speed == 0.5


def test_the_frame_follows_the_viewport_size_capped_by_max_pixels(
    host: UsdHost, tmp_path: Path, spin
):
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok")
    host.viewport_resized(800, 450, 1.5)
    spin(until=lambda: host.snapshot()["source"]["width"] == 1200)
    assert host.snapshot()["source"]["height"] == 675
    host.viewport_resized(4000, 2000, 1.0)  # 8 MP: down to max_pixels (1920 x 1080)
    spin(until=lambda: host.snapshot()["source"]["width"] != 1200)
    s = host.snapshot()["source"]
    assert s["width"] * s["height"] <= 1920 * 1080 and s["width"] == 2 * s["height"]


def test_a_viewport_size_reported_while_the_source_starts_reaches_it_once_ready(
    host: UsdHost, tmp_path: Path, spin
):
    """#180: the viewport settles between the side panels while the renderer is still
    starting; the source, spawned at the size known then, takes the new one on `ready`
    instead of keeping the old aspect until a panel is toggled."""
    host.receive("data", stages(tmp_path / "w", "valley"))
    assert host.source is not None and host.ready is None
    host.viewport_resized(1005, 850, 1.0)  # VIEW panel open, PRIMS collapsed
    spin(until=lambda: health(host)["status"] == "ok")
    spin(until=lambda: host.snapshot()["source"]["width"] == 1005)
    assert host.snapshot()["source"]["height"] == 850


def test_a_culled_view_pauses_its_source_and_kills_it_after_the_idle_time(
    engine: Engine, tmp_path: Path, spin
):
    from specto_app.hosts import HostContext

    submit_usd(engine)
    host = UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True, idle_kill_s=0.5)
    try:
        host.receive("data", stages(tmp_path / "w", "valley", "stage-a"))
        spin(until=lambda: health(host)["status"] == "ok")
        first = host.source.proc

        host.pause()  # off-screen: the source stops stepping, the process stays
        assert host.snapshot()["source"]["paused"] is True
        host.resume()
        assert host.snapshot()["source"]["paused"] is False and host.source.proc is first

        host.pause()
        spin(until=lambda: host.source is None, timeout=5)  # paused 0.5 s: killed
        assert first.wait(timeout=10) == 0 and host.snapshot()["source"]["killed"] is True
        host.step_item(1)  # stepping while killed waits for the resume
        assert host.source is None

        host.resume()  # relaunched; it loads the stage the view shows now
        spin(until=lambda: host.source is not None and health(host)["status"] == "ok")
        assert host.source.proc.pid != first.pid
        assert host.snapshot()["source"]["loaded"].endswith("stage-a.usda")
    finally:
        host.dispose()


def test_a_source_that_dies_puts_the_view_in_error(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok")
    host.source.proc.kill()
    spin(until=lambda: health(host)["status"] == "error")
    # TerminateProcess on Windows (exit code 1), SIGKILL on POSIX
    assert health(host)["message"].startswith(("the renderer exited", "the renderer was killed"))
    assert host.source is None


def test_a_stage_picked_later_is_the_one_shown(host: UsdHost, tmp_path: Path, spin):
    """One stage at a time, the last picked: a pick that grows the collection shows the
    stage it added; unpicking another one keeps the stage shown."""
    a, c, b = stages(tmp_path / "w", "stage-a", "valley", "stage-b").items
    host.receive("data", Collection(items=[b]))
    assert host.key == "stage-b"
    host.receive("data", Collection(items=[a, b]))  # stage-a picked next
    assert host.key == "stage-a" and host.step_state() == {"index": 0, "count": 2}
    host.receive("data", Collection(items=[a, b, c]))  # then the depot world
    assert host.key == "valley" and host.step_state() == {"index": 2, "count": 3}
    host.receive("data", Collection(items=[b, c]))  # stage-a unpicked
    assert host.key == "valley"
    host.step_item(-1)
    host.receive("data", Collection(items=[b, c]))  # a stage stepped to stays put
    assert host.key == "stage-b"


def test_the_status_bar_names_the_usd_keys_while_the_view_is_hovered():
    from specto_app.canvas.controller import GESTURES, status_hint

    info = {"id": "kobi", "type": "usd", "health": "ok", "healthMessage": "world.usda"}
    keys = (
        "PageUp/PageDown previous / next item, Home/End first / last  ·  "
        "drag turns and walks, right-drag looks (W A S D Q E fly, wheel sets speed), "
        "middle-drag pans, wheel dollies, Alt+drag orbits, F frames"
    )
    assert status_hint(info, hovered=True) == f"kobi: world.usda  ·  {keys}  ·  {GESTURES}"


def test_the_source_command_picks_the_renderer_process_for_a_view(monkeypatch):
    """One function chooses the source process: specto-usd-source with rtx when the view
    asks for rtx, or for auto on a Windows or Linux machine with ovrtx (#137); with Storm
    when it asks for storm, or for auto otherwise, on the machine's OpenUSD build (#143);
    the stub only where this machine has no Storm either. On macOS auto is Storm even
    with ovrtx reported. An explicit rtx asks for no capabilities (detecting ovrtx starts
    a child Python)."""
    monkeypatch.setattr(sys, "platform", "win32")
    from specto_app.views.usd_view import source_command
    from specto_core.machine import MachineCapability

    def view(renderer: str) -> dict:
        return {"type": "usd", "params": {"renderer": renderer}}

    def never() -> dict:
        raise AssertionError("an explicit rtx needs no capabilities")

    def caps(ovrtx: bool, storm: bool = True):
        return lambda: {
            "ovrtx": MachineCapability(name="ovrtx", present=ovrtx),
            "storm": MachineCapability(name="storm", present=storm),
        }

    def module(argv: list[str]) -> str:
        return argv[argv.index("-m") + 1]

    def renderer(argv: list[str]) -> str:
        return argv[argv.index("--renderer") + 1]

    assert renderer(source_command(view("auto"), caps(ovrtx=True), 64, 48)) == "rtx"
    assert renderer(source_command(view("rtx"), caps(ovrtx=True), 64, 48)) == "rtx"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert renderer(source_command(view("auto"), caps(ovrtx=True), 64, 48)) == "storm"
    assert renderer(source_command(view("rtx"), caps(ovrtx=True), 64, 48)) == "rtx"
    monkeypatch.setattr(sys, "platform", "linux")
    argv = source_command(view("auto"), caps(ovrtx=True, storm=False), 64, 48)
    assert module(argv) == "specto_app.usd.source" and renderer(argv) == "rtx"
    assert argv[argv.index("--width") + 1] == "64" and argv[argv.index("--height") + 1] == "48"
    assert "--fresh-renderer" in argv  # no second stage in a running ovrtx renderer (#155)
    assert renderer(source_command(view("rtx"), never, 64, 48)) == "rtx"
    for asked in ("auto", "storm"):
        argv = source_command(view(asked), caps(ovrtx=False), 64, 48)
        assert module(argv) == "specto_app.usd.source" and renderer(argv) == "storm"
        assert argv[argv.index("--width") + 1] == "64" and "--openusd" in argv
        argv = source_command(view(asked), caps(ovrtx=False, storm=False), 64, 48)
        assert module(argv) == "specto_app.frames.stub_usd_source"


def test_a_faked_capability_runs_the_stub_source_not_its_renderer(monkeypatch):
    """SPECTO_FAKE_CAPABILITIES makes the choice RTX or Storm (the quotas, the notes),
    but the process is the stub USD source: a scripted flow never starts a renderer."""
    monkeypatch.setattr(sys, "platform", "win32")
    from specto_app.views.usd_view import source_command
    from specto_core.machine import MachineCapability

    stub = "specto_app.frames.stub_usd_source"

    def module(argv: list[str]) -> str:
        return argv[argv.index("-m") + 1]

    def stands_in_for(argv: list[str]) -> str:
        return argv[argv.index("--renderer") + 1]

    def caps(ovrtx: bool, storm: bool):
        return lambda: {
            "ovrtx": MachineCapability(name="ovrtx", present=ovrtx),
            "storm": MachineCapability(name="storm", present=storm),
        }

    def view(renderer: str) -> dict:
        return {"type": "usd", "params": {"renderer": renderer}}

    monkeypatch.setenv("SPECTO_DISABLE_CAPABILITIES", "")  # conftest disables both
    monkeypatch.setenv("SPECTO_FAKE_CAPABILITIES", "ovrtx")
    for asked in ("auto", "rtx"):
        argv = source_command(view(asked), caps(True, False), 64, 48)
        assert module(argv) == stub and stands_in_for(argv) == "rtx"
    monkeypatch.setenv("SPECTO_FAKE_CAPABILITIES", "storm")
    argv = source_command(view("storm"), caps(False, True), 64, 48)
    assert module(argv) == stub and stands_in_for(argv) == "storm"
    monkeypatch.setenv("SPECTO_DISABLE_CAPABILITIES", "storm")  # disabled wins
    assert module(source_command(view("storm"), caps(False, True), 64, 48)) != stub
    monkeypatch.setenv("SPECTO_DISABLE_CAPABILITIES", "")
    monkeypatch.delenv("SPECTO_FAKE_CAPABILITIES")
    assert module(source_command(view("auto"), caps(True, False), 64, 48)) != stub
