"""The agent icon and the agent panel in the running shell, offscreen (#162): a scripted
agent's turn lands in the thread as the user's request, the reply rendered as markdown
(a code block with Copy) and the turn row with Undo, which reverts the whole turn; the
icon follows the turn (working while a tool runs, needs you for an unread answer, a
cross when the agent errors); Esc closes the panel and keeps the draft, pinned or not;
the thread comes back on the next launch; a new session starts below a divider; `/`
completes the project's commands; a formatted paste arrives as markdown. A scripted
agent stands in for the model; the real-mouse flow is tools/gui_flows/v2-162-agent-prompt.txt."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin_link
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, Qt, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtTest import QTest

from specto_app.app import find_item
from specto_app.prompt.scripted import ScriptedAgentSession

LEFT = Qt.MouseButton.LeftButton
NOMOD = Qt.KeyboardModifier.NoModifier

REPLY = (
    "Added **kobi**, a gallery of the front frames. Frames `00017` and `00093` have no rear.\n\n"
    '```json\n{ "frame": 17, "rear": null }\n```\n\n'
    "- kobi shows the front frames\n- more in [the run notes](https://example.com/notes)"
)


def gallery(label: str, x: float) -> dict:
    return {"type": "gallery", "label": label, "layout": {"position": [x, 0], "size": [640, 400]}}


def compose_turn(live) -> list[dict]:
    doc = live.document()
    doc["views"]["kobi"] = gallery("Front", 300)
    doc["links"]["l2"] = pin_link("kobi")
    return [
        {"text": REPLY},
        {"wait_ms": 120},
        {"tool": "submit_canvas", "args": {"document": doc, "label": "front gallery"}},
    ]


@pytest.fixture
def project(tmp_path):
    from specto_core import Project

    proj = Project.init(tmp_path / "proj", "demo")
    for name in ("runs/front/00001_1.jpg", "notes/a.md"):
        p = proj.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return proj


def start_app(project, aloop, spin, turns_for):
    from specto_app.app import SpectoApp

    sessions: list[ScriptedAgentSession] = []

    def factory(tools):
        s = ScriptedAgentSession(tools, turns_for(a.live))
        sessions.append(s)
        return s

    a = SpectoApp(
        project, jobs="inline", web=False, offscreen=True, serve=False, agent=factory, loop=aloop
    )
    a.start()
    a.sessions = sessions  # type: ignore[attr-defined]
    spin(50)
    return a


OURS = ("AgentPrompt", "AgentPanel", "AgentIcon", "ThreadRow", "ReplyBlocks", "SlashList")


@pytest.fixture
def qt_messages():
    """Every Qt message (QML warnings at load time included) while the test runs."""
    out: list[str] = []
    previous = qInstallMessageHandler(lambda kind, ctx, msg: out.append(msg))
    yield out
    qInstallMessageHandler(previous)


@pytest.fixture
def app(qapp, project, aloop, spin, qt_messages):
    a = start_app(
        project,
        aloop,
        spin,
        lambda live: [compose_turn(live), [{"text": "The file tree is under your mouse."}]],
    )
    yield a
    a.close()
    spin(20)


def item(app, name):
    return find_item(app.window.contentItem(), name)


def shown_items(app, name) -> list:
    return [
        it for it in _all(app.window.contentItem()) if it.objectName() == name and it.isVisible()
    ]


def visible(app, name) -> bool:
    return app.engine.call("shell.locate", {"name": name})["visible"]


def click(app, spin, name: str):
    it = item(app, name)
    assert it is not None and it.isVisible(), name
    p = it.mapToScene(it.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, LEFT, NOMOD, p)
    spin(30)


def open_panel(app, spin):
    item(app, "promptBar").focusInput()
    spin(until=lambda: visible(app, "promptInput"))


def type_text(app, text: str) -> None:
    """Type into the focused item as a keyboard would (``QTest.keyClicks`` takes widgets)."""
    for ch in text:
        key = ord(ch.upper()) if ch.isalnum() and ch.isascii() else 0
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QCoreApplication.sendEvent(app.window, QKeyEvent(kind, key, NOMOD, ch))


def plain(edit) -> str:
    return edit.getText(0, edit.property("length"))


def test_a_scripted_turn_renders_markdown_in_the_thread_and_its_undo_reverts_it(
    app, aspin, spin, qt_messages
):
    bar = app.prompt_bar
    before = app.live.document()
    open_panel(app, spin)
    assert bar.icon.state == "idle"

    type_text(app, "add the front frames")
    spin(until=lambda: bar.draft == "add the front frames")
    states: list[str] = []
    bar.icon.changed.connect(lambda: states.append(bar.icon.state))
    QTest.keyClick(app.window, Qt.Key.Key_Return)
    spin(until=lambda: bar.busy)
    assert item(app, "promptInput").property("text") == ""  # sent: the input empties

    aspin(until=lambda: not bar.busy)
    # waiting on the model, a tool running (the submit), the model again, done
    assert states == ["thinking", "working", "thinking", "idle"]
    assert bar.icon.state == "idle"  # it changed the canvas: nothing to read
    spin(until=lambda: visible(app, "replyUndo"))

    # the thread: the request, the reply as markdown, the turn row with Undo
    rows = [bar.thread.get(r) for r in range(bar.thread.rowCount())]
    assert [r["kind"] for r in rows] == ["divider", "user", "agent", "turn"]
    assert rows[3]["label"] == "front gallery" and rows[3]["counts"] == "1 view, 1 link"
    assert rows[3]["number"] == 1 and rows[3]["canUndo"] is True
    assert [plain(it) for it in shown_items(app, "userText")] == ["add the front frames"]
    # prose, code, prose: the markdown rendered, not shown raw
    prose = [plain(it) for it in shown_items(app, "replyMarkdown")]
    assert len(prose) == 2
    head = next(t for t in prose if t.startswith("Added"))
    assert head.startswith("Added kobi, a gallery") and "**" not in head  # bold
    assert "00017" in head and "`" not in head  # inline code
    tail = next(t for t in prose if "kobi shows the front frames" in t)
    assert "the run notes" in tail and "](" not in tail  # a list with a link
    codes = [plain(it) for it in shown_items(app, "replyCodeText")]
    assert codes == ['{ "frame": 17, "rear": null }']

    # Copy on the code block puts the code on the clipboard
    click(app, spin, "replyCodeCopy")
    assert QGuiApplication.clipboard().text() == '{ "frame": 17, "rear": null }'

    # the diff, on request
    click(app, spin, "replyDiff")
    spin(until=lambda: visible(app, "replyDiffText"))
    assert plain(item(app, "replyDiffText")).startswith("+ view kobi: gallery 'Front'")

    # the turn row's Undo reverts the whole turn, then says so
    click(app, spin, "replyUndo")
    spin(until=lambda: app.live.document() == before)
    assert bar.thread.get(3)["canUndo"] is False and bar.thread.get(3)["undone"] is True
    assert not visible(app, "replyUndo")
    # no warning from the agent prompt's own QML, from its load on (a link curve losing
    # its link on Undo warns apart from this ticket)
    assert [m for m in qt_messages if any(name in m for name in OURS)] == []


def test_only_the_latest_turn_carries_the_flow_names_after_a_plain_answer(app, aspin, spin):
    """#164: a turn that only answers adds no turn row; the earlier turn row keeps its
    Undo (its step is still the newest) but is no longer the latest turn's, so the names
    the flows drive (replyUndo, replyDiff) leave it."""
    bar = app.prompt_bar
    open_panel(app, spin)
    bar.submit("add the front frames")
    aspin(until=lambda: not bar.busy)
    spin(until=lambda: visible(app, "replyUndo"))
    assert visible(app, "replyDiff")

    bar.submit("where is the file tree?")
    aspin(until=lambda: not bar.busy)
    rows = [bar.thread.get(r) for r in range(bar.thread.rowCount())]
    assert [r["kind"] for r in rows] == ["divider", "user", "agent", "turn", "user", "agent"]
    assert rows[3]["canUndo"] is True and rows[3]["latest"] is False
    spin(until=lambda: not visible(app, "replyUndo"))
    assert not visible(app, "replyDiff")
    entry = rows[3]["entryId"]
    assert visible(app, f"turnUndo:{entry}") and visible(app, f"turnDiff:{entry}")


def _all(root):
    stack = [root]
    while stack:
        it = stack.pop()
        yield it
        stack.extend(it.childItems())


def test_an_answer_that_lands_while_the_panel_is_closed_makes_the_icon_need_the_user(
    app, aspin, spin
):
    bar = app.prompt_bar
    shakes: list[int] = []
    bar.icon.shake.connect(lambda: shakes.append(1))
    bar.submit("add the front frames")
    aspin(until=lambda: not bar.busy)
    bar.submit("what is under my mouse?")
    aspin(until=lambda: not bar.busy)
    assert bar.panel.open is False  # nothing opened by itself
    assert bar.icon.state == "needs_you" and bar.icon.asks == 1 and shakes == [1]
    art = item(app, "agentIconArt")
    spin(until=lambda: art.property("state") == "needs_you")
    assert visible(app, "agentIconBadge")
    bar.panel.summon()  # Ctrl+K, or a hover on the icon
    assert bar.icon.state == "idle" and bar.icon.asks == 0


def test_an_agent_that_errors_shows_a_cross_until_the_next_turn(qapp, project, aloop, spin, aspin):
    from specto_app.app import SpectoApp

    class Broken(ScriptedAgentSession):
        async def turn(self, prompt, context_text, *, on_delta=None, with_context=True):
            raise RuntimeError("the agent session ended during the turn")

    a = SpectoApp(
        project,
        jobs="inline",
        web=False,
        offscreen=True,
        agent=lambda tools: Broken(tools, []),
        loop=aloop,
    )
    a.start()
    try:
        bar = a.prompt_bar
        bar.submit("hello")
        aspin(until=lambda: not bar.busy)
        assert bar.icon.state == "error" and "session ended" in bar.icon.errorText
        spin(until=lambda: item(a, "agentIconArt").property("state") == "error")
        assert visible(a, "agentIconCross") and not visible(a, "agentIconEyes")
        agent = bar.thread.get(bar.thread.rowCount() - 1)
        assert agent["kind"] == "agent" and agent["status"] == "failed"
        assert "session ended" in agent["text"]
    finally:
        a.close()
        spin(20)


def test_an_agent_that_cannot_start_shows_the_cross_from_the_launch(qapp, project, aloop, spin):
    from specto_app.app import SpectoApp

    def factory(tools):
        raise AssertionError("never made")

    factory.unavailable = "the Claude Agent SDK is not installed"  # type: ignore[attr-defined]
    a = SpectoApp(project, jobs="inline", web=False, offscreen=True, agent=factory, loop=aloop)
    a.start()
    try:
        assert a.prompt_bar.icon.state == "error"
        assert "not installed" in a.prompt_bar.icon.errorText
    finally:
        a.close()
        spin(20)


def test_esc_closes_the_panel_pinned_or_not_and_keeps_the_draft(app, spin):
    bar = app.prompt_bar
    prompt = item(app, "promptBar")
    open_panel(app, spin)
    type_text(app, "half a thought")
    spin(until=lambda: bar.draft == "half a thought")
    click(app, spin, "panelPin")
    assert bar.panel.pinned is True
    QTest.keyClick(app.window, Qt.Key.Key_Escape)
    spin(until=lambda: not bar.panel.open)
    assert bar.panel.pinned is True  # the pin stays for next time
    spin(until=lambda: not visible(app, "promptInput"))
    assert item(app, "promptInput").property("text") == "half a thought"
    prompt.focusInput()  # Ctrl+K
    spin(until=lambda: visible(app, "promptInput"))
    assert item(app, "promptInput").property("text") == "half a thought"
    click(app, spin, "panelPin")
    assert bar.panel.pinned is False


def test_shift_enter_makes_a_new_line_and_enter_sends_both(app, aspin, spin):
    bar = app.prompt_bar
    open_panel(app, spin)
    type_text(app, "first line")
    QTest.keyClick(app.window, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    type_text(app, "- second")
    spin(until=lambda: bar.draft == "first line\n- second")
    QTest.keyClick(app.window, Qt.Key.Key_Return)
    aspin(until=lambda: bool(app.sessions) and not bar.busy)
    assert app.sessions[0].received[0]["prompt"] == "first line\n- second"


def test_the_thread_comes_back_on_the_next_launch_and_a_new_session_starts_below_a_divider(
    qapp, project, aloop, spin, aspin
):
    turns = lambda live: [[{"text": "One."}], [{"text": "Two."}], [{"text": "Three."}]]  # noqa: E731
    a = start_app(project, aloop, spin, turns)
    try:
        a.prompt_bar.submit("first")
        aspin(until=lambda: not a.prompt_bar.busy)
    finally:
        a.close()
        spin(20)
    (project.root / ".specto" / "agent-session.json").write_text('{"session_id": "s1"}')

    b = start_app(project, aloop, spin, turns)
    try:
        bar = b.prompt_bar
        rows = [bar.thread.get(r) for r in range(bar.thread.rowCount())]
        assert [(r["kind"], r["text"]) for r in rows][1:] == [("user", "first"), ("agent", "One.")]
        bar.submit("second")
        aspin(until=lambda: not bar.busy)
        first_session = b.sessions[0]

        assert bar.newSession() is True
        assert not (project.root / ".specto" / "agent-session.json").exists()
        last = bar.thread.get(bar.thread.rowCount() - 1)
        assert last["kind"] == "divider" and last["dividerText"].startswith("New session today")
        bar.submit("third")
        aspin(until=lambda: not bar.busy)
        assert len(b.sessions) == 2 and first_session.closed  # a fresh agent session
        assert "document" in b.sessions[1].received[0]["context"]  # it sees the whole canvas
        texts = [bar.thread.get(r)["text"] for r in range(bar.thread.rowCount())]
        # the old session stays readable above the divider
        assert texts == [
            "Session started",
            "first",
            "One.",
            "second",
            "One.",  # each session runs its script from the start
            "New session",
            "third",
            "One.",
        ]
    finally:
        b.close()
        spin(20)


def test_slash_lists_the_project_s_commands_and_greys_the_built_ins(app, spin):
    root = app.project.root
    skill = root / ".specto" / "skills" / "walk-through" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: walk-through\ndescription: Walk a view's chain from the project\n---\nbody\n",
        encoding="utf-8",
    )
    cmd = root / ".claude" / "commands" / "snap-report.md"
    cmd.parent.mkdir(parents=True)
    cmd.write_text("Canvas to PNG with a summary\n", encoding="utf-8")
    bar = app.prompt_bar
    rows = bar.slashCommands("/s")
    assert [(r["name"], r["kind"], r["available"]) for r in rows] == [
        ("specto:walk-through", "skill", True),
        ("snap-report", "command", True),
        ("status", "built-in", False),
    ]
    assert rows[0]["description"] == "Walk a view's chain from the project"
    assert rows[1]["description"] == "Canvas to PNG with a summary"

    open_panel(app, spin)
    type_text(app, "/s")
    spin(until=lambda: visible(app, "slashList"))
    QTest.keyClick(app.window, Qt.Key.Key_Down)
    QTest.keyClick(app.window, Qt.Key.Key_Tab)
    spin(until=lambda: bar.draft == "/snap-report ")
    assert not visible(app, "slashList")
    assert bar.panel.open and app.overworld.active is False  # Tab completed, no overworld


def test_a_formatted_paste_arrives_as_markdown_and_ctrl_shift_v_pastes_plain_text(app, spin):
    bar = app.prompt_bar
    mime = QMimeData()
    mime.setHtml("<p>Add a <b>depth</b> view:</p><ul><li>colormap: <code>turbo</code></li></ul>")
    mime.setText("Add a depth view:\ncolormap: turbo")
    QGuiApplication.clipboard().setMimeData(mime)
    open_panel(app, spin)
    QTest.keyClick(app.window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    spin(until=lambda: bar.draft != "")
    assert bar.draft == "Add a **depth** view:\n\n- colormap: `turbo`"
    assert visible(app, "pasteHint")
    item(app, "promptInput").setProperty("text", "")
    QTest.keyClick(
        app.window,
        Qt.Key.Key_V,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    spin(until=lambda: bar.draft != "")
    assert bar.draft == "Add a depth view:\ncolormap: turbo"


def test_the_status_bar_text_starts_clear_of_the_icon(app, spin):
    icon = item(app, "agentIcon")
    undo = item(app, "statusUndo")
    right = icon.mapToScene(icon.boundingRect().topRight()).x()
    assert undo.mapToScene(undo.boundingRect().topLeft()).x() >= right + 8
    # the icon floats above the status bar
    status = item(app, "statusBar")
    assert (
        icon.mapToScene(icon.boundingRect().bottomLeft()).y()
        > status.mapToScene(status.boundingRect().topLeft()).y()
    )


@pytest.mark.parametrize("size", [(1280, 800), (900, 620)])
def test_the_icon_sits_as_far_from_the_window_s_left_as_from_its_bottom(app, spin, size):
    app.window.resize(*size)
    spin(until=lambda: app.window.contentItem().height() == size[1])
    icon = item(app, "agentIcon")
    rect = icon.mapRectToScene(icon.boundingRect())
    left_gap = rect.left()
    bottom_gap = app.window.contentItem().height() - rect.bottom()
    assert abs(left_gap - bottom_gap) <= 1, (left_gap, bottom_gap)
    assert left_gap > 0


def test_the_panel_header_shows_the_s_character(app, spin):
    open_panel(app, spin)
    mark = item(app, "panelMark")
    assert mark is not None and mark.isVisible() and mark.property("small")
    assert find_item(mark, "sBody") is not None


def test_the_empty_canvas_hint_points_at_the_s_icon_not_a_prompt_bar(app):
    """#177: since #162 the agent is the S icon at the bottom left (hover opens its
    thread, Ctrl+K too); there is no prompt bar along the bottom edge."""
    text = item(app, "emptyCanvasHint").property("text")
    assert "the S at the bottom left" in text and "Ctrl+K" in text
    assert "prompt bar" not in text and "bottom edge" not in text


def test_the_agent_is_told_where_the_user_types_and_sees_a_turn():
    from specto_app.prompt.session import SYSTEM_PROMPT

    assert "agent panel" in SYSTEM_PROMPT and "the S icon" in SYSTEM_PROMPT
    assert "strip under the prompt bar" not in SYSTEM_PROMPT
