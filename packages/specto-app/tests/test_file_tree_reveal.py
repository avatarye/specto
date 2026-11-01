"""Seam 2 (#168): a selection received on the file tree's ``selection`` input is shown
like a pick: the rows are selected, their folders open, and the list scrolls to the
first one when it was off screen. Offscreen Qt, the real shell."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QRectF

from specto_core import Project

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item
from specto_core.links import Selection

FOLDERS = 120


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """One top folder (open by default) holding many closed folders of a few files."""
    proj = Project.init(tmp_path / "proj", "demo")
    for i in range(FOLDERS):
        d = proj.root / "vault" / f"d{i:03d}"
        d.mkdir(parents=True)
        for j in range(4):
            (d / f"note{j}.md").write_bytes(b"x")
    return proj


def test_a_received_selection_far_down_opens_its_folder_and_scrolls_to_it(app, spin):
    spin(until=lambda: find_item(app.window.contentItem(), "treeRow:fege:vault/d000") is not None)
    tree = find_item(app.window.contentItem(), "fileTreeList:fege")
    host = app.hosts.hosts["fege"]
    assert tree.property("contentY") == 0

    target = "vault/d100/note2.md"
    host.receive("selection", Selection(keys=[target], last=target))
    spin(until=lambda: find_item(app.window.contentItem(), f"treeRow:fege:{target}") is not None)

    assert host.model.selected == {target} and "vault/d100" in host.model.expanded
    assert host.snapshot()["selected"] == [target]
    row = find_item(app.window.contentItem(), f"treeRow:fege:{target}")
    box = row.mapRectToScene(QRectF(0, 0, row.width(), row.height()))
    view = tree.mapRectToScene(QRectF(0, 0, tree.width(), tree.height()))
    assert tree.property("contentY") > 0
    assert view.top() - 1 <= box.top() and box.bottom() <= view.bottom() + 1
    assert app.engine.live.data["views"]["fege"]["params"]["selected"] == [target]
