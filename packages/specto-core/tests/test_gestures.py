"""Human gestures (move, resize, close a view, delete a link) write the canvas document
through the same submit path as the agent: each gesture is one small submit and one
undo step (spec #35 story 31, ADR 0013)."""

from __future__ import annotations

import pytest
from doc_helpers import app_plugins, pin

from specto_core import gestures
from specto_core.canvas_document import file_tree_of
from specto_core.canvas_example import worked_example
from specto_core.errors import InvalidArgs, NotFound
from specto_core.live_canvas import LiveCanvas
from specto_core.names import is_name


@pytest.fixture
def live() -> LiveCanvas:
    live = LiveCanvas.headless("demo")
    live.submit(worked_example(), origin="agent:s1", label="compose")
    return live


def test_move_and_resize_are_one_undo_step_each(live: LiveCanvas):
    moved = gestures.move_view(live, "kobi", (320.0, 16.0))
    assert moved.views["changed"] == ["kobi"] and moved.origin == "user"
    assert live.document()["views"]["kobi"]["layout"]["position"] == [320.0, 16.0]

    resized = gestures.resize_view(live, "kobi", (700.0, 420.0))
    assert live.document()["views"]["kobi"]["layout"] == {
        "position": [320.0, 16.0],
        "size": [700.0, 420.0],
    }
    assert [h["label"] for h in live.history_summaries()] == [
        "compose",
        "move Cam A",
        "resize Cam A",
    ]

    assert live.undo().txn == resized.txn
    assert live.document()["views"]["kobi"]["layout"]["size"] == [640.0, 400.0]
    assert live.undo().txn == moved.txn
    assert live.document()["views"]["kobi"]["layout"]["position"] == [380.0, 0.0]


def test_moving_several_views_is_one_undo_step_locked_ones_included(live: LiveCanvas):
    """#84: dragging one view of a multi-selection moves the whole group by the same
    delta in one submit; a lock binds the agent, not the user."""
    gestures.set_locked(live, "tamu", True)
    depth = len(live.history)

    moved = gestures.move_views(live, {"kobi": (340.0, 60.0), "tamu": (340.0, 480.0)})

    doc = live.document()
    assert doc["views"]["kobi"]["layout"]["position"] == [340.0, 60.0]
    assert doc["views"]["tamu"]["layout"]["position"] == [340.0, 480.0]
    assert sorted(moved.views["changed"]) == ["kobi", "tamu"] and moved.origin == "user"
    assert len(live.history) == depth + 1
    assert live.history_summaries()[-1]["label"] == "move 2 views"

    live.undo()
    doc = live.document()
    assert doc["views"]["kobi"]["layout"]["position"] == [380.0, 0.0]
    assert doc["views"]["tamu"]["layout"]["position"] == [380.0, 420.0]

    # one view: the same label as move_view
    one = gestures.move_views(live, {"kobi": (320.0, 16.0)})
    assert one.views["changed"] == ["kobi"]
    assert live.history_summaries()[-1]["label"] == "move Cam A"
    with pytest.raises(NotFound):
        gestures.move_views(live, {"kobi": (0.0, 0.0), "nope": (0.0, 0.0)})


def test_closing_a_view_removes_it_with_its_links_and_undo_brings_all_back(live: LiveCanvas):
    before = live.document()

    res = gestures.close_view(live, "rena")  # the video: root view, four time links, one intaker

    doc = live.document()
    assert "rena" not in doc["views"] and doc["root_view"] is None
    assert res.views["removed"] == ["rena"]
    assert set(res.links["removed"]) == {"l4", "l9", "l10", "l11", "l12"}
    assert set(doc["links"]) == {"l1", "l2", "l3", "l5", "l6", "l7", "l8", "l13"}

    live.undo()
    assert live.document() == before


def test_deleting_a_link_from_its_node(live: LiveCanvas):
    res = gestures.delete_link(live, "l7")
    assert res.links == {"added": [], "removed": ["l7"], "changed": []}
    assert res.label == (
        "delete link: Selecting a frame in one gallery selects the same frame in the other."
    )
    assert "l7" not in live.document()["links"]
    with pytest.raises(NotFound):
        gestures.delete_link(live, "l7")


