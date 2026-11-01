"""The tile floor's event sources (#191): ``TileFloor`` fed by the canvas controller, the
agent icon and the overworld, observed through what QML reads (``uniforms``, ``chip``,
``dim``, ``ticking``). The cue maths themselves are seam 1 (test_tile_floor_cues.py)."""

from __future__ import annotations

import pytest
from app_doc_helpers import pin
from tile_floor_helpers import Bar, Clock, Controller, Overworld

from specto_app.canvas.floor import SELECT_A
from specto_app.canvas.tile_floor import TileFloor
from specto_app.theme import ThemeObject


@pytest.fixture
def rig(qapp):
    controller, bar, over, clock = Controller(), Bar(), Overworld(), Clock()
    controller._ids = [{"id": "fege", "x": 0.0, "y": 0.0, "w": 300.0, "h": 500.0}]
    floor = TileFloor(controller, prompt_bar=bar, overworld=over, theme=ThemeObject(), clock=clock)
    floor.setAgentAnchor(38.0, 700.0)
    yield floor, controller, bar, over, clock
    floor.close()


def lit(floor) -> list[int]:
    """The slots holding an effect (A > 0)."""
    u = floor.uniforms
    return [i for i in range(8) if u[f"p{i}"].z() > 0]


def add_view(controller, vid, x, y, w=300.0, h=200.0):
    controller._ids = [*controller._ids, {"id": vid, "x": x, "y": y, "w": w, "h": h}]
    controller.viewIdsChanged.emit()


def test_the_views_already_there_send_no_pulse(rig):
    floor, controller, *_ = rig
    controller.viewIdsChanged.emit()
    assert lit(floor) == []
    assert floor.ticking is False


def test_the_agent_thinking_ticks_the_floor_until_its_last_band_is_out(rig):
    floor, _, bar, _, clock = rig
    bar.icon.state = "thinking"
    bar.icon.changed.emit()
    assert floor.ticking is True
    assert floor.uniforms["worldLive"] is False  # at the S icon: the camera stays out
    assert floor.uniforms["p0"].y() == 32  # a band 32 px wide
    bar.icon.state = "idle"
    bar.icon.changed.emit()
    clock.t += 2.0
    floor.tick()
    assert floor.ticking is False
    assert lit(floor) == []


def test_a_turn_that_ends_with_nothing_waiting_sends_the_green_done_band(rig):
    floor, _, bar, _, clock = rig
    bar.turnFinished.emit()
    clock.t += 0.3
    floor.tick()
    (i,) = lit(floor)
    c = floor.uniforms[f"c{i}"]
    assert c.y() > c.x() and c.y() > c.z()  # ok green
    assert f"s{i}" not in floor.uniforms  # no soft colour, no wake (#201)
    assert c.w() == 0  # screen space, at the S icon


def test_a_turn_that_leaves_an_answer_waiting_sends_no_done_band(rig):
    floor, _, bar, *_ = rig
    bar.icon.state = "needs_you"
    bar.turnFinished.emit()
    assert all(floor.uniforms[f"c{i}"].y() < floor.uniforms[f"c{i}"].x() for i in lit(floor))


def test_a_new_view_on_screen_bands_in_world_space(rig):
    floor, controller, _, _, clock = rig
    add_view(controller, "kobi", 400, 100)
    clock.t += 0.4
    floor.tick()
    slots = lit(floor)
    assert len(slots) == 2
    assert all(floor.uniforms[f"c{i}"].w() == 1 for i in slots)
    assert floor.uniforms[f"g{slots[0]}"].toTuple() == (400, 100, 700, 300)
    assert floor.uniforms["worldLive"] is True  # on a view: the floor takes the camera


def test_a_far_new_view_shows_the_chip_and_a_click_pans_there(rig):
    floor, controller, *_ = rig
    add_view(controller, "pola", 1280 + 4200, 300)
    chip = floor.chip
    assert chip["show"] is True and chip["view"] == "pola" and chip["text"] == "4.2k px"
    assert floor.uniforms["worldLive"] is False  # the edge pulses are screen space
    floor.chipClicked()
    assert controller.jumped == ["pola"]
    assert floor.chip["show"] is False


def test_the_chip_goes_after_four_seconds_without_ticking_meanwhile(rig):
    floor, controller, _, _, clock = rig
    add_view(controller, "pola", 9000, 300)
    clock.t += 2.3  # the pulses are done; the chip stays
    floor.tick()
    assert floor.chip["show"] is True
    assert floor.ticking is False
    clock.t += 1.8
    floor.tick()
    assert floor.chip["show"] is False


def test_in_the_overworld_every_new_view_is_on_screen(rig):
    floor, controller, _, over, clock = rig
    over.active = True
    add_view(controller, "pola", 9000, 300)
    clock.t += 0.3
    floor.tick()
    assert floor.chip["show"] is False
    assert len(lit(floor)) == 2  # the view's two bands


