"""The prompt bar's agent loop at seam 1 (#44, spec #35 stories 10-16): a scripted agent
stands in for the model and calls the real in-process tools. One prompt turn is one
undo step, failed submits stay invisible and end the turn after three, Undo reverts
the whole turn, and every message carries the canvas context. No Qt, no model."""

from __future__ import annotations

import asyncio
import json

import pytest
from app_doc_helpers import pin, pin_link

from specto_app.prompt.context import CanvasContext
from specto_app.prompt.scripted import ScriptedAgentSession
from specto_app.prompt.turn import TurnTools, run_turn
from specto_core import CallContext, Engine, gestures
from specto_core.agent_tools import AgentTools
from specto_core.canvas_example import worked_example

PROMPT_BAR = CallContext(connection="prompt-bar", origin="agent:prompt")


def gallery(label: str, x: float) -> dict:
    return {
        "type": "gallery",
        "label": label,
        "layout": {"position": [x, 0], "size": [640, 400]},
    }


@pytest.fixture
def engine() -> Engine:
    return Engine(project=None)


@pytest.fixture
def tools(engine: Engine) -> TurnTools:
    agent_tools = AgentTools(lambda name, args: engine.call(name, args, PROMPT_BAR))
    return TurnTools(agent_tools, engine.live)


def context(engine: Engine, **kw) -> CanvasContext:
    return CanvasContext(
        document=engine.live.document(),
        viewport=kw.get("viewport", {"x": -12, "y": -12, "w": 1600, "h": 900}),
        mouse=kw.get("mouse"),
        selection=kw.get("selection", {}),
    )


def turn(engine, tools, session, prompt: str, ctx: CanvasContext | None = None, deltas=None):
    ctx = ctx or context(engine)
    on_delta = deltas.append if deltas is not None else None
    return asyncio.run(run_turn(session, tools, prompt, ctx, on_delta=on_delta))


def test_one_prompt_turn_with_several_submits_is_one_undo_step(engine, tools):
    before = engine.live.document()
    bad = engine.live.document()
    bad["views"]["kobi"] = gallery("Front", 300)
    bad["links"]["l2"] = pin_link("kobi")
    bad["root_view"] = "kobi"  # a gallery is no player: rejected with a fix-it
    good = engine.live.document()
    good["views"]["kobi"] = gallery("Front", 300)
    good["links"]["l2"] = pin_link("kobi")
    better = dict(good, views={**good["views"], "tamu": gallery("Rear", 960)})
    better["links"] = {**good["links"], "l3": pin_link("tamu")}
    session = ScriptedAgentSession(
        tools,
        [
            [
                {"tool": "state"},
                {"tool": "submit_canvas", "args": {"document": bad, "label": "front gallery"}},
                {"tool": "submit_canvas", "args": {"document": good, "label": "front gallery"}},
                {"tool": "snap", "args": {"target": "kobi"}},
                {"tool": "submit_canvas", "args": {"document": better, "label": "front and rear"}},
                {"text": "Added front and rear galleries next to the file tree."},
            ]
        ],
    )

    reply = turn(engine, tools, session, "add front and rear galleries")

    # the agent's first attempt failed and it retried: invisible to the user
    assert [c.is_error for c in session.calls] == [False, True, False, False, False]
    assert "root_view" in session.calls[1].text  # the fix-it the agent read
    assert reply.status == "changed" and reply.attempts_failed == 1
    assert "rejected" not in reply.text and reply.text.startswith("Added front and rear")
    assert reply.summary == "front and rear"  # the last submit's label, one line
    assert set(reply.touched) >= {"kobi", "tamu"}
    # one undo step for the whole turn, whichever way it is counted
    assert engine.live.cursor == 1 and len(engine.live.history) == 1
    assert reply.turn_txn == engine.live.history[0].id
    assert engine.live.history[0].origin == "agent:prompt"
    assert set(engine.live.data["views"]) == {"fege", "kobi", "tamu"}

    # the strip's Undo reverts the whole turn
    assert tools.undo_turn(reply) is True
    assert engine.live.document() == before
    assert tools.undo_turn(reply) is False  # nothing left of it to undo


