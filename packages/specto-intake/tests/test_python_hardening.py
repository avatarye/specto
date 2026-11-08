"""Seam 1 (#176): per-pick Python links (``resolves_to: value``) hardened. The bugs the
conformance suite found, each pinned here, and the robustness cases: a failing script's
traceback reaches the link status, a slow one times out without holding the others up,
large values, odd returns, edits and deletions after approval, concurrent links,
Unicode and Windows paths, heavy imports and ``python -I`` isolation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import specto_intake  # noqa: F401  (registers the intaker and read contents resolvers)
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager, JobManager
from specto_core.links import FilesValue, Selection
from specto_core.python_links import SHAPES, TrustStore, as_port_value, sha256_of
from specto_core.timebase import OWN_TIMELINE, TimeValue


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    return make_project(tmp_path, monkeypatch)


def make_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    """A project with geo/a and b.geojson and a readme; approvals in its own user dir."""
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    proj = Project.init(tmp_path / "proj", "harden")
    for rel in ("geo/a.geojson", "geo/b.geojson"):
        p = proj.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        fc = {"type": "FeatureCollection", "features": []}
        p.write_text(json.dumps(fc), encoding="utf-8")
    (proj.root / "geo" / "readme.md").write_text("# geo\n", encoding="utf-8")
    return proj


def write_link(proj: Project, name: str, body: str) -> str:
    rel = f".specto/links/{name}.py"
    (proj.root / rel).write_text(body, encoding="utf-8")
    return rel


def approve(proj: Project, rel: str) -> None:
    TrustStore().approve(sha256_of((proj.root / rel).read_bytes()), path=rel)


def engine_on(proj: Project, jobs: JobManager | None = None, **kw: Any) -> tuple[Engine, Clock]:
    engine = Engine(project=proj, jobs=jobs or InlineJobManager())
    clock = Clock()
    engine.links.clock = clock
    for k, v in kw.items():
        setattr(engine.links, k, v)
    return engine, clock


#: the target view and port per output type
TARGETS = {
    "selection": ("tora", "file_tree", "selection"),
    "content": ("mapa", "map", "features"),
    "time": ("vida", "video", "time"),
    "camera": ("sika", "3d", "camera"),
    "collection": ("kobi", "gallery", "data"),
}


def submit_link(
    engine: Engine,
    rel: str,
    output: str = "selection",
    *,
    lid: str = "l9",
    params: dict | None = None,
    source: tuple[str, str] = ("fege", "selected_files"),
) -> None:
    doc = engine.live.document()
    vid, vtype, port = TARGETS[output]
    doc["views"].setdefault(vid, {"type": vtype, "label": vid, "layout": {"position": [600, 0]}})
    if vtype == "file_tree" and not any(
        ln["target"] == {"view": vid, "port": "data"} for ln in doc["links"].values()
    ):
        doc["links"]["l5"] = {
            "kind": "intaker",
            "scope": "pinned",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": vid, "port": "data"},
            "params": {"sources": ["."]},
            "summary": {"sentence": "The second tree shows the project."},
        }
    doc["links"][lid] = {
        "kind": "python",
        "source": {"view": source[0], "port": source[1]},
        "target": {"view": vid, "port": port},
        "python": {"path": rel, "resolves_to": "value", "output": output},
        "params": params or {},
        "summary": {"sentence": f"{vid}.{port} follows {source[0]}.{source[1]}."},
    }
    engine.live.submit(doc, origin="agent:s1", label="python link")


def pick(engine: Engine, proj: Project, *paths: str) -> None:
    engine.links.emit("fege", "selected_files", FilesValue(root=str(proj.root), paths=list(paths)))


#: records what it got next to itself and returns the picked paths as a selection
ECHO = """\
import json, pathlib

def resolve(value, items, params, root):
    (pathlib.Path(root) / ".specto" / "links" / "echo.got").write_text(
        json.dumps({"value": value, "items": items})
    )
    return value["paths"]
