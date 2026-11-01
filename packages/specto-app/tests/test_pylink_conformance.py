"""Seam 2 (#176): the per-pick Python link (``resolves_to: value``, #168) conforms on
real view hosts, every source port x every output port that has a target view.

One canvas per source: the source view, every target view, and one Python link from the
source port into each target port. A real gesture on the source host (a click in the
file tree or the gallery, a camera from the 3d page, a video step, a map pick, a usd
viewport pick, the json view showing another item) runs every link once; the script
records the ``value`` and ``items`` it got (the shapes the contract names) and returns
the output its ``params.out`` holds, which the runtime converts to the output port type
and the target host shows: a file tree, gallery and usd view select it, a map and a json
view show the content, a video and a usd stage go to the time, a 3d and a usd view take
the camera, and a gallery, a file tree and a markdown view show a collection the script
built. No view has a ``files`` input, so ``files`` has no target here (seam 1 covers its
conversion). Headless: the usd views run the stub source, the gallery a fake item."""

from __future__ import annotations

import contextlib
import json
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from gallery_helpers import TESTDATA, FakeGallery
from scene3d_helpers import seed_scans
from web_helpers import FRAME_KEYS, Sink, make_geo, make_notes, make_run, registry

import specto_intake  # noqa: F401  (registers the intaker and read contents resolvers)
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_app.views.map_view import set_tiles_online
from specto_app.web.runtime import reset_runtime
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.python_links import TrustStore, sha256_of

LINK_FILE = ".specto/links/conform.py"
VIDEO = TESTDATA / "runs" / "run-001" / "video"
HAS_VIDEO = (VIDEO / "cam-a.mp4").exists()
FRAME_4 = 1_700_000_000_125_000_000 / 1e9  # cam-a.mp4's sidecar stamp of frame 4 (seconds)
BASE_2 = "/World/Blocks/Block_02/Base"
TOP_3 = "/World/Blocks/Block_03/Top"
#: a stage with time samples (the stub source plays its range)
ANIMATED = """#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Z"
    startTimeCode = 0
    endTimeCode = 48
    timeCodesPerSecond = 24
)
def Xform "World"
{
    def Cube "Box"
    {
        double3 xformOp:translate.timeSamples = { 0: (-6, 0, 1), 48: (6, 0, 1) }
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""
CAMERA = {
    "position": [3.0, 4.0, 12.5],
    "target": [3.0, 4.0, 2.5],
    "up": [0.0, 1.0, 0.0],
    "fov_y": 0.8,
    "near": 0.1,
    "far": 1000.0,
}
CAMERA_OUT = {"position": [10.0, 2.0, 10.0], "target": [0.0, 0.0, 0.0], "fov_y": 0.6}


def code(nonce: str) -> str:
    """Records what each run got (per link, ``params.name``) and returns ``params.out``.
    The nonce keeps the approval this module's own (the trust store may be shared)."""
    return f"""\
# {nonce}
import json, pathlib

def resolve(value, items, params, root):
    trace = pathlib.Path(root) / ".specto" / "links" / "trace" / (params["name"] + ".json")
    trace.parent.mkdir(exist_ok=True)
    runs = json.loads(trace.read_text()) if trace.exists() else []
    runs.append({{"value": value, "items": items[:2], "n_items": len(items)}})
    trace.write_text(json.dumps(runs))
    return params["out"]
"""


# -- the project ---------------------------------------------------------------------------
def make_project(root: Path) -> Project:
    proj = Project.init(root, "conform")
    make_run(proj.root / "runs" / "cam-a", 5)
    make_notes(proj.root / "notes")
    make_geo(proj.root / "geo")
    seed_scans(proj.root)
    (proj.root / "usd").mkdir()
    (proj.root / "usd" / "drive.usda").write_text(ANIMATED, encoding="utf-8")
    if HAS_VIDEO:
        (proj.root / "video").mkdir()
        shutil.copy2(VIDEO / "cam-a.mp4", proj.root / "video" / "cam-a.mp4")
        shutil.copy2(VIDEO / "cam-a.timeline.json", proj.root / "video" / "cam-a.timeline.json")
    (proj.root / LINK_FILE).write_text(code(uuid.uuid4().hex), encoding="utf-8")
    return proj


def sidecar(proj: Project, i: int) -> str:
    return sorted(p.name for p in (proj.root / "runs" / "cam-a").glob(f"{i:05d}_*.json"))[0]


def pinned(target: str, sources: list[str], screener: list[str] | None = None, **params) -> dict:
    p: dict[str, Any] = {"sources": sources, **params}
    if screener:
        p["screener"] = screener
    return {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": target, "port": "data"},
        "params": p,
        "summary": {"sentence": f"{target} shows {sources[0]}."},
    }


