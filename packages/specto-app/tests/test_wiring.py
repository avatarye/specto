"""The wiring layer (#82, ADR 0018; glossary: wiring layer): the backtick/tilde key
toggles it over the normal view: views dim, the port circles stay clear, every link is a
curve in its chain's colour from the source's output circle to the input circle it
occupies, with a link node (a dot) that opens the link view (the overworld's). Esc or the key
again leaves it; the key is ignored while a text field has the focus. Pan and select keep
working and the curves follow a view while it is dragged. The overworld keeps its own
wires and hides the layer while it shows. Offscreen Qt; the real keyboard is the scripted
flow tools/gui_flows/v2-82-wiring.txt."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from specto_core import CallContext

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item
from specto_app.canvas.controller import WIRING_HINT
from specto_app.theme import METRICS

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
NOMOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
SENTENCE = "The gallery shows the files picked in the file tree."
TWO_WAY = "Picking in one gallery picks in the other."
PAD, GUTTER, INSET = 12, METRICS["gutter"], METRICS["port_inset"]
STEP = METRICS["port_px"] + METRICS["port_stack"]


def compose(app, spin) -> None:
    """Two galleries fed by the file tree (l2, l3), their selections two-way (l4): the
    second gallery's input circles are the tree's and the first gallery's."""
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [300, 40], "size": [640, 400]},
    }
    doc["views"]["tamu"] = {
        "type": "gallery",
        "label": "Rear",
        "layout": {"position": [300, 460], "size": [640, 400]},
    }
    for lid, target in (("l2", "kobi"), ("l3", "tamu")):
        doc["links"][lid] = {
            "kind": "intaker",
            "scope": "selection",
            "label": "picked files",
            "source": {"view": "fege", "port": "selected_files"},
            "target": {"view": target, "port": "data"},
            "params": {"sources": ["."], "screener": ["@images"]},
            "summary": {"sentence": SENTENCE},
        }
    doc["links"]["l4"] = {
        "kind": "follow",
        "two_way": True,
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "tamu", "port": "selection"},
        "params": {},
        "summary": {"sentence": TWO_WAY},
    }
    app.engine.call("canvas.submit", {"document": doc, "label": "compose"}, AGENT)
    spin(until=lambda: "tamu" in app.hosts.hosts)


def shown(root, name: str) -> bool:
    item = find_item(root, name)
    return item is not None and item.isVisible()


def press_backtick(app, spin) -> None:
    QTest.keyClick(app.window, Qt.Key.Key_QuoteLeft)
    spin(20)


