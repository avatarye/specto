"""The usd view as the prompt-bar agent sees it (#142, seam 1): the standing reference
names its params with their choices, the system prompt says when to use one, ``snap``
reports the stage, renderer, frame size and camera (a fake host stands in for #137/#139),
and a scripted turn composes one. No Qt, no model, no renderer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from app_doc_helpers import pin

import specto_app.commands  # noqa: F401  (registers snap)
from specto_app.prompt.context import CanvasContext
from specto_app.prompt.reference import reference_text
from specto_app.prompt.scripted import ScriptedAgentSession
from specto_app.prompt.turn import TurnTools, run_turn
from specto_app.views.usd_meta import usd_snap_meta
from specto_core import CallContext, Engine, Project
from specto_core.agent_tools import AgentTools
from specto_core.links import CameraValue, Collection, FilesValue
from specto_core.machine import MachineCapability

PROMPT_BAR = CallContext(connection="prompt-bar", origin="agent:prompt")
WORLD = "C:/worlds/example/depot_world.usda"
CAMERA = CameraValue(position=(12.0, 3.0, 8.0), target=(0.0, 0.0, 0.0), up=(0.0, 0.0, 1.0))


class FakeUsdHost:
    """What #137/#139's host knows when snap asks: its view's params, the stage it
    shows, and what its source reported (renderer, frame size, camera)."""

    def __init__(self, engine: Engine, vid: str, **source: Any) -> None:
        self.engine, self.vid, self.source = engine, vid, source

    def snapshot(self) -> dict[str, Any]:
        params = self.engine.live.data["views"][self.vid].get("params", {})
        return usd_snap_meta(params, self.engine.capabilities, **self.source)


class FakeShell:
    """The shell's snap provider with one live host per view."""

    def __init__(self) -> None:
        self.hosts: dict[str, FakeUsdHost] = {}

    def rect_of(self, oid: str) -> None:
        return None

    def host_snapshot(self, view_id: str) -> dict[str, Any]:
        host = self.hosts.get(view_id)
        return host.snapshot() if host else {}

    def health_of(self, view_id: str) -> dict[str, str]:
        return {"status": "ok", "message": ""}

    def grab(self, oid: str, path: Path) -> dict[str, Any]:
        return {"error": "no window"}


class FakeApp:
    def __init__(self) -> None:
        self.shell = FakeShell()


def machine(ovrtx: bool, project: Project | None = None) -> Engine:
    cap = MachineCapability(name="ovrtx", present=ovrtx, reason="test machine")
    return Engine(project=project, host=FakeApp(), capabilities=lambda: {"ovrtx": cap})


@pytest.fixture
def engine() -> Engine:
    return machine(ovrtx=False)


def add_usd(engine: Engine, params: dict | None = None) -> str:
    doc = engine.live.document()
    doc["views"]["kobi"] = {
        "type": "usd",
        "label": "World",
        "layout": {"position": [330, 0], "size": [640, 420]},
        "params": params or {},
    }
    pin(doc, "kobi")
    engine.live.submit(doc, origin="agent:s1", label="a world")
    return "kobi"


def cards(engine: Engine) -> dict[str, dict]:
    ref = reference_text(lambda n, a: engine.call(n, a, PROMPT_BAR))
    pairs = (line[2:].split(": ", 1) for line in ref.splitlines() if line.startswith("- "))
    return {name: json.loads(card) for name, card in pairs}


def test_the_reference_gives_each_usd_param_its_choices(engine):
    params = cards(engine)["usd"]["params"]
    assert params["renderer"].startswith("auto|rtx|storm")
    assert params["mode"].startswith("rt|pt|minimal")
    assert params["camera_speed"].startswith("0.125..8")
    assert "camera prim path" in params["look_through"]
    assert params["max_pixels"].startswith(">=1") and "pixels" in params["max_pixels"]


def test_the_system_prompt_says_when_to_use_a_usd_view_and_what_it_never_does():
    from specto_app.prompt.session import SYSTEM_PROMPT

    assert "<<<<<<<" not in SYSTEM_PROMPT and ">>>>>>>" not in SYSTEM_PROMPT
    usd = next(p for p in SYSTEM_PROMPT.split("\n\n") if "USD" in p)
    assert "one stage" in usd and "player" in usd
    assert "@usd" in usd and "selected_files" in usd
    assert "global clock" in usd and "root view" in usd
    assert "camera port" in usd and "look_through" in usd and "never by running code" in usd
    assert "never edit" in usd and "USD files" in usd
    for param in ("renderer", "camera_speed", "max_pixels"):
        assert param in usd


def test_snap_of_a_usd_view_reports_the_stage_renderer_frame_and_camera():
    engine = machine(ovrtx=True)
    vid = add_usd(engine, {"look_through": "/World/Cameras/Front"})
    engine.host.shell.hosts[vid] = FakeUsdHost(
        engine, vid, path=WORLD, frame_size=(1280, 720), camera=CAMERA
    )

    host = engine.call("snap", {"target": vid})["meta"]["host"]

    assert host == {
        "path": WORLD,
        "renderer": "rtx",
        "renderer_asked": "auto",
        "mode": "rt",
        "frame_size": [1280, 720],
        "camera": {
            "position": [12.0, 3.0, 8.0],
            "target": [0.0, 0.0, 0.0],
            "up": [0.0, 0.0, 1.0],
            "fov_y": 0.8,
            "near": 0.1,
            "far": 1000.0,
        },
        "look_through": "/World/Cameras/Front",
        "camera_speed": 1.0,
        # the environment light (SPEC-31): the params' choice before the source says
        "environment": "stage",
        "environment_note": "",
        "sky": "ClearSky",
        "hdri": "sunny",
        "sky_controls": {},
        "ground": False,
        "sky_fetch": {},
        "hdri_fetch": {},
        "fallback_reason": "",
        "load_timings": {},
    }


