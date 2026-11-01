"""The agent icon's states and the agent panel's open, close, pin and size rules,
headless (#162). The icon: idle, thinking (waiting on the model), working (a tool runs),
needs you (an answer waits unread; nothing opens by itself) and error (the agent is
unavailable or its turn errored). The panel: hover on the icon opens it after 150 ms;
it stays open while the pointer, text, focus, a selection drag or the pin holds it,
and closes 400 ms after the last hold lets go; Esc closes it whatever holds it."""

from __future__ import annotations

from specto_app.prompt.states import (
    CLOSE_MS,
    OPEN_MS,
    IconStates,
    PanelRules,
    clamp_size,
    panel_height,
)


# -- the icon ---------------------------------------------------------------------------------
def test_a_turn_thinks_while_waiting_on_the_model_and_works_while_a_tool_runs():
    icon = IconStates()
    assert icon.state == "idle"
    icon.turn_started()
    assert icon.state == "thinking"
    icon.tool_started("submit_canvas")
    assert icon.state == "working"
    icon.tool_finished("submit_canvas")
    assert icon.state == "thinking"
    icon.streamed()
    assert icon.state == "thinking"
    icon.turn_finished("changed", panel_open=False)
    assert icon.state == "idle" and icon.asks == 0  # the canvas shows the change


def test_an_answer_that_lands_while_the_panel_is_closed_needs_the_user_until_it_opens():
    icon = IconStates()
    icon.turn_started()
    icon.turn_finished("answered", panel_open=False)
    assert icon.state == "needs_you" and icon.asks == 1
    icon.panel_opened()
    assert icon.state == "idle" and icon.asks == 0

    icon.turn_started()
    icon.turn_finished("answered", panel_open=True)  # read as it streamed
    assert icon.state == "idle"
    # three rejected documents: the agent says what it could not do, the user reads it
    icon.turn_started()
    icon.turn_finished("failed", panel_open=False)
    assert icon.state == "needs_you"


def test_an_errored_turn_or_an_unavailable_agent_shows_the_error_until_the_next_turn():
    icon = IconStates()
    icon.turn_started()
    icon.turn_finished("failed", error="RuntimeError: the agent session ended")
    assert icon.state == "error" and icon.error == "RuntimeError: the agent session ended"
    icon.panel_opened()
    assert icon.state == "error"  # reading the thread does not clear it
    icon.turn_started()
    assert icon.state == "thinking" and icon.error == ""

    icon.unavailable("the Claude Agent SDK is not installed")
    assert icon.state == "error" and "not installed" in icon.error


def test_tool_events_outside_a_turn_change_nothing():
    icon = IconStates()
    icon.tool_started("state")
    icon.tool_finished("state")
    icon.streamed()
    assert icon.state == "idle"


# -- the panel --------------------------------------------------------------------------------
def test_hover_on_the_icon_opens_the_panel_after_a_short_rest():
    rules = PanelRules()
    assert rules.set(icon=True) == ("open", OPEN_MS) and OPEN_MS == 150
    assert rules.open is False
    rules.fire()
    assert rules.open is True

    # passing over the icon: the pointer left before the rest was up
    rules = PanelRules()
    rules.set(icon=True)
    assert rules.set(icon=False) is None
    rules.fire()
    assert rules.open is False


def test_the_panel_closes_after_a_grace_once_nothing_holds_it():
    rules = PanelRules()
    rules.summon()  # Ctrl+K
    assert rules.open is True
    assert rules.set(panel=True) is None
    assert rules.set(panel=False) == ("close", CLOSE_MS) and CLOSE_MS == 400
    assert rules.set(icon=True) is None  # back over the icon (the gap counts as inside)
    assert rules.set(icon=False) == ("close", CLOSE_MS)
    rules.fire()
    assert rules.open is False


def test_text_focus_a_selection_drag_or_the_pin_keep_it_open_without_the_pointer():
    for hold in ("text", "focus", "drag", "pinned"):
        rules = PanelRules()
        rules.summon()
        if hold == "pinned":
            rules.toggle_pin()
        else:
            rules.set(**{hold: True})
        assert rules.set(panel=False, icon=False) is None, hold
        rules.fire()
        assert rules.open is True, hold
        if hold != "pinned":
            assert rules.set(**{hold: False}) == ("close", CLOSE_MS), hold


def test_esc_closes_even_when_pinned_keeps_the_pin_and_hover_must_come_back_to_reopen():
    rules = PanelRules()
    rules.set(icon=True)
    rules.fire()
    rules.toggle_pin()
    rules.set(text=True, focus=True)
    rules.escape()
    assert rules.open is False and rules.pinned is True
    # the pointer still rests on the icon: no reopen until it leaves and comes back
    assert rules.set(icon=True) is None
    rules.fire()
    assert rules.open is False
    rules.set(icon=False)
    assert rules.set(icon=True) == ("open", OPEN_MS)
    rules.fire()
    assert rules.open is True


def test_a_pinned_panel_opens_when_the_window_does():
    rules = PanelRules(pinned=True)
    assert rules.open is True
    rules.toggle_pin()
    assert rules.pinned is False and rules.set() == ("close", CLOSE_MS)


# -- sizes ------------------------------------------------------------------------------------
def test_the_panel_size_stays_inside_its_limits():
    # width 380 .. min(720, 60 % of the window); height 240 .. window - 120
    assert clamp_size(100, 100, 1600, 900) == (380, 240)
    assert clamp_size(2000, 2000, 1600, 900) == (720, 780)
    assert clamp_size(2000, 500, 1000, 800) == (600, 500)
    assert clamp_size(500, 500, 400, 300) == (380, 240)  # a tiny window: the minimum wins
    # unresized, the height hugs the thread up to 610
    assert panel_height(0, 120, 900) == 240
    assert panel_height(0, 400, 900) == 400
    assert panel_height(0, 2000, 900) == 610
    assert panel_height(0, 2000, 600) == 480
    assert panel_height(700, 100, 900) == 700  # resized: the user's height


def test_a_peer_request_waiting_for_the_user_needs_them():
    """#187: ask mode; during a turn it only counts, the icon keeps working."""
    s = IconStates()
    s.peer_asked()
    assert s.state == "needs_you" and s.asks == 1
    s.panel_opened()
    s.turn_started()
    s.peer_asked()
    assert s.state == "thinking" and s.asks == 1
