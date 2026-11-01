"""Seam 2 (#185): a text selection dragged out of a web view. The page's bridge sends
``drag {text, html}`` (``web/bridge.js``; bridge.test.mjs covers the page side) and the
host starts the drag: the text, its html and Specto's payload naming the file the text
came from, into a QML DropArea; no file URL, so an outside app takes the words."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from drag_helpers import DropTarget, drags_into
from web_helpers import compose_json, new_engine, registry, tree_emits_all

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtQuick import QQuickItem


@pytest.fixture
def target(qapp):
    t = DropTarget()
    yield t
    t.close()


@pytest.fixture
def json_view(qapp, tmp_path: Path):
    engine, proj = new_engine(tmp_path, frames=3)
    compose_json(engine)
    tree_emits_all(engine)
    reg = registry(engine)
    host = reg.host("tamu")
    host.bridge.ready()
    host.item = QQuickItem()  # headless: a stand-in for the page's item, the drag's source
    yield host, proj
    host.item.deleteLater()
    host.item = None


def test_a_dragged_selection_carries_its_text_and_the_file_it_came_from(qapp, json_view, target):
    host, proj = json_view
    host.show_key("00001")
    path = Path(host.current_path)
    with drags_into(target) as drags:
        host.bridge.send("drag", json.dumps({"text": '"frame": 1', "html": "<b>frame</b>"}))
        qapp.processEvents()  # the drag starts from the Qt loop, after the page's message
    assert len(drags) == 1
    drop = target.drops[0]
    assert drop["text"] == '"frame": 1' and drop["html"] == "<b>frame</b>"
    assert drop["urls"] == []
    doc = target.specto()
    assert doc["text"] == '"frame": 1'
    assert doc["project"] == str(proj.root).replace("\\", "/")
    (item,) = doc["items"]
    assert (item["view"], item["type"], item["key"], item["kind"]) == (
        "tamu",
        "json",
        "00001",
        "text",
    )
    assert item["path"] == os.path.relpath(path, proj.root).replace("\\", "/")
    assert item["label"]


def test_a_selection_with_nothing_shown_is_text_alone_and_no_text_is_no_drag(
    qapp, json_view, target
):
    host, _ = json_view
    host.show_key(None)
    with drags_into(target) as drags:
        host.bridge.send("drag", json.dumps({"text": "loose words"}))
        host.bridge.send("drag", json.dumps({"text": ""}))
        host.bridge.send("drag", "not json")
        qapp.processEvents()
    assert len(drags) == 1
    assert target.specto()["items"] == [] and target.drops[0]["text"] == "loose words"
