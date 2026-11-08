"""The scratch pad's agent tools (#184): scratch_read, scratch_append and scratch_write
on a page's markdown, scratch_pages, scratch_page_add, scratch_page_rename and
scratch_page_remove for the named pages, over the project's .specto/scratch. A turn's
changes fold into one step on the pad's stack, which the thread's scratch row undoes;
every prompt's context names the pad's pages (size and headings). A scripted agent runs
the real tools, at seam 1 and in the offscreen shell."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from specto_app.prompt.context import CanvasContext, render_context
from specto_app.prompt.scratch import SCRATCH_TOOLS, ScratchTools, context_items
from specto_app.prompt.scripted import ScriptedAgentSession
from specto_app.prompt.turn import TurnTools, run_turn
from specto_app.views.scratch_view import to_png
from specto_core import CallContext, Engine, Project
from specto_core.agent_tools import AgentTools
from specto_core.jobs import InlineJobManager
from specto_core.scratch import ScratchStore


def jpeg(path: Path) -> Path:
    img = QImage(10, 10, QImage.Format.Format_RGB32)
    img.fill(QColor("green"))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(buf.data().data()))
    return path


@pytest.fixture
def engine(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    project = Project.init(tmp_path / "proj", "scratch")
    jpeg(project.root / "runs" / "front.jpg")
    (project.root / "notes.md").write_text("# notes\n", encoding="utf-8")
    return Engine(project=project, jobs=InlineJobManager())


def turn_tools(engine: Engine) -> TurnTools:
    ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
    agent = AgentTools(lambda n, a: engine.call(n, a, ctx))
    scratch = ScratchTools(lambda: engine.scratch, to_png=to_png)
    return TurnTools(agent, engine.live, scratch=scratch)


def scripted_turn(tools: TurnTools, steps: list[dict]):
    session = ScriptedAgentSession(tools, [steps])
    ctx = CanvasContext(
        document=tools.live.document(),
        viewport={"x": 0, "y": 0, "w": 9, "h": 9},
        scratch=context_items(tools.scratch.store),
    )
    return asyncio.run(run_turn(session, tools, "keep these", ctx)), session


def test_a_turn_appends_and_writes_as_one_undo_step(engine: Engine):
    engine.scratch.append("the user's own note")  # before the turn
    tools = turn_tools(engine)
    reply, session = scripted_turn(
        tools,
        [
            {"tool": "scratch_append", "args": {"markdown": "## Drift\nlidar drifts at 00042"}},
            {"tool": "scratch_append", "args": {"image": "runs/front.jpg", "label": "front"}},
            {"tool": "scratch_append", "args": {"markdown": "the notes:", "file": "notes.md"}},
            {"tool": "scratch_read"},
            {"text": "Kept the drift, the front frame and the notes."},
        ],
    )
    assert not [c for c in session.calls if c.is_error], [c.text for c in session.calls]
    store = engine.scratch
    assert store.text() == (
        "the user's own note\n\n## Drift\nlidar drifts at 00042\n\n"
        "![front](p1.assets/front.png)\n\nthe notes:\n\n[notes.md](../../notes.md)\n"
    )
    png = store.root / ".specto" / "scratch" / "p1.assets" / "front.png"
    assert png.read_bytes().startswith(b"\x89PNG")  # the jpeg, stored as png
    read = session.calls[3].data
    assert read["markdown"] == store.text() and read["id"] == "p1"
    assert read["links"] == [
        {"target": "p1.assets/front.png", "image": True, "label": "front"},
        {"target": "../../notes.md", "image": False, "label": "notes.md"},
    ]
    # one step for the turn, above the user's own
    assert len(reply.scratch) == 3 and reply.scratch_step == store.top.id
    assert [s.origin for s in store.history] == ["user", "agent:prompt"]
    assert reply.status == "answered" and reply.turn_txn is None  # the canvas is untouched

    assert tools.undo_scratch(reply) is True
    assert store.text() == "the user's own note\n" and reply.scratch_undone
    assert not png.exists()
    assert tools.undo_scratch(reply) is False

    # scratch_write replaces the page
    reply, _ = scripted_turn(
        tools, [{"tool": "scratch_write", "args": {"markdown": "# Summary\nall in one\n"}}]
    )
    assert store.text() == "# Summary\nall in one\n" and reply.scratch_step is not None


def test_the_scratch_row_undo_waits_for_its_step_to_be_the_newest(engine: Engine):
    tools = turn_tools(engine)
    reply, _ = scripted_turn(tools, [{"tool": "scratch_append", "args": {"markdown": "agent"}}])
    engine.scratch.append("the user's later note")
    assert tools.undo_scratch(reply) is False
    engine.scratch.undo()
    assert tools.undo_scratch(reply) is True and engine.scratch.text() == ""


def test_the_errors_the_agent_gets_back(engine: Engine):
    tools = turn_tools(engine)
    _, session = scripted_turn(
        tools,
        [
            {"tool": "scratch_append", "args": {}},
            {"tool": "scratch_append", "args": {"image": "notes.md"}},
            {"tool": "scratch_append", "args": {"file": "missing.txt"}},
            {"tool": "scratch_write", "args": {}},
            {"tool": "scratch_read", "args": {"page": "nope"}},
            {"tool": "scratch_page_remove", "args": {}},
            {"tool": "scratch_page_remove", "args": {"page": "p1"}},
        ],
    )
    assert all(c.is_error for c in session.calls), [c.text for c in session.calls]
    assert "nothing to append" in session.calls[0].text
    assert "not a picture" in session.calls[1].text
    assert "keeps at least one page" in session.calls[6].text


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path forms")
@pytest.mark.parametrize("arg", ["image", "file"])
@pytest.mark.parametrize(
    "text",
    [
        *(r"\\host\share\x.png", "//host/share/x.png", r"/\host/share\x.png"),
        *(r"\\?\UNC\host\share\x.png", "x.png:host"),
    ],
)
def test_an_append_s_path_is_judged_before_any_filesystem_call(
    engine, monkeypatch, arg, text, trap_paths
):
    """SPEC-73: scratch_append's image and file args went to ``Path.is_file`` in
    ``ScratchTools._path``, so an agent naming ``\\\\host\\share\\x`` made Windows connect to
    the host. The text passes the path guard first and the agent gets the refusal."""
    from specto_core.errors import InvalidArgs

    tools = ScratchTools(lambda: engine.scratch, to_png=to_png)
    assert tools.store is not None  # the store opens (reads its own files) before the trap
    calls: list[str] = []
    trap_paths(calls)
    try:
        with pytest.raises(InvalidArgs) as err:
            tools.run("scratch_append", {arg: text})
    finally:
        monkeypatch.undo()
    assert calls == []
    assert any(w in str(err.value) for w in ("network path", "device path", "stream name"))


def test_the_context_names_the_pages_with_their_headings(engine: Engine):
    assert context_items(ScratchStore(engine.project.root / "elsewhere")) == []  # empty pad
    engine.scratch.append("# Long finding\n" + "z" * 500 + "\n## Detail")
    engine.scratch.add_page("lidar")
    items = context_items(engine.scratch)
    assert items == [
        {"page": "p1", "name": "Page 1", "chars": 526, "headings": ["# Long finding", "## Detail"]},
        {"page": "p2", "name": "lidar", "chars": 0, "active": True},
    ]
    text = render_context(CanvasContext(document=None, viewport={}, scratch=items))
    body = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert body["scratch"] == items and "zzz" not in text
    assert "scratch_read" in text
    empty = render_context(CanvasContext(document=None, viewport={}))
    assert '"scratch"' not in empty


def test_the_session_offers_the_scratch_tools(engine: Engine):
    from specto_app.prompt.session import SYSTEM_PROMPT, sdk_tools

    names = {t.name for t in sdk_tools(turn_tools(engine))}
    assert set(SCRATCH_TOOLS) <= names
    assert set(SCRATCH_TOOLS) == {
        "scratch_read",
        "scratch_append",
        "scratch_write",
        "scratch_pages",
        "scratch_page_add",
        "scratch_page_rename",
        "scratch_page_remove",
    }
    for word in ("scratch_append", "scratch_write", "scratch_page_add", "params.page"):
        assert word in SYSTEM_PROMPT


def test_pages_by_name_or_id_in_one_turn(engine: Engine):
    engine.scratch.append("on page one")
    tools = turn_tools(engine)
    reply, session = scripted_turn(
        tools,
        [
            {"tool": "scratch_page_add", "args": {"name": "lidar"}},
            {"tool": "scratch_append", "args": {"markdown": "drift at 00042"}},  # the new page
            {"tool": "scratch_append", "args": {"markdown": "back home", "page": "Page 1"}},
            {"tool": "scratch_page_rename", "args": {"page": "p2", "name": "lidar drift"}},
            {"tool": "scratch_page_add", "args": {"name": "spare", "at": 0}},
            {"tool": "scratch_page_remove", "args": {"page": "spare"}},
            {"tool": "scratch_pages"},
            {"tool": "scratch_read", "args": {"page": "lidar drift"}},
        ],
    )
    assert not [c for c in session.calls if c.is_error], [c.text for c in session.calls]
    store = engine.scratch
    assert [p["name"] for p in store.pages] == ["Page 1", "lidar drift"]
    assert store.text("p1") == "on page one\n\nback home\n"
    assert [(p["id"], p["active"]) for p in session.calls[6].data["pages"]] == [
        ("p1", True),  # spare was shown when it closed: its neighbour shows
        ("p2", False),
    ]
    assert session.calls[7].data["markdown"] == "drift at 00042\n"
    # the whole turn, pages too, is the one step the row undoes
    assert tools.undo_scratch(reply) is True
    assert store.page_ids() == ["p1"] and store.text() == "on page one\n"


# -- in the shell --------------------------------------------------------------------------------
@pytest.fixture
def app(qapp, engine, aloop, spin):
    from specto_app.app import SpectoApp

    sessions: list[ScriptedAgentSession] = []
    turns = [
        [
            {"tool": "scratch_append", "args": {"markdown": "from the agent"}},
            {"text": "Kept it."},
        ]
    ]

    def factory(tools):
        s = ScriptedAgentSession(tools, [list(t) for t in turns])
        sessions.append(s)
        return s

    a = SpectoApp(
        engine.project,
        jobs="inline",
        web=False,
        offscreen=True,
        serve=True,
        agent=factory,
        loop=aloop,
    )
    a.start()
    a.sessions = sessions  # type: ignore[attr-defined]
    spin(50)
    yield a
    a.close()
    spin(20)


def test_the_prompt_bar_shows_a_scratch_row_with_undo(app, aspin, spin):
    app.engine.scratch.append("# mine")
    bar = app.prompt_bar
    assert bar.submit("keep a note") is True
    aspin(until=lambda: not bar.busy)
    context = app.sessions[0].received[0]["context"]
    body = json.loads(context.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert body["scratch"] == [
        {"page": "p1", "name": "Page 1", "chars": 7, "active": True, "headings": ["# mine"]}
    ]
    store = app.engine.scratch
    assert store.text() == "# mine\n\nfrom the agent\n"
    rows = [e for e in bar.thread.thread.entries if e.kind == "scratch"]
    assert len(rows) == 1 and rows[0].label == "scratch pad: add from the agent"
    live = bar._live(rows[0])
    assert live["canUndo"] is True and live["latest"] is True
    assert bar.undoScratchAt(rows[0].id) is True
    assert store.text() == "# mine\n"
    assert bar._live(rows[0])["undone"] is True and bar._live(rows[0])["canUndo"] is False
