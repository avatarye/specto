"""#219 acceptance, seam 2 (the running shell offscreen, a scripted agent on the real
tools): asked to remember a layout as "review", the agent authors a layouts skill and a
/layout command and writes the views and links to ``.specto/layouts/review.json``; the
thread shows the skill, command and data rows with Open and Undo; after the user moves
a view, ``/layout review`` (a project slash command the prompt bar lets through,
dispatched without the canvas context) reads the file back and restores the layout as
one undoable turn; the rows' Undo removes the command and the file, and the next
prompt's context lists the commands again. No built-in layout tool anywhere."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin_link

from specto_app.prompt.scripted import ScriptedAgentSession
from specto_app.prompt.session import project_commands, slash_command

SEED = ("runs/front/00001_1.jpg", "runs/rear/00001_1.jpg", "notes/a.md")
SKILL = {
    "name": "layouts",
    "description": "Remember a layout of views and links by name, or bring one back.",
    "body": "# Layouts\n\nSave: data_write layouts/<name>.json with the document's views and "
    "links. Restore: data_read it, then one submit_canvas with its views and links as "
    "edits. Never approve link code.\n",
}
COMMAND = {
    "name": "layout",
    "description": "Bring back a saved layout by name.",
    "body": "Invoke the specto:layouts skill, call state, then bring back the layout "
    "named $ARGUMENTS.\n",
    "argument_hint": "<name>",
}


def gallery(label: str, x: float) -> dict:
    return {"type": "gallery", "label": label, "layout": {"position": [x, 0], "size": [640, 400]}}


@pytest.fixture
def project(tmp_path):
    from specto_core import Project

    proj = Project.init(tmp_path / "proj", "demo")
    for name in SEED:
        p = proj.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return proj


@pytest.fixture
def app(qapp, project, aloop, spin):
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


def context_block(session: ScriptedAgentSession, n: int) -> dict:
    text = session.received[n]["context"]
    return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def test_the_agent_makes_a_layouts_skill_and_a_layout_command_and_restores_review(app, aspin, spin):
    from specto_core.gestures import move_view

    bar, live, root = app.prompt_bar, app.live, app.project.root
    good = live.document()
    good["views"]["kobi"] = gallery("Front", 300)
    good["views"]["tamu"] = gallery("Rear", 960)
    good["links"]["l2"] = pin_link("kobi")
    good["links"]["l3"] = pin_link("tamu")
    saved = {"name": "review", "views": good["views"], "links": good["links"]}
    sessions: list[ScriptedAgentSession] = []

    def factory(tools):
        s = ScriptedAgentSession(
            tools,
            [
                [{"tool": "submit_canvas", "args": {"document": good, "label": "front and rear"}}],
                [
                    {"tool": "save_skill", "args": SKILL},
                    {"tool": "save_command", "args": COMMAND},
                    {
                        "tool": "data_write",
                        "args": {"path": "layouts/review.json", "text": json.dumps(saved)},
                    },
                    {"text": "Saved this layout as review; /layout review brings it back."},
                ],
                [
                    {"skill": "specto:layouts"},
                    {"tool": "data_read", "args": {"path": "layouts/review.json"}},
                    {
                        "tool": "submit_canvas",
                        "args": {
                            "edits": {"views": saved["views"], "links": saved["links"]},
                            "label": "restored layout review",
                        },
                    },
                ],
                [{"text": "ok"}],
            ],
        )
        sessions.append(s)
        return s

    bar.factory = factory
    bar.submit("add front and rear galleries")
    aspin(until=lambda: not bar.busy)
    assert bar.status == "changed" and set(live.data["views"]) == {"fege", "kobi", "tamu"}

    # -- "remember this layout as review": a skill, a command and a data file, no canvas change
    bar.submit("remember this layout as review")
    aspin(until=lambda: not bar.busy)
    session = sessions[0]
    assert bar.status == "answered"
    assert (root / ".specto" / "skills" / "layouts" / "SKILL.md").exists()
    assert (root / ".claude" / "commands" / "layout.md").exists()
    layout_file = root / ".specto" / "layouts" / "review.json"
    assert json.loads(layout_file.read_text(encoding="utf-8")) == saved
    assert not [c for c in session.calls if c.is_error]
    assert bar.skillNote == "saved skill specto:layouts"
    assert bar.commandNote == "saved command /layout" and bar.canUndoCommands is True
    assert bar.dataNote == "wrote .specto/layouts/review.json" and bar.canUndoData is True
    assert bar.canUndoTurn is False  # this turn changed no canvas
    assert project_commands(root) == ["layout"]
    assert slash_command("/layout review", root) == "layout"
    rows = {r["name"]: r for r in bar.slashCommands("/lay") if r["available"]}
    assert set(rows) == {"layout", "specto:layouts"}  # the command, and the skill by short name
    assert rows["layout"] == {
        "name": "layout",
        "description": COMMAND["description"],
        "kind": "command",
        "available": True,
    }
    entries = bar.thread.thread.entries
    kinds = [e.kind for e in entries if e.kind in ("skill", "command", "data")]
    assert kinds == ["skill", "command", "data"]
    command_row = next(e for e in entries if e.kind == "command")
    data_row = next(e for e in entries if e.kind == "data")
    assert command_row.label == "saved command /layout"
    assert data_row.label == "wrote .specto/layouts/review.json"
    bar.panel.summon()
    spin(until=lambda: app.engine.call("shell.locate", {"name": "replyCommand"})["visible"])
    for name in ("CommandOpen", "CommandUndo", "Data", "DataOpen", "DataUndo"):
        assert app.engine.call("shell.locate", {"name": f"reply{name}"})["visible"] is True, name
    # Open: the command rendered (frontmatter as lines, not YAML), the data file fenced
    command_md = bar.skillMarkdownAt(command_row.id)
    assert "**/layout**" in command_md and "$ARGUMENTS" in command_md and "---" not in command_md
    assert "arguments: `<name>`" in command_md
    data_md = bar.skillMarkdownAt(data_row.id)
    assert data_md.startswith("**.specto/layouts/review.json**") and "```json" in data_md

    # -- the user moves a view; /layout review brings the saved layout back, one undo step
    move_view(live, "kobi", (320, 40))
    assert live.data["views"]["kobi"]["layout"]["position"] == [320, 40]
    bar.submit("/layout review")
    aspin(until=lambda: not bar.busy)
    sent = session.received[2]
    assert sent["prompt"] == "/layout review" and sent["with_context"] is False
    assert [name for name, _ in session.skills_used] == ["specto:layouts"]
    read = next(c for c in session.calls if c.name == "data_read")
    assert json.loads(json.loads(read.text)["text"]) == saved
    assert bar.status == "changed" and bar.summary == "restored layout review"
    assert live.data["views"]["kobi"]["layout"]["position"] == [300, 0]
    assert set(live.data["links"]) == set(good["links"])
    assert bar.canUndoTurn is True
    assert bar.undoTurn() is True
    assert live.data["views"]["kobi"]["layout"]["position"] == [320, 40]

    # -- the rows' Undo: the file and the command go, and the agent hears of the change
    assert bar.undoDataAt(data_row.id) is True and not layout_file.exists()
    assert bar.undoCommandsAt(command_row.id) is True
    assert not (root / ".claude" / "commands" / "layout.md").exists()
    assert slash_command("/layout review", root) == ""  # refused again from now on
    bar.submit("ok")
    aspin(until=lambda: not bar.busy)
    ctx = context_block(session, 3)
    assert ctx["commands"] == [] and [s["name"] for s in ctx["skills"]] == ["specto:layouts"]
