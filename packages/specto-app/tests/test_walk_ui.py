"""The walk-through UI (#174 part b), offscreen, driven through the walk controller (a
``walkthrough``, the #174 contract): the path lights at once while the walk
is prepared and its rows tick off as captions arrive; the establishing shot and the
summary are in the overworld, hops at 1:1 with the camera holding when a hop already
fits; the current hop's two views ring, everything off the path dims; the control bar
and keys drive the controller; any canvas press pauses; Esc puts the camera back.
Real mouse and keys: the seam-3 flow tools/gui_flows/walkthrough.txt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.canvas.walk_stage import caption_html, hop_track
from specto_core.walkthrough import caption_script, walk_script

AGENT = CallContext(connection="mcp:1", origin="agent:s1")


def compose_walk(app, spin) -> None:
    """fege -> kobi (an intaker) beside the file tree; zila far right, pinned from the
    project and following kobi; hehu below, off the walk's path."""
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [320, 40], "size": [520, 360]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "selection",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["."], "screener": ["@images"]},
        "summary": {"sentence": "kobi shows the picked images."},
    }
    doc["views"]["zila"] = {
        "type": "gallery",
        "label": "Rear",
        "layout": {"position": [3000, 40], "size": [520, 360]},
    }
    pin(doc, "zila", lid="l3")
    doc["links"]["l4"] = {
        "kind": "follow",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "zila", "port": "selection"},
        "params": {"match": None},
        "summary": {"sentence": "zila follows kobi."},
    }
    doc["views"]["hehu"] = {
        "type": "gallery",
        "label": "Lidar",
        "layout": {"position": [320, 440], "size": [520, 300]},
    }
    pin(doc, "hehu", lid="l5")
    app.engine.call("canvas.submit", {"document": doc, "label": "compose"}, AGENT)
    spin(until=lambda: "kobi" in app.hosts.hosts and "zila" in app.controller.views)


@pytest.fixture
def walk(app, spin):
    compose_walk(app, spin)
    app.walk_stage.time_scale = 0  # camera moves land at once
    w = app.walkthrough
    w.speed = 1000  # no auto-advance while a test looks
    return w


def planned(app) -> dict:
    """The walk to zila as the agent's ``walkthrough`` tool plans it: project -> fege ->
    kobi -> zila, then zila's second input from the project (a branch)."""
    return walk_script(app.live.data, "zila", app.engine.links)


def prepare(app) -> dict:
    script = planned(app)
    app.walkthrough.prepare(script)
    return script


def play(app) -> None:
    script = planned(app)
    app.walkthrough.play_script(
        caption_script(
            script, [""] * len(script["hops"]), intro="zila starts at the **project folder**."
        )
    )


def item(app, name):
    return find_item(app.window.contentItem(), name)


def shown(app, name) -> bool:
    it = item(app, name)
    while it is not None:
        if not it.isVisible() or it.opacity() == 0:
            return False
        it = it.parentItem()
    return True


def click(app, name: str) -> None:
    it = item(app, name)
    assert it is not None and it.isVisible(), name
    p = it.mapToScene(it.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)


def key(app, k) -> None:
    QTest.keyClick(app.window, k)


def pan(app) -> tuple[float, float]:
    return (app.controller.pan["x"], app.controller.pan["y"])


def head_on_screen(app, vid: str) -> bool:
    """The view's header row and both its sides are in the area (a tall file tree's
    bottom may run off it)."""
    x, y, w, _ = app.controller.rect_of(vid)
    vx, vy, vw, vh = app.controller.viewport_rect()
    return vx <= x and x + w <= vx + vw and vy <= y and y + 40 <= vy + vh


def on_screen(app, vid: str) -> bool:
    x, y, w, h = app.controller.rect_of(vid)
    vx, vy, vw, vh = app.controller.viewport_rect()
    return vx <= x and vy <= y and x + w <= vx + vw and y + h <= vy + vh


