"""The walk-through's agent tools in a prompt turn (#174): a scripted agent asks for the
plan with ``walkthrough``, writes every caption in one pass and plays the script with
``walkthrough_play``; the turn changes nothing on the canvas and ends with the walk. A
turn that ends before playing hands the templates to the player. No Qt, no model."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from specto_app.prompt.context import CanvasContext
from specto_app.prompt.scripted import ScriptedAgentSession
from specto_app.prompt.session import SYSTEM_PROMPT, sdk_tools, stop_hooks
from specto_app.prompt.turn import TurnTools, run_turn
from specto_app.walk.tools import WALK_TOOLS, WalkTools, walk_request
from specto_core import CallContext, Engine
from specto_core.agent_tools import AgentTools
from specto_core.canvas_document import file_tree_of
from specto_core.walkthrough import WalkError

PROMPT_BAR = CallContext(connection="prompt-bar", origin="agent:prompt")


class Player:
    """Records what the walk UI would show."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.cancelled = False

    def prepare(self, script: dict[str, Any]) -> None:
        self.calls.append(("prepare", script))

    def play_script(self, script: dict[str, Any]) -> None:
        if self.cancelled:
            raise WalkError("the user closed the walk-through while it was being prepared")
        self.calls.append(("play", script))

    def turn_ended(self) -> None:
        self.calls.append(("turn_ended", None))

    def set_speed(self, speed: str) -> None:
        self.calls.append(("speed", speed))


@pytest.fixture
def engine() -> Engine:
    eng = Engine(project=None)
    doc = eng.live.document()
    tree = file_tree_of(doc["views"])
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [400, 0], "size": [640, 400]},
    }
    doc["links"]["l9"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": tree, "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"screener": ["*.jpg"]},
        "summary": {"sentence": "kobi shows the images you pick in the file tree"},
    }
    eng.call("canvas.submit", {"document": doc, "label": "kobi"}, CallContext(origin="user"))
    return eng


@pytest.fixture
def player() -> Player:
    return Player()


@pytest.fixture
def tools(engine: Engine, player: Player) -> TurnTools:
    agent_tools = AgentTools(lambda name, args: engine.call(name, args, PROMPT_BAR))
    walk = WalkTools(lambda: (engine.live.data, engine.links), player)
    return TurnTools(agent_tools, engine.live, walk=walk)


def turn(engine: Engine, tools: TurnTools, session: ScriptedAgentSession, prompt: str):
    ctx = CanvasContext(
        document=engine.live.document(),
        viewport={"x": -12, "y": -12, "w": 1600, "h": 900},
        mouse=None,
        selection={},
    )
    return asyncio.run(run_turn(session, tools, prompt, ctx))


def test_the_agent_plans_captions_and_plays_the_walk_in_one_turn(engine, tools, player):
    before = engine.live.document()
    session = ScriptedAgentSession(
        tools,
        [
            [
                {"tool": "walkthrough", "args": {"view": "kobi"}},
                {
                    "tool": "walkthrough_play",
                    "args": {
                        "view": "kobi",
                        "captions": [
                            "The file tree lists all **3** files of the project.",
                            "kobi keeps the **.jpg** files you pick.",
                        ],
                        "intro": "kobi shows the frames you pick.",
                        "summary": "From the project folder to kobi in **2** links.",
                    },
                },
            ]
        ],
    )
    reply = turn(engine, tools, session, "walk me through kobi")
    plan = json.loads(session.calls[0].text)
    assert session.calls[0].is_error is False
    assert plan["target"] == "kobi" and plan["hop_count"] == 2
    assert [(h["from"], h["to"]) for h in plan["hops"]][1][1] == "kobi"
    assert plan["hops"][1]["kind_label"] == "intaker · follows your pick"
    assert plan["hops"][1]["note"].endswith("screener: *.jpg")
    assert plan["hops"][1]["caption"]  # the template, to write from
    assert "to_rect" not in plan["hops"][0]  # the UI's camera targets stay out
    assert json.loads(session.calls[1].text) == {"playing": "kobi", "hops": 2}
    kinds = [c[0] for c in player.calls]
    assert kinds == ["prepare", "play", "turn_ended"]
    prepared, played = player.calls[0][1], player.calls[1][1]
    assert prepared["captioned"] is False and played["captioned"] is True
    assert played["source"] == "agent"
    assert [h["caption"] for h in played["hops"]] == [
        "The file tree lists all **3** files of the project.",
        "kobi keeps the **.jpg** files you pick.",
    ]
    assert played["intro"] == "kobi shows the frames you pick."
    assert tools.done is True  # the session stops the model after the play
    assert reply.status == "answered" and reply.summary == "walk-through: kobi"
    assert reply.turn_txn is None
    assert engine.live.document() == before  # the walk never changes the canvas