def test_the_window_frame_follows_the_app_window(live: LiveCanvas):
    res = gestures.set_window_frame(live, (1920.0, 1080.0))
    assert live.document()["window_frame"]["rect"] == {"x": 0.0, "y": 0.0, "w": 1920.0, "h": 1080.0}
    assert res.label == "resize window"
    assert gestures.set_window_frame(live, (1920.0, 1080.0)).empty


def test_a_fresh_canvas_file_tree_follows_the_window_height():
    """A fresh canvas's file tree fills the visible canvas area (#125): a window resize
    on the untouched canvas sizes it to the frame's height, in the same one step, and
    keeps it beside the project node at the origin."""
    live = LiveCanvas.headless("demo")
    tree = file_tree_of(live.data["views"])
    for h in (640.0, 812.0, 1200.0):
        res = gestures.set_window_frame(live, (1300.0, h))
        assert res.label == "resize window" and res.views["changed"] == [tree]
        assert live.document()["views"][tree]["layout"] == {
            "position": [80.0, 0.0],
            "size": [280.0, h],
        }


def test_an_arranged_canvas_keeps_its_file_tree_through_a_window_resize(live: LiveCanvas):
    """Once the canvas holds more than the fresh tree, the tree is the user's (or the
    agent's) layout: a window resize leaves it be."""
    before = live.document()["views"]["fege"]["layout"]
    res = gestures.set_window_frame(live, (1300.0, 600.0))
    assert res.views["changed"] == [] and live.document()["views"]["fege"]["layout"] == before


def test_resizing_from_a_left_or_top_border_moves_the_origin_in_the_same_step(live: LiveCanvas):
    res = gestures.resize_view(live, "kobi", (660.0, 400.0), position=(360.0, 0.0))
    assert res.label == "resize Cam A" and res.views["changed"] == ["kobi"]
    assert live.document()["views"]["kobi"]["layout"] == {
        "position": [360.0, 0.0],
        "size": [660.0, 400.0],
    }
    live.undo()
    assert live.document()["views"]["kobi"]["layout"] == {
        "position": [380.0, 0.0],
        "size": [640.0, 400.0],
    }


def test_opening_the_image_view_of_a_gallery_adds_it_once_beside_the_gallery(live: LiveCanvas):
    """Double-click or Enter on a gallery tile (spec #35 story 38): one submit adds an image
    view fed by the same intaker and following the gallery's selection both ways; the
    next activation reuses it."""
    before = live.document()
    vid, res = gestures.open_image_view(live, "kobi")
    doc = live.document()
    assert is_name(vid) and res is not None and res.origin == "user"
    assert res.views["added"] == [vid] and set(res.links["added"]) == {"l14", "l15"}
    image = doc["views"][vid]
    gallery = doc["views"]["kobi"]
    assert image["type"] == "image" and image["label"] == "Cam A image"
    gx, gy = gallery["layout"]["position"]
    gw, gh = gallery["layout"]["size"]
    # beside the gallery, in the first spot down that no view covers (#106)
    assert image["layout"]["position"] == [gx + gw + 20, 820.0] and gy < 820.0
    assert image["layout"]["size"] == [gw, gh]
    data, follow = doc["links"]["l14"], doc["links"]["l15"]
    assert data["kind"] == "intaker" and data["params"] == doc["links"]["l2"]["params"]
    assert data["source"] == doc["links"]["l2"]["source"]
    assert data["target"] == {"view": vid, "port": "data"}
    assert data["scope"] == doc["links"]["l2"]["scope"] == "pinned"
    assert follow == {
        "kind": "follow",
        "label": "image of the picked frame",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": vid, "port": "selection"},
        "two_way": True,
        "params": {"match": None},
        "summary": {
            "sentence": "The image view shows the frame picked in Cam A.",
            "diagram": None,
        },
    }
    assert live.history_summaries()[-1]["label"] == "open image of Cam A"

    # already open: nothing is submitted
    assert gestures.open_image_view(live, "kobi") == (vid, None)
    assert len(live.history) == 2
    live.undo()
    assert live.document() == before
    with pytest.raises(NotFound):
        gestures.open_image_view(live, "fege")  # the file tree is not a gallery


