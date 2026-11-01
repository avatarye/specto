"""Seam 1 (#41): follow links on the link runtime. A selection emitted on one gallery
resolves, through a follow link matching on one key field (``frame``), to the item of the
other gallery (and of the JSON inspector) whose key differs because its run is keyed
differently; two-way follow works from either end without echoing back; a selection
that arrives before the other collection resolves once that collection lands. Worked
values: the first run-001 frames (cam-a 6, cam-b 4, jpg + json sidecar)."""

from __future__ import annotations

from pathlib import Path

import pytest
from intake_helpers import copy_frames, needs_testdata, tree_files

import specto_intake  # noqa: F401  (registers the intaker resolver)
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import Collection, FilesValue, Selection

pytestmark = needs_testdata

CAM_A_KEYS = r"(?P<cam>cam-a)/(?P<frame>\d{5})_\d+\.jpg$"
CAM_B_KEYS = r"(?P<cam>cam-b)/(?P<frame>\d{5})_\d+\.jpg$"
JSON_KEYS = r"(?P<frame>\d{5})_\d+\.json$"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "frames")
    copy_frames(proj.root / "runs" / "run-001" / "cam-a", 6, "cam-a")
    copy_frames(proj.root / "runs" / "run-001" / "cam-b", 4, "cam-b")
    return proj


def _gallery(label: str, x: int) -> dict:
    return {"type": "gallery", "label": label, "layout": {"position": [x, 0], "size": [640, 400]}}


def _intaker(target: str, sources: list[str], screener: str, keys: str, group_by: list[str]):
    return {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": target, "port": "data"},
        "params": {"sources": sources, "screener": [screener], "keys": keys, "group_by": group_by},
        "summary": {"sentence": f"The {target} collection: {sources[0]} ({screener})."},
    }


def _follow(lid: str, src: str, tgt: str, *, match: str | None, two_way: bool = False) -> dict:
    return {
        "kind": "follow",
        "two_way": two_way,
        "source": {"view": src, "port": "selection"},
        "target": {"view": tgt, "port": "selection"},
        "params": {"match": match},
        "summary": {"sentence": f"Selecting in {src} selects the same frame in {tgt}."},
    }


def linked_galleries(doc: dict, *, match: str | None = "frame") -> dict:
    """Cam-a and cam-b galleries keyed by series and frame (so their keys differ), a JSON
    inspector keyed by frame alone, cam-a <-> cam-b two-way on frame, cam-a -> json."""
    doc["views"]["kobi"] = _gallery("Front", 300)
    doc["views"]["tamu"] = _gallery("Rear", 960)
    doc["views"]["mihu"] = {
        "type": "json",
        "label": "Frame metadata",
        "layout": {"position": [1620, 0], "size": [400, 800]},
    }
    doc["links"]["l2"] = _intaker("kobi", ["runs/run-001"], "@images", CAM_A_KEYS, ["cam", "frame"])
    doc["links"]["l3"] = _intaker("tamu", ["runs/run-001"], "@images", CAM_B_KEYS, ["cam", "frame"])
    doc["links"]["l6"] = _intaker("mihu", ["runs/run-001/cam-a"], "@json", JSON_KEYS, ["frame"])
    doc["links"]["l7"] = _follow("l7", "kobi", "tamu", match=match, two_way=True)
    doc["links"]["l8"] = _follow("l8", "kobi", "mihu", match=match)
    return doc


def all_files(engine: Engine, project: Project) -> FilesValue:
    """What the file tree emits for a pick of its root (#231)."""
    return tree_files(project.root)


def keys_of(col: Collection) -> list[str]:
    return [it.key for it in col.items]


def pick(key: str) -> Selection:
    return Selection(keys=[key], last=key)