def test_selecting_a_view_lights_its_footprint_and_a_drag_hides_it(rig):
    floor, controller, _, _, clock = rig
    controller._sel = ["fege"]
    controller.selectionChanged.emit()
    clock.t += 0.3
    floor.tick()
    (i,) = lit(floor)
    r, w, a, _ = floor.uniforms[f"p{i}"].toTuple()
    assert (r, w, a) == (0, 20, pytest.approx(SELECT_A))  # R 0, w 20, A 0.4 (#220)
    assert floor.ticking is False  # held: static
    controller._drag = {"fege": {"x": 10, "y": 0, "w": 300, "h": 500}}
    controller.dragRectsChanged.emit()
    clock.t += 0.3
    floor.tick()
    assert lit(floor) == []


def test_the_wiring_layer_dims_the_floor_but_not_in_the_overworld(rig):
    floor, controller, _, over, _ = rig
    assert floor.dim == 1.0
    controller._wiring = True
    controller.wiringChanged.emit()
    assert floor.dim == pytest.approx(0.3)
    over.active = True
    over.activeChanged.emit()
    assert floor.dim == 1.0


def test_unchanged_uniforms_notify_nobody(rig):
    floor, *_ = rig
    seen = []
    floor.uniformsChanged.connect(lambda: seen.append(1))
    for _ in range(5):
        floor.tick()
    assert seen == []


# -- in the shell -------------------------------------------------------------------------------


@pytest.fixture
def qt_messages():
    from PySide6.QtCore import qInstallMessageHandler

    out: list[str] = []
    previous = qInstallMessageHandler(lambda kind, ctx, msg: out.append(msg))
    yield out
    qInstallMessageHandler(previous)


def test_the_shell_mounts_the_floor_under_the_canvas_and_the_overworld(qt_messages, app, spin):
    from specto_app.app import find_item

    floor = app.tile_floor
    assert app.qml.rootContext().contextProperty("tileFloor") is floor
    root = app.window.contentItem()
    assert find_item(root, "tileFloor") is not None
    assert find_item(root, "floorChip") is not None
    # the S icon's centre, in the canvas area's px: 12 + 26 from the left and the bottom
    x, y = floor.cues.agent_anchor
    assert x == pytest.approx(38) and y == pytest.approx(app.window.height() - 38)
    app.overworld.toggle()
    spin(30)
    app.overworld.toggle()
    spin(30)
    ours = ("TileFloor", "FloorChip", "Main.qml", "CanvasArea", "Overworld", "tileFloor")
    assert [m for m in qt_messages if any(n in m for n in ours)] == []


def test_an_agent_view_on_screen_pulses_and_the_floor_then_rests(app, spin):
    live = app.engine.live
    doc = live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [320, 0], "size": [400, 300]},
    }
    pin(doc, "kobi")
    live.submit(doc, origin="agent:s1", label="front gallery")
    spin(400)
    floor = app.tile_floor
    assert lit(floor)
    assert floor.chip["show"] is False
    spin(until=lambda: not floor.ticking, timeout=5)
    assert lit(floor) == []


def test_a_far_agent_view_shows_the_chip_at_the_window_edge_towards_it(app, spin):
    from specto_app.app import find_item

    live = app.engine.live
    doc = live.document()
    doc["views"]["pola"] = {
        "type": "gallery",
        "label": "Far",
        "layout": {"position": [9000, 100], "size": [400, 300]},
    }
    pin(doc, "pola")
    live.submit(doc, origin="agent:s1", label="a far gallery")
    spin(300)
    chip = find_item(app.window.contentItem(), "floorChip")
    assert chip.isVisible() and chip.opacity() > 0
    area = find_item(app.window.contentItem(), "canvasArea")
    # the view is far to the right: the chip hugs the right edge, and names it
    assert chip.x() + chip.width() > area.width() - 60
    texts = [c.property("text") for c in chip.childItems()[0].childItems()]
    assert texts[:2] == ["pola", app.tile_floor.chip["text"]]


def test_a_real_agent_turn_bands_while_it_thinks_and_once_done(qapp, project, aloop, aspin, spin):
    """The prompt bar's own icon and turn signals: thinking bands during the turn, the
    green done band when it ends with nothing waiting."""
    from specto_app.app import SpectoApp
    from specto_app.prompt.scripted import ScriptedAgentSession

    def turns(live):
        doc = live.document()
        doc["views"]["kobi"] = {
            "type": "gallery",
            "label": "Front",
            "layout": {"position": [400, 0], "size": [400, 300]},
        }
        pin(doc, "kobi")
        return [[{"wait_ms": 300}, {"tool": "submit_canvas", "args": {"document": doc}}]]

    a = SpectoApp(
        project,
        jobs="inline",
        web=False,
        offscreen=True,
        serve=False,
        agent=lambda tools: ScriptedAgentSession(tools, turns(a.live)),
        loop=aloop,
    )
    a.start()
    try:
        floor, bar = a.tile_floor, a.prompt_bar
        seen = set()
        floor.uniformsChanged.connect(
            lambda: seen.update(p.kind for p in floor.cues._live_pulses(floor.clock()))
        )
        bar.submit("add the front frames")
        aspin(until=lambda: not bar.busy)
        assert bar.icon.state == "idle"
        assert "think" in seen
        assert any(p.kind == "done" for p in floor.cues._pulses)
    finally:
        a.close()
        spin(20)