"""


def got(proj: Project, name: str = "echo") -> Any:
    return json.loads((proj.root / ".specto" / "links" / f"{name}.got").read_text())


def test_a_file_trees_picks_reach_the_script_without_the_whole_project_as_items(
    project: Project,
):
    # the tree's data is the whole project: handing that to the script on every pick
    # would cost a serialisation of the whole listing; the script has root instead
    # (and since #231 the tree's collection carries the root alone anyway)
    rel = write_link(project, "echo", ECHO)
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel)
    assert engine.links.collection_of("fege").items == []
    pick(engine, project, "geo/a.geojson")

    assert engine.links.status("l9")["state"] == "resolved"
    assert got(project) == {
        "value": {"root": str(project.root), "paths": ["geo/a.geojson"]},
        "items": [],
    }


def test_a_folder_returned_for_a_map_stands_for_its_geojson_files_only(project: Project):
    # the fix-it itself says "return the .geojson paths or their folders": a folder that
    # also holds a readme (a gis folder with its metadata) must still show
    rel = write_link(
        project, "geo", 'def resolve(value, items, params, root):\n    return {"paths": ["geo"]}\n'
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel, "content")
    pick(engine, project, "geo/a.geojson")

    assert engine.links.status("l9")["state"] == "resolved", engine.links.status("l9")
    features = engine.links.input_of("mapa", "features")
    assert [(e.path, e.format) for e in features.entries] == [
        ("geo/a.geojson", "geojson"),
        ("geo/b.geojson", "geojson"),
    ]


# -- an intaker-grade collection output (part 3) -----------------------------------------------
#: builds a gallery's whole collection from the picks: one item per frame number, the
#: picture and its sidecar as two parts, keyed and grouped the way an intaker would
FRAMES = """\
import pathlib, re

def resolve(value, items, params, root):
    out = {}
    for p in value["paths"] or []:
        m = re.search(r"(\\d{5})_\\d+\\.(jpg|json)$", p)
        if m:
            key = m.group(1)
            it = out.setdefault(key, {"key": key, "parts": [], "keys": {"frame": key}})
            it["parts"].append(p)
    return {"items": [out[k] for k in sorted(out)]}
