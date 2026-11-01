"""Seam 2 (#168): a per-pick Python link drives a second file tree, and the map follows
that tree. Tree A shows the recordings (``recordings/<session>/<clip>``);
Python link 1 maps the picked recording to its annotation folder
(``annotations/.../geo``) on tree B's ``selection`` input; tree B
selects and reveals it and its files go out on ``selected_files``, where a read
contents link puts the GeoJSONs on the map. Real hosts, headless (no QML); the link
code is approved through the trust store, by hash."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from web_helpers import registry

pytest.importorskip("PySide6.QtCore")

import specto_intake  # noqa: F401  (registers the intaker and read contents resolvers)
from specto_app.views.file_tree import FileTreeHost
from specto_app.views.map_view import MapViewHost, set_tiles_online
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import Selection
from specto_core.python_links import TrustStore, sha256_of

LINK_FILE = ".specto/links/clip_to_geo.py"
WORLDS = "recordings/s01"
PROPS = "annotations/s01"


def mirror_code(nonce: str) -> str:
    """World folder or file -> its geo folder; every run leaves its paths in a
    trace file. The nonce keeps each test's code (and so its approval) its own: the
    trust store is shared by every test in the session."""
    return f"""\
# {nonce}
import json, pathlib

def resolve(value, items, params, root):
    trace = pathlib.Path(root) / ".specto" / "links" / "clip_to_geo.ran"
    runs = json.loads(trace.read_text()) if trace.exists() else []
    runs.append(value["paths"])
    trace.write_text(json.dumps(runs))
    out = []
    for p in value["paths"] or []:
        parts = p.split("/")
        if parts[:1] == ["recordings"] and len(parts) >= 4:
            out.append("/".join(["annotations", *parts[1:3], "geo"]))
    return sorted(set(out))