def test_opening_a_gallery_preview_adds_it_once_beside_the_gallery(live: LiveCanvas):
    """#106: a double-click on a tile that is not an image opens the gallery's preview
    instead of its image view: one submit adds a preview fed by the same intaker and
    following the gallery's selection both ways; the next activation reuses it."""
    vid, res = gestures.open_gallery_preview(live, "kobi")
    doc = live.document()
    assert is_name(vid) and res is not None and res.origin == "user"
    preview = doc["views"][vid]
    assert preview["type"] == "preview" and preview["label"] == "Cam A preview"
    gx, gy = doc["views"]["kobi"]["layout"]["position"]
    gw, _gh = doc["views"]["kobi"]["layout"]["size"]
    assert preview["layout"]["position"] == [gx + gw + 20, 820.0] and gy < 820.0
    data, follow = doc["links"]["l14"], doc["links"]["l15"]
    assert data["params"] == doc["links"]["l2"]["params"]
    assert data["target"] == {"view": vid, "port": "data"}
    assert follow["source"] == {"view": "kobi", "port": "selection"}
    assert follow["target"] == {"view": vid, "port": "selection"} and follow["two_way"] is True
    assert follow["summary"]["sentence"] == "The preview shows the item picked in Cam A."
    assert live.history_summaries()[-1]["label"] == "open preview of Cam A"
    assert gestures.open_gallery_preview(live, "kobi") == (vid, None)
    # the image view is a separate thing: it is still added on its own
    image, _ = gestures.open_image_view(live, "kobi")
    assert image != vid and live.document()["views"][image]["type"] == "image"
    # below the preview, not on top of it
    _x, py = preview["layout"]["position"]
    assert live.document()["views"][image]["layout"]["position"][1] > py


def test_the_user_locks_and_unlocks_a_view_and_the_lock_binds_the_agent(live: LiveCanvas):
    from specto_core.canvas_document import CanvasInvalid

    locked = gestures.set_locked(live, "kobi", True)
    assert locked.label == "lock Cam A" and locked.origin == "user"
    assert live.document()["views"]["kobi"]["locked"] is True
    moved = live.document()
    moved["views"]["kobi"]["layout"]["position"] = [999.0, 0.0]
    with pytest.raises(CanvasInvalid) as e:
        live.submit(moved, origin="agent:s1", label="move it")
    assert e.value.problems[0]["path"] == "/views/kobi/layout"
    gestures.move_view(live, "kobi", (999.0, 0.0))  # the lock is the user's: their move passes
    assert gestures.set_locked(live, "kobi", False).label == "unlock Cam A"
    moved["views"]["kobi"]["locked"] = False
    assert live.submit(moved, origin="agent:s1", label="move it").touched == []


def _picks_link(doc: dict, view: str) -> dict:
    (link,) = [ln for ln in doc["links"].values() if ln["target"] == {"view": view, "port": "data"}]
    return link