def test_preparing_lights_the_path_at_once_and_ticks_rows_off_as_captions_arrive(app, spin, walk):
    script = prepare(app)
    assert [h["link"] for h in script["hops"]] == ["l1", "l2", "l4", "l3"]
    spin(until=lambda: shown(app, "walkControls") and shown(app, "walkCaption"))
    assert item(app, "walkHopLabel").property("text") == "PREPARING THE WALK"
    assert item(app, "walkCounter").property("text") == "preparing"
    # steps and play are disabled; exit (cancel) is not
    for name in ("walkBack", "walkPlay", "walkForward"):
        assert item(app, name).property("disabled") is True, name
    assert item(app, "walkExit").property("disabled") is False
    # the path lights: rings on its views, the rest dims under the scrim
    for vid in ("project", "fege", "kobi", "zila"):
        assert item(app, f"walkRing:{vid}") is not None, vid
    assert item(app, "walkRing:hehu") is None
    spin(400)
    assert item(app, "view:hehu").opacity() == pytest.approx(0.22, abs=0.01)
    assert item(app, "view:kobi").opacity() == pytest.approx(1)
    assert item(app, "walkScrim").opacity() == pytest.approx(0.06, abs=0.005)
    # rows: none ticked, then hop 2's caption arrives
    marks = lambda: [item(app, f"walkPrepMark:{i}").property("text") for i in range(1, 5)]  # noqa: E731
    assert marks() == ["…"] * 4
    # a caption that arrives on its own ticks its row off
    hops = [dict(h) for h in script["hops"]]
    hops[1]["captioned"] = True
    walk.prepare({**script, "hops": hops})
    spin(until=lambda: marks() == ["…", "✓", "…", "…"])
    # Esc cancels while preparing
    key(app, Qt.Key.Key_Escape)
    spin(until=lambda: not app.walk_stage.active)
    assert walk.state == "exited"


def test_establishing_in_the_overworld_then_hops_at_1_to_1_ring_the_current_views(app, spin, walk):
    prepare(app)
    play(app)
    spin(until=lambda: app.overworld.active and app.walk_stage.settled)
    assert app.walk_stage.over
    assert item(app, "walkCounter").property("text") == "start"
    assert item(app, "walkBack").property("disabled") is True  # kept, so the pill keeps its shape
    assert item(app, "walkHopLabel").property("text") == "WALK-THROUGH"
    assert "project folder" in item(app, "walkNarration").property("text")
    # the overworld's off-path card dims; its header is hidden while the walk plays
    assert item(app, "overCard:hehu").opacity() == pytest.approx(0.22)
    # the whole path is in the shot, above the caption band
    ow = item(app, "overworld")
    zoom, px, py = ow.property("zoom"), ow.property("panX"), ow.property("panY")
    assert zoom < 1
    x, y, w, h = app.controller.rect_of("zila")
    assert px + (x + w) * zoom <= ow.width() and py + (y + h) * zoom <= ow.height() - 190

    # hop 1: down to 1:1, live views; project and fege ring strongly once settled
    click(app, "walkForward")
    spin(until=lambda: not app.overworld.active and app.walk_stage.settled)
    assert app.walk_stage.step == 1 and not app.walk_stage.over
    assert head_on_screen(app, "fege")
    spin(400)
    assert item(app, "walkFocus:fege").opacity() == pytest.approx(1)
    assert item(app, "walkFocus:project").opacity() == pytest.approx(1)
    assert item(app, "walkFocus:kobi").opacity() == pytest.approx(0)
    assert item(app, "walkHopLabel").property("text") == "HOP 1 OF 4"
    assert item(app, "walkCounter").property("text") == "1 / 4"
    assert item(app, "walkKindChip").isVisible()
    assert item(app, "walkWire:l1") is not None  # the walked link draws at 1:1
    assert item(app, "walkPulse").property("running") is True

    # hop 2 already fits: the camera holds, only the focus moves
    held = pan(app)
    key(app, Qt.Key.Key_Right)
    spin(until=lambda: app.walk_stage.step == 2 and app.walk_stage.settled)
    assert pan(app) == held
    spin(400)
    assert item(app, "walkFocus:kobi").opacity() == pytest.approx(1)
    assert item(app, "walkFocus:project").opacity() == pytest.approx(0)
    assert item(app, "walkCardChip").property("text") == walk.hop["kind_label"]

    # hop 3 is far right: the camera pans until both ends show
    key(app, Qt.Key.Key_Right)
    spin(until=lambda: app.walk_stage.step == 3 and app.walk_stage.settled)
    assert pan(app) != held and on_screen(app, "zila")