"""


def make_mirror(root: Path, nonce: str) -> None:
    for i, clip in enumerate(("clip1", "clip2")):
        world = root / WORLDS / clip / "w.usda"
        world.parent.mkdir(parents=True)
        world.write_text("#usda 1.0\n", encoding="utf-8")
        geo = root / PROPS / clip / "geo"
        geo.mkdir(parents=True)
        point = {"type": "Point", "coordinates": [-122.0 - i, 47.0 + i]}
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": point}]}
        (geo / "a.geojson").write_text(json.dumps(fc), encoding="utf-8")
    (root / LINK_FILE).write_text(mirror_code(nonce), encoding="utf-8")


def mirror_doc(engine: Engine) -> dict:
    doc = engine.live.document()
    doc["views"]["tora"] = {
        "type": "file_tree",
        "label": "annotations",
        "layout": {"position": [400, 0]},
    }
    doc["views"]["mapa"] = {"type": "map", "label": "geo", "layout": {"position": [800, 0]}}
    doc["links"]["l5"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "tora", "port": "data"},
        "params": {"sources": ["annotations"]},
        "summary": {"sentence": "Tree B shows the annotations folder."},
    }
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "tora", "port": "selection"},
        "python": {"path": LINK_FILE, "resolves_to": "value", "output": "selection"},
        "summary": {"sentence": "Tree B selects the picked recording's annotation folder."},
    }
    doc["links"]["l10"] = {
        "kind": "read_contents",
        "source": {"view": "tora", "port": "selected_files"},
        "target": {"view": "mapa", "port": "features"},
        "params": {"sources": ["."]},
        "summary": {"sentence": "The map shows the GeoJSONs tree B picks."},
    }
    return doc


@pytest.fixture(autouse=True)
def offline():
    set_tiles_online(False)
    yield
    set_tiles_online(None)


@pytest.fixture
def nonce() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def setup(tmp_path: Path, nonce: str):
    proj = Project.init(tmp_path / "proj", "mirror")
    make_mirror(proj.root, nonce)
    engine = Engine(project=proj, jobs=InlineJobManager())
    clock = [100.0]
    engine.links.clock = lambda: clock[0]
    reg = registry(engine)
    tree_a = reg.host("fege")
    engine.live.submit(mirror_doc(engine), origin="agent:s1", label="mirror")
    tree_b, mapa = reg.host("tora"), reg.host("mapa")
    assert isinstance(tree_a, FileTreeHost) and isinstance(tree_b, FileTreeHost)
    assert isinstance(mapa, MapViewHost)
    return engine, proj, clock, tree_a, tree_b, mapa


def pick(engine, clock, tree: FileTreeHost, path: str) -> None:
    """A user click on the row (a second click well after the first: no debounce)."""
    clock[0] += 1.0
    if tree.model.row_of(path) < 0:
        tree.expand_path(path.rpartition("/")[0])  # the folders above, listed on open (#231)
    row = tree.model.row_of(path)
    assert row >= 0, f"{path} is not shown"
    tree.click(row)
    engine.links.poll()


def runs(proj: Project) -> list:
    trace = proj.root / ".specto" / "links" / "clip_to_geo.ran"
    return json.loads(trace.read_text()) if trace.exists() else []


def layers(mapa: MapViewHost) -> list[str]:
    return [lay["id"] for lay in mapa.layers()]


def test_unapproved_link_code_never_runs_and_tree_b_stays_unpicked(setup):
    engine, proj, clock, tree_a, tree_b, mapa = setup
    pick(engine, clock, tree_a, f"{WORLDS}/clip1")
    pick(engine, clock, tree_a, f"{WORLDS}/clip2")

    assert runs(proj) == []
    assert engine.links.status("l9")["state"] == "needs_approval"
    assert tree_b.model.selected == set()
    assert layers(mapa) == []


def test_picking_a_recording_selects_its_annotation_folder_and_the_map_follows(setup):
    engine, proj, clock, tree_a, tree_b, mapa = setup
    TrustStore().approve(sha256_of((proj.root / LINK_FILE).read_bytes()), path=LINK_FILE)

    pick(engine, clock, tree_a, f"{WORLDS}/clip1")
    geo1 = f"{PROPS}/clip1/geo"
    assert runs(proj) == [[f"{WORLDS}/clip1/w.usda"]]
    assert engine.links.input_of("tora", "selection") == Selection(keys=[geo1], last=geo1)
    # tree B shows the pick: selected, its ancestors open, the folder's row on screen
    assert tree_b.model.selected == {geo1}
    assert {f"{PROPS}/clip1", PROPS}.issubset(tree_b.model.expanded)
    assert tree_b.model.row_of(geo1) >= 0
    assert tree_b.snapshot()["selected"] == [f"{geo1}/a.geojson"]
    # persisted like a pick (navigation, no undo step)
    assert engine.live.data["views"]["tora"]["params"]["selected"] == [geo1]
    assert layers(mapa) == [f"{PROPS}/clip1/geo/a"]

    # changing the pick follows live
    pick(engine, clock, tree_a, f"{WORLDS}/clip2")
    geo2 = f"{PROPS}/clip2/geo"
    assert tree_b.model.selected == {geo2}
    assert engine.live.data["views"]["tora"]["params"]["selected"] == [geo2]
    assert layers(mapa) == [f"{PROPS}/clip2/geo/a"]
    assert len(runs(proj)) == 2

    # the user's own pick in tree B still works, and the received pick does not come
    # back to override it until tree A changes
    own = f"{PROPS}/clip1/geo"
    clock[0] += 1.0
    tree_b.click(tree_b.model.row_of(own))
    assert tree_b.model.selected == {own}
    assert layers(mapa) == [f"{PROPS}/clip1/geo/a"]
    assert len(runs(proj)) == 2


def test_a_received_selection_before_the_tree_has_data_lands_when_it_does(tmp_path: Path):
    proj = Project.init(tmp_path / "proj", "mirror")
    make_mirror(proj.root, uuid.uuid4().hex)
    engine = Engine(project=proj, jobs=InlineJobManager())
    engine.live.submit(mirror_doc(engine), origin="agent:s1", label="mirror")
    reg = registry(engine)
    tree_b = reg.host("tora")
    data = engine.links.input_of("tora", "data")
    geo2 = f"{PROPS}/clip2/geo"

    tree_b.receive("data", None)
    tree_b.receive("selection", Selection(keys=[geo2], last=geo2))
    assert tree_b.model.selected == set()
    tree_b.receive("data", data)
    assert tree_b.model.selected == {geo2}
    assert f"{PROPS}/clip2" in tree_b.model.expanded


def test_a_tree_cannot_drive_its_own_selection(setup):
    engine, *_ = setup
    doc = engine.live.document()
    doc["links"]["l9"]["target"] = {"view": "fege", "port": "selection"}
    with pytest.raises(Exception, match="cycle"):
        engine.live.submit(doc, origin="agent:s1", label="loop")
