"""The Claude Code session behind the prompt bar, short of a live model (#44, ADR 0015,
research #30): the guards (an inherited API key must not outrank the user's login; the
home CLAUDE.md must not leak in), the restricted tool set and project content, the
slash allowlist, and the six Specto tools as in-process SDK tools."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from specto_app.prompt.session import (
    agent_options,
    claude_md_excludes,
    scrub_environment,
    sdk_tools,
    slash_command,
)
from specto_app.prompt.turn import TurnTools
from specto_core import CallContext, Engine, Project
from specto_core.agent_tools import AGENT_TOOL_NAMES, AgentTools

PROMPT_BAR = CallContext(connection="prompt-bar", origin="agent:prompt")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    proj = Project.init(tmp_path / "data" / "run42", "run42")
    return proj.root


def skill(root: Path, where: str, name: str) -> None:
    d = root / where / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\nhello\n", encoding="utf-8")


def test_an_inherited_api_key_is_removed_unless_the_user_keeps_it():
    env = {
        "PATH": "/bin",
        "ANTHROPIC_API_KEY": "sk-stale",
        "ANTHROPIC_AUTH_TOKEN": "tok",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
    }
    removed = scrub_environment(env)
    assert removed == [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
    ]
    assert env == {"PATH": "/bin"}

    kept = {"ANTHROPIC_API_KEY": "sk-mine", "SPECTO_AGENT_KEEP_API_KEY": "1", "CLAUDECODE": "1"}
    assert scrub_environment(kept) == ["CLAUDECODE"]
    assert kept["ANTHROPIC_API_KEY"] == "sk-mine"


def test_the_home_claude_md_and_every_ancestor_memory_file_are_excluded(root: Path):
    patterns = claude_md_excludes(root)
    home_like = root.parent.parent  # tmp_path stands in for the home directory here
    as_posix = home_like.as_posix()
    assert f"{as_posix}/CLAUDE.md" in patterns
    assert f"{as_posix}/.claude/CLAUDE.md" in patterns
    assert f"{as_posix}/.claude/rules/**" in patterns
    assert f"{root.parent.as_posix()}/CLAUDE.local.md" in patterns
    assert f"{root.anchor.replace(chr(92), '/').rstrip('/')}/CLAUDE.md" in patterns
    # nothing inside the project is excluded: its own CLAUDE.md is the point
    assert not any(p.startswith(root.as_posix() + "/") for p in patterns)
    assert all("\\" not in p for p in patterns)


def test_the_session_is_restricted_to_specto_tools_and_project_content(root: Path):
    skill(root, ".specto", "hello")
    skill(root, ".claude", "frames")
    (root / ".specto" / "commands").mkdir()
    (root / ".specto" / "commands" / "review.md").write_text("review $ARGUMENTS", encoding="utf-8")
    server = {"type": "sdk", "name": "specto", "instance": object()}

    opts = agent_options(root, server)

    assert opts.tools == ["Skill"] and opts.disallowed_tools == []
    assert opts.allowed_tools == ["mcp__specto__*"]
    assert opts.mcp_servers == {"specto": server} and opts.strict_mcp_config is True
    assert opts.permission_mode == "auto"
    assert Path(opts.cwd) == root and opts.setting_sources == ["project"]
    assert opts.plugins == [{"type": "local", "path": str(root / ".specto")}]
    assert opts.skills == ["frames", "specto:hello"]
    manifest = json.loads((root / ".specto" / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "specto"
    settings = json.loads(opts.settings)
    assert settings["claudeMdExcludes"] == claude_md_excludes(root)
    assert opts.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert opts.include_partial_messages is True
    assert "Specto" in opts.system_prompt and "submit_canvas" in opts.system_prompt


def test_a_project_without_skills_gets_no_skill_tool_and_no_plugin(root: Path):
    opts = agent_options(root, {"type": "sdk", "name": "specto", "instance": object()})
    assert opts.tools == [] and opts.skills == [] and opts.plugins == []
    assert not (root / ".specto" / ".claude-plugin").exists()


def test_slash_commands_pass_only_for_the_project_s_own(root: Path):
    skill(root, ".specto", "hello")
    skill(root, ".claude", "frames")
    (root / ".claude" / "commands").mkdir()
    (root / ".claude" / "commands" / "triage.md").write_text("x", encoding="utf-8")
    (root / ".specto" / "commands").mkdir()
    (root / ".specto" / "commands" / "review.md").write_text("x", encoding="utf-8")

    assert slash_command("add a gallery", root) is None  # not a slash command at all
    assert slash_command("/specto:review the front run", root) == "specto:review"
    assert slash_command("/specto:hello", root) == "specto:hello"
    assert slash_command("/frames", root) == "frames"
    assert slash_command("/triage now", root) == "triage"
    assert slash_command("/init", root) == ""  # a built-in: refused
    assert slash_command("/config", root) == ""
    assert slash_command("/review", root) == ""  # the plugin one needs its namespace


def test_the_six_specto_tools_as_in_process_sdk_tools():
    engine = Engine(project=None)
    tools = TurnTools(AgentTools(lambda n, a: engine.call(n, a, PROMPT_BAR)), engine.live)
    tools.begin()
    sdk = {t.name: t for t in sdk_tools(tools)}
    assert tuple(sdk) == AGENT_TOOL_NAMES
    assert "required" not in sdk["submit_canvas"].input_schema  # edits or document (#98)
    assert set(sdk["submit_canvas"].input_schema["properties"]) == {"edits", "document", "label"}
    assert sdk["describe"].input_schema["required"] == ["target"]

    state = asyncio.run(sdk["state"].handler({}))
    assert not state.get("is_error")
    assert json.loads(state["content"][0]["text"])["document"]["schema_version"] == 2
    bad = engine.live.document()
    bad["root_view"] = "fege"
    rejected = asyncio.run(sdk["submit_canvas"].handler({"document": bad, "label": "root"}))
    assert rejected["is_error"] is True and "root_view" in rejected["content"][0]["text"]
    # the document may travel as JSON text too (ids written twice are caught that way)
    good = engine.live.document()
    good["views"]["fege"]["label"] = "Data"
    ok = asyncio.run(sdk["submit_canvas"].handler({"document": json.dumps(good), "label": "l"}))
    assert not ok.get("is_error") and engine.live.data["views"]["fege"]["label"] == "Data"
    assert tools.labels == ["l"]


def test_the_session_id_is_kept_in_the_project_and_resumed_next_launch(root: Path):
    """Story 17: one session per window that remembers the conversation, across restarts."""
    from specto_app.prompt.session import agent_options, load_session_id, save_session_id

    assert load_session_id(root) is None
    save_session_id(root, "sess-123")
    assert load_session_id(root) == "sess-123"
    assert (root / ".specto" / "agent-session.json").exists()
    opts = agent_options(root, server=None, resume="sess-123")
    assert opts.resume == "sess-123"
    assert agent_options(root, server=None).resume is None


def test_the_system_prompt_tells_the_agent_to_follow_the_selection_unless_data_is_named():
    """Fix after user test 1: every view the agent fed from the file tree was pinned to one
    run, so changing the picks changed nothing. The prompt names the default and the one
    reason to pin."""
    from specto_app.prompt.session import SYSTEM_PROMPT

    assert 'scope "selection"' in SYSTEM_PROMPT and "selected_files" in SYSTEM_PROMPT
    assert 'scope "pinned"' in SYSTEM_PROMPT and "by name" in SYSTEM_PROMPT
    assert "link summary" in SYSTEM_PROMPT


class _FakeClient:
    """Plays one turn's SDK messages back (no CLI)."""

    def __init__(self, messages: list) -> None:
        self.messages = messages
        self.queries: list = []

    async def query(self, prompt) -> None:
        self.queries.append(prompt)

    async def receive_response(self):
        for m in self.messages:
            yield m


