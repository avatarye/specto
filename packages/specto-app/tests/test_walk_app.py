"""The walk-through in the running shell, offscreen (#174): the root context has
``walkthrough``; "walk me through <view>" in the prompt bar plays the agent's script, or
the template script when there is no agent. The walk UI itself is #174 part (b)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin_link

from specto_app.prompt.scripted import ScriptedAgentSession
from specto_core import CallContext

SEED = ("runs/front/00001_1.jpg", "runs/front/00002_1.jpg", "notes/a.md")


@pytest.fixture
def project(tmp_path):
    from specto_core import Project

    proj = Project.init(tmp_path / "proj", "demo")
    for name in SEED:
        p = proj.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return proj


def make_app(project, aloop, factory):
    from specto_app.app import SpectoApp

    a = SpectoApp(
        project, jobs="inline", web=False, offscreen=True, serve=False, agent=factory, loop=aloop
    )
    a.start()
    doc = a.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [400, 0], "size": [640, 400]},
    }
    doc["links"]["l9"] = pin_link("kobi", sources=["runs/front"])
    a.engine.call("canvas.submit", {"document": doc, "label": "kobi"}, CallContext(origin="user"))
    return a


TURN = [
    {"tool": "walkthrough", "args": {"view": "kobi"}},
    {
        "tool": "walkthrough_play",
        "args": {"view": "kobi", "captions": ["The pinned intaker keeps **2** frames."]},
    },
]


def test_walk_me_through_plays_the_agents_script(qapp, project, aloop, aspin, spin):
    sessions: list[ScriptedAgentSession] = []

    def factory(tools):
        s = ScriptedAgentSession(tools, [TURN])
        sessions.append(s)
        return s

    app = make_app(project, aloop, factory)
    try:
        spin(50)
        walk = app.qml.rootContext().contextProperty("walkthrough")
        assert walk is app.walkthrough and walk.state == "idle"
        assert app.prompt_bar.submit("walk me through kobi")
        aspin(lambda: app.prompt_bar.status != "working")
        assert walk.state == "playing" and walk.step == 0 and walk.target == "kobi"
        assert walk.script["source"] == "agent"
        assert walk.script["hops"][0]["caption"] == "The pinned intaker keeps **2** frames."
        assert walk.script["hops"][0]["count_out"] == {"n": 2, "unit": "items"}
        assert app.prompt_bar.reply.summary == "walk-through: kobi"
        assert app.live.cursor == 1  # only the setup submit: the walk changed nothing
        walk.exit()
        assert walk.state == "exited"
    finally:
        app.close()
        spin(20)


def test_without_an_agent_the_walk_plays_its_template_captions(qapp, project, aloop, spin):
    def factory(tools):  # pragma: no cover - never asked: there is no agent
        raise AssertionError("no session without an agent")

    factory.unavailable = "the Claude Agent SDK is not installed"  # type: ignore[attr-defined]
    app = make_app(project, aloop, factory)
    try:
        spin(50)
        assert app.prompt_bar.submit("walk me through kobi")
        walk = app.walkthrough
        assert walk.state == "playing" and walk.script["source"] == "template"
        assert walk.hopCount == 1 and walk.pathViews == ["project", "kobi"]
    finally:
        app.close()
        spin(20)