def test_a_double_click_opens_a_dedicated_view_of_the_files_kind(live: LiveCanvas):
    """#43: a double-click on a file row opens a dedicated view: a view of the type that
    shows the file's kind (the registry's typed view, never the preview), fed by a pinned
    link from the project source to exactly that file, so later picks never change it.
    Its label is the file's name and its folder; a file at the root shows the folder as
    "." (headless) or the project's name. One submit, one undo step; the tree's picks
    are not touched."""
    plugins = app_plugins()
    opened = gestures.open_file(live, "fege", "notes/plan.md", mode="dedicated", plugins=plugins)
    vid = opened.view
    assert is_name(vid) and opened.action == "created" and opened.submit is not None
    assert opened.submit.origin == "user" and opened.submit.views["added"] == [vid]
    doc = live.document()
    view = doc["views"][vid]
    assert view["type"] == "markdown" and view["label"] == "plan.md · notes"
    assert view["layout"]["position"] == [380.0, 820.0]  # beside the tree, first free spot
    link = _picks_link(doc, vid)
    assert (link["kind"], link["scope"]) == ("intaker", "pinned")
    assert link["source"] == {"view": "project", "port": "all_files"}
    assert link["params"]["sources"] == ["notes/plan.md"]
    assert "selected" not in doc["views"]["fege"]["params"]
    assert live.history_summaries()[-1]["label"] == "open plan.md"
    live.undo()
    assert vid not in live.document()["views"]

    # the kind's own view: an image, a video, a stage, a point cloud, a pdf, a page; a
    # geojson file's typed view is the map, fed by read_contents on its features port
    for path, vtype in [
        ("look/a.jpg", "image"),
        ("look/b.mp4", "video"),
        ("look/c.usda", "usd"),
        ("look/d.ply", "3d"),
        ("look/e.pdf", "pdf"),
        ("look/f.html", "html"),
        ("look/g.json", "json"),
        ("look/h.txt", "json"),
    ]:
        v = gestures.open_file(live, "fege", path, mode="dedicated", plugins=plugins).view
        assert live.document()["views"][v]["type"] == vtype, path
    geo = gestures.open_file(live, "fege", "sites.geojson", mode="dedicated", plugins=plugins)
    doc = live.document()
    assert doc["views"][geo.view]["type"] == "map"
    assert doc["views"][geo.view]["label"] == "sites.geojson · ."
    (feed,) = [ln for ln in doc["links"].values() if ln["target"]["view"] == geo.view]
    assert feed["kind"] == "read_contents" and feed["target"]["port"] == "features"
    assert feed["source"] == {"view": "project", "port": "all_files"}
    assert feed["params"]["sources"] == ["sites.geojson"]

    # already open: the file's dedicated view is recognised by its link (a view the
    # agent pinned to that one file counts too), focused, never duplicated, no submit
    steps = len(live.history)
    again = gestures.open_file(live, "fege", "sites.geojson", mode="dedicated", plugins=plugins)
    assert again == gestures.OpenedFile(geo.view, "focused", None, "dedicated")
    doc = live.document()
    doc["views"]["lito"] = {"type": "json", "label": "the run", "layout": {"size": [300, 300]}}
    pin(doc, "lito", sources=["meta/run.json"])
    live.submit(doc, origin="agent:s1", label="pin the run's json")
    assert gestures.open_file(live, "fege", "meta/run.json", plugins=plugins).view == "lito"
    assert gestures.dedicated_view_of(live.document(), "meta/other.json") is None
    assert len(live.history) == steps + 1  # the agent's pin alone


def test_opening_a_file_contextually_adds_a_preview_on_the_trees_picks(live: LiveCanvas):
    """Ctrl+double-click on a file row (or the agent's ``canvas.open_file`` in its
    contextual mode, #106, #122, #43): no preview exists, so one submit adds one beside
    the file tree in the first free spot, fed by a selection intaker off the tree's
    picks, and the file is picked in the tree (navigation), so the preview shows it now
    and follows later picks. A file of a kind the preview cannot show falls back to the
    dedicated open: with no view for it at all, the "Not supported" text view."""
    plugins = app_plugins()
    opened = gestures.open_file(live, "fege", "notes/plan.md", mode="contextual", plugins=plugins)
    vid = opened.view
    assert is_name(vid) and opened.action == "created" and opened.submit is not None
    assert opened.submit.origin == "user" and opened.submit.views["added"] == [vid]
    doc = live.document()
    view = doc["views"][vid]
    assert view["type"] == "preview" and view["label"] == "Files preview"
    # beside the tree (x = 80 + 280 + gap); the spots next to the galleries are taken
    assert view["layout"]["position"] == [380.0, 820.0]
    link = _picks_link(doc, vid)
    assert (link["kind"], link["scope"], link["label"]) == ("intaker", "selection", "picked files")
    assert link["source"] == {"view": "fege", "port": "selected_files"}
    assert link["params"]["sources"] == ["."]
    assert link["summary"]["sentence"] == "The preview shows the file picked in Files."
    assert doc["views"]["fege"]["params"]["selected"] == ["notes/plan.md"]
    assert live.history_summaries()[-1]["label"] == "open plan.md"
    live.undo()
    assert vid not in live.document()["views"]
    odd = gestures.open_file(live, "fege", "notes/plan.xyz", mode="contextual", plugins=plugins)
    assert (odd.action, odd.mode) == ("created", "dedicated")
    view = live.document()["views"][odd.view]
    assert view["type"] == "text" and view["label"] == "plan.xyz · notes"
    assert view["params"]["text"] == "Not supported: plan.xyz (.xyz)"
    assert live.document()["views"]["fege"]["params"]["selected"] == ["notes/plan.xyz"]
    with pytest.raises(NotFound):
        gestures.open_file(live, "kobi", "notes/plan.md", plugins=plugins)  # not a file tree
    with pytest.raises(InvalidArgs):
        gestures.open_file(live, "fege", "notes/plan.md", mode="quick", plugins=plugins)