def test_captions_that_miss_hops_come_back_with_a_fix_and_the_agent_retries(engine, tools, player):
    session = ScriptedAgentSession(
        tools,
        [
            [
                {"tool": "walkthrough", "args": {"view": "kobi"}},
                {"tool": "walkthrough_play", "args": {"view": "kobi", "captions": ["one"]}},
                {"tool": "walkthrough_play", "args": {"view": "kobi", "captions": ["a", "b"]}},
            ]
        ],
    )
    reply = turn(engine, tools, session, "walk me through kobi")
    assert session.calls[1].is_error is True
    assert "one caption per hop" in session.calls[1].text
    assert session.calls[2].is_error is False
    assert tools.failures == 0  # only rejected submits count against the turn
    assert reply.summary == "walk-through: kobi"


def test_a_view_with_no_walk_is_an_error_the_agent_can_report(engine, tools, player):
    session = ScriptedAgentSession(
        tools, [[{"tool": "walkthrough", "args": {"view": "nope"}}, {"text": "No view nope."}]]
    )
    reply = turn(engine, tools, session, "walk me through nope")
    assert session.calls[0].is_error is True and "nope" in session.calls[0].text
    assert reply.status == "answered" and reply.summary == "No view nope."
    assert [c[0] for c in player.calls] == ["turn_ended"]


def test_a_turn_that_ends_without_playing_leaves_the_player_to_play_templates(
    engine, tools, player
):
    session = ScriptedAgentSession(
        tools, [[{"tool": "walkthrough", "args": {"view": "kobi"}}, {"text": "Done."}]]
    )
    turn(engine, tools, session, "walk me through kobi")
    assert [c[0] for c in player.calls] == ["prepare", "turn_ended"]


def test_a_walk_the_user_cancelled_is_not_played(engine, tools, player):
    player.cancelled = True
    session = ScriptedAgentSession(
        tools,
        [
            [
                {"tool": "walkthrough", "args": {"view": "kobi"}},
                {"tool": "walkthrough_play", "args": {"view": "kobi", "captions": ["", ""]}},
            ]
        ],
    )
    turn(engine, tools, session, "walk me through kobi")
    assert session.calls[1].is_error is True and "closed" in session.calls[1].text
    assert tools.done is False


def test_the_walk_tools_reach_the_session_and_end_the_turn_like_a_submit(tools):
    names = {t.name for t in sdk_tools(tools)}
    assert set(WALK_TOOLS) <= names
    assert "walkthrough" in SYSTEM_PROMPT and "walkthrough_play" in SYSTEM_PROMPT
    matcher = stop_hooks(lambda: True)["PostToolUse"][0].matcher
    assert "walkthrough_play" in matcher and "submit_canvas" in matcher


def test_walk_requests_name_a_view_on_the_canvas(engine):
    data = engine.live.data
    assert walk_request("walk me through kobi", data) == "kobi"
    assert walk_request("Please walk through the view Kobi.", data) == "kobi"
    assert walk_request("walk me through nope", data) is None
    assert walk_request("show me kobi", data) is None


def test_the_agent_sets_the_walk_speed_when_the_user_asks_for_it(engine, tools, player):
    """#183: walkthrough_play's optional speed (slow, normal, fast) is the user's walk
    speed setting; an unknown one comes back with a fix."""
    session = ScriptedAgentSession(
        tools,
        [
            [
                {"tool": "walkthrough", "args": {"view": "kobi"}},
                {
                    "tool": "walkthrough_play",
                    "args": {"view": "kobi", "captions": ["", ""], "speed": "warp"},
                },
                {
                    "tool": "walkthrough_play",
                    "args": {"view": "kobi", "captions": ["", ""], "speed": "slow"},
                },
            ]
        ],
    )
    turn(engine, tools, session, "walk me through kobi, slowly")
    assert session.calls[1].is_error is True and "slow, normal or fast" in session.calls[1].text
    assert [c for c in player.calls if c[0] == "speed"] == [("speed", "slow")]
    assert [c[0] for c in player.calls].index("speed") < [c[0] for c in player.calls].index("play")
    assert "speed" in WALK_TOOLS["walkthrough_play"].params["properties"]
    assert "speed" in SYSTEM_PROMPT
