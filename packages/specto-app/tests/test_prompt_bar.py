"""The prompt bar in the running shell, offscreen (#44, spec #35 stories 8-16): typing a
request composes the canvas through submit with no permission prompts, one turn is one
undo step, the thread's turn row shows the summary with Undo (the agent panel, #162),
changed views are outlined, and every prompt carries the canvas context (viewport,
mouse, selection, document). A scripted agent stands in for the model; the real-mouse
flows are tools/gui_flows/v2-44-prompt-bar.txt and v2-162-agent-prompt.txt."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin_link

from specto_app.prompt.scripted import ScriptedAgentSession


def gallery(label: str, x: float) -> dict:
    return {
        "type": "gallery",
        "label": label,
        "layout": {"position": [x, 0], "size": [640, 400]},
    }


def turns_for(live) -> list[list[dict]]:
    bad = live.document()
    bad["views"]["kobi"] = gallery("Front", 300)
    bad["links"]["l2"] = pin_link("kobi")
    bad["root_view"] = "kobi"
    good = live.document()
    good["views"]["kobi"] = gallery("Front", 300)
    good["links"]["l2"] = pin_link("kobi")
    better = dict(good, views={**good["views"], "tamu": gallery("Rear", 960)})
    better["links"] = {**good["links"], "l3": pin_link("tamu")}
    return [
        [
            {"tool": "submit_canvas", "args": {"document": bad, "label": "front gallery"}},
            {"tool": "submit_canvas", "args": {"document": good, "label": "front gallery"}},
            {"tool": "submit_canvas", "args": {"document": better, "label": "front and rear"}},
            {"text": "Added front and rear galleries next to the file tree."},
        ],
        [{"text": "The file tree is under your mouse."}],
        [{"text": "Still there."}],
        [{"text": "That is notes/a.md."}],
    ]


SEED = ("runs/front/00001_1.jpg", "runs/front/00001_1.json", "runs/rear/00001_1.jpg", "notes/a.md")


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

    sessions: list[ScriptedAgentSession] = []

    def factory(tools):
        s = ScriptedAgentSession(tools, turns_for(a.live))
        sessions.append(s)
        return s

    a = SpectoApp(
        project, jobs="inline", web=False, offscreen=True, serve=True, agent=factory, loop=aloop
    )
    a.start()
    a.sessions = sessions  # type: ignore[attr-defined]
    spin(50)
    yield a
    a.close()
    spin(20)


def context_block(session: ScriptedAgentSession, n: int) -> dict:
    text = session.received[n]["context"]
    return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def test_typing_a_request_composes_the_canvas_as_one_undo_step(app, aspin, spin):
    bar = app.prompt_bar
    outlined: list[list[str]] = []
    bar.touched.connect(lambda ids: outlined.append(list(ids)))
    before = app.live.document()

    assert bar.submit("add front and rear galleries") is True
    assert bar.busy and bar.status == "working"
    aspin(until=lambda: not bar.busy)

    # the canvas changed through submit, as one undo step, with no prompt in between
    assert set(app.live.data["views"]) == {"fege", "kobi", "tamu"}
    assert app.controller.undoDepth == 1 and app.controller.undoCursor == 1
    assert app.live.history[0].origin == "agent:prompt"
    # the failed first attempt stayed invisible
    assert bar.attemptsFailed == 1
    assert not [n for n in app.notices.log if n["level"] == "error"]
    # the strip: one line, Undo, the changed views outlined
    assert bar.status == "changed" and bar.summary == "front and rear"
    assert bar.replyText.startswith("Added front and rear")
    assert bar.canUndoTurn is True
    bar.panel.summon()  # the panel opens on hover or Ctrl+K, never by itself (#162)
    spin(until=lambda: app.engine.call("shell.locate", {"name": "replyUndo"})["visible"])
    assert app.engine.call("shell.locate", {"name": "promptInput"})["visible"] is True
    assert outlined and set(outlined[-1]) >= {"kobi", "tamu"}
    assert "kobi" in app.hosts.hosts and "tamu" in app.hosts.hosts

    # Undo in the strip reverts the whole turn
    assert bar.undoTurn() is True
    assert app.live.document() == before and app.controller.undoCursor == 0
    assert bar.canUndoTurn is False
    assert bar.undoTurn() is False


def test_every_prompt_carries_the_canvas_context(app, aspin):
    bar = app.prompt_bar
    app.controller.setPan(400, 0)
    # the pan writes the window frame once it settles (NAV_SETTLE_MS): land it now, or it
    # lands between two turns and the next prompt carries the document again (#178)
    app.controller.flush_navigation()
    tree = app.live.data["views"]["fege"]["layout"]
    inside = (tree["position"][0] + 40, tree["position"][1] + 40)
    bar.setMouse(*inside)

    bar.submit("add front and rear galleries")
    aspin(until=lambda: not bar.busy)
    bar.submit("what is under my mouse?")
    aspin(until=lambda: not bar.busy)

    session = app.sessions[0]
    first, second = context_block(session, 0), context_block(session, 1)
    assert first["viewport"] == app.controller.viewport
    assert first["viewport"]["x"] == 400 - 12 and first["viewport"]["w"] > 600
    assert first["mouse"] == {"x": inside[0], "y": inside[1], "over": "fege"}
    assert set(first["document"]["views"]) == {"fege"}  # the first turn saw the whole document
    # the project's files, folder by folder, so "the front frames" resolves to a path
    assert first["files"] == {
        "project": {
            "root": str(app.project.root),
            "count": 4,
            "folders": {
                "notes": {".md": 1},
                "runs/front": {".jpg": 1, ".json": 1},
                "runs/rear": {".jpg": 1},
            },
        }
    }
    assert first["selection"] == {}
    # the second turn: the agent wrote the document itself, so its hash stands in
    assert "document" not in second and second["document_hash"] != first["document_hash"]
    assert session.received[1]["prompt"] == "what is under my mouse?"
    assert bar.status == "answered" and bar.canUndoTurn is False
    assert bar.summary == "The file tree is under your mouse."

    # a user gesture changed it since: the whole document travels again
    from specto_core.gestures import move_view

    move_view(app.live, "kobi", (320, 40))
    bar.clearMouse()
    bar.submit("and now?")
    aspin(until=lambda: not bar.busy)
    third = context_block(session, 2)
    assert set(third["document"]["views"]) == {"fege", "kobi", "tamu"}
    assert third["document"]["views"]["kobi"]["layout"]["position"] == [320, 40]
    assert third["mouse"] is None

    # files picked in the tree (its selected_files port) travel as its selection
    from specto_core.links import FilesValue

    app.engine.links.emit(
        "fege", "selected_files", FilesValue(root=str(app.project.root), paths=["notes/a.md"])
    )
    bar.submit("and this file?")
    aspin(until=lambda: not bar.busy)
    picked = context_block(session, 3)
    assert picked["selection"] == {
        "fege": {"selected_files": {"count": 1, "paths": ["notes/a.md"]}}
    }
    assert "document" not in picked  # nothing changed the document since


def test_a_project_slash_command_leaves_the_context_unseen(app, aspin):
    """SPEC-30: a project slash command goes to the CLI alone (``verbatim=False``: no
    canvas context), so the turn after it still carries what the agent has not seen: the
    document a user gesture changed and the commands added since its last turn."""
    from specto_core.gestures import move_view

    bar = app.prompt_bar
    bar.submit("add front and rear galleries")
    aspin(until=lambda: not bar.busy)
    session = app.sessions[0]
    assert "document" in context_block(session, 0)

    # since that turn the user moved a view and the project gained a command
    move_view(app.live, "kobi", (320, 40))
    commands = app.project.root / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "layout.md").write_text("Review the layout. $ARGUMENTS\n", encoding="utf-8")

    assert bar.submit("/layout review") is True
    aspin(until=lambda: not bar.busy)
    assert session.received[1]["prompt"] == "/layout review"
    assert session.received[1]["verbatim"] is False  # the context never reached the agent

    bar.submit("what changed?")
    aspin(until=lambda: not bar.busy)
    after = context_block(session, 2)
    assert after["document"]["views"]["kobi"]["layout"]["position"] == [320, 40]
    assert [c["name"] for c in after["commands"]] == ["/layout"]

    # that turn did carry them: the next one has the hash alone again
    bar.submit("and now?")
    aspin(until=lambda: not bar.busy)
    settled = context_block(session, 3)
    assert "document" not in settled and "commands" not in settled


def test_ask_agent_to_change_prefills_the_field(app, spin):
    """The link popover's "Ask agent to change" (#45) calls ``promptBar.prefill``: the
    text lands in the field (``draft``), focused, with the bar up."""
    bar = app.prompt_bar
    assert bar.draft == ""
    bar.prefill('Change link l2 ("The gallery shows the front frames") so that ')
    spin(until=lambda: bar.draft != "")
    assert bar.draft == 'Change link l2 ("The gallery shows the front frames") so that '
    spin(until=lambda: app.engine.call("shell.locate", {"name": "promptInput"})["visible"])


def test_a_built_in_slash_command_is_refused_before_it_reaches_the_session(app):
    bar = app.prompt_bar
    assert bar.submit("/init") is False
    assert bar.submit("   ") is False
    assert app.sessions == []
    assert any("/init" in n["message"] for n in app.notices.log)


def test_the_detailed_diff_of_a_turn_is_there_on_request(app, aspin, spin):
    """Story 15: the strip shows one line; the diff is a click away, quiet by default."""
    bar = app.prompt_bar
    assert bar.turnDiff == ""
    bar.submit("add front and rear galleries")
    aspin(until=lambda: not bar.busy)
    lines = bar.turnDiff.splitlines()
    assert lines[0].startswith("+ view kobi: gallery 'Front'")
    assert lines[1].startswith("+ view tamu: gallery 'Rear'")
    bar.panel.summon()
    spin(until=lambda: app.engine.call("shell.locate", {"name": "replyDiff"})["visible"])
    detailed = app.engine.call("canvas.history", {"txn": bar.reply.turn_txn, "patch": True})
    assert detailed["history"][0]["diff"] == lines
    bar.undoTurn()
    assert bar.turnDiff == ""


def test_the_model_chip_switches_the_model_for_the_next_turn_and_is_remembered(app, aspin, spin):
    """#73: Opus / Sonnet / Haiku in the prompt bar; the choice reaches the session before
    its next turn and is kept per user in the app settings, not in the canvas."""
    from specto_app.prompt.bar import saved_model

    bar = app.prompt_bar
    assert bar.model == "sonnet" and bar.modelLabel == "Sonnet"  # the default (#73)
    assert [m["name"] for m in bar.models] == ["opus", "sonnet", "haiku"]

    bar.submit("add front and rear galleries")
    aspin(until=lambda: not bar.busy)
    bar.panel.summon()
    spin(until=lambda: app.engine.call("shell.locate", {"name": "modelChip"})["visible"])
    assert app.sessions[0].models == ["sonnet"]  # the session was told before it started

    assert bar.setModel("opus") is True and bar.model == "opus" and bar.modelLabel == "Opus"
    assert bar.setModel("gpt") is False and bar.model == "opus"  # not one of the three
    bar.submit("what is under my mouse?")
    aspin(until=lambda: not bar.busy)
    assert app.sessions[0].models == ["sonnet", "opus"]  # applied on the next turn
    assert len(app.sessions) == 1  # the live session was not replaced
    assert saved_model() == "opus"  # per user, in the app settings
    assert "model" not in json.dumps(app.live.document())  # not in the canvas document
    bar.setModel("sonnet")


def test_the_app_s_claude_session_gets_the_reference_built_from_its_own_engine(app):
    """#73: the app's view types (file tree, gallery, ...) and link kinds reach the live
    session's system prompt; no CLI starts for this (the session is only constructed)."""
    app.agent = "claude"
    session = app._session_factory()(app.prompt_bar.tools)
    assert "file_tree" in session.reference and "gallery" in session.reference
    assert "intaker" in session.reference