def test_three_invalid_submits_end_the_turn_with_a_short_failure(engine, tools):
    before = engine.live.document()
    bad = engine.live.document()
    bad["views"]["kobi"] = gallery("Map", 300)
    bad["links"]["l2"] = pin_link("kobi")
    bad["root_view"] = "kobi"
    attempt = {"tool": "submit_canvas", "args": {"document": bad, "label": "a map"}}
    session = ScriptedAgentSession(
        tools,
        [[attempt, attempt, attempt, attempt, {"text": "I couldn't add the map view."}]],
    )

    reply = turn(engine, tools, session, "add a map")

    errors = [c for c in session.calls if c.is_error]
    assert len(errors) == 4
    assert "third" in errors[2].text and "tell the user" in errors[2].text
    assert "no more" in errors[3].text  # the fourth is refused outright
    assert reply.status == "failed" and reply.attempts_failed == 3
    assert reply.text == "I couldn't add the map view." and reply.summary == reply.text
    assert reply.turn_txn is None and engine.live.history == []
    assert engine.live.document() == before


def test_the_message_carries_the_canvas_context(engine, tools):
    engine.live.submit(worked_example(), origin="agent:prompt", label="compose")
    session = ScriptedAgentSession(tools, [[{"text": "The rear gallery is tamu."}]])
    ctx = context(
        engine,
        viewport={"x": 388, "y": -12, "w": 1400, "h": 820},
        mouse={"x": 700, "y": 200, "over": "tamu"},
        selection={"kobi": {"selection": {"keys": ["00004"], "last": "00004"}}},
    )

    reply = turn(engine, tools, session, "which view is under my mouse?", ctx)

    assert reply.status == "answered" and reply.turn_txn is None
    sent = session.received[0]
    assert sent["prompt"] == "which view is under my mouse?"
    block = json.loads(sent["context"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert set(block["document"]["views"]) == {
        "fege",
        "kobi",
        "tamu",
        "rena",
        "pola",
        "mihu",
        "vesa",
    }
    assert block["viewport"] == {"x": 388, "y": -12, "w": 1400, "h": 820}
    assert block["mouse"] == {"x": 700, "y": 200, "over": "tamu"}
    assert block["selection"]["kobi"]["selection"]["keys"] == ["00004"]


def test_the_reply_streams_into_the_strip_as_it_is_written(engine, tools):
    session = ScriptedAgentSession(tools, [[{"text": "Nothing to change here."}]])
    deltas: list[str] = []

    reply = turn(engine, tools, session, "hello", deltas=deltas)

    assert len(deltas) > 1 and "".join(deltas) == "Nothing to change here."
    assert reply.text == "Nothing to change here."


def test_an_agent_undo_during_the_turn_leaves_the_steps_as_they_are(engine, tools):
    """The agent undid a step from before the turn: no squash, nothing for the strip's
    Undo to revert; the top bar's undo history still has everything."""
    doc = engine.live.document()
    doc["views"]["kobi"] = gallery("Front", 300)
    doc["links"]["l2"] = pin_link("kobi")
    engine.live.submit(doc, origin="agent:prompt", label="front gallery")
    session = ScriptedAgentSession(tools, [[{"tool": "undo"}, {"text": "Undid the gallery."}]])

    reply = turn(engine, tools, session, "undo that")

    assert reply.status == "answered" and reply.turn_txn is None
    assert engine.live.cursor == 0 and len(engine.live.history) == 1
    assert set(engine.live.data["views"]) == {"fege"}


def test_the_reply_carries_the_turn_s_timing_breakdown(engine, tools):
    """#73: the baseline for the latency work. Each tool call is timed by name, the
    first streamed token is timed from the turn's start, and one log line says it all."""
    from specto_app.prompt.turn import timing_line

    good = engine.live.document()
    good["views"]["v2"] = gallery("Front", 300)
    session = ScriptedAgentSession(
        tools,
        [
            [
                {"tool": "state"},
                {"tool": "submit_canvas", "args": {"document": good, "label": "front gallery"}},
                {"text": "Added a front gallery."},
            ]
        ],
    )

    reply = turn(engine, tools, session, "add a front gallery")

    assert [name for name, _ in reply.tool_calls] == ["state", "submit_canvas"]
    assert all(ms >= 0 for _, ms in reply.tool_calls)
    assert reply.first_token_ms >= 0 and reply.total_ms >= reply.first_token_ms
    line = timing_line(reply)
    assert "first token" in line and "state" in line and "submit_canvas" in line
    assert "ms" in line and "USD" not in line  # no cost known: none shown
    reply.cost_usd, reply.session_cost_usd = 0.0864, 0.236
    assert "0.086 USD this turn (0.236 session)" in timing_line(reply)


def test_the_context_says_the_document_is_the_state(engine, tools):
    """#73: with the document in the context (or unchanged since the agent's last turn)
    the agent composes straight away; the note no longer sends it to the state tool."""
    from specto_app.prompt.context import render_context

    full = render_context(context(engine))
    assert "it is the state" in full and "call state" not in full
    unchanged = render_context(CanvasContext(document=None, viewport={}, document_hash="abc"))
    assert "still current" in unchanged and "call state" not in unchanged


def two_galleries(engine: Engine) -> None:
    doc = engine.live.document()
    for vid, x in (("kobi", 400), ("tamu", 1100)):
        doc["views"][vid] = gallery(vid, x)
        pin(doc, vid)
    engine.live.submit(doc, origin="user", label="setup")


def move(vid: str, x: float, y: float) -> dict:
    return {"edits": {"views": {vid: {"layout": {"position": [x, y]}}}}, "label": f"move {vid}"}


def test_a_drag_the_user_makes_mid_turn_is_kept_and_a_clash_is_no_failed_attempt(engine, tools):
    """#98's conflict rule through the turn's tools: the turn marks its start; the user's
    drag of tamu survives the agent's edit of kobi; the agent's edit of tamu itself is
    refused with tamu's live entry, which does not count toward the three attempts."""
    two_galleries(engine)
    tools.begin()
    gestures.move_views(engine.live, {"tamu": (1200, 500)})

    landed = tools.call("submit_canvas", move("kobi", 330, 0))
    clash = tools.call("submit_canvas", move("tamu", 330, 450))

    assert not landed.is_error and clash.is_error
    assert "by the user" in clash.text and "[1200.0,500.0]" in clash.text
    assert tools.failures == 0
    views = engine.live.data["views"]
    assert views["kobi"]["layout"]["position"] == [330.0, 0.0]
    assert views["tamu"]["layout"]["position"] == [1200.0, 500.0]

    # the turn's undo step is the agent's alone: the user's drag stays its own step
    txn, touched, _ = tools.end()
    assert touched == ["kobi"] and engine.live.record(txn).origin == "agent:prompt"
    engine.live.undo(origin="user")
    views = engine.live.data["views"]
    assert views["kobi"]["layout"]["position"] == [400.0, 0.0]
    assert views["tamu"]["layout"]["position"] == [1200.0, 500.0]


def test_a_successful_edit_ends_the_turn_and_its_label_is_the_reply(engine, tools):
    """The user's decision on #98: no closing reply after a change. The turn's tools say
    the turn is done once a submit changed the canvas, and the strip shows the label."""
    two_galleries(engine)
    session = ScriptedAgentSession(
        tools, [[{"tool": "submit_canvas", "args": move("kobi", 330, 0)}]]
    )

    reply = turn(engine, tools, session, "move kobi next to the file tree")

    assert tools.done is True
    assert reply.status == "changed" and reply.summary == "move kobi"
    assert reply.text == "move kobi"


def test_an_error_reached_the_agent_only_when_it_said_or_did_something(engine, tools):
    """SPEC-30: an error with no assistant output (the CLI refused the turn) did not reach
    the model; an error after a reply or a tool call did."""
    from specto_app.prompt.turn import TurnOutcome

    class Ends(ScriptedAgentSession):
        def __init__(self, tools, steps, outcome_text: str) -> None:
            super().__init__(tools, [steps])
            self.outcome_text = outcome_text

        async def turn(self, prompt, context_text, **kw):
            await super().turn(prompt, context_text, **kw)
            return TurnOutcome(text=self.outcome_text, is_error=True, error="it failed")

    refused = turn(engine, tools, Ends(tools, [], ""), "hello")
    assert refused.status == "failed" and refused.reached_agent is False
    answered = turn(engine, tools, Ends(tools, [], "Half an answer"), "hello")
    assert answered.status == "failed" and answered.reached_agent is True
    worked = turn(engine, tools, Ends(tools, [{"tool": "state"}], ""), "hello")
    assert worked.status == "failed" and worked.reached_agent is True
    fine = turn(engine, tools, ScriptedAgentSession(tools, [[{"text": "Hi."}]]), "hello")
    assert fine.reached_agent is True
