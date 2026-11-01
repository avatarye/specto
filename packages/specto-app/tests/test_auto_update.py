"""Seam 2 (#102): the auto-update switch (the eye toggle since #126) in the header of a
view fed by an intaker, its state in the status bar, and refresh: on the view while
auto-update is off, and on the project node (the whole project rescans). The switch is
one user submit; a refresh is no undo step."""

from __future__ import annotations

import test_shell
from test_shell import click_item, copy_frames, gallery_doc, needs_testdata, submit

from specto_app.app import find_item

pytestmark = needs_testdata
frames_project = test_shell.frames_project
app = test_shell.app

OFF = "auto-update off · refresh"
ON = "auto-update on"


def _item(app, name: str):
    return find_item(app.window.contentItem(), name)


def test_the_switch_turns_auto_update_on_and_the_status_bar_says_so(app, spin):
    submit(app, gallery_doc(app), "gallery")
    spin(until=lambda: "kobi" in app.hosts.hosts)
    assert app.controller.views["kobi"]["autoUpdate"] is False
    app.controller.hoverView("kobi")
    assert OFF in app.controller.statusHint
    switch, refresh = _item(app, "autoUpdate:kobi"), _item(app, "refresh:kobi")
    assert switch is not None and switch.isVisible() and not switch.property("checked")
    assert refresh is not None and refresh.isVisible()

    click_item(app, "autoUpdate:kobi")
    spin(until=lambda: app.controller.views["kobi"]["autoUpdate"] is True)

    assert app.live.data["links"]["l2"]["params"]["watch"] is True
    assert app.live.history_summaries()[-1]["label"] == "auto-update Cam A on"
    assert ON in app.controller.statusHint
    # nothing to refresh while auto-update is on: its slot stays, out of sight (#126)
    assert switch.property("checked") and refresh.isVisible() and not refresh.isEnabled()
    spin(until=lambda: refresh.property("opacity") == 0)

    app.controller.undo()
    spin(until=lambda: app.controller.views["kobi"]["autoUpdate"] is False)
    assert OFF in app.controller.statusHint


def test_the_file_tree_auto_updates_by_default(app, spin, frames_project):
    """#115: a new canvas watches the project folder for the file tree; files arriving
    there show up with no refresh and no undo step."""
    spin(until=lambda: "fege" in app.hosts.hosts)
    tree = app.hosts.hosts["fege"]
    spin(until=lambda: len(tree.model.all_files()) == 12)
    assert app.controller.views["fege"]["autoUpdate"] is True
    app.controller.hoverView("fege")
    assert ON in app.controller.statusHint
    # the tree follows the folders it loaded itself (#231): the runtime watches nothing
    assert app.engine.links.watching() == [] and tree._watch.isActive()
    steps = len(app.live.history_summaries())

    copy_frames(frames_project.root / "runs" / "cam-a", 8)  # two frames arrive

    spin(until=lambda: len(tree.model.all_files()) == 16, timeout=10)
    assert len(app.live.history_summaries()) == steps


def test_with_the_file_tree_switched_off_the_project_node_refreshes_it(app, spin, frames_project):
    spin(until=lambda: "fege" in app.hosts.hosts)
    tree = app.hosts.hosts["fege"]
    spin(until=lambda: len(tree.model.all_files()) == 12)
    click_item(app, "autoUpdate:fege")
    spin(until=lambda: app.controller.views["fege"]["autoUpdate"] is False)
    assert app.engine.links.watching() == []
    steps = len(app.live.history_summaries())

    copy_frames(frames_project.root / "runs" / "cam-a", 8)  # two frames arrive
    click_item(app, "refresh:project")

    spin(until=lambda: len(tree.model.all_files()) == 16)
    assert len(app.live.history_summaries()) == steps  # a rescan, not an undo step


def test_refresh_on_a_view_rescans_its_intaker(app, spin, frames_project):
    submit(app, gallery_doc(app, "all_files"), "gallery")
    spin(until=lambda: getattr(app.hosts.hosts.get("kobi"), "collection", None) is not None)
    kobi = app.hosts.hosts["kobi"]
    assert len(kobi.collection.items) == 6

    copy_frames(frames_project.root / "runs" / "cam-a", 7)
    click_item(app, "refresh:kobi")

    spin(until=lambda: len(kobi.collection.items) == 7)
