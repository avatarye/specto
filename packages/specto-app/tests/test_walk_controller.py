"""Seam 2 for the walk-through (#174): ``WalkController`` as QML sees it, the root
context's ``walkthrough``. Steps 0 (establishing), 1..N (hops), N + 1 (summary);
auto-advance on by default; step, play/pause, replay and exit per the controls design
(x-controls.png); the agent's prepare/play and the template fallback."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtCore")

from specto_app.walk.controller import WalkController
from specto_app.walk.playback import dwell_ms
from specto_core.walkthrough import WalkError, caption_script, walk_script


def link(src, src_port, dst, dst_port, kind="follow", **extra):
    return {
        "kind": kind,
        "source": {"view": src, "port": src_port},
        "target": {"view": dst, "port": dst_port},
        "summary": {"sentence": f"{src} feeds {dst} with what it picks"},
        **extra,
    }


def doc() -> dict:
    """project -> fege (file tree) -> kobi (gallery) -> zila (image), and zila also
    pinned from the project: three hops on the main line plus one branch."""
    v = lambda t, x: {"type": t, "label": "", "layout": {"position": [x, 0], "size": [300, 200]}}  # noqa: E731
    return {
        "views": {"fege": v("file_tree", 80), "kobi": v("gallery", 400), "zila": v("image", 800)},
        "links": {
            "l1": link("project", "all_files", "fege", "data", "intaker"),
            "l2": link("fege", "selected_files", "kobi", "data", "intaker", scope="selection"),
            "l3": link("kobi", "selection", "zila", "selection"),
            "l4": link("project", "all_files", "zila", "data", "intaker", scope="pinned"),
        },
    }


@pytest.fixture
def walk(qapp):
    c = WalkController(lambda: (doc(), None))
    events: list[tuple] = []
    c.stepEntered.connect(lambda s: events.append(("entered", s)))
    c.exited.connect(lambda: events.append(("exited",)))
    c.failed.connect(lambda m: events.append(("failed", m)))
    c.events = events  # type: ignore[attr-defined]
    yield c
    c.timer.stop()
    c.deleteLater()


def test_a_walk_starts_on_the_establishing_shot_playing(walk):
    assert walk.state == "idle" and walk.active is False and walk.step == -1
    assert walk.start("zila") is True
    assert (walk.state, walk.active, walk.autoAdvance) == ("playing", True, True)
    assert walk.step == 0 and walk.hopCount == 4 and walk.target == "zila"
    assert walk.caption == walk.script["intro"]
    assert walk.hop == {}
    assert walk.pathViews == ["project", "fege", "kobi", "zila"]
    assert walk.focusViews == walk.pathViews
    assert walk.events == [("entered", 0)]
    assert walk.timer.isActive() and walk.timer.interval() == walk.dwellMs
    assert walk.script["source"] == "template" and walk.script["captioned"] is True


def test_stepping_moves_through_the_hops_to_the_summary(walk):
    walk.start("zila")
    walk.stepForward()
    assert walk.step == 1 and walk.hop["link"] == "l1"
    assert walk.focusViews == ["project", "fege"]
    assert walk.caption == walk.hop["caption"]
    walk.stepForward()
    walk.stepForward()
    walk.stepForward()
    assert walk.step == 4 and walk.hop["branch_start"] is True
    walk.stepForward()
    assert walk.step == 5 and walk.state == "summary" and walk.autoAdvance is False
    assert walk.caption == walk.script["summary"] and not walk.timer.isActive()
    walk.stepForward()  # nothing past the summary
    assert walk.step == 5
    walk.stepBack()
    assert walk.step == 4 and walk.state == "paused"
    assert [e[1] for e in walk.events if e[0] == "entered"] == [0, 1, 2, 3, 4, 5, 4]


def test_step_back_at_the_establishing_shot_does_nothing(walk):
    walk.start("zila")
    walk.stepBack()
    assert walk.step == 0 and walk.events == [("entered", 0)]


def test_auto_advance_plays_the_whole_walk_and_stops_at_the_summary(walk, spin):
    walk.speed = 0.005  # 15 to 35 ms per step
    walk.start("zila")
    spin(until=lambda: walk.state == "summary", timeout=5.0)
    assert [e[1] for e in walk.events if e[0] == "entered"] == [0, 1, 2, 3, 4, 5]


def test_pause_stops_auto_advance_and_play_resumes_on_the_same_hop(walk):
    walk.start("zila")
    walk.stepForward()
    walk.pause()
    assert walk.state == "paused" and walk.autoAdvance is False and not walk.timer.isActive()
    walk.events.clear()
    walk.play()
    assert walk.state == "playing" and walk.step == 1 and walk.timer.isActive()
    assert walk.events == [("entered", 1)]  # the pulse restarts
    walk.togglePlay()
    assert walk.state == "paused"
    walk.togglePlay()
    assert walk.state == "playing"


def test_stepping_while_playing_keeps_playing_and_restarts_the_dwell(walk):
    walk.start("zila")
    walk.stepForward()
    assert walk.state == "playing" and walk.timer.isActive()
    assert walk.timer.interval() == dwell_ms(walk.caption)


def test_the_dwell_starts_over_when_the_camera_settles_on_the_current_step(walk, spin):
    walk.start("zila")
    walk.stepForward()
    spin(120)
    before = walk.timer.remainingTime()
    walk.settled(1)
    assert walk.timer.remainingTime() > before  # the full dwell again, from the landing
    walk.settled(0)  # a stale landing changes nothing
    walk.pause()
    walk.settled(1)
    assert not walk.timer.isActive()  # paused: nothing to time


def test_home_end_and_go_to_jump(walk):
    walk.start("zila")
    walk.goTo(3)
    assert walk.step == 3 and walk.hop["link"] == "l3"
    walk.end()
    assert walk.state == "summary" and walk.step == 5
    walk.home()
    assert walk.step == 0 and walk.state == "paused"
    walk.goTo(99)
    assert walk.step == 5


def test_replay_reuses_the_script_from_the_establishing_shot(walk):
    walk.start("zila")
    script = walk.script
    walk.end()
    walk.replay()
    assert walk.state == "playing" and walk.step == 0 and walk.script == script
    walk.end()
    walk.play()  # play on the summary is a replay
    assert walk.state == "playing" and walk.step == 0


def test_exit_closes_the_walk_and_says_so(walk):
    walk.start("zila")
    walk.stepForward()
    walk.exit()
    assert walk.state == "exited" and walk.active is False and walk.step == -1
    assert walk.events[-1] == ("exited",) and not walk.timer.isActive()
    walk.stepForward()
    assert walk.step == -1


def test_a_walk_that_cannot_be_planned_fails_with_the_reason(walk):
    assert walk.start("nope") is False
    assert walk.state == "idle" and "nope" in walk.error
    assert walk.events[-1][0] == "failed"


def test_the_agent_prepares_then_plays_its_captions(walk):
    script = walk_script(doc(), "zila")
    walk.prepare(script)
    assert walk.state == "preparing" and walk.active is True and walk.step == -1
    assert walk.script["captioned"] is False and walk.hopCount == 4
    assert walk.caption == "" and walk.autoAdvance is False and walk.events == []
    played = caption_script(script, ["one", "two", "three", "four"], intro="Here we go.")
    walk.play_script(played)
    assert walk.state == "playing" and walk.step == 0 and walk.caption == "Here we go."
    walk.stepForward()
    assert walk.caption == "one"


def test_a_turn_that_ends_while_preparing_plays_the_templates(walk):
    script = walk_script(doc(), "zila")
    walk.prepare(script)
    walk.turn_ended()
    assert walk.state == "playing" and walk.step == 0
    assert walk.script["source"] == "template"
    assert walk.script["hops"][0]["caption"] == script["hops"][0]["caption"]
    walk.turn_ended()  # nothing to do once it plays
    assert walk.state == "playing"


def test_esc_while_preparing_cancels_and_a_late_play_is_refused(walk):
    script = walk_script(doc(), "zila")
    walk.prepare(script)
    walk.exit()
    assert walk.state == "exited" and walk.events == [("exited",)]
    with pytest.raises(WalkError):
        walk.play_script(caption_script(script, [""] * 4))
    walk.turn_ended()
    assert walk.state == "exited"


def test_the_dwell_reads_at_200_words_a_minute_after_a_2_s_settle():
    """#183: 2 s + 300 ms a word (about 200 wpm), clamped to 5 to 15 s."""
    assert dwell_ms("") == 5000
    assert dwell_ms("word " * 10) == 5000
    assert dwell_ms("w " * 20) == 8000  # 2 s + 20 x 300 ms
    assert dwell_ms("**bold** words, a path: C:/a/b/c.png") == 5000
    assert dwell_ms("word " * 200) == 15000