def _result(total_cost: float, **kw):
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype="success",
        duration_ms=kw.get("duration_ms", 1200),
        duration_api_ms=kw.get("api_ms", 1000),
        is_error=False,
        num_turns=1,
        session_id="s1",
        total_cost_usd=total_cost,
        usage=kw.get("usage"),
        result="done",
    )


def test_the_turn_reports_its_output_tokens_per_model_call_and_in_all(root: Path):
    """#98's bench: output tokens next to seconds. The result's usage is the turn's
    total; each model call's own count is the final usage its stream's message_delta
    carries (the AssistantMessage's usage is the count at message_start)."""
    from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock

    from specto_app.prompt.session import ClaudeAgentSession, _Job

    engine = Engine(project=None)
    tools = TurnTools(AgentTools(lambda n, a: engine.call(n, a, PROMPT_BAR)), engine.live)
    session = ClaudeAgentSession(tools, root, model="sonnet")

    def event(ev):
        return StreamEvent(uuid="u", session_id="s1", event=ev)

    messages = [
        event({"type": "message_start", "message": {"usage": {"output_tokens": 3}}}),
        AssistantMessage(content=[TextBlock("x")], model="m", usage={"output_tokens": 3}),
        event({"type": "message_delta", "delta": {}, "usage": {"output_tokens": 62}}),
        event({"type": "message_start", "message": {"usage": {"output_tokens": 1}}}),
        event({"type": "message_delta", "delta": {}, "usage": {"output_tokens": 9}}),
        _result(0.01, usage={"output_tokens": 71}),
    ]

    async def run():
        return await session._turn(_FakeClient(messages), _Job("move", "ctx", None, True))

    outcome = asyncio.run(run())

    assert outcome.output_tokens == 71
    assert outcome.call_output_tokens == [62, 9]


