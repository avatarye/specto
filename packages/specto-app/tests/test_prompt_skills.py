"""Skills the prompt-bar agent writes for its project (#161): ``save_skill`` /
``delete_skill`` write ``.specto/skills/<name>/SKILL.md`` (namespaced ``specto:<name>``),
validated, never declaring allowed-tools; the turn's skill changes undo as one step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specto_app.prompt.session import project_skills
from specto_app.prompt.skills import SkillStore, parse_frontmatter
from specto_core import Project
from specto_core.errors import InvalidArgs, NotFound


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return Project.init(tmp_path / "proj", "proj").root


def test_save_writes_the_skill_file_with_its_frontmatter_and_the_session_lists_it(root: Path):
    store = SkillStore(root)

    change = store.save(
        "front-run-layout",
        "Lay out a front run: gallery left, image right.",
        "Put the gallery left of the image view.\n",
    )

    path = root / ".specto" / "skills" / "front-run-layout" / "SKILL.md"
    assert (
        change.path == path
        and change.before is None
        and change.qualified == "specto:front-run-layout"
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    meta, body = parse_frontmatter(text)
    assert meta == {
        "name": "front-run-layout",
        "description": "Lay out a front run: gallery left, image right.",
    }
    assert body == "Put the gallery left of the image view.\n"
    assert project_skills(root) == ["specto:front-run-layout"]


@pytest.mark.parametrize(
    "name, description, body, why",
    [
        ("Front_Run", "d", "b", "kebab-case"),
        ("front--run", "d", "b", "kebab-case"),
        ("-front", "d", "b", "kebab-case"),
        ("a" * 65, "d", "b", "64"),
        ("front", "", "b", "description"),
        ("front", "two\nlines", "b", "one line"),
        ("front", "d" * 1025, "b", "1024"),
        ("front", "d", "", "body"),
        ("front", "d", "x" * 20_001, "20000"),
        ("front", "d", "---\nallowed-tools: Bash\n---\nrun it", "frontmatter"),
    ],
)
def test_save_refuses_a_bad_name_description_or_body_and_writes_nothing(
    root: Path, name: str, description: str, body: str, why: str
):
    with pytest.raises(InvalidArgs) as err:
        SkillStore(root).save(name, description, body)
    assert why in str(err.value)
    assert not (root / ".specto" / "skills").exists()


def test_a_description_with_quotes_and_colons_round_trips(root: Path):
    store = SkillStore(root)
    desc = 'When the user says "compare runs": two galleries side by side # not a comment'
    store.save("compare-runs", desc, "body\n")
    meta, _ = parse_frontmatter(store.path("compare-runs").read_text(encoding="utf-8"))
    assert meta["description"] == desc


def test_a_skill_file_never_declares_allowed_tools(root: Path):
    store = SkillStore(root)
    store.save("x", "allowed-tools: Bash", "allowed-tools: Bash\n")
    meta, _ = parse_frontmatter(store.path("x").read_text(encoding="utf-8"))
    assert set(meta) == {"name", "description"}


def test_save_updates_a_skill_and_delete_removes_it_each_keeping_the_previous_file(root: Path):
    store = SkillStore(root)
    first = store.save("notes", "Notes layout.", "one\n")
    second = store.save("notes", "Notes layout.", "two\n")
    assert second.before == first.after and second.after.endswith("two\n")

    gone = store.delete("notes")
    assert gone.before == second.after and gone.after is None
    assert not (root / ".specto" / "skills" / "notes").exists()
    assert project_skills(root) == []
    with pytest.raises(NotFound):
        store.delete("notes")


def test_undo_restores_the_previous_file_or_removes_the_new_one_in_one_step(root: Path):
    store = SkillStore(root)
    store.save("kept", "Kept.", "v1\n")
    v1 = store.path("kept").read_text(encoding="utf-8")
    turn = [store.save("kept", "Kept.", "v2\n"), store.save("fresh", "Fresh.", "new\n")]

    assert store.undo(turn) is True

    assert store.path("kept").read_text(encoding="utf-8") == v1
    assert not (root / ".specto" / "skills" / "fresh").exists()
    assert project_skills(root) == ["specto:kept"]
    # a deletion undone brings the file back
    gone = store.delete("kept")
    assert store.undo([gone]) is True
    assert store.path("kept").read_text(encoding="utf-8") == v1


def test_undo_refuses_when_the_file_changed_since_and_changes_nothing(root: Path):
    store = SkillStore(root)
    turn = [store.save("a", "A.", "a\n"), store.save("b", "B.", "b\n")]
    store.path("b").write_text("edited by hand\n", encoding="utf-8")

    assert store.undo(turn) is False

    assert store.path("a").exists()
    assert store.path("b").read_text(encoding="utf-8") == "edited by hand\n"


# -- the tools in a turn (seam 1: a scripted agent, the real tools) ---------------------------
def turn_tools(root: Path):
    from specto_app.prompt.turn import TurnTools
    from specto_core import CallContext, Engine
    from specto_core.agent_tools import AgentTools

    engine = Engine(project=None)
    ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
    agent = AgentTools(lambda n, a: engine.call(n, a, ctx))
    return TurnTools(agent, engine.live, skills=SkillStore(root))


def scripted_turn(tools, steps: list[dict]):
    import asyncio

    from specto_app.prompt.context import CanvasContext
    from specto_app.prompt.scripted import ScriptedAgentSession
    from specto_app.prompt.turn import run_turn

    session = ScriptedAgentSession(tools, [steps])
    ctx = CanvasContext(document=tools.live.document(), viewport={"x": 0, "y": 0, "w": 9, "h": 9})
    return asyncio.run(run_turn(session, tools, "remember this layout", ctx)), session


def test_a_turn_that_saves_a_skill_reports_it_without_changing_the_canvas(root: Path):
    tools = turn_tools(root)
    before = tools.live.document()
    save = {"name": "front-run", "description": "Front runs.", "body": "Gallery left.\n"}

    reply, session = scripted_turn(
        tools,
        [
            {"tool": "save_skill", "args": {"name": "Bad Name", "description": "x", "body": "y"}},
            {"tool": "save_skill", "args": save},
            {"text": "Saved."},
        ],
    )

    bad, ok = session.calls
    assert bad.is_error and "kebab-case" in bad.text  # a fix-it, never raised
    assert not ok.is_error and json.loads(ok.text)["saved"] == "specto:front-run"
    assert "Skill tool" in json.loads(ok.text)["note"]
    assert [c.qualified for c in reply.skills] == ["specto:front-run"]
    assert reply.status == "answered" and reply.turn_txn is None
    assert tools.live.document() == before
    assert tools.failures == 0  # a skill error is no failed submit
    assert (root / ".specto" / "skills" / "front-run" / "SKILL.md").exists()


def test_the_skill_tools_join_the_in_process_server_only_with_a_project(root: Path):
    from specto_app.prompt.session import sdk_tools
    from specto_core.agent_tools import AGENT_TOOL_NAMES

    names = [t.name for t in sdk_tools(turn_tools(root))]
    assert names == [*AGENT_TOOL_NAMES, "save_skill", "delete_skill"]
    sdk = {t.name: t for t in sdk_tools(turn_tools(root))}
    assert sdk["save_skill"].input_schema["required"] == ["name", "description", "body"]


# -- auto-load: the live session reconnects with resume once the skills changed -----------
class _Client:
    """A ``ClaudeSDKClient`` stand-in: records each connect's options; a turn runs
    ``on_query`` (the agent's tool calls) and ends with a result for session ``s1``."""

    connects: list = []
    on_query = None

    def __init__(self, options) -> None:
        self.options = options

    async def __aenter__(self):
        self.connects.append(self.options)
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def query(self, prompt) -> None:
        if self.on_query is not None:
            type(self).on_query()

    async def receive_response(self):
        from claude_agent_sdk import ResultMessage

        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s1",
            total_cost_usd=0.0,
        )


