"""The agent's view of the project folder and of the user's removals (#169).

1. The context's files summary always comes from the project root: a second file tree
   pinned to one subfolder used to replace it with that subfolder's listing (the
   runtime's ``project_files`` follows whichever tree off the project resolved last),
   so the count jumped from turn to turn. ``list_files`` finds what the summary folded.
2. The user closes a view while the agent works: the agent's edit that only adds a view
   goes through, and removing the closed view again is a no-op that says so.

Seam 1 (a scripted agent over the real tools) and seam 2 (the prompt bar in the running
shell, offscreen)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from specto_app.prompt.context import CanvasContext
from specto_app.prompt.files import ProjectFiles
from specto_app.prompt.scripted import ScriptedAgentSession
from specto_app.prompt.turn import TurnTools, run_turn
from specto_core import CallContext, Engine, Project, gestures
from specto_core.agent_tools import AgentTools

SEED = (
    "notes/a.md",
    "annotations/archive/notes/rigB/day1/take1/geo/track.geojson",
    "annotations/archive/notes/rigB/day1/take1/geo/zone.geojson",
    "recordings/archive/clips/rigB/day1/take1/clip.usda",
)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "demo")
    for name in SEED:
        p = proj.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return proj


def turn_tools(engine: Engine, root: Path | None = None) -> TurnTools:
    ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
    agent = AgentTools(lambda n, a: engine.call(n, a, ctx))
    return TurnTools(agent, engine.live, files=ProjectFiles(root) if root else None)


def scripted_turn(tools: TurnTools, steps: list[dict], during=None):
    """One turn of ``steps``; ``during`` (the user's gesture) runs while the agent
    "thinks" at a ``wait_ms`` step."""
    session = ScriptedAgentSession(tools, [steps])
    ctx = CanvasContext(document=tools.live.document(), viewport={"x": 0, "y": 0, "w": 9, "h": 9})

    async def run():
        task = asyncio.ensure_future(run_turn(session, tools, "go", ctx))
        if during is not None:
            await asyncio.sleep(0.02)
            during()
        return await task

    return asyncio.run(run()), session


# -- list_files ------------------------------------------------------------------------------
def test_the_agent_lists_a_folder_and_a_wrong_guess_gets_the_right_one(project: Project):
    tools = turn_tools(Engine(project=None), project.root)
    _, session = scripted_turn(
        tools,
        [
            {"tool": "list_files", "args": {"path": "annotations/notes"}},
            {"tool": "list_files", "args": {"path": "annotations/archive/notes", "depth": 3}},
            {"tool": "list_files", "args": {"glob": "**/geo/*.geojson"}},
        ],
    )

    wrong, listed, found = session.calls
    assert wrong.is_error and "annotations has: archive" in wrong.text
    rows = json.loads(listed.text)["folders"]
    assert rows[-1] == {
        "path": "annotations/archive/notes/rigB/day1/take1",
        "files": 2,
        "folders": 1,
    }
    assert json.loads(found.text)["matches"] == 2
    assert tools.failures == 0


# -- the user removed a view mid-turn ------------------------------------------------------
def gallery_edits(label: str) -> dict:
    return {
        "views": {
            "new1": {
                "type": "gallery",
                "label": label,
                "layout": {"position": [400, 0], "size": [600, 400]},
            }
        },
        "links": {
            "new2": {
                "kind": "intaker",
                "scope": "pinned",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "new1", "port": "data"},
                "params": {"sources": ["."]},
                "summary": {"sentence": "Always shows the project."},
            }
        },
    }


def test_an_edit_adding_a_view_goes_through_after_the_user_closed_one_mid_turn():
    """The depot report, replayed: the agent added koha (and l4) last turn; the user
    closes it while the agent works on the next request, which only adds a view."""
    engine = Engine(project=None)
    tools = turn_tools(engine)
    scripted_turn(
        tools, [{"tool": "submit_canvas", "args": {"edits": gallery_edits("first"), "label": "a"}}]
    )
    (koha,) = [v for v, d in engine.live.data["views"].items() if d["label"] == "first"]

    reply, session = scripted_turn(
        tools,
        [
            {"wait_ms": 80},
            {"tool": "submit_canvas", "args": {"edits": gallery_edits("second"), "label": "b"}},
            {"tool": "submit_canvas", "args": {"edits": {"views": {koha: None}}, "label": "c"}},
        ],
        during=lambda: gestures.close_view(engine.live, koha),
    )

    added, removed = session.calls
    assert not added.is_error, added.text
    assert reply.status == "changed" and reply.attempts_failed == 0
    labels = {d["label"] for d in engine.live.data["views"].values()}
    assert "second" in labels and "first" not in labels
    assert koha not in json.loads(added.text)["renamed"].values()
    # removing it again: nothing to do, and the result says why
    assert not removed.is_error and json.loads(removed.text)["already_removed"] == [koha]


# -- the context's summary (seam 2) --------------------------------------------------------
@pytest.fixture
def app(qapp, project, aloop, spin):
    pytest.importorskip("PySide6.QtQuick")
    from specto_app.app import SpectoApp

    a = SpectoApp(
        project,
        jobs="inline",
        web=False,
        offscreen=True,
        serve=True,
        agent=lambda tools: ScriptedAgentSession(tools, []),
        loop=aloop,
    )
    a.start()
    spin(50)
    yield a
    a.close()
    spin(20)


def test_the_files_summary_is_the_whole_folder_whatever_tree_resolved_last(app, spin, monkeypatch):
    from app_doc_helpers import pin_link

    bar = app.prompt_bar
    before = bar.context().files["project"]
    assert before["count"] == len(SEED) and before["root"] == str(app.project.root)

    # a second tree pinned to one subfolder resolves: it used to become the project's
    # listing (the jumping count, also on the project node's tip); only a whole-folder
    # tree sets it now, and the context reads that listing without walking the folder
    import specto_core.project_listing as pl

    walks: list[str] = []
    real_walk = pl.walk_project
    monkeypatch.setattr(pl, "walk_project", lambda root: walks.append(root) or real_walk(root))
    bar.tools.files.listing.forget()
    doc = app.live.document()
    doc["views"]["tamu"] = {
        "type": "file_tree",
        "label": "annotations",
        "layout": {"position": [400, 0], "size": [300, 500]},
    }
    doc["links"]["l9"] = pin_link("tamu", "file_tree", ["annotations"])
    app.live.submit(doc, origin="user", label="second tree")
    spin(until=lambda: app.engine.links.input_of("tamu", "data") is not None)
    assert app.engine.links.project_files is None  # no tree stands for the folder (#231)

    after = bar.context().files["project"]
    bar.context()
    assert walks == [app.project.root]  # one walk of the project root, cached: not per prompt
    assert after["count"] == len(SEED)
    assert set(after["folders"]) == {
        "notes",
        "annotations/archive/notes/rigB/day1/take1/geo",
        "recordings/archive/clips/rigB/day1/take1",
    }
    # and the agent has list_files beside the summary
    assert bar.tools.files is not None


def test_without_a_whole_folder_tree_the_root_is_walked_once_until_a_rescan(
    project: Project, monkeypatch
):
    """The depot is large: the fallback walk (no tree takes the whole folder) is cached
    and taken again only when an intaker off the project source resolves anew."""
    import specto_core.project_listing as pl
    import specto_intake  # noqa: F401  (registers the intaker)
    from specto_core.jobs import InlineJobManager

    engine = Engine(project=project, jobs=InlineJobManager())
    doc = engine.live.document()
    (lid,) = [k for k, ln in doc["links"].items() if ln["source"]["view"] == "project"]
    doc["links"][lid]["params"]["sources"] = ["notes"]  # the tree shows a part only
    engine.live.submit(doc, origin="user", label="narrow the tree")
    assert engine.links.project_files is None
    walks: list = []
    real_walk = pl.walk_project
    monkeypatch.setattr(pl, "walk_project", lambda root: walks.append(root) or real_walk(root))
    files = ProjectFiles(project.root, engine=engine)

    assert files.summary()["count"] == len(SEED)
    files.run("list_files", {"path": "annotations"})
    files.summary()
    assert len(walks) == 1
    engine.links.refresh()  # the project node's refresh: the next read walks again
    files.summary()
    assert len(walks) == 2
    files.close()