def test_the_logged_cost_is_the_turn_s_delta_of_the_session_s_cumulative_total(root: Path):
    """#73: the CLI's total_cost_usd is the session's running total (launch.log showed
    0.150, 0.236, 0.356 for three turns); the turn reports its own share and the total."""
    from claude_agent_sdk import StreamEvent

    from specto_app.prompt.session import ClaudeAgentSession, _Job

    engine = Engine(project=None)
    tools = TurnTools(AgentTools(lambda n, a: engine.call(n, a, PROMPT_BAR)), engine.live)
    session = ClaudeAgentSession(tools, root, model="sonnet")
    delta = StreamEvent(
        uuid="u",
        session_id="s1",
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
    )

    async def run(messages):
        job = _Job("hello", "ctx", None, True)
        return await session._turn(_FakeClient(messages), job)

    first = asyncio.run(run([delta, _result(0.150)]))
    second = asyncio.run(run([delta, _result(0.236, api_ms=800)]))

    assert first.cost_usd == pytest.approx(0.150) and first.session_cost_usd == pytest.approx(0.150)
    assert second.cost_usd == pytest.approx(0.086) and second.session_cost_usd == pytest.approx(
        0.236
    )
    assert second.api_ms == 800 and second.duration_ms == 1200
    assert first.first_token_ms >= 0 and first.text == "Hi"


def test_the_model_choice_applies_on_the_next_turn_of_the_live_session(root: Path):
    """#73: the chip picks opus / sonnet / haiku; a running session switches through the
    SDK's set_model before its next prompt (no restart), once per change."""
    from specto_app.prompt.session import DEFAULT_MODEL, MODELS, ClaudeAgentSession, _Job

    assert MODELS == ("opus", "sonnet", "haiku") and DEFAULT_MODEL == "sonnet"
    engine = Engine(project=None)
    tools = TurnTools(AgentTools(lambda n, a: engine.call(n, a, PROMPT_BAR)), engine.live)

    fresh = ClaudeAgentSession(tools, root)
    fresh.set_model("sonnet")  # before the CLI starts: the option carries it
    assert agent_options(root, server=None, model=fresh.model).model == "sonnet"

    session = ClaudeAgentSession(tools, root, model="sonnet")
    session.set_model("haiku")  # while it runs: the next turn switches first

    class _Switching(_FakeClient):
        def __init__(self, messages):
            super().__init__(messages)
            self.models: list = []

        async def set_model(self, model=None):
            self.models.append(model)

    client = _Switching([_result(0.01)])

    async def run():
        return await session._turn(client, _Job("hello", "ctx", None, True))

    asyncio.run(run())
    asyncio.run(run())
    assert client.models == ["haiku"] and len(client.queries) == 2