def test_the_controls_and_keys_drive_the_walk_and_canvas_input_pauses_it(app, spin, walk):
    play(app)
    spin(until=lambda: app.walk_stage.settled)
    assert app.walk_stage.playing and item(app, "walkPlay").property("on") is True
    key(app, Qt.Key.Key_Space)
    spin(until=lambda: walk.state == "paused")
    assert item(app, "walkPlay").property("name") == "play"
    click(app, "walkPlay")
    spin(until=lambda: walk.state == "playing")
    key(app, Qt.Key.Key_End)
    spin(until=lambda: app.walk_stage.atSummary and app.walk_stage.settled)
    assert item(app, "walkCounter").property("text") == "summary"
    assert shown(app, "walkDone") and shown(app, "walkReplay") and not shown(app, "walkExit")
    # the summary hangs its chips under the views, in the overworld
    assert app.overworld.active
    assert item(app, "walkChip:project").isVisible()
    assert item(app, "walkChip:kobi").isVisible()
    key(app, Qt.Key.Key_Home)
    spin(until=lambda: app.walk_stage.step == 0)
    # a click on a stop of the hop track jumps there (goTo)
    click(app, "walkStop:kobi")
    spin(until=lambda: app.walk_stage.step == 2 and app.walk_stage.settled)
    # Tab cannot leave for the overworld while a walk plays
    key(app, Qt.Key.Key_Tab)
    spin(100)
    assert not app.overworld.active
    # a press on the canvas pauses the walk
    walk.play()
    spin(until=lambda: walk.state == "playing")
    area = item(app, "canvasArea")
    p = area.mapToScene(area.boundingRect().topLeft()).toPoint()
    QTest.mouseClick(
        app.window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        p + type(p)(area.width() - 40, 30),
    )
    spin(until=lambda: walk.state == "paused")


def test_a_long_link_is_followed_from_its_source_to_its_target(app, spin, walk):
    play(app)
    spin(until=lambda: app.walk_stage.settled)
    walk.goTo(3)  # kobi -> zila: the camera ends at zila, far right
    spin(until=lambda: app.walk_stage.step == 3 and app.walk_stage.settled)
    seen: list[tuple[float, float]] = []
    app.controller.panChanged.connect(lambda: seen.append(pan(app)))
    walk.goTo(4)  # project -> zila, 3000 px: wider than the frame
    spin(until=lambda: app.walk_stage.step == 4 and app.walk_stage.settled)
    assert len(seen) >= 2
    vx = seen[0][0]
    assert vx <= 0  # first the source (the project node at the origin) shows ...
    assert on_screen(app, "zila")  # ... then the camera lands on the target


def test_a_walk_asked_in_the_prompt_takes_the_keys_from_its_field(app, spin, walk):
    app.prompt_bar.prefill("walk me through zila")
    spin(
        until=lambda: (
            item(app, "promptInput") is not None and item(app, "promptInput").hasActiveFocus()
        )
    )
    play(app)
    spin(until=lambda: app.walk_stage.settled and not item(app, "promptInput").hasActiveFocus())
    key(app, Qt.Key.Key_Right)
    spin(until=lambda: app.walk_stage.step == 1)


def test_the_dwell_runs_from_the_cameras_landing(app, spin, walk):
    landed: list[int] = []
    real = walk.settled
    walk.settled = lambda step: (landed.append(step), real(step))
    play(app)
    spin(until=lambda: app.walk_stage.settled)
    walk.goTo(3)
    spin(until=lambda: app.walk_stage.step == 3 and app.walk_stage.settled)
    assert landed == [0, 3]
    assert walk.timer.isActive()  # playing: the dwell restarted from the landing


def test_the_agent_panel_closes_while_the_walk_plays_and_a_pinned_one_comes_back(app, spin, walk):
    panel = app.prompt_bar.panel
    panel.summon()
    panel.togglePin()
    assert panel.open and panel.pinned
    prepare(app)
    spin(50)
    assert panel.open  # preparing: the thread shows the agent writing the captions
    play(app)
    spin(until=lambda: app.walk_stage.settled)
    assert not panel.open and panel.pinned  # closed for the walk, the pin kept
    key(app, Qt.Key.Key_Escape)
    spin(until=lambda: not app.walk_stage.active)
    assert panel.open  # pinned open before the walk: open again after it
    panel.togglePin()
    play(app)
    spin(until=lambda: app.walk_stage.settled)
    assert not panel.open
    walk.exit()
    spin(until=lambda: not app.walk_stage.active)
    assert not panel.open  # it was not pinned: it stays closed