def test_the_walk_speed_scales_the_reading_time():
    assert dwell_ms("w " * 20, "slow") == 12000
    assert dwell_ms("w " * 20, "normal") == 8000
    assert dwell_ms("w " * 20, "fast") == 4800
    assert dwell_ms("", "fast") == 3000 and dwell_ms("word " * 200, "slow") == 22500
    assert dwell_ms("w " * 20, "warp") == 8000  # not a speed: normal


@pytest.fixture
def fresh_speed():
    from PySide6.QtCore import QSettings

    from specto_app.walk.controller import WALK_SPEED

    QSettings().remove(WALK_SPEED)
    yield
    QSettings().remove(WALK_SPEED)


def test_the_walk_speed_is_the_users_setting_and_times_the_dwell(qapp, fresh_speed):
    walk = WalkController(lambda: (doc(), None))
    seen: list[str] = []
    walk.walkSpeedChanged.connect(lambda: seen.append(walk.walkSpeed))
    assert walk.walkSpeed == "normal"
    walk.start("zila")
    normal = walk.timer.interval()
    walk.setWalkSpeed("slow")
    assert walk.walkSpeed == "slow" and seen == ["slow"]
    assert walk.dwellMs == dwell_ms(walk.caption, "slow")
    assert walk.timer.isActive() and walk.timer.interval() == walk.dwellMs > normal
    walk.setWalkSpeed("warp")  # not a speed: nothing changes
    assert walk.walkSpeed == "slow" and seen == ["slow"]
    walk.cycleWalkSpeed()  # slow -> normal -> fast -> slow
    assert walk.walkSpeed == "normal"
    walk.cycleWalkSpeed()
    assert walk.walkSpeed == "fast"
    walk.timer.stop()
    walk.deleteLater()
    again = WalkController(lambda: (doc(), None))  # kept for the next walk and session
    assert again.walkSpeed == "fast"
    again.deleteLater()