def view(vtype: str, label: str, x: int) -> dict:
    return {"type": vtype, "label": label, "layout": {"position": [x, 900], "size": [300, 200]}}


# -- targets: (view, port, output type, out, check) -----------------------------------------
class Target:
    def __init__(
        self,
        name: str,
        view_id: str,
        port: str,
        output: str,
        out: Callable[[Project], Any],
        check: Callable[[Any, Project, Any], bool],
    ) -> None:
        self.name, self.view, self.port, self.output = name, view_id, port, output
        self.out, self.check = out, check


def frames_collection(proj: Project) -> dict:
    """A collection built the way an intaker would: frames keyed by number, the jpg and
    its sidecar as the two parts (relative paths)."""
    items = []
    for i in range(5):
        jpg = sorted((proj.root / "runs" / "cam-a").glob(f"{i:05d}_*.jpg"))[0].name
        items.append(
            {
                "key": f"{i:05d}",
                "parts": {".jpg": f"runs/cam-a/{jpg}", ".json": f"runs/cam-a/{sidecar(proj, i)}"},
                "keys": {"frame": f"{i:05d}"},
            }
        )
    return {"items": items}


def _usd_selected(host, proj, _ctx) -> bool:
    return host.selected == [TOP_3]


def _usd_time(host, proj, _ctx) -> bool:
    return host.snapshot()["source"].get("timecode") == 36.0


def _usd_camera(host, proj, _ctx) -> bool:
    cam = host.ctx.links.input_of(host.view_id, "camera")
    sent = host._last_camera or {}
    return cam is not None and sent.get("fov_y") == pytest.approx(0.6)


def _scene_camera(host, proj, ctx) -> bool:
    host.flush()
    got = ctx["sinks"]["seno"].last_port("camera")
    return got is not None and got["position"] == CAMERA_OUT["position"]


TARGETS = [
    Target(
        "tree_selection",
        "tobi",
        "selection",
        "selection",
        lambda proj: ["geo/route.geojson"],
        lambda h, proj, ctx: h.model.selected == {"geo/route.geojson"},
    ),
    Target(
        "gallery_selection",
        "gabi",
        "selection",
        "selection",
        lambda proj: "00003",
        lambda h, proj, ctx: ctx["fakes"]["gabi"].selected() == 3,
    ),
    Target(
        "usd_selection",
        "zudo",
        "selection",
        "selection",
        lambda proj: {"keys": [f"drive#{TOP_3}"]},
        _usd_selected,
    ),
    Target(
        "map_features",
        "mapo",
        "features",
        "content",
        lambda proj: {"paths": ["geo"]},
        lambda h, proj, ctx: [lay["id"] for lay in h.layers()] == ["geo/points", "geo/route"],
    ),
    Target(
        "json_content",
        "heso",
        "content",
        "content",
        lambda proj: [f"runs/cam-a/{sidecar(proj, 1)}"],
        lambda h, proj, ctx: (h.current_data or {}).get("frame") == 1,
    ),
    Target(
        "usd_time",
        "zudo",
        "time",
        "time",
        lambda proj: 1.5,
        _usd_time,
    ),
    Target(
        "scene_camera",
        "seno",
        "camera",
        "camera",
        lambda proj: CAMERA_OUT,
        _scene_camera,
    ),
    Target(
        "usd_camera",
        "zudo",
        "camera",
        "camera",
        lambda proj: CAMERA_OUT,
        _usd_camera,
    ),
    Target(
        "gallery_data",
        "gako",
        "data",
        "collection",
        frames_collection,
        lambda h, proj, ctx: ctx["fakes"]["gako"].count() == 5 and h.keys[0] == "00000",
    ),
    Target(
        "tree_data",
        "tere",
        "data",
        "collection",
        frames_collection,
        lambda h, proj, ctx: (
            len(h.model.all_files()) == 10
            and h.model.all_files()[0].startswith("runs/cam-a/00000_")
        ),
    ),
    Target(
        "markdown_data",
        "mado",
        "data",
        "collection",
        lambda proj: {"items": [{"key": "alpha", "parts": {".md": "notes/alpha.md"}}]},
        lambda h, proj, ctx: (
            h.current_key == "alpha"
            and Path(h.current_path or "").resolve() == (proj.root / "notes" / "alpha.md").resolve()
        ),
    ),
]
if HAS_VIDEO:
    TARGETS.append(
        Target(
            "video_time",
            "vido",
            "time",
            "time",
            lambda proj: FRAME_4,
            lambda h, proj, ctx: h.frame == 4,
        )
    )