def test_the_session_prompt_carries_the_view_types_and_link_kinds_so_describe_is_not_needed():
    """#73 round trips: the baseline showed Opus calling describe and Haiku calling state
    before a one-submit change. The standing reference (built from the engine: every view
    type's ports and params, every link kind's params and examples) rides in the system
    prompt, cached by the CLI; the prompt says the context's document is the state."""
    from specto_app.hosts import HostContext, HostRegistry
    from specto_app.prompt.reference import reference_text
    from specto_app.prompt.session import SYSTEM_PROMPT
    from specto_app.views import factories

    # as the app builds it (app.py): core's view types plus the app's (3d, urdf, ...)
    engine = Engine(project=None)
    reg = HostRegistry(HostContext(engine, None), load_entry_points=False)
    for factory in factories():
        reg.add_factory(factory)
    ref = reference_text(lambda n, a: engine.call(n, a, PROMPT_BAR))
    reg.dispose_all()

    assert "gallery" in ref and "file_tree" in ref and "selected_files" in ref
    assert "intaker" in ref and '"scope":"selection"' in ref and '"scope":"pinned"' in ref
    assert "follow" in ref and "time_map" in ref and "read_contents" in ref
    assert "tile" in ref and "urdf" in ref  # view types' params with their defaults
    # about 6-7k tokens, read from the cache after turn one. Measured with the app's view
    # types: 23.9k on main before SPEC-43 (the old 16000 counted core's only), 25.8k
    # with the urdf view's card (2.0k); a new card is trimmed before this is raised
    assert len(ref) < 26500
    opts = agent_options(Path("."), server=None, reference=ref)
    assert opts.system_prompt.startswith(SYSTEM_PROMPT) and opts.system_prompt.endswith(ref)
    assert "do not call state" in SYSTEM_PROMPT and "do not call describe" in SYSTEM_PROMPT
    assert "one submit_canvas" in SYSTEM_PROMPT


def test_the_session_prompt_explains_view_roles_and_the_reference_gives_each_type_its_role():
    """ADR 0020: the agent composes by role, a browser feeding a viewer or player, rather
    than reaching for one view per data type."""
    from specto_app.prompt.reference import reference_text
    from specto_app.prompt.session import SYSTEM_PROMPT

    engine = Engine(project=None)
    ref = reference_text(lambda n, a: engine.call(n, a, PROMPT_BAR))
    cards = dict(line[2:].split(": ", 1) for line in ref.splitlines() if line.startswith("- "))
    picked = ("file_tree", "gallery", "video", "pdf", "3d")
    assert {name: json.loads(cards[name])["role"] for name in picked} == {
        "file_tree": "browser",
        "gallery": "browser",
        "video": "player",
        "pdf": "viewer",
        "3d": "scene",
    }
    for role in ("browser", "viewer", "player", "scene"):
        assert role in SYSTEM_PROMPT
    assert "browser's selection feeds" in SYSTEM_PROMPT


def test_the_session_prompt_makes_the_preview_the_default_quick_look():
    """#106 (ADR 0020 decision 3): "show me this" is one preview, whatever the file's
    kind; the typed viewers are for links that need their ports. The reference card of
    the preview says so too (its manifest, as the app registers it)."""
    from specto_app.prompt.reference import reference_text
    from specto_app.prompt.session import SYSTEM_PROMPT
    from specto_app.views.preview_view import PREVIEW_PLUGIN

    assert "quick look" in SYSTEM_PROMPT and "preview" in SYSTEM_PROMPT
    assert "typed ports" in SYSTEM_PROMPT
    engine = Engine(project=None)
    engine.plugins.register(PREVIEW_PLUGIN)
    ref = reference_text(lambda n, a: engine.call(n, a, PROMPT_BAR))
    cards = dict(line[2:].split(": ", 1) for line in ref.splitlines() if line.startswith("- "))
    card = json.loads(cards["preview"])
    assert card["role"] == "viewer" and "double-click" in card["doc"]
    assert card["ports"]["inputs"] == {"data": "collection", "selection": "selection"}
    assert "time" not in card["ports"]["inputs"]


def test_the_model_stops_after_a_submit_that_changed_the_canvas(root: Path):
    """The user's decision on #98: no closing reply after a change. A PostToolUse hook on
    submit_canvas stops the model once the turn's tools say a submit changed the canvas
    (a rejected or empty submit lets it go on). Playing a walk-through ends the turn the
    same way (#174)."""
    done = {"value": False}
    options = agent_options(root, server=None, done=lambda: done["value"])
    (matcher,) = options.hooks["PostToolUse"]
    assert matcher.matcher == "mcp__specto__submit_canvas|mcp__specto__walkthrough_play"
    (hook,) = matcher.hooks

    assert asyncio.run(hook({}, "u1", {})) == {}
    done["value"] = True
    stop = asyncio.run(hook({}, "u1", {}))
    assert stop["continue_"] is False and stop["stopReason"]
    assert not agent_options(root, server=None).hooks  # no turn tools, no hook


