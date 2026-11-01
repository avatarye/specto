"""Slash commands the prompt-bar agent writes for its project (#219): ``save_command``
/ ``delete_command`` write ``.claude/commands/<name>.md`` (the project command
``/<name>``), validated, never declaring allowed-tools or a shell line; the turn's
command changes undo as one step; a command saved in a turn loads by resuming the
session before the next, like a skill (#161)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specto_app.prompt.commands import CommandStore, list_commands
from specto_app.prompt.session import project_commands, slash_command
from specto_app.prompt.skills import parse_frontmatter, skills_key
from specto_core import Project
from specto_core.errors import InvalidArgs, NotFound


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return Project.init(tmp_path / "proj", "proj").root


def test_save_writes_the_command_file_and_the_prompt_bar_lets_it_through(root: Path):
    store = CommandStore(root)

    change = store.save(
        "layout",
        "Bring back a saved layout by name, or save the current one.",
        "Invoke the specto:layouts skill, then do this: $ARGUMENTS\n",
        "<name> | save <name>",
    )

    path = root / ".claude" / "commands" / "layout.md"
    assert change.path == path and change.before is None and change.qualified == "/layout"
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    assert meta == {
        "description": "Bring back a saved layout by name, or save the current one.",
        "argument-hint": "<name> | save <name>",
    }
    assert body == "Invoke the specto:layouts skill, then do this: $ARGUMENTS\n"
    assert project_commands(root) == ["layout"]
    assert slash_command("/layout review", root) == "layout"
    assert slash_command("/init", root) == ""
    assert list_commands(root) == [
        {"name": "/layout", "description": meta["description"]},
    ]


@pytest.mark.parametrize(
    "name, description, body, hint, why",
    [
        ("Layout", "d", "b", "", "kebab-case"),
        ("lay--out", "d", "b", "", "kebab-case"),
        ("a" * 65, "d", "b", "", "64"),
        ("layout", "", "b", "", "description"),
        ("layout", "two\nlines", "b", "", "one line"),
        ("layout", "d", "", "", "body"),
        ("layout", "d", "x" * 20_001, "", "20000"),
        ("layout", "d", "---\nallowed-tools: Bash\n---\nrun it", "", "frontmatter"),
        ("layout", "d", "Status:\n!`git status`\nthen go", "", "shell"),
        ("layout", "d", "b", "h" * 201, "200"),
    ],
)
def test_save_refuses_a_bad_name_description_body_or_hint_and_writes_nothing(
    root: Path, name: str, description: str, body: str, hint: str, why: str
):
    with pytest.raises(InvalidArgs) as err:
        CommandStore(root).save(name, description, body, hint)
    assert why in str(err.value)
    assert not (root / ".claude" / "commands").exists()


def test_a_command_file_never_declares_allowed_tools_or_a_model(root: Path):
    store = CommandStore(root)
    store.save("layout", "allowed-tools: Bash", "model: opus\nallowed-tools: Bash\n")
    meta, body = parse_frontmatter(store.path("layout").read_text(encoding="utf-8"))
    assert set(meta) == {"description"} and "Bash" in meta["description"]
    assert body.startswith("model: opus")  # body text, not frontmatter


def test_save_updates_and_delete_removes_each_keeping_the_previous_file(root: Path):
    store = CommandStore(root)
    first = store.save("layout", "One.", "one\n")
    second = store.save("/layout", "Two.", "two\n")  # a leading slash is tolerated
    assert second.before == first.after and second.action == "saved"
    gone = store.delete("layout")
    assert gone.before == second.after and gone.after is None and gone.action == "deleted"
    assert not store.path("layout").exists() and project_commands(root) == []
    with pytest.raises(NotFound):
        store.delete("layout")


def test_undo_restores_the_previous_file_or_removes_the_new_one_in_one_step(root: Path):
    store = CommandStore(root)
    turn1 = [store.save("layout", "One.", "one\n")]
    v1 = store.path("layout").read_text(encoding="utf-8")
    turn2 = [store.save("layout", "Two.", "two\n"), store.save("runs", "Runs.", "runs\n")]

    assert store.undo(turn2) is True
    assert store.path("layout").read_text(encoding="utf-8") == v1
    assert not store.path("runs").exists()
    assert store.undo(turn1) is True
    assert not store.path("layout").exists()


def test_undo_refuses_when_the_file_changed_since_and_changes_nothing(root: Path):
    store = CommandStore(root)
    turn = [store.save("a", "A.", "a\n"), store.save("b", "B.", "b\n")]
    store.path("b").write_text("edited by hand\n", encoding="utf-8")
    assert store.undo(turn) is False
    assert store.path("a").exists()
    assert store.path("b").read_text(encoding="utf-8") == "edited by hand\n"


def test_a_command_change_moves_the_session_s_reload_key_like_a_skill(root: Path):
    before = skills_key(root)
    CommandStore(root).save("layout", "L.", "l\n")
    after = skills_key(root)
    assert after != before and [k[0] for k in after] == [".claude/commands/layout.md"]


# -- the tools in a turn (seam 1: a scripted agent, the real tools) ---------------------------
def turn_tools(root: Path):
    from specto_app.prompt.turn import TurnTools
    from specto_core import CallContext, Engine
    from specto_core.agent_tools import AgentTools

    engine = Engine(project=None)
    ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
    agent = AgentTools(lambda n, a: engine.call(n, a, ctx))
    return TurnTools(agent, engine.live, commands=CommandStore(root))


def scripted_turn(tools, steps: list[dict]):
    import asyncio

    from specto_app.prompt.context import CanvasContext
    from specto_app.prompt.scripted import ScriptedAgentSession
    from specto_app.prompt.turn import run_turn

    session = ScriptedAgentSession(tools, [steps])
    ctx = CanvasContext(document=tools.live.document(), viewport={"x": 0, "y": 0, "w": 9, "h": 9})
    return asyncio.run(run_turn(session, tools, "make a /layout command", ctx)), session


def test_a_turn_that_saves_a_command_reports_it_without_changing_the_canvas(root: Path):
    tools = turn_tools(root)
    before = tools.live.document()
    save = {"name": "layout", "description": "Layouts.", "body": "Do $ARGUMENTS\n"}

    reply, session = scripted_turn(
        tools,
        [
            {"tool": "save_command", "args": {"name": "Bad Name", "description": "x", "body": "y"}},
            {"tool": "save_command", "args": save},
            {"text": "Saved."},
        ],
    )

    bad, ok = session.calls
    assert bad.is_error and "kebab-case" in bad.text
    assert not ok.is_error and json.loads(ok.text)["saved"] == "/layout"
    assert json.loads(ok.text)["path"] == ".claude/commands/layout.md"
    assert [c.qualified for c in reply.commands] == ["/layout"]
    assert reply.status == "answered" and reply.turn_txn is None
    assert tools.live.document() == before and tools.failures == 0
    assert tools.undo_commands(reply) is True
    assert reply.commands == [] and [c.name for c in reply.commands_undone] == ["layout"]
    assert not (root / ".claude" / "commands" / "layout.md").exists()


def test_the_command_tools_join_the_in_process_server_only_with_a_store(root: Path):
    from specto_app.prompt.session import sdk_tools
    from specto_core.agent_tools import AGENT_TOOL_NAMES

    names = [t.name for t in sdk_tools(turn_tools(root))]
    assert names == [*AGENT_TOOL_NAMES, "save_command", "delete_command"]


def test_a_command_saved_in_a_turn_loads_by_resuming_the_session_before_the_next(
    root: Path, monkeypatch
):
    """The CLI reads the project's commands at connect, so after a turn that saved one
    the session reconnects with resume (the same path as a skill, #161): the next turn
    runs on a CLI that knows /layout."""
    import asyncio

    import claude_agent_sdk
    from test_prompt_skills import _Client

    from specto_app.prompt.session import ClaudeAgentSession

    connects: list = []
    monkeypatch.setattr(_Client, "connects", connects)
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _Client)
    store = CommandStore(root)
    tools = turn_tools(root)

    async def run() -> None:
        session = ClaudeAgentSession(tools, root, environ={}, log=lambda s: None)
        _Client.on_query = lambda: store.save("layout", "L.", "l\n")
        await session.turn("make a layout command", "ctx")
        _Client.on_query = None
        await session.turn("/layout review", "ctx", verbatim=False)
        await session.turn("again", "ctx")  # nothing changed: the same connection
        await session.close()

    asyncio.run(run())

    assert len(connects) == 2 and [o.resume for o in connects] == [None, "s1"]
    assert [o.skills for o in connects] == [[], []]  # a command is no skill allowlist entry
    assert all(o.setting_sources == ["project"] for o in connects)  # .claude/commands loads


def test_the_session_prompt_says_when_to_save_a_command_and_how_data_works():
    from specto_app.prompt.session import SYSTEM_PROMPT

    assert "save_command" in SYSTEM_PROMPT and "$ARGUMENTS" in SYSTEM_PROMPT
    assert "delete_command" in SYSTEM_PROMPT
    assert "data_write" in SYSTEM_PROMPT and ".specto/layouts/<name>.json" in SYSTEM_PROMPT
    assert "never grant" in SYSTEM_PROMPT  # a restored python link is not approved by it
