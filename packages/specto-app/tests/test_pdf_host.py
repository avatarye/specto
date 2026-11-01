"""Seam 2 (#69): the pdf view on ports, headless. The pdf host shows the selected pdf of
its collection (or the first) with its page count, keeps the page, zoom and fit the user
reaches for ``snap``, and lands on the canvas through the registry from a file tree ->
intaker composition screened on ``@pdf``."""

from __future__ import annotations

from pathlib import Path

import pytest
from app_doc_helpers import pin
from pdf_helpers import make_pdf
from web_helpers import intaker, new_engine, registry, tree_emits_all

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_app.hosts import HostContext
from specto_app.views.pdf_view import PdfHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection, Selection


def papers(root: Path) -> Collection:
    """``a.pdf`` (3 pages), ``b.pdf`` (5 pages) and a note without a pdf part."""
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for name, pages in (("a", 3), ("b", 5)):
        p = make_pdf(root / f"{name}.pdf", pages, title=name)
        items.append(Item(key=name, parts={".pdf": str(p)}, meta={"rel": {".pdf": p.name}}))
    note = root / "notes.md"
    note.write_text("# notes\n", encoding="utf-8")
    items.append(Item(key="notes", parts={".md": str(note)}, meta={"rel": {".md": note.name}}))
    return Collection(items=items, root=str(root))


@pytest.fixture
def engine(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    """QtPdf (and QPdfWriter's text) need a QGuiApplication, headless or not."""
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    return Engine(project=None, jobs=InlineJobManager())


def test_pdf_host_shows_the_selected_pdf_with_its_page_count(engine: Engine, tmp_path: Path):
    ctx = HostContext(engine, None)
    doc = engine.live.document()
    doc["views"]["v3"] = {"type": "pdf", "label": "Papers", "layout": {}}
    pin(doc, "v3")
    engine.live.submit(doc, origin="agent:s1", label="pdf")
    host = PdfHost(ctx, "v3", ctx.manifest_for("pdf"), headless=True)
    assert host.key is None and ctx.health_of("v3")["status"] == "paused"

    col = papers(tmp_path / "papers")
    host.receive("data", col)
    assert host.keys == ["a", "b"]  # the note has no pdf part: not listed
    assert host.key == "a" and host.index == 0 and host.page_count == 3  # the first until picked
    assert host.path.endswith("a.pdf")
    assert ctx.health_of("v3") == {"status": "ok", "message": "a.pdf  3 pages", "hint": ""}

    # a selection arriving on the port is shown; an item without a pdf part is not
    host.receive("selection", Selection(keys=["b"], last="b"))
    assert host.key == "b" and host.page_count == 5
    host.receive("selection", Selection(keys=["notes"], last="notes"))
    assert host.key == "b"
    assert ctx.links.output_of("v3", "selection") is None  # nothing goes out of a pdf view

    # the item reports where the user is: page (0-based), zoom and fit, kept for snap
    host.on_view_changed(2, 1.5, "none")
    snap = host.snapshot()
    assert snap["count"] == 2 and snap["selected"] == {"index": 1, "key": "b"}
    assert snap["page"] == 2 and snap["page_count"] == 5
    assert snap["zoom"] == 1.5 and snap["fit"] == "none"
    assert snap["document"]["status"] == "ok" and snap["document"]["path"].endswith("b.pdf")

    # a file that is not a pdf inside: health says so
    bad = tmp_path / "papers" / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    broken = Item(key="bad", parts={".pdf": str(bad)}, meta={"rel": {".pdf": "bad.pdf"}})
    items = [*col.items, broken]
    host.receive("data", Collection(items=items, root=col.root))
    assert host.key == "b"  # the pick survives a new collection
    host.receive("selection", Selection(keys=["bad"], last="bad"))
    assert host.key == "bad" and host.page_count == 0
    assert ctx.health_of("v3")["status"] == "error"
    assert "bad.pdf" in ctx.health_of("v3")["message"]

    # a selection that arrives before the data is honoured once the data lands
    host.receive("data", None)
    assert host.key is None and host.page_count == 0
    assert ctx.health_of("v3")["status"] == "paused"
    host.receive("selection", Selection(keys=["a"], last="a"))
    host.receive("data", col)
    assert host.key == "a" and host.page_count == 3


def test_registry_feeds_a_pdf_view_from_a_file_tree_intaker_screened_on_pdf(qapp, tmp_path: Path):
    """The agent's composition: a pdf view fed by a pinned intaker over ``papers/``
    screened on ``@pdf`` shows the first paper, headless, through the real resolver."""
    engine, proj = new_engine(tmp_path)
    make_pdf(proj.root / "papers" / "report.pdf", 4, title="report")
    make_pdf(proj.root / "papers" / "slides.pdf", 2, title="slides")
    (proj.root / "papers" / "readme.md").write_text("# papers\n", encoding="utf-8")
    engine.links.resolve("l1")  # the project scan ran before the papers were written
    doc = engine.live.document()
    doc["views"]["guge"] = {"type": "pdf", "label": "Papers", "layout": {"size": [800, 600]}}
    link = intaker("selected_files", "guge", ["papers"], ["@pdf"])
    del link["params"]["keys"]
    doc["links"]["l2"] = link
    engine.live.submit(doc, origin="agent:s1", label="the papers")
    reg = registry(engine)
    h = reg.host("guge")
    assert isinstance(h, PdfHost)
    tree_emits_all(engine)
    assert h.keys == ["report", "slides"]  # keyed under the intaker's source folder
    assert h.key == "report" and h.page_count == 4
    assert h.ctx.health_of("guge")["message"] == "report.pdf  4 pages"