def test_the_session_prompt_prefers_edits_and_the_reference_shows_one_per_worked_prompt():
    """#98: the agent writes only what changes. The system prompt makes a simple change
    one submit_canvas with edits, the whole document only for a rewrite, and a change
    ends the turn with no reply; the reference shows a move and a new view as edits."""
    from specto_app.prompt.reference import reference_text
    from specto_app.prompt.session import SYSTEM_PROMPT

    assert "one submit_canvas call with edits" in SYSTEM_PROMPT
    assert "whole document only" in SYSTEM_PROMPT and "null removes" in SYSTEM_PROMPT
    assert "ends your turn" in SYSTEM_PROMPT and "A submit must be the whole" not in SYSTEM_PROMPT
    engine = Engine(project=None)
    ref = reference_text(lambda n, a: engine.call(n, a, PROMPT_BAR))
    examples = [
        json.loads(line.split(": ", 1)[1]) for line in ref.splitlines() if line.startswith("edits ")
    ]
    assert [list(e["edits"]) for e in examples] == [["views"], ["views", "links"]]
    move, gallery = (e["edits"] for e in examples)
    assert move["views"]["kobi"] == {"layout": {"position": [330, 0]}}
    assert set(gallery["views"]) == {"new1"} and set(gallery["links"]) == {"new2"}
    # the example is a valid edit of a canvas with that file tree and gallery
    from specto_core.live_canvas import LiveCanvas

    live = LiveCanvas.headless("demo")
    tree = next(iter(live.data["views"]))
    text = json.dumps(gallery).replace('"fege"', json.dumps(tree))
    assert live.submit(edits=text, origin="agent:prompt").renamed["new2"] == "l2"


class _LiveClient:
    """A connected ``ClaudeSDKClient`` stand-in whose turn never ends; leaving it is the
    CLI's disconnect (``exit_hangs``: a CLI that does not end on stdin EOF)."""

    exit_hangs = False
    log: list[str] = []

    def __init__(self, options) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        self.log.append("disconnect")
        if self.exit_hangs:
            await asyncio.Event().wait()

    async def query(self, prompt) -> None:
        pass

    async def receive_response(self):
        await asyncio.Event().wait()
        yield None


@pytest.mark.parametrize("cli", ["ends on EOF", "ignores EOF"])
def test_closing_mid_turn_ends_the_session_within_its_bound(root: Path, monkeypatch, cli):
    """#118: closing the window while a turn runs. The worker holds the client until the
    turn ends, so the close cancels the turn; a CLI that still does not end is killed
    once the bound passes (a live CLI holds the asyncio loop's close forever)."""
    import time

    import claude_agent_sdk

    from specto_app.prompt import session as session_mod

    log: list[str] = []
    monkeypatch.setattr(_LiveClient, "log", log)
    monkeypatch.setattr(_LiveClient, "exit_hangs", cli == "ignores EOF")
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _LiveClient)
    monkeypatch.setattr(session_mod, "CLOSE_S", 0.3)
    monkeypatch.setattr(session_mod, "reap_cli", lambda pids=(): log.append("reap"))
    engine = Engine(project=None)
    tools = TurnTools(AgentTools(lambda n, a: engine.call(n, a, PROMPT_BAR)), engine.live)

    async def run() -> float:
        session = session_mod.ClaudeAgentSession(tools, root, environ={}, log=lambda s: None)
        turn = asyncio.ensure_future(session.turn("hi", "ctx"))
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        await session.close()
        took = time.monotonic() - t0
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        return took

    took = asyncio.run(run())
    assert log[0] == "disconnect" and log[-1] == "reap"
    assert took < (0.2 if cli == "ends on EOF" else 0.3 + 1.2)


class _SpawningClient:
    """A ``ClaudeSDKClient`` stand-in whose connect starts a real child that never reads
    its stdin (a CLI that ignores EOF) and whose disconnect never ends."""

    started: list = []

    def __init__(self, options) -> None:
        pass

    async def __aenter__(self):
        import subprocess
        import sys

        # a CLI that never reads stdin and whose own child (the venv's python launcher
        # runs the base interpreter) must go with it
        code = "import time; time.sleep(60)"
        proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE)
        self.started.append(proc)
        return self

    async def __aexit__(self, *exc) -> None:
        await asyncio.Event().wait()