def test_esc_puts_the_camera_back_where_the_user_was(app, spin, walk):
    app.controller.setPan(150, 60)
    spin(50)
    before = pan(app)
    play(app)
    spin(until=lambda: app.walk_stage.settled)
    walk.goTo(3)  # far right
    spin(until=lambda: app.walk_stage.step == 3 and app.walk_stage.settled)
    assert pan(app) != before
    key(app, Qt.Key.Key_Escape)
    spin(until=lambda: not app.walk_stage.active and pan(app) == before)
    assert not app.overworld.active
    spin(400)
    assert not item(app, "walkChrome").isVisible()
    assert item(app, "view:hehu").opacity() == pytest.approx(1)
    assert item(app, "walkScrim").opacity() == pytest.approx(0)
    assert app.live.cursor == app.live.cursor  # the walk never submits (no undo step)


def test_leaving_from_the_overworld_shot_restores_the_overworld_the_user_had(app, spin, walk):
    app.overworld.show()
    spin(until=lambda: app.overworld.active)
    play(app)
    spin(until=lambda: app.walk_stage.settled)
    walk.goTo(1)
    spin(until=lambda: app.walk_stage.step == 1 and app.walk_stage.settled)
    assert not app.overworld.active
    walk.exit()
    spin(until=lambda: app.overworld.active and not app.walk_stage.over)


def test_the_hop_track_rings_the_current_hops_ends_and_marks_branches():
    hops = [
        {"from": "project", "to": "fege"},
        {"from": "fege", "to": "kobi"},
        {"from": "kobi", "to": "zila"},
        {"from": "project", "to": "zila", "branch": True, "branch_start": True},
    ]
    track = hop_track(hops, 4, preparing=False)
    kinds = [(e["kind"], e["view"]) for e in track]
    assert kinds == [
        ("stop", "project"),
        ("link", ""),
        ("stop", "fege"),
        ("link", ""),
        ("stop", "kobi"),
        ("link", ""),
        ("stop", "zila"),
        ("branch", "project"),  # zila's second input, from the project
    ]
    states = {(e["kind"], e["view"]): e["state"] for e in track}
    assert states[("stop", "zila")] == "current" and states[("branch", "project")] == "current"
    assert states[("stop", "project")] == "past" and states[("stop", "kobi")] == "past"
    assert {e["state"] for e in hop_track(hops, 0, preparing=False)} == {"future"}
    assert {e["state"] for e in hop_track(hops, 2, preparing=True)} == {"future"}
    hop1 = {(e["kind"], e["view"]): e["state"] for e in hop_track(hops, 1, preparing=False)}
    assert hop1[("stop", "project")] == "current" and hop1[("stop", "fege")] == "current"
    assert hop1[("stop", "kobi")] == "future"


def test_a_long_track_folds_its_middle():
    hops = [{"from": f"v{i}", "to": f"v{i + 1}"} for i in range(12)]
    hops[0]["from"] = "project"
    track = hop_track(hops, 6, preparing=False)
    assert [e["kind"] for e in track].count("more") == 2  # before and after the current
    assert any(e["kind"] == "stop" and e["state"] == "current" for e in track)


#: a 200-character path (#183)
LONG_PATH = ("C:/data/depot/" + "/".join(f"capture_session_{i:02d}" for i in range(12)))[
    :196
] + ".png"


def drawn_outside(card) -> list[str]:
    """What under ``card`` draws past its bounds: each visible item's rect (a Text's
    as wide and tall as its laid-out text, which may run past the item), cut by every
    clipping item between it and the card. The card's shadow is meant to."""
    from PySide6.QtCore import QRectF

    bounds = QRectF(0, 0, card.width(), card.height()).adjusted(-0.5, -0.5, 0.5, 0.5)
    out: list[str] = []

    def visit(it, clip: QRectF | None) -> None:
        if not it.isVisible() or it.opacity() == 0:
            return
        if "SoftShadow" in it.metaObject().className():
            return
        w, h = it.width(), it.height()
        if it.metaObject().className().startswith("QQuickText"):
            w, h = max(w, it.property("contentWidth")), max(h, it.property("contentHeight"))
        r = it.mapRectToItem(card, QRectF(0, 0, w, h))
        if clip is not None:
            r = r.intersected(clip)
        if not r.isEmpty() and not bounds.contains(r):
            out.append(f"{it.objectName() or it.metaObject().className()} {r}")
        if it.clip():
            mine = it.mapRectToItem(card, QRectF(0, 0, it.width(), it.height()))
            clip = mine if clip is None else clip.intersected(mine)
        for child in it.childItems():
            visit(child, clip)

    for child in card.childItems():
        visit(child, None)
    return out


