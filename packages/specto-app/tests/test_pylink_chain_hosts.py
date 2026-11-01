"""Seam 2 (#176): the seam-3 flow's two-hop chain of per-pick Python links, headless on
real hosts, from the flow's own seed and canvas (tools/gui_flows/v2-176-*). Hop one: a
recording picked in tree A selects its annotation folder in tree B. Hop two, off
tree B's picks: the map shows the folder's GeoJSONs (its readme stays out) and a
gallery shows the clip's frames, a collection the script built."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from gallery_helpers import CAM_A, FakeGallery, needs_testdata
from web_helpers import registry

import specto_intake  # noqa: F401  (registers the intaker and read contents resolvers)
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_app.views.map_view import set_tiles_online
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager

FLOWS = Path(__file__).resolve().parents[3] / "tools" / "gui_flows"
WORLDS = "recordings/s01"
PROPS = "annotations/s01"
pytestmark = needs_testdata


@pytest.fixture
def chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    set_tiles_online(False)
    root = tmp_path / "proj"
    seeded = subprocess.run(
        [sys.executable, str(FLOWS / "v2-176-seed.py"), str(root), "--approve"],
        capture_output=True,
        text=True,
        env={**os.environ, "SPECTO_TESTDATA": str(CAM_A.parents[2])},
    )
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    proj = Project.init(root, "v2-176-proj")
    engine = Engine(project=proj, jobs=InlineJobManager())
    clock = [100.0]
    engine.links.clock = lambda: clock[0]
    reg = registry(engine)
    fakes: dict[str, FakeGallery] = {}

    class Galleries(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes[view_id] = FakeGallery()
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[view_id])

    reg.add_factory(Galleries())
    doc = json.loads((FLOWS / "v2-176-canvas.json").read_text(encoding="utf-8"))
    engine.live.submit(doc, origin="agent:drive", label="the chain")
    hosts = {vid: reg.host(vid) for vid in doc["views"]}
    yield engine, clock, hosts, fakes
    reg.dispose_all()
    engine.links.close()
    set_tiles_online(None)


def pick_world(engine, clock, tree, clip: str) -> None:
    clock[0] += 1.0
    path = f"{WORLDS}/{clip}"
    for i in range(1, len(path.split("/"))):
        folder = "/".join(path.split("/")[:i])
        if folder not in tree.model.expanded:
            tree.toggle(tree.model.row_of(folder))
    assert tree.click(tree.model.row_of(path))
    engine.links.poll()


def test_a_recording_picked_in_tree_a_drives_tree_b_the_map_and_the_frames(chain):
    engine, clock, hosts, fakes = chain
    tree_a, tree_b, mapa, gala = (hosts[v] for v in ("fege", "tora", "mapa", "gala"))

    pick_world(engine, clock, tree_a, "clip2")
    assert {
        lid: engine.links.status(lid)["state"] for lid in ("l9", "l10", "l11")
    } == dict.fromkeys(("l9", "l10", "l11"), "resolved")
    assert tree_b.model.selected == {f"{PROPS}/clip2/geo"}
    assert [lay["id"] for lay in mapa.layers()] == [
        f"{PROPS}/clip2/geo/track",
        f"{PROPS}/clip2/geo/zone",
    ]
    assert gala.keys == ["clip2/frame1", "clip2/frame2", "clip2/frame3"]
    assert fakes["gala"].count() == 3

    # the pick follows live through both hops
    pick_world(engine, clock, tree_a, "clip3")
    assert tree_b.model.selected == {f"{PROPS}/clip3/geo"}
    assert [lay["id"] for lay in mapa.layers()] == [f"{PROPS}/clip3/geo/track"]
    assert gala.keys == ["clip3/frame1"]

    # the user's own pick in tree B drives hop two alone
    clock[0] += 1.0
    assert tree_b.click(tree_b.model.row_of(f"{PROPS}/clip1"))  # the whole clip folder
    assert [lay["id"] for lay in mapa.layers()] == [f"{PROPS}/clip1/geo/track"]
    assert gala.keys == ["clip1/frame1", "clip1/frame2"]