@pytest.mark.parametrize("sdk_reaper", ["removed", "a no-op"])
def test_closing_kills_a_cli_that_ignores_eof_without_the_sdks_private_reaper(
    root: Path, monkeypatch, sdk_reaper
):
    """#150: the close bound does not rest on the Agent SDK's private
    ``_kill_active_children``: the session finds the CLI its connect started among its
    children and kills its process tree once ``CLOSE_S`` passes."""
    import sys
    import time

    import claude_agent_sdk
    from claude_agent_sdk._internal.transport import subprocess_cli

    from specto_app.prompt import procs
    from specto_app.prompt import session as session_mod

    if sdk_reaper == "removed":
        monkeypatch.delattr(subprocess_cli, "_kill_active_children", raising=False)
    else:
        monkeypatch.setattr(subprocess_cli, "_kill_active_children", lambda: None)
    started: list = []
    monkeypatch.setattr(_SpawningClient, "started", started)
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _SpawningClient)
    monkeypatch.setattr(session_mod, "CLOSE_S", 0.3)
    monkeypatch.setattr(session_mod, "CLI_NAMES", (Path(sys.executable).stem.lower(),))
    engine = Engine(project=None)
    tools = TurnTools(AgentTools(lambda n, a: engine.call(n, a, PROMPT_BAR)), engine.live)

    async def run() -> set[int]:
        session = session_mod.ClaudeAgentSession(tools, root, environ={}, log=lambda s: None)
        await session.start()
        (cli,) = started
        deadline = time.monotonic() + 5
        tree: set[int] = set()
        while not tree and time.monotonic() < deadline:  # the launcher starts its child
            tree = {pid for pid, ppid, _ in procs._table() if ppid == cli.pid}
            await asyncio.sleep(0.05)
        await session.close()
        return tree

    try:
        tree = asyncio.run(run())
        (cli,) = started
        deadline = time.monotonic() + 5
        while cli.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert cli.poll() is not None, "the CLI outlived the close"
        time.sleep(0.2)
        alive = {pid for pid, _, _ in procs._table()}
        assert not tree & alive, "the CLI's own child outlived the close"
    finally:
        for proc in started:
            if proc.poll() is None:
                procs.kill_tree(proc.pid)


def test_the_sessions_command_line_stays_under_the_windows_limit(app):
    """#199: Windows refuses a command line over 32767 characters (CreateProcess fails
    with WinError 206, surfaced as FileNotFoundError, which the Agent SDK relabels
    CLINotFoundError "Claude Code not found"), and the system prompt with the full
    app's reference had grown past it on the command line. The session hands the
    prompt over as a file (``--system-prompt-file``), so the spawn stays far under the
    limit whatever the reference grows to."""
    import subprocess

    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from specto_app.prompt.session import SYSTEM_PROMPT

    session = app.prompt_bar.factory(app.prompt_bar.tools)
    assert len(session.reference) > 10000  # the full app's view types and link kinds
    session.resume_id = "d7f0a0a4-3202-462d-b388-e8ba4a9002d1"
    options = session.options()
    transport = SubprocessCLITransport("x", options)
    exe = "C:/Users/someone/.venv/Lib/site-packages/claude_agent_sdk/_bundled/claude.exe"
    transport._cli_path = exe
    cmd = transport._build_command()

    assert len(subprocess.list2cmdline(cmd)) < 32767 - 8000  # room for a deep project path
    prompt = options.system_prompt
    assert isinstance(prompt, dict) and prompt["type"] == "file"
    text = Path(prompt["path"]).read_text(encoding="utf-8")
    assert text.startswith(SYSTEM_PROMPT) and text.endswith(session.reference)


