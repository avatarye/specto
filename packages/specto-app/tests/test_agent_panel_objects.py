"""The agent panel's Qt objects, offscreen (#162): the icon's needs-you shake timer, the
panel's hover timers, its size kept inside the window and remembered per project with
its pin, and the thread as a list model that pages older turns in."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtQuick")

from specto_app.prompt.panel import SHAKE_MS, AgentIcon, AgentPanel, ThreadModel
from specto_app.prompt.thread import AgentThread


def test_needs_you_shakes_every_four_seconds_until_the_panel_opens(qapp, spin):
    icon = AgentIcon()
    shakes: list[int] = []
    icon.shake.connect(lambda: shakes.append(1))
    icon.turn_started()
    icon.turn_finished("answered", panel_open=False)
    assert icon.state == "needs_you" and icon.asks == 1
    assert icon.shaking and icon._shake.interval() == SHAKE_MS == 4000
    assert shakes == [1]  # once on entering, then every 4 s
    icon._shake.timeout.emit()
    assert len(shakes) == 2
    icon.panel_opened()
    assert icon.state == "idle" and not icon.shaking
    icon.turn_started()
    icon.turn_finished("failed", error="boom")
    assert icon.state == "error" and icon.errorText == "boom" and not icon.shaking
    icon.stop()


def test_the_panel_opens_on_a_resting_hover_and_closes_after_the_grace(qapp, spin, tmp_path):
    panel = AgentPanel(tmp_path)
    panel.hoverIcon(True)
    assert panel.open is False
    spin(until=lambda: panel.open, timeout=2)
    panel.hoverIcon(False)
    spin(250)
    assert panel.open is True  # inside the 400 ms grace
    spin(until=lambda: not panel.open, timeout=2)
    panel.summon()
    panel.setHolds(True, False, False)  # text in the input
    spin(500)
    assert panel.open is True
    panel.escape()
    assert panel.open is False
    panel.stop()


def test_size_and_pin_are_remembered_per_project_and_kept_inside_the_window(qapp, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    panel = AgentPanel(a)
    assert (panel.width, panel.storedHeight) == (480, 0)  # the default: hug the thread
    panel.setWindowSize(1600, 900)
    panel.resizeTo(900, 1000)
    assert (panel.width, panel.storedHeight) == (720, 780)
    assert panel.heightFor(100) == 780
    panel.togglePin()
    panel.commit()
    panel.stop()

    again = AgentPanel(a)
    assert (again.width, again.storedHeight, again.pinned, again.open) == (720, 780, True, True)
    other = AgentPanel(b)
    assert (other.width, other.storedHeight, other.pinned) == (480, 0, False)
    assert other.heightFor(300) == 300
    # a smaller window shrinks what shows, not what is remembered
    again.setWindowSize(1000, 700)
    assert (again.shownWidth, again.heightFor(0)) == (600, 580)
    again.setWindowSize(1600, 900)
    assert again.shownWidth == 720
    for p in (again, other):
        p.stop()


def test_the_thread_model_lists_the_latest_turns_and_pages_older_ones_in(qapp, spin, tmp_path):
    thread = AgentThread(tmp_path, page_turns=2)
    for i in range(5):
        thread.add("user", text=f"q{i}")
        thread.add("agent", text=f"**a{i}**\n\n```py\nx = {i}\n```", status="answered")
    model = ThreadModel(AgentThread(tmp_path, page_turns=2))
    kinds = [model.get(r)["kind"] for r in range(model.rowCount())]
    assert kinds == ["user", "agent", "user", "agent"]
    assert model.get(1)["blocks"] == [
        {"kind": "md", "text": "**a3**", "lang": ""},
        {"kind": "code", "text": "x = 3", "lang": "py"},
    ]
    assert model.hasOlder is True
    model.loadOlder()
    assert model.loading is True  # the skeleton rows show for a moment
    spin(until=lambda: not model.loading, timeout=2)
    assert model.rowCount() == 8
    model.loadOlder()
    spin(until=lambda: not model.loading, timeout=2)
    assert model.get(0)["kind"] == "divider"
    assert model.get(0)["dividerText"].startswith("Session started today")
    assert model.hasOlder is False