def test_follow_on_frame_matches_items_whose_keys_differ_by_run(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    res = engine.live.submit(linked_galleries(engine.live.document()), origin="agent:s1", label="f")
    assert res.links["added"] == ["l2", "l3", "l6", "l7", "l8"]
    links.emit("project", "all_files", all_files(engine, project))
    assert keys_of(links.input_of("kobi", "data"))[:2] == ["cam-a/00000", "cam-a/00001"]
    assert keys_of(links.input_of("tamu", "data")) == [f"cam-b/0000{i}" for i in range(4)]
    assert keys_of(links.input_of("mihu", "data"))[:2] == ["00000", "00001"]
    # nothing selected yet: the follow links wait for a selection, nothing fails
    assert links.status("l7")["state"] == "waiting" and links.status("l8")["state"] == "waiting"

    links.emit("kobi", "selection", pick("cam-a/00002"))

    assert links.input_of("tamu", "selection") == pick("cam-b/00002")
    assert links.input_of("mihu", "selection") == pick("00002")
    assert links.status("l7")["state"] == "resolved"

    # a frame the cam-b run never recorded: an empty selection, not a stale one
    links.emit("kobi", "selection", pick("cam-a/00005"))
    assert links.input_of("tamu", "selection") == Selection(keys=[], last=None)
    assert links.input_of("mihu", "selection") == pick("00005")

    # several keys map item by item; ``last`` follows the last picked
    links.emit(
        "kobi", "selection", Selection(keys=["cam-a/00001", "cam-a/00003"], last="cam-a/00001")
    )
    assert links.input_of("tamu", "selection") == Selection(
        keys=["cam-b/00001", "cam-b/00003"], last="cam-b/00001"
    )


def test_two_way_follow_works_from_either_end_without_echo(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    engine.live.submit(linked_galleries(engine.live.document()), origin="agent:s1", label="f")
    links.emit("project", "all_files", all_files(engine, project))
    got: dict[str, list] = {"kobi": [], "tamu": [], "mihu": []}
    for vid in got:
        links.attach(vid, lambda port, value, vid=vid: got[vid].append((port, value)))
    for g in got.values():
        g.clear()  # the collections already delivered on attach are not the point here

    links.emit("tamu", "selection", pick("cam-b/00001"))
    assert links.input_of("kobi", "selection") == pick("cam-a/00001")
    assert got["kobi"] == [("selection", pick("cam-a/00001"))]
    assert got["tamu"] == []  # the cam-b gallery is not told what it just said
    assert got["mihu"] == []  # one-way: mihu follows the cam-a gallery's own choice only

    # the cam-a gallery re-emits what it received (a host that republishes): the
    # cam-b gallery already shows it, so nothing goes round again
    links.emit("kobi", "selection", pick("cam-a/00001"))
    assert got["tamu"] == []
    assert links.input_of("mihu", "selection") == pick("00001")

    links.emit("kobi", "selection", pick("cam-a/00003"))
    assert got["tamu"] == [("selection", pick("cam-b/00003"))]
    assert got["kobi"] == [("selection", pick("cam-a/00001"))]  # nothing came back


def test_a_selection_made_before_the_other_collection_resolves_when_it_lands(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    engine.live.submit(linked_galleries(engine.live.document()), origin="agent:s1", label="f")
    files = all_files(engine, project)
    # only the cam-a images so far: the cam-b collection is empty
    links.emit(
        "project",
        "all_files",
        FilesValue(root=files.root, paths=[p for p in files.paths if "cam-a" in p]),
    )
    assert links.input_of("tamu", "data").items == []
    links.emit("kobi", "selection", pick("cam-a/00002"))
    assert links.input_of("tamu", "selection") == Selection(keys=[], last=None)

    links.emit("project", "all_files", files)  # the cam-b frames arrive

    assert links.input_of("tamu", "selection") == pick("cam-b/00002")
    assert links.input_of("mihu", "selection") == pick("00002")


def test_follow_without_a_match_field_passes_the_keys_through(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    engine.live.submit(
        linked_galleries(engine.live.document(), match=None), origin="agent:s1", label="f"
    )
    links.emit("project", "all_files", all_files(engine, project))
    links.emit("kobi", "selection", pick("cam-a/00002"))
    assert links.input_of("tamu", "selection") == pick("cam-a/00002")