def test_a_spawn_error_reaches_the_thread_with_the_real_oserror(root: Path, monkeypatch):
    """#199: the Agent SDK turns any FileNotFoundError from the spawn into "Claude Code
    not found at <path>", a misleading line when the file exists. The session's start
    error names the real OSError (with its WinError) and, for WinError 206, says the
    command line was the problem."""
    import claude_agent_sdk
    from claude_agent_sdk import CLINotFoundError

    from specto_app.prompt import session as session_mod
    from specto_core.errors import SpectoError

    class _Client:
        def __init__(self, options) -> None:
            pass

        async def __aenter__(self):
            cause = FileNotFoundError(2, "The filename or extension is too long", None, 206)
            cause.winerror = 206  # the constructor sets it on Windows only
            raise CLINotFoundError("Claude Code not found at: C:/x/claude.exe") from cause

        async def __aexit__(self, *exc) -> None:
            pass

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _Client)
    engine = Engine(project=None)
    tools = TurnTools(AgentTools(lambda n, a: engine.call(n, a, PROMPT_BAR)), engine.live)
    log: list[str] = []
    session = session_mod.ClaudeAgentSession(tools, root, environ={}, log=log.append)
    session.resume_id = "d7f0a0a4-3202-462d-b388-e8ba4a9002d1"
    with pytest.raises(SpectoError) as info:
        asyncio.run(session.start())
    assert session.resume_id  # the CLI never ran: the conversation is intact
    msg = info.value.message
    assert "Claude Code not found at: C:/x/claude.exe" in msg
    assert "FileNotFoundError" in msg and "WinError 206" in msg and "too long" in msg
    assert "32767" in msg and "command line" in msg
    assert any("WinError 206" in line for line in log)


def test_the_session_runs_the_system_claude_unless_told_otherwise(tmp_path: Path):
    """The system's ``claude`` on PATH signs the window in as the user's own Claude Code
    does; ``SPECTO_AGENT_CLI`` names another, or ``bundled`` for the SDK's own."""
    from specto_app.prompt.session import system_cli

    exe = tmp_path / ("claude.exe" if sys.platform == "win32" else "claude")
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    assert Path(system_cli({"PATH": str(tmp_path)})) == exe
    assert system_cli({"PATH": str(tmp_path / "none")}) is None  # the SDK's bundled CLI
    assert system_cli({}) is None
    assert system_cli({"PATH": str(tmp_path), "SPECTO_AGENT_CLI": "bundled"}) is None
    assert system_cli({"SPECTO_AGENT_CLI": "/opt/cc/claude"}) == "/opt/cc/claude"
    assert agent_options(tmp_path, server=None, cli_path=str(exe)).cli_path == str(exe)


def test_the_preferred_build_wins_over_paths_public_claude_when_installed(tmp_path: Path):
    """A window opened from the Dock or Finder gets a PATH whose first ``claude`` is the
    public build, which signs in with an Anthropic login: on a machine whose Claude Code
    signs in through a cloud provider every turn failed with 401 OAuth access token is
    invalid. ``SPECTO_CLAUDE_CLI`` names the build that carries the machine's provider:
    it wins when installed and executable, PATH decides otherwise, and SPECTO_AGENT_CLI
    still wins over both."""
    from specto_app.prompt.session import system_cli

    name = "claude.exe" if sys.platform == "win32" else "claude"
    public = tmp_path / "bin" / name
    preferred = tmp_path / "home" / "tools" / "bin" / name
    for exe in (public, preferred):
        exe.parent.mkdir(parents=True)
        exe.write_text("", encoding="utf-8")
        exe.chmod(0o755)
    env = {"PATH": str(public.parent), "SPECTO_CLAUDE_CLI": str(preferred)}
    assert Path(system_cli(env)) == preferred
    assert system_cli({**env, "SPECTO_AGENT_CLI": "/opt/cc/claude"}) == "/opt/cc/claude"
    missing = {**env, "SPECTO_CLAUDE_CLI": str(tmp_path / "nowhere" / name)}
    assert Path(system_cli(missing)) == public
    if sys.platform != "win32":
        preferred.chmod(0o644)  # not executable: not a usable build
        assert Path(system_cli(env)) == public


def test_a_refused_login_the_cli_does_not_retry_says_how_to_sign_in():
    """An invalid OAuth token fails at once with a bare result ("Failed to authenticate.
    API Error: 401 OAuth access token is invalid."): the thread says the credentials were
    refused, which CLI ran, and how to sign in, not just the 401."""
    from specto_app.prompt.session import auth_result

    text = "Failed to authenticate. API Error: 401 OAuth access token is invalid."
    msg = auth_result(text, "/Users/u/.local/bin/claude")
    assert "cannot sign in" in msg and "401 OAuth access token is invalid" in msg
    assert "/Users/u/.local/bin/claude" in msg and "SPECTO_AGENT_CLI" in msg
    assert "bundled CLI" in auth_result(text, None)
    assert auth_result("No conversation found with session ID: d7f0", None) == ""


