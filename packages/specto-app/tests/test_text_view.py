"""The ``text`` view (SPEC-32): one short text the document carries in ``params.text``, the
"Not supported: <name> (<ext>)" view a file no view type shows opens in. Seam 2
(headless host) and the offscreen window (its body and its title)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app_doc_helpers import pin
from web_helpers import new_engine, registry

pytest.importorskip("PySide6.QtQuick")

from specto_app.app import find_item
from specto_app.views.text_view import TextHost
from specto_core import CallContext
from specto_core.canvas_document import file_tree_of
from specto_core.gestures import open_file

AGENT = CallContext(connection="mcp:1", origin="agent:s1")


def test_the_text_view_shows_the_documents_text_and_follows_its_params(qapp, tmp_path: Path):
    engine, proj = new_engine(tmp_path, frames=1)
    (proj.root / "notes" / "blob.bin").write_bytes(b"\x00" * 16)
    reg = registry(engine)
    doc = engine.live.document()
    doc["views"]["tuli"] = {
        "type": "text",
        "label": "blob.bin · notes",
        "layout": {"position": [380, 0], "size": [640, 480]},
        "params": {"text": "Not supported: blob.bin (.bin)"},
    }
    pin(doc, "tuli", sources=["notes/blob.bin"])
    engine.live.submit(doc, origin="agent:s1", label="a text view")
    host = reg.host("tuli")
    assert isinstance(host, TextHost)
    assert host.snapshot()["text"] == "Not supported: blob.bin (.bin)"
    assert engine.plugins.view("text").accepts == []

    doc = engine.live.document()
    doc["views"]["tuli"]["params"]["text"] = "something else"
    engine.live.submit(doc, origin="agent:s1", label="relabel")
    assert host.snapshot()["text"] == "something else"
    engine.live.undo()
    assert host.snapshot()["text"] == "Not supported: blob.bin (.bin)"


def test_an_unsupported_file_opens_a_text_view_saying_so_under_its_title(app, spin, project):
    """A double-click on a file no view shows (seam 2 through the window): the body says
    "Not supported: <name> (<ext>)" and the header shows ``<name>: <file> · <folder>``."""
    (project.root / "notes").mkdir()
    (project.root / "notes" / "blob.bin").write_bytes(b"\x00" * 16)
    tree = file_tree_of(app.live.document()["views"])
    opened = open_file(app.live, tree, "notes/blob.bin", plugins=app.engine.plugins)
    vid = opened.view
    spin(until=lambda: vid in app.hosts.hosts)
    spin(100)
    body = find_item(app.window.contentItem(), f"text:{vid}:body")
    assert body is not None and body.property("text") == "Not supported: blob.bin (.bin)"
    title = find_item(app.window.contentItem(), f"title:{vid}")
    assert title is not None and title.property("text") == f"{vid}: blob.bin · notes"