def target_views(doc: dict) -> None:
    v, ln = doc["views"], doc["links"]
    v["tobi"] = view("file_tree", "Tree B", 0)
    ln["p_torb"] = pinned("tobi", ["geo"])
    v["gabi"] = view("gallery", "Gallery B", 320)
    ln["p_galb"] = pinned("gabi", ["runs/cam-a"], ["@images"], keys=FRAME_KEYS)
    v["zudo"] = view("usd", "Stage B", 640)
    ln["p_usdb"] = pinned("zudo", ["usd"])
    v["mapo"] = view("map", "Map B", 960)
    v["heso"] = view("json", "JSON B", 1280)
    v["seno"] = view("3d", "Scene B", 1600)
    ln["p_scnb"] = pinned("seno", ["scans"], ["@3d"])
    v["gako"] = view("gallery", "Gallery C", 1920)
    v["tere"] = view("file_tree", "Tree D", 2240)
    v["mado"] = view("markdown", "Notes B", 2560)
    if HAS_VIDEO:
        v["vido"] = view("video", "Video B", 2880)
        ln["p_vidb"] = pinned("vido", ["video"], ["*.mp4"])


# -- sources: (view id, port, set up, gesture, expected value check) ------------------------
class Source:
    def __init__(
        self,
        name: str,
        view_id: str,
        port: str,
        add: Callable[[dict], None],
        gesture: Callable[[Any, Any, Any], None],
        value: Callable[[Any, Project], bool],
        n_items: int | None = None,
    ) -> None:
        self.name, self.view, self.port = name, view_id, port
        self.add, self.gesture, self.value, self.n_items = add, gesture, value, n_items


def _add_tree(doc: dict) -> None:
    pass  # the canvas's own file tree (fege), fed by the project intaker


def _tree_click(host, ctx, spin) -> None:
    if "geo" not in host.model.expanded:
        host.toggle(host.model.row_of("geo"))
    row = host.model.row_of("geo/points.geojson")
    assert row >= 0, [r["path"] for r in host.rows()][:20]
    assert host.click(row)
    assert host.ctx.links.output_of("fege", "selected_files").paths == ["geo/points.geojson"]


def _add_gallery(doc: dict) -> None:
    doc["views"]["kobi"] = view("gallery", "Gallery A", 0) | {"layout": {"position": [0, 0]}}
    doc["links"]["p_kobi"] = pinned("kobi", ["runs/cam-a"], ["@images"], keys=FRAME_KEYS)


def _add_scene(doc: dict) -> None:
    doc["views"]["sika"] = view("3d", "Scene A", 0) | {"layout": {"position": [0, 0]}}
    doc["links"]["p_scna"] = pinned("sika", ["scans"], ["@3d"])


def _scene_camera_out(host, ctx, spin) -> None:
    host.bridge.ready()
    reply = json.loads(host.bridge.emit("camera", json.dumps(CAMERA)))
    assert reply["ok"], reply


def _add_video(doc: dict) -> None:
    doc["views"]["vida"] = view("video", "Video A", 0) | {"layout": {"position": [0, 0]}}
    doc["links"]["p_vida"] = pinned("vida", ["video"], ["*.mp4"])


def _add_map(doc: dict) -> None:
    doc["views"]["mapa"] = view("map", "Map A", 0) | {"layout": {"position": [0, 0]}}
    doc["links"]["p_mapa"] = {
        "kind": "read_contents",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "mapa", "port": "features"},
        "params": {"sources": ["geo"]},
        "summary": {"sentence": "Map A shows the GeoJSONs under geo."},
    }


def _map_pick(host, ctx, spin) -> None:
    pick = {"keys": ["geo/points#1"], "last": "geo/points#1"}
    reply = json.loads(host.bridge.emit("selection", json.dumps(pick)))
    assert reply["ok"], reply


def _add_usd(doc: dict) -> None:
    doc["views"]["husa"] = view("usd", "Stage A", 0) | {"layout": {"position": [0, 0]}}
    doc["links"]["p_usda"] = pinned("husa", ["usd"])


def _usd_pick(host, ctx, spin) -> None:
    host.bridge.pick(0.0, -0.6, False)
    spin(until=lambda: host.ctx.links.output_of("husa", "selection") is not None, timeout=10)


def _add_json(doc: dict) -> None:
    doc["views"]["duka"] = view("json", "JSON A", 0) | {"layout": {"position": [0, 0]}}
    doc["links"]["p_jsoa"] = pinned("duka", ["runs/cam-a"], ["@json"], keys=FRAME_KEYS)


def _json_show(host, ctx, spin) -> None:
    host.show_key("00002")


def _is_content_of_frame_2(v: Any, proj: Project) -> bool:
    (entry,) = v["entries"]
    assert Path(v["root"]).resolve() == proj.root.resolve()
    # a content entry is keyed by its path without extension, as read_contents keys it
    side = f"runs/cam-a/{sidecar(proj, 2)}"
    assert (entry["key"], entry["path"]) == (side.removesuffix(".json"), side)
    assert (entry["format"], entry["data"]["frame"]) == ("json", 2)
    return True