def test_only_the_users_auth_settings_reach_the_session(tmp_path: Path):
    """From the user's settings the session takes how Claude Code authenticates (helpers,
    the provider, its region and models) and nothing else (ADR 0015)."""
    from specto_app.prompt.session import user_auth_settings, user_settings_path

    user = {
        "awsCredentialExport": '"/opt/cc/claude" default-credential-export',
        "awsAuthRefresh": "sso-login",
        "apiKeyHelper": "",
        "hooks": {"Stop": []},
        "permissions": {"allow": ["Bash"]},
        "enabledPlugins": {"x@y": True},
        "env": {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": "us-west-2",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "sonnet-x",
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            "PATH": "/evil",
        },
    }
    path = tmp_path / "cfg" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps(user), encoding="utf-8")
    assert user_settings_path({"CLAUDE_CONFIG_DIR": str(path.parent)}) == path
    auth = user_auth_settings(path)
    assert auth == {
        "awsCredentialExport": user["awsCredentialExport"],
        "awsAuthRefresh": "sso-login",
        "env": {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": "us-west-2",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "sonnet-x",
        },
    }
    settings = json.loads(agent_options(tmp_path, server=None, auth=auth).settings)
    assert settings["awsCredentialExport"] == user["awsCredentialExport"]
    assert settings["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert settings["claudeMdExcludes"] == claude_md_excludes(tmp_path)
    assert agent_options(tmp_path, server=None).setting_sources == ["project"]
    assert user_auth_settings(tmp_path / "missing.json") == {}
    path.write_text("not json", encoding="utf-8")
    assert user_auth_settings(path) == {}


def test_the_env_guard_keeps_the_provider_vars():
    """The API-key guard drops inherited keys, never the provider's own variables."""
    env = {
        "ANTHROPIC_API_KEY": "sk",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_REGION": "us-west-2",
        "AWS_PROFILE": "p",
    }
    assert scrub_environment(env) == ["ANTHROPIC_API_KEY"]
    assert env == {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2", "AWS_PROFILE": "p"}


def test_an_auth_refusal_fails_the_turn_at_once_with_what_to_do(root: Path):
    """A refused credential (an expired AWS token: 403 authentication_failed) makes the
    CLI retry with backoff for minutes, and the thread sat on "working". The first such
    api_retry interrupts the CLI and the turn fails with the reason and the fix; other
    retries (an overload) are left to the CLI's backoff."""
    from claude_agent_sdk import ResultMessage, SystemMessage

    from specto_app.prompt.session import ClaudeAgentSession, _Job, auth_failure

    def retry(status, error):
        data = {
            "type": "system",
            "subtype": "api_retry",
            "attempt": 1,
            "max_retries": 10,
            "error_status": status,
            "error": error,
        }
        return SystemMessage(subtype="api_retry", data=data)

    assert auth_failure(retry(529, "overloaded_error").data) == ""
    assert "sign in" in auth_failure(retry(401, "").data)

    class _Interruptible(_FakeClient):
        interrupted = 0

        async def interrupt(self) -> None:
            self.interrupted += 1

    engine = Engine(project=None)
    tools = TurnTools(AgentTools(lambda n, a: engine.call(n, a, PROMPT_BAR)), engine.live)
    session = ClaudeAgentSession(tools, root, model="sonnet", log=lambda _: None)
    client = _Interruptible(
        [
            retry(529, "overloaded_error"),
            retry(403, "authentication_failed"),
            retry(403, "authentication_failed"),
            ResultMessage(
                subtype="error_during_execution",
                duration_ms=10,
                duration_api_ms=0,
                is_error=True,
                num_turns=0,
                session_id="s1",
                result="",
            ),
        ]
    )

    async def run():
        return await session._turn(client, _Job("hi", "ctx", None, True))

    outcome = asyncio.run(run())

    assert client.interrupted == 1
    assert outcome.is_error
    assert "403 authentication_failed" in outcome.error and "sign in again" in outcome.error
