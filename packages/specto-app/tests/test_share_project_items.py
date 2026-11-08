"""SPEC-91: a project that itself lives on a share (``\\\\host\\share\\proj``; a mapped
drive resolves to one too) serves its own items. The items host judges a part's path
against the root of what delivered it (``ItemResolver.guard_root``): a collection's or a
content value's root, else the project's, an intaker table's or the scratch store's. A
network part outside that share, and a part with a bare leading slash (the current
drive's root on Windows), stay refused. No network is touched: ``is_file`` on the fake
share answers from the test."""

from __future__ import annotations

import os
import pathlib
from types import SimpleNamespace

import pytest

from specto_app.views.scratch_view import ScratchItems
from specto_app.web.capabilities import Capabilities
from specto_app.web.items import CompositeItems, ItemTable, LinkItems
from specto_app.web.scheme import locate_item
from specto_core.contents import ContentEntry, ContentValue
from specto_core.kinds import Item
from specto_core.links import Collection

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows share paths")

SHARE = "\\\\fakehost\\share\\proj"


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """``Path.is_file`` on the fake host answers True and is recorded; any other
    filesystem call naming it fails the test (it would connect out)."""
    asked: list[str] = []
    is_file = pathlib.Path.is_file

    def fake_is_file(self, *a, **k):
        if "fakehost" in str(self) or "elsewhere" in str(self):
            asked.append(str(self))
            return True
        return is_file(self, *a, **k)

    def trap(owner: object, name: str) -> None:
        orig = getattr(owner, name)

        def guarded(*args, **kwargs):
            if any("fakehost" in str(a) or "elsewhere" in str(a) for a in args):
                raise AssertionError(f"{name}{args} would reach the network")
            return orig(*args, **kwargs)

        monkeypatch.setattr(owner, name, guarded)

    monkeypatch.setattr(pathlib.Path, "is_file", fake_is_file)
    for name in ("stat", "exists", "resolve", "open"):
        trap(pathlib.Path, name)
    for name in ("stat", "open"):
        trap(os, name)
    for name in ("isfile", "exists", "realpath"):
        trap(os.path, name)
    return asked


def ask(items, url_path: str) -> tuple[pathlib.Path | None, dict]:
    caps = Capabilities.of({"read:link:l1": None})
    entry: dict = {}
    got = locate_item(
        "items", url_path, "v1.page", caps_of=lambda _p: caps, items=items, entry=entry
    )
    return got, entry


def links_with(value) -> LinkItems:
    value.link = "l1"
    return LinkItems(SimpleNamespace(inputs={("v", "data"): value}, root=SHARE))


def test_a_content_item_on_a_share_project_is_served(served):
    value = ContentValue(
        root=SHARE, entries=[ContentEntry(key="geo/a", path="geo/a.geojson", format="geojson")]
    )
    got, entry = ask(links_with(value), "/l1/geo%2Fa/geojson")
    assert entry.get("status") != "refused" and got is not None
    assert served == [SHARE + "\\geo\\a.geojson"]


def test_a_collection_without_a_root_is_judged_against_the_project(served):
    part = SHARE + "\\scans\\a.png"
    value = Collection(items=[Item(key="a", parts={".png": part})])
    got, entry = ask(links_with(value), "/l1/a/png")
    assert entry.get("status") != "refused" and served == [part]
    # a part on another share is not the project's
    value = Collection(items=[Item(key="a", parts={".png": "\\\\elsewhere\\share\\a.png"})])
    got, entry = ask(links_with(value), "/l1/a/png")
    assert got is None and entry["status"] == "refused" and served == [part]


def test_an_intaker_table_and_the_scratch_store_on_a_share_are_served(served):
    part = SHARE + "\\shots\\a.png"
    table = ItemTable(root=SHARE)
    table.set_items("l1", [{"key": "a", "parts": {".png": part}}])
    got, entry = ask(CompositeItems([table]), "/l1/a/png")
    assert entry.get("status") != "refused" and served == [part]
    # a table that names no root trusts no share
    bare = ItemTable()
    bare.set_items("l1", [{"key": "a", "parts": {".png": part}}])
    got, entry = ask(bare, "/l1/a/png")
    assert got is None and entry["status"] == "refused"

    store = SimpleNamespace(
        root=pathlib.PurePath(SHARE),
        page_ids=lambda: ["n1"],
        md_path=lambda page: pathlib.Path(SHARE) / ".specto" / "scratch" / f"{page}.md",
    )
    got, entry = ask(ScratchItems("l1", store), "/l1/n1/md")
    assert entry.get("status") != "refused" and served[-1].endswith("n1.md")


def test_a_part_with_a_bare_leading_slash_is_refused(served, monkeypatch):
    """On Windows ``/x`` is the current drive's root, never a recorded part's path: it is
    refused by its text, not stat'ed."""
    table = ItemTable(root="C:/proj")
    table.set_items("l1", [{"key": "a", "parts": {".png": "/x.png"}}])
    called: list[str] = []
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self, *a, **k: called.append(str(self)))
    got, entry = ask(table, "/l1/a/png")
    assert got is None and entry["status"] == "refused" and called == []