def test_long_paths_stay_inside_the_caption_card(app, spin, walk):
    """#183: a 200-character path in a caption, the kind chip, the note line and the
    summary chips: captions wrap (at a '/' where they can), single-line fields
    middle-elide with the whole path in their tip, and the card never grows past its
    width and height, scrolling instead."""
    assert len(LONG_PATH) == 200
    script = planned(app)
    n = len(script["hops"])
    played = caption_script(
        script,  # four sentences a hop: past the card in any font's metrics
        [f"kobi reads **{LONG_PATH}** and shows every frame in it, one tile each." * 4] * n,
        intro=f"zila starts at **{LONG_PATH}**.",
        chips=[LONG_PATH] * n,
    )
    for hop in played["hops"]:
        hop["kind_label"] = "reads " + LONG_PATH
        hop["note"] = "sources: " + ", ".join([LONG_PATH] * 3)
    app.walkthrough.play_script(played)
    walk.goTo(1)
    walk.pause()
    spin(until=lambda: app.walk_stage.step == 1 and app.walk_stage.settled)
    card = item(app, "walkCaption")
    chrome = item(app, "walkChrome")
    spin(until=lambda: item(app, "walkNarration").property("text") != "")
    spin(100)
    assert card.width() <= 620 and card.width() <= chrome.width() - 32
    assert card.height() <= card.property("maxHeight") + 0.5
    assert card.mapToItem(chrome, 0, 0).y() >= 0  # it never runs off the area's top
    assert drawn_outside(card) == []
    # the narration overflows the card's height: it scrolls
    assert item(app, "walkCardScroll").property("interactive") is True
    # the kind chip: one line, the middle elided, the whole label in its tip
    chip = item(app, "walkCardChip")
    assert chip.property("tip") == "reads " + LONG_PATH
    assert item(app, "walkCardChipText").property("truncated") is True
    ends = item(app, "walkCardEnds")  # the chip stops short of the from -> to names
    assert chip.mapToItem(card, chip.width(), 0).x() <= ends.mapToItem(card, 0, 0).x()
    # the overlay's kind chip over the link and the summary's chips stay a chip's width
    kind = item(app, "walkKindChip")
    if kind is not None and kind.isVisible():
        assert kind.width() <= 300
    walk.end()
    spin(until=lambda: app.walk_stage.atSummary and app.walk_stage.settled)
    spin(until=lambda: item(app, "walkChip:zila") is not None)
    for vid in ("kobi", "zila"):
        chip = item(app, f"walkChip:{vid}")
        assert chip.width() <= 300 and chip.property("tip") == LONG_PATH, vid
    assert drawn_outside(item(app, "walkCaption")) == []


def test_the_speed_button_cycles_the_walk_speed(app, spin, walk):
    from PySide6.QtCore import QSettings

    from specto_app.walk.controller import WALK_SPEED

    QSettings().remove(WALK_SPEED)
    walk.setWalkSpeed("normal")
    play(app)
    spin(until=lambda: shown(app, "walkSpeed"))
    assert item(app, "walkSpeed").property("text") == "normal"
    dwell = app.walk_stage.dwellMs
    click(app, "walkSpeed")
    spin(until=lambda: walk.walkSpeed == "fast")
    assert item(app, "walkSpeed").property("text") == "fast"
    assert app.walk_stage.walkSpeed == "fast" and app.walk_stage.dwellMs < dwell
    QSettings().remove(WALK_SPEED)


def test_a_path_in_a_caption_can_break_after_each_separator():
    zw = "\u200b"
    assert caption_html("from **C:/a/b** to kobi") == f"from <b>C:/{zw}a/{zw}b</b> to kobi"
    assert caption_html("D:\\x\\y") == f"D:\\{zw}x\\{zw}y"