def test_a_turn_that_saves_a_skill_shows_it_in_the_strip_with_open_and_undo(app, aspin, spin):
    """#161: the agent saves a project skill; the strip says "saved skill specto:<name>"
    with Open (the file, rendered as markdown) and Undo (the previous file back, one
    step); the agent uses the skill in its next turn; a skill change it did not make
    itself (the Undo) reaches it in the next prompt's context."""
    bar = app.prompt_bar
    sessions: list[ScriptedAgentSession] = []
    save = {
        "name": "front-run",
        "description": "Lay out a front run.",
        "body": "# Front run\n\nPut the gallery **left** of the image view.\n",
    }
    update = dict(save, body="# Front run\n\nPut the gallery **right** of the image view.\n")

    def factory(tools):
        s = ScriptedAgentSession(
            tools,
            [
                [{"tool": "save_skill", "args": save}, {"text": "Saved the layout as a skill."}],
                [{"skill": "specto:front-run"}, {"text": "Following specto:front-run."}],
                [{"tool": "save_skill", "args": update}, {"text": "Updated it."}],
                [{"text": "ok"}],
            ],
        )
        sessions.append(s)
        return s

    bar.factory = factory
    assert bar.skillNote == "" and bar.canUndoSkills is False

    bar.submit("remember: front runs get the gallery left of the image")
    aspin(until=lambda: not bar.busy)

    path = app.project.root / ".specto" / "skills" / "front-run" / "SKILL.md"
    v1 = path.read_text(encoding="utf-8")
    assert bar.status == "answered" and bar.summary == "Saved the layout as a skill."
    assert bar.skillNote == "saved skill specto:front-run"
    assert bar.canUndoSkills is True
    bar.panel.summon()
    spin(until=lambda: app.engine.call("shell.locate", {"name": "replySkill"})["visible"])
    assert app.engine.call("shell.locate", {"name": "replySkillUndo"})["visible"] is True
    assert app.engine.call("shell.locate", {"name": "replySkillOpen"})["visible"] is True
    # Open: the file as markdown (the frontmatter shown as a heading line, not raw YAML)
    assert bar.skillPath == str(path)
    assert "Put the gallery **left** of the image view." in bar.skillMarkdown
    assert "specto:front-run" in bar.skillMarkdown and "---" not in bar.skillMarkdown
    assert app.live.history == []  # no canvas change, no canvas undo step

    # the next turn uses it (the scripted agent reads the skill as the Skill tool would)
    bar.submit("lay out the front run")
    aspin(until=lambda: not bar.busy)
    assert sessions[0].skills_used == [("specto:front-run", v1)]
    assert bar.skillNote == "" and bar.canUndoSkills is False  # this turn saved none
    # the skill row is no longer the latest turn's: it gives up the flows' names (#164)
    spin(until=lambda: not app.engine.call("shell.locate", {"name": "replySkill"})["visible"])
    assert app.engine.call("shell.locate", {"name": "replySkillUndo"})["visible"] is False

    # an update; Undo puts the previous file back in one step
    bar.submit("no, the gallery goes right")
    aspin(until=lambda: not bar.busy)
    assert "**right**" in path.read_text(encoding="utf-8")
    assert bar.undoSkills() is True
    assert path.read_text(encoding="utf-8") == v1
    assert bar.canUndoSkills is False and bar.skillsUndone is True
    assert bar.undoSkills() is False

    # the agent did not make that change: the next prompt tells it the skills now
    bar.submit("and now?")
    aspin(until=lambda: not bar.busy)
    assert "skills" not in context_block(sessions[0], 1)  # it saved that one itself
    assert context_block(sessions[0], 3)["skills"] == [
        {"name": "specto:front-run", "description": "Lay out a front run."}
    ]