"""


def frame_files(proj: Project, n: int = 3) -> list[str]:
    out = []
    for i in range(n):
        for ext in ("jpg", "json"):
            rel = f"runs/cam-a/{i:05d}_{1700000000000000000 + i * 60_000_000}.{ext}"
            p = proj.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(
                b"\xff\xd8\xff\xd9" if ext == "jpg" else json.dumps({"frame": i}).encode()
            )
            out.append(rel)
    return out


def test_a_collection_output_is_built_the_way_an_intaker_builds_one(project: Project):
    files = frame_files(project)
    rel = write_link(project, "frames", FRAMES)
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel, "collection")
    pick(engine, project, *files)

    assert engine.links.status("l9")["state"] == "resolved", engine.links.status("l9")
    col = engine.links.input_of("kobi", "data")
    assert col.root == str(project.root) and col.link == "l9"
    assert [it.key for it in col.items] == ["00000", "00001", "00002"]
    it = col.items[1]
    jpg, side = project.root / files[2], project.root / files[3]
    # parts are absolute, keyed by role (the extension), as the intaker's are
    assert it.parts == {".jpg": str(jpg), ".json": str(side)}
    assert it.index == 1
    assert it.meta["keys"] == {"frame": "00001"}
    assert it.meta["rel"] == {".jpg": files[2], ".json": files[3]}
    assert it.meta["stat"] == {
        ".jpg": [jpg.stat().st_size, jpg.stat().st_mtime_ns],
        ".json": [side.stat().st_size, side.stat().st_mtime_ns],
    }
    assert it.meta["root"] == str(project.root).replace("\\", "/")
    assert it.meta["size"] == jpg.stat().st_size + side.stat().st_size

    # the same files picked again in another order: the same collection, not delivered
    # again (its generation stays), as an intaker's rescan that found nothing new
    generation = col.generation
    engine.links.clock.now += 1
    pick(engine, project, *reversed(files))
    assert engine.links.input_of("kobi", "data").generation == generation


@pytest.mark.parametrize(
    ("returned", "says"),
    [
        ('["runs/cam-a/nope.jpg"]', "runs/cam-a/nope.jpg does not exist"),
        (
            '[{"key": "a", "parts": ["geo/a.geojson"]}, {"key": "a", "parts": ["geo/b.geojson"]}]',
            "key 'a' is given twice",
        ),
        ('{"items": [{"key": "a"}]}', "item 'a' has no parts"),
        ('{"items": 3}', 'return {"root"?: ..., "items": ['),
    ],
)
def test_a_collection_that_does_not_fit_fails_with_a_fixit(project: Project, returned, says):
    rel = write_link(
        project, "bad", f"def resolve(value, items, params, root):\n    return {returned}\n"
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel, "collection")
    pick(engine, project, "geo/a.geojson")

    status = engine.links.status("l9")
    assert status["state"] == "failed"
    assert says in status["message"], status["message"]
    assert engine.links.input_of("kobi", "data") is None


def test_the_shorthand_shapes_of_a_collection(project: Project):
    # a bare list of paths: one item per file, keyed by its path without extension
    rel = write_link(
        project,
        "short",
        "def resolve(value, items, params, root):\n"
        "    return ['geo/a.geojson', {'parts': {'main': root + '/geo/b.geojson'}}]\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    submit_link(engine, rel, "collection")
    pick(engine, project, "geo/a.geojson")

    col = engine.links.input_of("kobi", "data")
    assert [(it.key, list(it.parts)) for it in col.items] == [
        ("geo/a", [".geojson"]),
        ("geo/b", ["main"]),
    ]
    assert col.items[1].meta["rel"] == {"main": "geo/b.geojson"}
    assert col.items[0].meta["keys"] == {"stem": "geo/a"}


def test_a_collection_for_a_3d_view_carries_its_scenes(project: Project, monkeypatch):
    from test_scene3d import write_cloud_ply

    monkeypatch.setenv("SPECTO_CACHE_DIR", str(project.root.parent / "cache"))
    (project.root / "scans").mkdir()
    write_cloud_ply(project.root / "scans" / "cloud.ply")
    rel = write_link(
        project,
        "scans",
        "def resolve(value, items, params, root):\n    return ['scans/cloud.ply']\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    doc = engine.live.document()
    doc["views"]["sika"] = {"type": "3d", "label": "scans", "layout": {"position": [600, 0]}}
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "sika", "port": "data"},
        "python": {"path": rel, "resolves_to": "value", "output": "collection"},
        "summary": {"sentence": "The 3D view shows the scans the script names."},
    }
    engine.live.submit(doc, origin="agent:s1", label="scans")
    pick(engine, project, "geo/a.geojson")

    col = engine.links.input_of("sika", "data")
    assert engine.links.status("l9")["state"] == "resolved", engine.links.status("l9")
    assert col.items[0].meta["scene"]["kind"] == "pointcloud"


def test_a_3d_collection_from_a_script_names_no_mtl_outside_the_project(
    project: Project, monkeypatch: pytest.MonkeyPatch
):
    """SPEC-80: a Python link's collection is prepared with the project as its root, as an
    intaker's is with its source: an OBJ whose ``mtllib`` climbs out of the project names
    that MTL under ``missing`` and never opens, stats or resolves it (so its texture maps,
    never read, are not named)."""
    import os

    monkeypatch.setenv("SPECTO_CACHE_DIR", str(project.root.parent / "cache"))
    elsewhere = project.root.parent / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "o.mtl").write_text("newmtl x\nmap_Kd secret.png\n", encoding="ascii")
    (project.root / "meshes").mkdir()
    (project.root / "meshes" / "panel.obj").write_text(
        "mtllib ../../elsewhere/o.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="ascii"
    )
    rel = write_link(
        project,
        "meshes",
        "def resolve(value, items, params, root):\n    return ['meshes/panel.obj']\n",
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    doc = engine.live.document()
    doc["views"]["sika"] = {"type": "3d", "label": "meshes", "layout": {"position": [600, 0]}}
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "sika", "port": "data"},
        "python": {"path": rel, "resolves_to": "value", "output": "collection"},
        "summary": {"sentence": "The 3D view shows the meshes the script names."},
    }
    engine.live.submit(doc, origin="agent:s1", label="meshes")

    reached: list[str] = []

    def trap(owner: object, name: str) -> None:
        orig = getattr(owner, name)

        def recorded(path, *args, **kwargs):
            if "elsewhere" in str(path):
                reached.append(f"{name}({path})")
            return orig(path, *args, **kwargs)

        monkeypatch.setattr(owner, name, recorded)

    for name in ("stat", "lstat", "is_file", "exists", "resolve", "open", "read_text"):
        trap(Path, name)
    for name in ("stat", "lstat", "open"):
        trap(os, name)
    pick(engine, project, "geo/a.geojson")

    col = engine.links.input_of("sika", "data")
    assert engine.links.status("l9")["state"] == "resolved", engine.links.status("l9")
    assert col.items[0].meta["scene"]["missing"] == ["../../elsewhere/o.mtl"]
    assert reached == []


def test_bare_seconds_from_a_script_land_on_the_targets_own_timeline(project: Project):
    # a gallery whose frames carry stamps runs on the "stamp" clock; a script's plain
    # number names no timeline, and the gallery's clock still finds the nearest frame
    files = frame_files(project)
    rel = write_link(
        project, "secs", "def resolve(value, items, params, root):\n    return 1700000000.07\n"
    )
    approve(project, rel)
    engine, _ = engine_on(project)
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "frames", "layout": {"position": [600, 0]}}
    doc["links"]["l5"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["runs/cam-a"], "screener": ["*.jpg"]},
        "summary": {"sentence": "The gallery shows the frames."},
    }
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "kobi", "port": "time"},
        "python": {"path": rel, "resolves_to": "value", "output": "time"},
        "summary": {"sentence": "The gallery goes to the script's time."},
    }
    engine.live.submit(doc, origin="agent:s1", label="time")
    pick(engine, project, files[0])

    time = engine.links.input_of("kobi", "time")
    assert time is not None and time.t == pytest.approx(1700000000.07)
    assert engine.links.global_clock.nearest("kobi", time) == "00001_1700000000060000000"


# -- every output type: the shapes it takes and the fix-it for one it does not -----------------
@pytest.mark.parametrize(
    ("output", "returned", "expected"),
    [
        ("selection", "k1", Selection(keys=["k1"], last="k1")),
        ("selection", 7, Selection(keys=["7"], last="7")),
        ("selection", ["a", "b"], Selection(keys=["a", "b"], last="b")),
        ("selection", {"keys": ["a", "b"], "last": "a"}, Selection(keys=["a", "b"], last="a")),
        ("selection", [], Selection(keys=[], last=None)),
        ("files", [r"geo\a.geojson"], "geo/a.geojson"),
        ("files", {"paths": ["geo/a.geojson"]}, "geo/a.geojson"),
        ("time", 2.5, TimeValue(t=2.5, timeline=OWN_TIMELINE)),
        ("time", {"t": 2.5, "timeline": "usd"}, TimeValue(t=2.5, timeline="usd")),
        ("camera", {"position": [1, 2, 3], "target": [0, 0, 0]}, (1.0, 2.0, 3.0)),
        ("content", ["geo/a.geojson"], ["geo/a.geojson"]),
        ("content", {"paths": ["geo"]}, ["geo/a.geojson", "geo/b.geojson", "geo/readme.md"]),
        ("collection", ["geo/a.geojson"], ["geo/a"]),
    ],
)
def test_each_output_type_takes_its_shapes(project: Project, output, returned, expected):
    out = as_port_value(output, returned, root=str(project.root), where="x.py:resolve")
    if output == "files":
        assert out == FilesValue(root=str(project.root), paths=[expected])
    elif output == "camera":
        assert out.position == expected and out.fov_y == 0.8
    elif output == "content":
        assert [e.path for e in out.entries] == expected
    elif output == "collection":
        assert [it.key for it in out.items] == expected
    else:
        assert out == expected


@pytest.mark.parametrize(
    ("output", "returned"),
    [
        ("selection", {"x": 1}),
        ("files", 42),
        ("files", {"paths": ["a"], "extra": 1}),
        ("content", 42),
        ("content", ["geo/nope.bin"]),
        ("time", "soon"),
        ("time", {"t": "soon"}),
        ("camera", [1, 2, 3]),
        ("camera", {"position": [1, 2]}),
        ("collection", 42),
        ("collection", [{"parts": 5}]),
    ],
)
def test_a_wrong_shape_names_the_shapes_its_port_takes(project: Project, output, returned):
    with pytest.raises(TypeError) as e:
        as_port_value(output, returned, root=str(project.root), where="x.py:resolve")
    msg = str(e.value)
    assert msg.startswith("x.py:resolve returned ") and f"for a {output} output" in msg
    assert msg.endswith(f"return {SHAPES[output]}")