def test_the_backtick_key_toggles_the_wiring_layer_over_the_normal_view(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    c = app.controller
    assert c.wiring is False
    assert not shown(root, "wiringLayer") and find_item(root, "wire:l2") is None

    press_backtick(app, spin)
    assert c.wiring is True
    assert shown(root, "wiringLayer")
    for lid in ("l1", "l2", "l3", "l4"):
        assert shown(root, f"wire:{lid}"), lid
        assert shown(root, f"wireNode:{lid}"), lid
    assert shown(root, "canvasArea") and shown(root, "slot:kobi")  # the views stay
    assert c.undoDepth == 1  # looking is not an edit
    # the status bar's one line says how to leave; off, it names the key
    assert c.statusHint == WIRING_HINT  # the status bar's one line (#80) is the layer's
    assert find_item(root, "statusHint").property("text") == WIRING_HINT
    c.hoverView("fege")  # a hovered view does not take the line from the layer
    assert c.statusHint == WIRING_HINT

    # Shift+backtick is the same key (tilde); the second press leaves the layer
    QTest.keyClick(app.window, Qt.Key.Key_AsciiTilde, Qt.KeyboardModifier.ShiftModifier)
    spin(20)
    assert c.wiring is False
    assert not shown(root, "wiringLayer") and find_item(root, "wire:l2") is None
    assert find_item(root, "wireNode:l2") is None
    # off, a hovered healthy view names the key among its gestures
    assert c.statusHint.startswith("fege: ") and c.statusHint.endswith("` wiring layer")
    c.leaveView("fege")
    assert c.statusHint == ""


def test_the_key_types_a_backtick_in_the_prompt_bar_instead_of_toggling_the_layer(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    prompt = find_item(root, "promptBar")
    prompt.focusInput()
    spin(until=lambda: find_item(root, "promptInput").hasActiveFocus())
    field = find_item(root, "promptInput")
    QTest.keyClick(app.window, Qt.Key.Key_QuoteLeft)
    spin(10)
    assert field.property("text") == "`"
    assert app.controller.wiring is False
    # the field lets go: the key is the layer's again
    find_item(root, "canvasArea").forceActiveFocus()
    spin(5)
    press_backtick(app, spin)
    assert app.controller.wiring is True


def click(app, spin, name: str):
    item = find_item(app.window.contentItem(), name)
    assert item is not None and item.isVisible(), name
    p = item.mapToScene(item.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, LEFT, NOMOD, p)
    spin(30)
    return item


def test_a_link_node_in_the_layer_is_a_dot_that_opens_the_link_view_until_the_layer_goes(app, spin):
    """#121: the node is a small dot with no label; a click opens the link view, which
    carries the summary sentence, Delete and Ask agent to change."""
    compose(app, spin)
    root = app.window.contentItem()
    app.controller.setWiring(True)
    spin(20)
    node = find_item(root, "wireNode:l2")
    assert node.width() == node.height() == METRICS["link_dot_hit"]
    dot = find_item(node, "dot:wireNode:l2")
    assert dot.width() == dot.height() == METRICS["link_dot_px"] and dot.isVisible()
    assert not shown(root, "linkView")
    click(app, spin, "wireNode:l2")
    assert app.overworld.selectedLink == "l2"
    assert shown(root, "linkView")
    assert find_item(root, "linkSentence").property("text") == SENTENCE
    for name in ("linkDelete", "linkAskAgent", "linkClose"):
        assert shown(root, name), name
    assert find_item(root, "linkSummary") is None and find_item(root, "linkAdvanced") is None
    # the selected link's curve is drawn selected
    assert find_item(root, "wire:l2").property("selected") is True
    # Esc closes the link view first, whatever has the focus (a gallery takes Esc for
    # its quick look); the layer stays
    find_item(root, "slot:kobi").forceActiveFocus()
    QTest.keyClick(app.window, Qt.Key.Key_Escape)
    spin(30)
    assert not shown(root, "linkView") and app.controller.wiring is True
    assert shown(root, "wire:l2")
    # the next Esc leaves the layer; the selection goes with it
    QTest.keyClick(app.window, Qt.Key.Key_Escape)
    spin(30)
    assert app.controller.wiring is False
    assert find_item(root, "wire:l2") is None
    assert app.overworld.selectedLink == ""
    # with the layer off, Esc is the shell's again (nothing of the layer takes it)
    app.controller.selectView("kobi", False)
    QTest.keyClick(app.window, Qt.Key.Key_Escape)
    spin(30)
    assert app.controller.wiring is False
    assert app.controller.undoDepth == 1


def area_rect(root, name: str) -> tuple[float, float, float, float]:
    """The item's rect in canvas-area px (the layer is anchored to the area)."""
    item = find_item(root, name)
    assert item is not None, name
    p = item.mapToItem(find_item(root, "canvasArea"), item.boundingRect().topLeft())
    return (p.x(), p.y(), item.width(), item.height())


def curve_end(root, link_id: str) -> tuple[float, float]:
    """Where the curve arrives, in canvas-area px."""
    w = find_item(root, f"wire:{link_id}")
    return (w.property("x2"), w.property("y2"))


def test_curves_attach_to_the_port_circles_and_follow_a_view_while_it_is_dragged(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    app.controller.setWiring(True)
    spin(20)
    # the two-way link is Rear's second input circle: its curve arrives there, the
    # intaker's at the first; both leave their source's output circle
    for lid, port in (("l3", "port:tamu:in"), ("l4", "port:tamu:in:1"), ("l2", "port:kobi:in")):
        px, py, pw, ph = area_rect(root, port)
        assert curve_end(root, lid) == (px + pw / 2, py + ph / 2), lid
    ox, oy, ow, oh = area_rect(root, "port:kobi:out")
    w4 = find_item(root, "wire:l4")
    assert (w4.property("x1"), w4.property("y1")) == (ox + ow / 2, oy + oh / 2)
    assert curve_end(root, "l3") == (PAD + 300 + GUTTER, PAD + 460 + GUTTER + INSET)
    assert curve_end(root, "l4")[1] == PAD + 460 + GUTTER + INSET + STEP

    # a real header drag: the curve into Front and the node on it move with the card
    node = find_item(root, "wireNode:l2")
    nx0 = node.x()
    header = find_item(root, "header:kobi")
    p0 = header.mapToScene(header.boundingRect().topLeft() + QPoint(60, 15)).toPoint()
    QTest.mousePress(app.window, LEFT, NOMOD, p0)
    QTest.mouseMove(app.window, p0 + QPoint(40, 60))
    QTest.mouseMove(app.window, p0 + QPoint(80, 120))
    spin(10)
    assert curve_end(root, "l2") == (PAD + 380 + GUTTER, PAD + 160 + GUTTER + INSET)
    assert find_item(root, "wireNode:l2").x() == nx0 + 40
    QTest.mouseRelease(app.window, LEFT, NOMOD, p0 + QPoint(80, 120))
    spin(10)
    assert app.live.data["views"]["kobi"]["layout"]["position"] == [380.0, 160.0]
    assert curve_end(root, "l2") == (PAD + 380 + GUTTER, PAD + 160 + GUTTER + INSET)
    assert app.controller.selectedViews == ["kobi"]  # select kept working under the layer

    # a pan moves everything together
    app.controller.setPan(100.0, 50.0)
    spin(10)
    assert curve_end(root, "l2") == (PAD + 280 + GUTTER, PAD + 110 + GUTTER + INSET)
    assert app.controller.undoDepth == 2  # the move only


def test_the_views_dim_under_the_layer_and_the_overworld_hides_it(app, spin):
    compose(app, spin)
    root = app.window.contentItem()
    frame = find_item(root, "frame:kobi")
    port = find_item(root, "port:kobi:in")
    assert frame.opacity() == 1
    app.controller.setWiring(True)
    spin(until=lambda: frame.opacity() < 0.6)  # the fade settles
    assert port.opacity() == 1 and port.isVisible()  # the ports stay clear
    # the overworld draws its own wires; the layer waits underneath and returns after
    ow = app.overworld
    ow.toggle()
    spin(until=lambda: ow.active)
    assert not shown(root, "wiringLayer") and shown(root, "overWire:l2")
    assert find_item(root, "wire:l2") is None
    assert app.controller.wiring is True
    ow.toggle()
    spin(until=lambda: not ow.active)
    assert shown(root, "wiringLayer") and shown(root, "wire:l2")
    app.controller.setWiring(False)
    spin(until=lambda: frame.opacity() == 1)


def test_link_dots_in_the_layer_sit_on_their_wires_clear_of_headers_and_port_circles(app, spin):
    """#90 in the normal view, #121 for dots: the layer's link dots follow the
    overworld's rule (the shared ``chains.place_nodes``): on the wire, at the spot
    nearest the middle clear of the view headers and port circles, else at the middle;
    never off the wire. The v2-45 canvas (five views, six links, several wires lying in
    the header bands)."""
    from test_overworld import compose_four

    compose_four(app, spin)
    press_backtick(app, spin)
    assert app.controller.wiring
    spin(60)  # the node chips report their sizes, the places settle
    root = app.window.contentItem()

    def scene_rect(item):
        p = item.mapToScene(item.boundingRect().topLeft())
        return (p.x(), p.y(), item.width(), item.height())

    def overlaps(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

    views = ("fege", "kobi", "tamu", "rena", "pola")
    keep_off = {}
    for v in views:
        for name in (f"header:{v}", f"port:{v}:in", f"port:{v}:in:1", f"port:{v}:out"):
            item = find_item(root, name)
            if item is not None and item.isVisible():
                keep_off[name] = scene_rect(item)
    assert "header:kobi" in keep_off and "port:pola:in:1" in keep_off
    places = app.controller.nodePlaces
    wires = {w["id"]: w for w in app.controller.wires}
    for lid in ("l1", "l2", "l3", "l4", "l5", "l6"):
        node = find_item(root, f"wireNode:{lid}")
        assert node is not None and node.isVisible(), lid
        nr = scene_rect(node)
        assert nr[2] == nr[3] == METRICS["link_dot_hit"], (lid, nr)  # a dot (#121)
        assert on_wire(places[lid], wires[lid]), lid
        if places[lid] != {"x": wires[lid]["nx"], "y": wires[lid]["ny"]}:
            for name, r in keep_off.items():
                assert not overlaps(nr, r), (lid, nr, name, r)
        # centred on the controller's place (canvas px; the layer adds pad - pan)
        area = find_item(root, "wiringLayer")
        c = node.mapToItem(area, node.boundingRect().center())
        pan = app.controller.pan
        assert abs(c.x() - (PAD + places[lid]["x"] - pan["x"])) < 0.5, lid
        assert abs(c.y() - (PAD + places[lid]["y"] - pan["y"])) < 0.5, lid
    # a wire over the views' bodies keeps its dot at the middle, and so does the short
    # wire from the tree into kobi, which lies wholly in the header bands
    for lid in ("l2", "l3"):
        assert places[lid] == {"x": wires[lid]["nx"], "y": wires[lid]["ny"]}, lid


def on_wire(place: dict, wire: dict) -> bool:
    """True when ``place`` is one of the spots a link dot may take on ``wire`` (#121)."""
    from specto_app.canvas.chains import NODE_STEPS, curve_point

    return any(
        abs(x - place["x"]) < 1e-6 and abs(y - place["y"]) < 1e-6
        for x, y in (curve_point(wire, t) for t in NODE_STEPS)
    )


def test_link_dots_in_the_layer_stay_inside_the_canvas_area(app, spin):
    """#121: panned so a wire's middle lies above the canvas area, its dot moves along
    the wire into the area (with the top bar gone, #120, a dot above the area is off the
    window); a pan back returns it to the middle."""
    compose(app, spin)
    app.controller.setWiring(True)
    spin(30)
    wire = next(w for w in app.controller.wires if w["id"] == "l3")  # tree -> tamu, steep
    assert app.controller.nodePlaces["l3"] == {"x": wire["nx"], "y": wire["ny"]}
    app.controller.setPan(0.0, wire["ny"] + 40.0)
    spin(30)
    vy, vh = app.controller.viewport["y"], app.controller.viewport["h"]
    assert vy > wire["ny"]  # the middle is above the area
    place = app.controller.nodePlaces["l3"]
    half = METRICS["link_dot_hit"] / 2
    assert vy <= place["y"] - half and place["y"] + half <= vy + vh
    assert on_wire(place, wire)
    node = find_item(app.window.contentItem(), "wireNode:l3")
    area = find_item(app.window.contentItem(), "wiringLayer")
    c = node.mapToItem(area, node.boundingRect().center())
    assert abs(c.y() - (PAD + place["y"] - app.controller.pan["y"])) < 0.5
    app.controller.home()
    spin(30)
    assert app.controller.nodePlaces["l3"] == {"x": wire["nx"], "y": wire["ny"]}


def hover(app, spin, name: str) -> None:
    item = find_item(app.window.contentItem(), name)
    assert item is not None and item.isVisible(), name
    QTest.mouseMove(app.window, QPoint(1, 1))
    spin(20)
    QTest.mouseMove(app.window, item.mapToScene(item.boundingRect().center()).toPoint())
    spin(60 + METRICS["tip_grace_ms"])  # a tip still open hands over after a dwell (#127)


def test_hover_tips_show_under_the_layer_and_the_link_summary_is_the_input_port_tip(app, spin):
    """#111: the layer takes no hover from the views under it (a header button's and a
    port circle's tips show as with the layer off). #121: the link's summary is the tip
    of the input port it filters; the link node, a dot, only names its ends."""
    compose(app, spin)
    root = app.window.contentItem()
    press_backtick(app, spin)
    assert app.controller.wiring
    hover(app, spin, "close:kobi")
    assert shown(root, "tip:close:kobi")
    hover(app, spin, "port:kobi:in")
    assert shown(root, "tip:port:kobi:in")
    tip = find_item(root, "port:kobi:in").property("tip")
    assert tip["filter"] == "The gallery shows the files picked in the file tree"
    hover(app, spin, "wireNode:l2")
    assert shown(root, "tip:wireNode:l2") and not shown(root, "tip:port:kobi:in")
    # #127: one line, no filter; the ends are links
    parts = find_item(root, "wireNode:l2").property("tip")["parts"]
    assert "".join(p["text"] for p in parts) == "fege → kobi · click to open the link"
    assert [p.get("view") for p in parts if p.get("view")] == ["fege", "kobi"]
    # the box holds its line (a tip with no data section still sizes to its parts)
    box = find_item(root, "tip:wireNode:l2").mapRectToScene(
        find_item(root, "tip:wireNode:l2").boundingRect()
    )
    for end in ("fege", "kobi"):
        link = find_item(root, f"tipLink:wireNode:l2:{end}")
        r = link.mapRectToScene(link.boundingRect())
        assert link.isVisible() and box.contains(r), end
    # the tip goes while the link is open
    click(app, spin, "wireNode:l2")
    assert shown(root, "linkView") and not shown(root, "tip:wireNode:l2")