def test_a_skill_saved_in_a_turn_loads_by_resuming_the_session_before_the_next(
    root: Path, monkeypatch
):
    """The CLI fixes its skills allowlist at connect (an in-session reload_skills sees
    the new file but the Skill tool refuses it: "not in this session's skills
    allowlist"), so after a turn that changed a skill the session reconnects with
    resume: same conversation, the new skill listed."""
    import asyncio

    import claude_agent_sdk

    from specto_app.prompt.session import ClaudeAgentSession

    connects: list = []
    monkeypatch.setattr(_Client, "connects", connects)
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _Client)
    store = SkillStore(root)
    tools = turn_tools(root)

    async def run() -> None:
        session = ClaudeAgentSession(tools, root, environ={}, log=lambda s: None)
        _Client.on_query = lambda: store.save("front-run", "Front runs.", "Gallery left.\n")
        await session.turn("remember this", "ctx")
        _Client.on_query = None
        await session.turn("use it", "ctx")
        await session.turn("again", "ctx")  # nothing changed: the same connection
        # a skill changed outside a turn (by hand, or the strip's Undo) loads too
        store.delete("front-run")
        await session.turn("and now", "ctx")
        await session.close()

    asyncio.run(run())

    assert [o.skills for o in connects] == [[], ["specto:front-run"], []]
    assert [o.tools for o in connects] == [[], ["Skill"], []]
    assert [o.resume for o in connects] == [None, "s1", "s1"]


def test_the_session_prompt_says_when_to_save_a_skill_and_when_not():
    from specto_app.prompt.session import SYSTEM_PROMPT

    assert "save_skill" in SYSTEM_PROMPT and "repeatable way of composing" in SYSTEM_PROMPT
    assert "one-off" in SYSTEM_PROMPT and "specto:<name>" in SYSTEM_PROMPT
    assert "delete_skill" in SYSTEM_PROMPT
