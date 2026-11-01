"""The port capsule on the canvas (#160, glossary: port capsule): when a view is moved so
that its output circle comes within the join distance of the input circle it feeds (both
show the source's identicon), a solid capsule in the chain's colour joins the two, the
identicon at its midpoint; it holds while the view moves away until the circles pass the
break distance, then splits. Join and split animate briefly. Hovering the capsule shows
the link's tip (the input port's, #127). In the wiring layer (#82) the capsule stands
for the link: the link's curve and dot hide and a click on the capsule opens the link
view. Offscreen Qt; the real mouse is the scripted flow tools/gui_flows/v2-160-capsule.txt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.theme import COLORS, METRICS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
NOMOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
ANIM = METRICS["anim_ms"]
PORT = METRICS["port_px"]
GRACE = METRICS["tip_grace_ms"]
FOLLOW = "The rear gallery picks what the front gallery picks."


def compose(app, spin) -> None:
    """Two galleries side by side, tops aligned, 200 px apart: the front one's selection
    feeds the rear one (l2, the rear gallery's first input circle); both pinned off the
    project source."""
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [400, 40], "size": [400, 300]},
    }
    doc["views"]["tamu"] = {
        "type": "gallery",
        "label": "Rear",
        "layout": {"position": [1000, 40], "size": [400, 300]},
    }
    doc["links"]["l2"] = {
        "kind": "follow",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "tamu", "port": "selection"},
        "params": {},
        "summary": {"sentence": FOLLOW},
    }
    pin(doc, "kobi")
    pin(doc, "tamu")
    app.engine.call("canvas.submit", {"document": doc, "label": "compose"}, AGENT)
    spin(until=lambda: "tamu" in app.hosts.hosts)


def centre(item) -> QPointF:
    return item.mapToScene(item.boundingRect().center())


def shown(root, name: str) -> bool:
    item = find_item(root, name)
    return item is not None and item.isVisible()


def drag_header(app, spin, vid: str, dx: int, held=None) -> None:
    """Press on the view's header, move in steps, release: a real mouse move."""
    item = find_item(app.window.contentItem(), "move:" + vid)
    r = item.boundingRect()
    p0 = item.mapToScene(r.topLeft() + QPointF(r.width() / 2, r.height() / 2)).toPoint()
    QTest.mousePress(app.window, LEFT, NOMOD, p0)
    for i in range(1, 5):
        QTest.mouseMove(app.window, p0 + QPoint(dx * i // 4, 0))
        spin(5)
    if held is not None:
        held()
    QTest.mouseRelease(app.window, LEFT, NOMOD, p0 + QPoint(dx, 0))
    spin(10)


def gap(app) -> float:
    """The canvas px between the two views' rects."""
    k, t = app.live.data["views"]["kobi"]["layout"], app.live.data["views"]["tamu"]["layout"]
    return t["position"][0] - (k["position"][0] + k["size"][0])


def test_a_view_moved_near_its_partner_joins_their_ports_in_a_capsule_until_moved_away(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    c = app.controller
    assert c.capsuleMap["l2"]["joined"] is False
    capsule = find_item(root, "capsule:l2")
    assert capsule is not None and not capsule.isVisible()
    # the project source's links have no port circle on its node: no capsule
    assert {p for p in c.capsuleIds} == {"l2"}

    # drag the rear gallery to 20 px from the front one: the circles' centres are 24 px
    # apart, inside the join distance
    drag_header(app, spin, "tamu", -180)
    assert gap(app) == 20
    assert c.capsuleMap["l2"]["joined"] is True
    spin(ANIM + 100)
    assert capsule.isVisible() and capsule.property("progress") == 1
    out_port, in_port = find_item(root, "port:kobi:out"), find_item(root, "port:tamu:in")
    a, b = centre(out_port), centre(in_port)
    icon = find_item(root, "capsuleIcon:l2")
    m = centre(icon)
    # the identicon sits at the midpoint, the same as the ports show, in the chain colour
    assert abs(m.x() - (a.x() + b.x()) / 2) <= 0.5 and abs(m.y() - (a.y() + b.y()) / 2) <= 0.5
    assert icon.property("icon") == in_port.property("icon") == out_port.property("icon")
    assert icon.property("tone").name() == COLORS[c.views["tamu"]["chain"]]
    assert icon.width() == PORT
    # the solid body spans both circles: from the output circle's far side to the input's
    body = find_item(root, "capsuleBody:l2")
    box = body.mapRectToScene(body.childrenRect())
    assert box.left() <= a.x() - PORT / 2 + 0.5 and box.right() >= b.x() + PORT / 2 - 0.5
    assert abs(box.center().y() - a.y()) <= 0.5 and box.height() == PORT
    assert body.property("tone").name() == COLORS[c.views["tamu"]["chain"]]

    # pulled 20 px further (44 px apart): past the join distance, the capsule holds, during
    # the drag and after the drop; it follows the moving port
    def holding():
        assert c.capsuleMap["l2"]["joined"] is True
        assert abs(centre(icon).x() - (centre(out_port).x() + centre(in_port).x()) / 2) <= 0.5

    drag_header(app, spin, "tamu", 20, held=holding)
    assert gap(app) == 40 and c.capsuleMap["l2"]["joined"] is True
    spin(ANIM + 100)
    assert capsule.isVisible()

    # 20 px more (64 px apart): past the break distance, it splits
    drag_header(app, spin, "tamu", 20)
    assert gap(app) == 60 and c.capsuleMap["l2"]["joined"] is False
    spin(ANIM + 100)
    assert not capsule.isVisible()
    # back to 44 px: split, it stays split
    drag_header(app, spin, "tamu", -20)
    spin(ANIM + 100)
    assert c.capsuleMap["l2"]["joined"] is False and not capsule.isVisible()


def test_join_and_split_animate_briefly(app, spin):
    """The state flips at once; the capsule grows out of the two circles and shrinks back
    into them over the theme's animation time."""
    compose(app, spin)
    root = app.window.contentItem()
    c = app.controller
    capsule = find_item(root, "capsule:l2")
    c.set_drag_rect("tamu", (820.0, 40.0, 400.0, 300.0))
    assert c.capsuleMap["l2"]["joined"] is True
    assert capsule.property("progress") < 1  # the join has only started
    spin(ANIM + 100)
    assert capsule.property("progress") == 1
    c.set_drag_rect("tamu", (1000.0, 40.0, 400.0, 300.0))
    assert c.capsuleMap["l2"]["joined"] is False
    assert capsule.isVisible() and capsule.property("progress") > 0  # still splitting
    spin(ANIM + 100)
    assert not capsule.isVisible()
    c.cancelDrag("tamu")


def test_hovering_the_capsule_shows_the_links_tip(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    c = app.controller
    drag_header(app, spin, "tamu", -180)
    spin(ANIM + 100)
    QTest.mouseMove(app.window, centre(find_item(root, "capsuleIcon:l2")).toPoint())
    spin(GRACE + 100)
    tip = find_item(root, "tip:capsule:l2")
    assert tip is not None and tip.isVisible()
    assert find_item(root, "capsule:l2").property("tip") == c.views["tamu"]["inPorts"][0]["tip"]
    assert find_item(root, "tipFilter:capsule:l2").property("text") == FOLLOW[:-1]
    # the ports under it show no tip of their own
    assert not shown(root, "tip:port:tamu:in") and not shown(root, "tip:port:kobi:out")
    QTest.mouseMove(app.window, QPoint(app.window.width() - 5, app.window.height() - 5))
    spin(2 * GRACE + 100)
    assert not shown(root, "tip:capsule:l2")


def test_in_the_wiring_layer_the_capsule_stands_for_the_link_and_opens_it(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    drag_header(app, spin, "tamu", -180)
    spin(ANIM + 100)
    QTest.keyClick(app.window, Qt.Key.Key_QuoteLeft)
    spin(ANIM + 100)
    assert app.controller.wiring is True
    # the joined link's curve and dot give way to the capsule; the others stay
    assert shown(root, "capsule:l2")
    assert not shown(root, "wire:l2") and not shown(root, "wireNode:l2")
    assert shown(root, "wire:l3") and shown(root, "wireNode:l3")
    assert not shown(root, "linkView")
    QTest.mouseClick(app.window, LEFT, NOMOD, centre(find_item(root, "capsuleIcon:l2")).toPoint())
    spin(50)
    assert shown(root, "linkView")
    assert find_item(root, "linkSentence").property("text") == FOLLOW
    assert app.controller.undoDepth == 2  # the compose and the drag: looking is no edit
