"""The S character's poses (#170), headless: what the agent icon shows in each state.
Behaviour from the design's README; the eyes' travel is the art's (here a fixed one, so
the expectations do not move when the art is swapped)."""

from __future__ import annotations

from dataclasses import replace

from specto_app.logo import logo
from specto_app.prompt.icon_pose import art_for_qml, blink_delay, pose

ART = replace(logo(), travel=(8.0, 9.0))


def test_idle_shows_open_eyes_at_rest_that_blink_and_follow_the_gaze():
    p = pose("idle", ART)
    assert (p.eyes, p.offset, p.motion, p.gaze) == ("open", (0, 0), "blink", True)
    assert (p.body, p.body_opacity, p.tile, p.border) == ("text", 1.0, "surface", "border_strong")


def test_thinking_looks_up_right_with_narrowed_eyes_and_looks_around():
    p = pose("thinking", ART)
    assert (p.eyes, p.offset, p.squash, p.motion) == ("open", (5, -6), 0.85, "look_around")
    assert not p.gaze


def test_thinking_stays_inside_a_smaller_travel():
    p = pose("thinking", replace(ART, travel=(4.0, 3.0)))
    assert p.offset == (4, -3)


def test_working_starts_at_the_left_end_of_the_travel_and_scans():
    p = pose("working", ART)
    assert (p.eyes, p.offset, p.motion, p.gaze) == ("open", (-8, 0), "scan", False)


def test_needs_you_has_wide_eyes_that_follow_the_gaze_within_their_travel():
    p = pose("needs_you", replace(ART, wide_travel=(4.7, 5.5)))
    assert (p.eyes, p.body, p.tile, p.border) == ("wide", "agent", "agent_soft", "agent")
    assert p.motion == "shake" and p.offset == (0, 0) and p.gaze


def test_wide_eyes_the_art_gives_no_travel_hold_at_rest():
    p = pose("needs_you", replace(ART, wide_travel=(0.0, 0.0)))
    assert p.eyes == "wide" and not p.gaze


def test_error_shows_x_eyes_over_a_faded_s_and_nothing_moves():
    p = pose("error", ART)
    assert (p.eyes, p.body_opacity, p.motion, p.gaze) == ("x", 0.45, "none", False)


def test_an_unknown_state_draws_as_idle():
    assert pose("busy", ART) == pose("idle", ART)


def test_qml_gets_the_parts_and_every_state_s_pose():
    q = art_for_qml(ART)
    assert q["travel"] == [8, 9]
    assert q["poses"]["thinking"] == {
        "eyes": "open",
        "dx": 5,
        "dy": -6,
        "squash": 0.85,
        "bodyOpacity": 1.0,
        "motion": "look_around",
        "gaze": False,
    }
    assert set(q["poses"]) == {"idle", "thinking", "working", "needs_you", "error"}


def test_blinks_come_every_four_to_seven_seconds():
    assert blink_delay(0.0) == 4000
    assert blink_delay(0.5) == 5500
    assert blink_delay(0.999) <= 7000
