"""The view header standard (#126, design 05 section 2): the auto-update eye at the left
end (#158), the title (the name bold, the label muted), then the controls in a fixed
order, each one 22 px round button with a 14 px icon. View controls are always visible
(the user's decision of 2026-10-24, #158): at rest, hovered or selected alike; one that
is on wears the accent fill. Offscreen Qt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.theme import COLORS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
ORDER = ["autoUpdate", "refresh", "lock", "float", "collapse", "maximize", "close"]


def item(app, name: str):
    found = find_item(app.window.contentItem(), name)
    assert found is not None, name
    return found


def scene_x(it) -> float:
    return it.mapToScene(it.boundingRect().topLeft()).x()


def add_gallery(app, spin, vid: str = "kobi", position=(380, 40)) -> None:
    doc = app.live.document()
    doc["views"][vid] = {
        "type": "gallery",
        "label": "Front frames",
        "layout": {"position": list(position), "size": [480, 300]},
    }
    pin(doc, vid)
    app.engine.call("canvas.submit", {"document": doc, "label": f"add {vid}"}, AGENT)
    spin(until=lambda: vid in app.hosts.hosts)


def shown(app, vid: str) -> list[str]:
    """The header controls of ``vid`` a user sees: in a slot and not faded out."""
    out = []
    for name in ORDER:
        it = find_item(app.window.contentItem(), f"{name}:{vid}")
        if it is not None and it.isVisible() and it.property("opacity") > 0.5:
            out.append(name)
    return out


def test_the_controls_keep_a_fixed_order_of_round_buttons_and_show_at_rest(app, spin):
    add_gallery(app, spin)
    buttons = [item(app, f"{name}:kobi") for name in ORDER]
    xs = [scene_x(b) for b in buttons]
    assert xs == sorted(xs)
    for b in buttons:
        assert b.width() == b.height() == 22
        assert b.property("radius") == 11
    # the eye leads the header, before the name; the rest follow the title (#158)
    eye, title = buttons[0], item(app, "title:kobi")
    header = item(app, "header:kobi")
    assert scene_x(eye) - header.mapToScene(header.boundingRect().topLeft()).x() == 12
    assert scene_x(eye) + eye.width() < scene_x(title) < scene_x(buttons[1])
    # nothing is on (auto-update off, unlocked, docked, expanded, not maximised), the
    # pointer is away and nothing is selected: every control shows all the same
    area = item(app, "canvasArea")
    far = area.mapToScene(area.boundingRect().bottomRight()).toPoint() - QPoint(30, 30)
    QTest.mouseMove(app.window, far)
    spin(until=lambda: shown(app, "kobi") == ORDER)
    assert not any(b.property("on") for b in buttons)

    # hovering the header changes nothing: no fade, nothing moves
    QTest.mouseMove(app.window, header.mapToScene(header.boundingRect().center()).toPoint())
    spin(300)
    assert shown(app, "kobi") == ORDER
    assert [scene_x(b) for b in buttons] == xs


def test_a_control_that_is_on_is_lit_and_refresh_waits_for_auto_update_off(app, spin):
    add_gallery(app, spin)
    app.controller.toggleLock("kobi")
    app.controller.toggleAutoUpdate("kobi")
    spin(until=lambda: item(app, "autoUpdate:kobi").property("on"))
    assert item(app, "lock:kobi").property("on")
    # with auto-update on there is nothing to refresh: refresh stays out of sight, in
    # its slot, selected or not; every other control shows
    without_refresh = [n for n in ORDER if n != "refresh"]
    spin(until=lambda: shown(app, "kobi") == without_refresh)
    assert item(app, "refresh:kobi").isVisible()
    app.controller.selectView("kobi", False)
    spin(300)
    assert shown(app, "kobi") == without_refresh

    app.controller.clearSelection()
    app.controller.toggleFloat("kobi")
    spin(until=lambda: item(app, "float:kobi").property("on"))
    app.controller.toggleCollapse("kobi")
    spin(until=lambda: item(app, "collapse:kobi").property("on"))
    assert shown(app, "kobi") == without_refresh
    # collapsed: the icon is the title bar with its arrow down (expand)
    assert item(app, "collapse:kobi").property("name") == "expand"


def test_maximise_is_corners_out_and_maximised_corners_in(app, spin):
    add_gallery(app, spin)
    button = item(app, "maximize:kobi")
    assert button.property("name") == "maximize" and not button.property("on")
    app.controller.maximize("kobi")
    spin(until=lambda: button.property("on"))
    assert button.property("name") == "restore" and "maximize" in shown(app, "kobi")
    app.controller.maximize("")
    spin(until=lambda: not button.property("on"))
    assert shown(app, "kobi") == ORDER


def test_a_view_no_intaker_feeds_has_no_auto_update_or_refresh_slot(app, spin):
    add_gallery(app, spin)
    doc = app.live.document()
    doc["views"]["mihu"] = {
        "type": "json",
        "label": "Frame metadata",
        "layout": {"position": [900, 40], "size": [400, 300]},
    }
    doc["links"]["lf"] = {
        "kind": "follow",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "mihu", "port": "selection"},
        "summary": {"sentence": "The json view follows the gallery."},
    }
    app.engine.call("canvas.submit", {"document": doc, "label": "add mihu"}, AGENT)
    spin(until=lambda: "mihu" in app.controller.views)
    spin(until=lambda: shown(app, "mihu") == ORDER[2:])
    assert not item(app, "autoUpdate:mihu").isVisible()
    assert not item(app, "refresh:mihu").isVisible()
    # close keeps the far right: the same muscle memory on every view type
    close, header = item(app, "close:mihu"), item(app, "header:mihu")
    right = header.mapToScene(header.boundingRect().topRight()).x()
    assert right - (scene_x(close) + close.width()) == 12


def test_the_title_is_the_name_bold_then_the_label_muted(app, spin):
    add_gallery(app, spin)
    title = item(app, "title:kobi")
    assert title.property("text") == "kobi: Front frames"
    label = item(app, "label:kobi")
    assert label.property("text") == ": Front frames" and not label.property("font").bold()
    assert label.property("color").name() == QColor(COLORS["text_muted"]).name()


def test_frames_stay_one_and_a_half_px_selected_or_not(app, spin):
    """#201: the selection shows as the floor's footprint and the ink tone, not a thicker
    border."""
    add_gallery(app, spin)
    frame = item(app, "frame:kobi")
    assert frame.property("strokeWidth") == 1.5
    app.controller.selectView("kobi", False)
    spin(until=lambda: frame.property("tone").name() == "#3a3630")
    assert frame.property("strokeWidth") == 1.5


def test_a_click_on_a_control_taps_only_the_view_on_top(app, spin):
    """Two views on the same spot: a click on the upper header's lock locks that view
    alone, never the one under it, whose lock sits under the pointer too (#158)."""
    add_gallery(app, spin)
    add_gallery(app, spin, "tamu")
    upper, lower = item(app, "lock:tamu"), item(app, "lock:kobi")
    assert upper.mapToScene(upper.boundingRect().center()) == lower.mapToScene(
        lower.boundingRect().center()
    )
    p = upper.mapToScene(upper.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    spin(until=lambda: upper.property("on") or lower.property("on"))
    spin(300)
    assert [upper.property("on"), lower.property("on")].count(True) == 1


# -- the contextual tag (SPEC-32) ---------------------------------------------------------------
def add_preview(app, spin, vid: str, *, on_picks: bool, position=(380, 40)) -> None:
    """A preview beside the tree: on the tree's picks (a selection intaker, the
    contextual view) or pinned to the notes folder (the agent's fixed quick look)."""
    from specto_core.canvas_document import file_tree_of, next_id

    (app.project.root / "notes").mkdir(exist_ok=True)
    doc = app.live.document()
    doc["views"][vid] = {
        "type": "preview",
        "label": "Files preview",
        "layout": {"position": list(position), "size": [480, 300]},
    }
    if on_picks:
        tree = file_tree_of(doc["views"])
        doc["links"][next_id(doc["links"], "l")] = {
            "kind": "intaker",
            "scope": "selection",
            "source": {"view": tree, "port": "selected_files"},
            "target": {"view": vid, "port": "data"},
            "params": {"sources": ["."]},
            "summary": {"sentence": "The preview shows the file picked in the tree."},
        }
    else:
        pin(doc, vid, sources=["notes"])
    app.engine.call("canvas.submit", {"document": doc, "label": f"add {vid}"}, AGENT)
    spin(until=lambda: vid in app.hosts.hosts)


def visible(app, name: str) -> bool:
    it = find_item(app.window.contentItem(), name)
    return it is not None and it.isVisible()


def test_a_contextual_preview_says_so_in_its_header_and_on_its_overworld_card(app, spin):
    """SPEC-32: a preview on the file tree's picks is the contextual view: its header carries
    a "Contextual" tag and "follows selection" after the title, and its overworld card the
    same tag, so it cannot be taken for a dedicated view. A preview the agent pinned to
    fixed sources, and every other view (a gallery on the picks included), show none."""
    add_preview(app, spin, "pela", on_picks=True)
    add_preview(app, spin, "mesa", on_picks=False, position=(380, 400))
    add_gallery(app, spin, "kobi", position=(900, 40))
    tag = item(app, "contextual:pela")
    assert tag.isVisible() and tag.property("text") == "Contextual"
    follows = item(app, "follows:pela")
    assert follows.isVisible() and follows.property("text") == "follows selection"
    assert item(app, "title:pela").property("text") == "pela: Files preview"
    assert not visible(app, "contextual:mesa") and not visible(app, "follows:mesa")
    assert not visible(app, "contextual:kobi") and not visible(app, "follows:kobi")

    ow = app.overworld
    ow.toggle()
    spin(until=lambda: ow.active)
    assert visible(app, "overContextual:pela")
    assert not visible(app, "overContextual:mesa") and not visible(app, "overContextual:kobi")