SOURCES = [
    Source(
        "file_tree.selected_files",
        "fege",
        "selected_files",
        _add_tree,
        _tree_click,
        lambda v, proj: v == {"root": str(proj.root), "paths": ["geo/points.geojson"]},
        n_items=0,  # a file tree's data is the whole project: never handed to the script
    ),
    Source(
        "gallery.selection",
        "kobi",
        "selection",
        _add_gallery,
        lambda host, ctx, spin: ctx["fakes"]["kobi"].user_click(2),
        lambda v, proj: v == {"keys": ["00002"], "last": "00002"},
        n_items=5,
    ),
    Source(
        "3d.camera",
        "sika",
        "camera",
        _add_scene,
        _scene_camera_out,
        lambda v, proj: v == CAMERA,
        n_items=5,
    ),
    Source(
        "map.selection",
        "mapa",
        "selection",
        _add_map,
        _map_pick,
        lambda v, proj: v == {"keys": ["geo/points#1"], "last": "geo/points#1"},
        n_items=0,
    ),
    Source(
        "usd.selection",
        "husa",
        "selection",
        _add_usd,
        _usd_pick,
        lambda v, proj: v == {"keys": [f"drive#{BASE_2}"], "last": f"drive#{BASE_2}"},
        n_items=1,
    ),
    Source(
        "json.content",
        "duka",
        "content",
        _add_json,
        _json_show,
        _is_content_of_frame_2,
        n_items=5,
    ),
]
if HAS_VIDEO:
    SOURCES.append(
        Source(
            "video.time",
            "vida",
            "time",
            _add_video,
            lambda host, ctx, spin: host.step(4),
            lambda v, proj: v == pytest.approx(FRAME_4),
            n_items=1,
        )
    )


@pytest.fixture(autouse=True)
def offline():
    set_tiles_online(False)
    yield
    set_tiles_online(None)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    reset_runtime()
    proj = make_project(tmp_path / "proj")
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
    yield engine, proj, clock, reg, fakes
    reg.dispose_all()
    engine.links.close()
    reset_runtime()


def runs(proj: Project, name: str) -> list:
    trace = proj.root / ".specto" / "links" / "trace" / f"{name}.json"
    return json.loads(trace.read_text()) if trace.exists() else []


@pytest.mark.parametrize("source", SOURCES, ids=[s.name for s in SOURCES])
def test_every_output_port_takes_a_per_pick_result_from_this_source(world, source, spin):
    engine, proj, clock, reg, fakes = world
    doc = engine.live.document()
    source.add(doc)
    target_views(doc)
    for t in TARGETS:
        doc["links"][f"py_{t.name}"] = {
            "kind": "python",
            "source": {"view": source.view, "port": source.port},
            "target": {"view": t.view, "port": t.port},
            "python": {"path": LINK_FILE, "resolves_to": "value", "output": t.output},
            "params": {"name": t.name, "out": t.out(proj)},
            "summary": {"sentence": f"{t.view}.{t.port} follows {source.view}.{source.port}."},
        }
    renamed = engine.live.submit(doc, origin="agent:s1", label="conformance").renamed
    lid = {t.name: renamed.get(f"py_{t.name}", f"py_{t.name}") for t in TARGETS}
    hosts = {vid: reg.host(vid) for vid in engine.live.data["views"]}
    sinks = {"seno": Sink(hosts["seno"].bridge)}
    hosts["seno"].bridge.ready()
    ctx = {"fakes": fakes, "sinks": sinks}
    usd = [h for vid, h in hosts.items() if engine.live.data["views"][vid]["type"] == "usd"]
    spin(until=lambda: all(h.stage for h in usd), timeout=15)

    # the user's gesture on the source, then the approval: every link runs on that value
    clock[0] += 1.0
    source.gesture(hosts[source.view], ctx, spin)
    assert all(runs(proj, t.name) == [] for t in TARGETS)  # unapproved: nothing ran
    TrustStore().approve(sha256_of((proj.root / LINK_FILE).read_bytes()), path=LINK_FILE)
    for t in TARGETS:
        engine.links.resolve(lid[t.name])

    failed = {}
    for t in TARGETS:
        status = engine.links.status(lid[t.name])
        got = runs(proj, t.name)
        if status["state"] != "resolved" or not got:
            failed[t.name] = status
            continue
        assert source.value(got[-1]["value"], proj), (t.name, json.dumps(got[-1]["value"]))
        if source.n_items is not None:
            assert got[-1]["n_items"] == source.n_items, (t.name, got[-1]["n_items"])
    assert failed == {}, json.dumps(failed, indent=1)

    def shown() -> list[str]:
        return [t.name for t in TARGETS if not t.check(hosts[t.view], proj, ctx)]

    with contextlib.suppress(AssertionError):  # the next line names what is not shown
        spin(until=lambda: shown() == [], timeout=10)
    assert shown() == []