def test_snap_says_storm_runs_where_auto_finds_no_ovrtx_and_storm_has_no_mode(engine):
    vid = add_usd(engine, {"mode": "pt", "camera_speed": 2})
    engine.host.shell.hosts[vid] = FakeUsdHost(engine, vid, path=WORLD)

    host = engine.call("snap", {"target": vid})["meta"]["host"]

    assert (host["renderer"], host["renderer_asked"], host["mode"]) == ("storm", "auto", None)
    assert host["camera_speed"] == 2.0
    # before the source's first frame: no frame size, no camera yet
    assert host["frame_size"] is None and host["camera"] is None


def test_snap_reports_the_renderer_the_source_runs_after_a_fallback():
    engine = machine(ovrtx=True)
    vid = add_usd(engine, {"renderer": "auto"})
    engine.host.shell.hosts[vid] = FakeUsdHost(engine, vid, path=WORLD, renderer="storm")

    host = engine.call("snap", {"target": vid})["meta"]["host"]

    assert (host["renderer"], host["renderer_asked"], host["mode"]) == ("storm", "auto", None)


# -- a prompt-bar turn, scripted ---------------------------------------------------------
class StageHost(FakeUsdHost):
    """A fake host that shows the stage its data port last received (the first item)."""

    def __init__(self, engine: Engine, vid: str) -> None:
        super().__init__(engine, vid, frame_size=(1280, 720), camera=CAMERA)
        engine.links.attach(vid, self.receive)

    def receive(self, port: str, value: Any) -> None:
        if port == "data" and isinstance(value, Collection) and value.items:
            self.source["path"] = value.items[0].primary


def test_a_prompt_bar_turn_adds_a_usd_view_for_the_picks_then_looks_through_a_camera(tmp_path):
    project = Project.init(tmp_path / "proj", "worlds")
    for name, text in (("worlds/valley.usda", "#usda 1.0"), ("notes/readme.md", "# hi")):
        (project.root / name).parent.mkdir(parents=True, exist_ok=True)
        (project.root / name).write_text(text, encoding="utf-8")
    engine = machine(ovrtx=False, project=project)
    tree = next(v for v, d in engine.live.data["views"].items() if d["type"] == "file_tree")
    tools = TurnTools(
        AgentTools(lambda name, args: engine.call(name, args, PROMPT_BAR)), engine.live
    )
    example = engine.call("describe", {"target": "view:usd"})["info"]["example"]
    intaker = example["links"][0] | {"source": {"view": tree, "port": "selected_files"}}
    intaker["target"] = {"view": "new1", "port": "data"}
    show = {
        "edits": {
            "views": {
                "new1": example["view"] | {"layout": {"position": [330, 0], "size": [640, 420]}}
            },
            "links": {"new2": intaker},
        },
        "label": "show the picked world in a usd view",
    }
    session = ScriptedAgentSession(tools, [[{"tool": "submit_canvas", "args": show}]])

    def turn(prompt: str):
        ctx = CanvasContext(
            document=engine.live.document(),
            viewport={"x": 0, "y": 0, "w": 1600, "h": 900},
            mouse=None,
            selection={},
        )
        return asyncio.run(run_turn(session, tools, prompt, ctx))

    reply = turn("show the world I pick")

    assert reply.status == "changed" and reply.text == "show the picked world in a usd view"
    vid = next(v for v, d in engine.live.data["views"].items() if d["type"] == "usd")
    assert engine.live.data["views"][vid]["params"] == {"renderer": "auto"}
    assert len(engine.live.history) == 1  # one undo step

    # the user picks a world and a note: the screener keeps the stage
    host = engine.host.shell.hosts[vid] = StageHost(engine, vid)
    engine.links.emit(
        tree,
        "selected_files",
        FilesValue(root=str(project.root), paths=["worlds/valley.usda", "notes/readme.md"]),
    )
    assert len(engine.links.inputs_of(vid)["data"].items) == 1
    assert host.source["path"].endswith("valley.usda")

    look = {"views": {vid: {"params": {"look_through": "/World/Cameras/Front"}}}}
    session.turns = [
        [{"tool": "submit_canvas", "args": {"edits": look, "label": "look through Front"}}],
        [
            {"tool": "snap", "args": {"target": vid}},
            {"text": "It shows valley.usda with Storm."},
        ],
    ]
    assert turn("look through the front camera").status == "changed"
    assert engine.live.data["views"][vid]["params"]["look_through"] == "/World/Cameras/Front"

    reply = turn("what does the world view show?")
    meta = json.loads(session.calls[-1].text)["meta"]["host"]
    assert meta["path"].endswith("valley.usda")
    assert (meta["renderer"], meta["look_through"]) == ("storm", "/World/Cameras/Front")
    assert meta["frame_size"] == [1280, 720] and meta["camera"]["up"] == [0.0, 0.0, 1.0]
    assert reply.status != "changed" and len(engine.live.history) == 2