def test_opening_a_file_reuses_a_preview_and_puts_it_on_the_picks(live: LiveCanvas):
    """A preview already on the canvas shows the next file, whatever its kind, instead of
    a new view, the selected preview winning over the newest. One fed by a pinned
    intaker is re-pointed at the tree's picks (one submit, #122); one already on the
    picks gets the file picked in the tree (no submit). Typed views are never reused."""
    doc = live.document()
    doc["views"]["lito"] = {"type": "preview", "label": "second", "layout": {"size": [300, 300]}}
    pin(doc, "lito", sources=["notes"])
    live.submit(doc, origin="agent:s1", label="a pinned preview")

    plugins = app_plugins()
    opened = gestures.open_file(
        live, "fege", "geo/other.geojson", mode="contextual", plugins=plugins
    )
    assert (opened.view, opened.action) == ("lito", "repointed")
    assert opened.submit is not None and opened.submit.origin == "user"
    assert opened.submit.views["added"] == []
    doc = live.document()
    link = _picks_link(doc, "lito")
    assert (link["scope"], link["source"]) == (
        "selection",
        {"view": "fege", "port": "selected_files"},
    )
    assert doc["views"]["fege"]["params"]["selected"] == ["geo/other.geojson"]
    assert doc["views"]["pola"]["type"] == "map"  # the map was left alone
    assert live.history_summaries()[-1]["label"] == "show the picks of Files in second"

    # on the picks already: the pick is the whole gesture
    steps = len(live.history)
    opened = gestures.open_file(
        live, "fege", "papers/report.pdf", mode="contextual", plugins=plugins
    )
    assert opened == gestures.OpenedFile("lito", "selected", None, "contextual")
    assert live.document()["views"]["fege"]["params"]["selected"] == ["papers/report.pdf"]
    assert len(live.history) == steps  # navigation, not an undo step

    # two previews: the selected one is preferred, else the newest
    doc = live.document()
    doc["views"]["mesa"] = {"type": "preview", "label": "third", "layout": {"size": [300, 300]}}
    pin(doc, "mesa", sources=["notes"])
    live.submit(doc, origin="agent:s1", label="another preview")
    ctx = dict(mode="contextual", plugins=plugins)
    pick = gestures.open_file(live, "fege", "notes/c.md", prefer=["kobi", "lito"], **ctx)
    assert pick.view == "lito"
    assert gestures.open_file(live, "fege", "notes/b.json", **ctx).view == "mesa"


def test_repointing_a_preview_relabels_it_unless_the_label_was_written_by_hand(
    live: LiveCanvas,
):
    """#86 on the preview: a preview that only ever showed a file (its label is that
    file's name) is named for the picks it shows now when re-pointed; a label the agent
    or user wrote stays, and undo brings the old link and label back."""
    doc = live.document()
    doc["views"]["lito"] = {"type": "preview", "label": "plan.md", "layout": {"size": [300, 300]}}
    pin(doc, "lito", sources=["notes/plan.md"])
    live.submit(doc, origin="agent:s1", label="show the plan")
    before = live.document()
    ctx = dict(mode="contextual", plugins=app_plugins())
    gestures.open_file(live, "fege", "videos/front.mp4", **ctx)
    assert live.document()["views"]["lito"]["label"] == "Files preview"
    assert live.history_summaries()[-1]["label"] == "show the picks of Files in lito"
    live.undo()
    assert live.document()["views"]["lito"] == before["views"]["lito"]
    assert live.document()["links"] == before["links"]

    doc = live.document()
    doc["views"]["lito"]["label"] = "quick look"
    live.submit(doc, origin="agent:s1", label="relabel")
    gestures.open_file(live, "fege", "notes/more.md", **ctx)
    assert live.document()["views"]["lito"]["label"] == "quick look"
    assert live.history_summaries()[-1]["label"] == "show the picks of Files in quick look"


def test_an_agent_opens_a_file_through_the_canvas_open_file_command(engine):
    """``canvas.open_file`` is the gesture as a command (the prompt bar's "open this
    file"): the tree defaults to the canvas's file tree, the submit carries the caller's
    origin, and a preferred view of the type is honoured. Its ``mode`` is the gesture's
    (#43): ``contextual`` stays the command's default (the agent's quick look, as before
    #43), ``dedicated`` a view of the file's own; the result says which way it went."""
    from specto_core import CallContext

    app_plugins(engine.plugins)
    engine.live.submit(worked_example(), origin="agent:s1", label="compose")
    ctx = CallContext(connection="prompt-bar", origin="agent:s1")
    res = engine.call("canvas.open_file", {"path": "notes/plan.md"}, ctx)
    vid = res["view"]
    assert is_name(vid) and (res["action"], res["mode"]) == ("created", "contextual")
    assert res["submit"]["origin"] == "agent:s1" and res["submit"]["views"]["added"] == [vid]
    assert engine.live.document()["views"][vid]["type"] == "preview"
    again = engine.call("canvas.open_file", {"path": "x.png", "prefer": [vid]}, ctx)
    assert again["view"] == vid and again["action"] == "selected"  # it follows the picks
    with pytest.raises(NotFound):
        engine.call("canvas.open_file", {"tree": "kobi", "path": "x.png"}, ctx)

    own = engine.call("canvas.open_file", {"path": "notes/plan.md", "mode": "dedicated"}, ctx)
    assert (own["action"], own["mode"]) == ("created", "dedicated") and own["view"] != vid
    doc = engine.live.document()
    assert doc["views"][own["view"]]["type"] == "markdown"
    assert doc["views"][own["view"]]["label"] == "plan.md · notes"
    twice = engine.call("canvas.open_file", {"path": "notes/plan.md", "mode": "dedicated"}, ctx)
    assert twice == {"view": own["view"], "action": "focused", "submit": None, "mode": "dedicated"}
    # a stage the preview cannot show: the contextual open falls back to the usd view
    stage = engine.call("canvas.open_file", {"path": "worlds/a.usda", "mode": "contextual"}, ctx)
    assert (stage["action"], stage["mode"]) == ("created", "dedicated")
    assert engine.live.document()["views"][stage["view"]]["type"] == "usd"
    with pytest.raises(InvalidArgs):
        engine.call("canvas.open_file", {"path": "x.png", "mode": "quick"}, ctx)


def test_the_auto_update_switch_turns_watch_on_and_off_for_the_views_intaker(live: LiveCanvas):
    """#102: the switch in the header of a view fed by an intaker sets that intaker's
    params.watch; one user submit, one undo step. Off unless someone turned it on."""
    assert gestures.auto_update_of(live.document(), "kobi") is False
    assert gestures.auto_update_of(live.document(), "pola") is None  # no intaker feeds a map

    on = gestures.set_auto_update(live, "kobi", True)
    assert on.origin == "user" and on.links["changed"] == ["l2"]
    assert live.document()["links"]["l2"]["params"]["watch"] is True
    assert gestures.auto_update_of(live.document(), "kobi") is True
    assert live.history_summaries()[-1]["label"] == "auto-update Cam A on"

    gestures.set_auto_update(live, "kobi", False)
    assert live.document()["links"]["l2"]["params"]["watch"] is False
    live.undo()
    assert live.document()["links"]["l2"]["params"]["watch"] is True

    with pytest.raises(NotFound):
        gestures.set_auto_update(live, "pola", True)
