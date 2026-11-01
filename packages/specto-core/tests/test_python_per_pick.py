"""Seam 1 (#168): per-pick Python links. With ``resolves_to: value`` the source's current
value is part of the resolve key and is handed to the script as
``entry(value, items, params, root)``; the script re-runs out of process whenever the
value changes (debounced, stale results dropped), only after the user approved its
content (ADR 0006), and its result is converted to the declared output port type.

The worked example: a recording picked in one file tree selects the
annotation folder in a second tree, and a map shows that folder's GeoJSONs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specto_core import Engine, Project
from specto_core.contents import ContentValue
from specto_core.jobs import InlineJobManager, JobInfo, JobManager
from specto_core.links import FilesValue, Selection

CLIP_TO_GEO = ".specto/links/clip_to_geo.py"
GEO_FILES = ".specto/links/geo_files.py"
CLIPS = ("clip1", "clip2")
WORLD = "recordings/s01/{}/w.usda"
GEO = "annotations/s01/{}/geo"

#: recordings/<session>/<clip>/... -> annotations/.../geo;
#: every run appends the value it got to a trace file next to the code
MIRROR = """\
import json, pathlib

def resolve(value, items, params, root):
    trace = pathlib.Path(root) / ".specto" / "links" / "clip_to_geo.ran"
    runs = json.loads(trace.read_text()) if trace.exists() else []
    runs.append(value)
    trace.write_text(json.dumps(runs))
    out = []
    for p in value["paths"] or []:
        parts = p.split("/")
        if parts[:1] == ["recordings"] and len(parts) >= 4:
            out.append("/".join(["annotations", *parts[1:3], "geo"]))
    return sorted(set(out))
"""

#: the same mapping, but the geo folders go out as files to read (a content output)
GEO_CONTENT = MIRROR.replace("clip_to_geo.ran", "geo_files.ran").replace(
    "return sorted(set(out))", 'return {"paths": sorted(set(out))}'
)


def feature(x: float) -> dict:
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Point", "coordinates": [x, 1]},
    }


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    proj = Project.init(tmp_path / "proj", "mirror")
    for i, clip in enumerate(CLIPS):
        world = proj.root / WORLD.format(clip)
        world.parent.mkdir(parents=True)
        world.write_text("#usda 1.0\n", encoding="utf-8")
        geo = proj.root / GEO.format(clip)
        geo.mkdir(parents=True)
        fc = {"type": "FeatureCollection", "features": [feature(i), feature(i + 0.5)]}
        (geo / "a.geojson").write_text(json.dumps(fc), encoding="utf-8")
        (geo / "notes.bin").write_bytes(b"\0")
    (proj.root / CLIP_TO_GEO).write_text(MIRROR, encoding="utf-8")
    (proj.root / GEO_FILES).write_text(GEO_CONTENT, encoding="utf-8")
    return proj


def mirror_doc(engine: Engine, *, map_link: bool = False) -> dict:
    doc = engine.live.document()
    doc["views"]["tora"] = {
        "type": "file_tree",
        "label": "annotations",
        "layout": {"position": [400, 0]},
    }
    doc["links"]["l5"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "tora", "port": "data"},
        "params": {"sources": ["annotations"]},
        "summary": {"sentence": "The second tree shows the annotations folder."},
    }
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "fege", "port": "selected_files"},
        "target": {"view": "tora", "port": "selection"},
        "python": {"path": CLIP_TO_GEO, "resolves_to": "value", "output": "selection"},
        "summary": {"sentence": "The annotation tree selects the picked recording's folder."},
    }
    if map_link:
        doc["views"]["mapa"] = {"type": "map", "label": "geo", "layout": {"position": [800, 0]}}
        doc["links"]["l10"] = {
            "kind": "python",
            "source": {"view": "fege", "port": "selected_files"},
            "target": {"view": "mapa", "port": "features"},
            "python": {"path": GEO_FILES, "resolves_to": "value", "output": "content"},
            "summary": {"sentence": "The map shows the geo GeoJSONs of the picked recording."},
        }
    return doc


def runs(project: Project, name: str = "clip_to_geo") -> list:
    trace = project.root / ".specto" / "links" / f"{name}.ran"
    return json.loads(trace.read_text()) if trace.exists() else []


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def pick(engine: Engine, project: Project, clip: str) -> None:
    engine.links.emit(
        "fege", "selected_files", FilesValue(root=str(project.root), paths=[WORLD.format(clip)])
    )


def selected(clip: str) -> Selection:
    return Selection(keys=[GEO.format(clip)], last=GEO.format(clip))


def make_engine(project: Project, jobs: JobManager | None = None) -> tuple[Engine, Clock]:
    engine = Engine(project=project, jobs=jobs or InlineJobManager())
    clock = Clock()
    engine.links.clock = clock
    return engine, clock


def test_unapproved_per_pick_code_never_runs_whatever_is_picked(project: Project):
    engine, clock = make_engine(project)
    engine.live.submit(mirror_doc(engine), origin="agent:s1", label="mirror")
    for clip in CLIPS:
        pick(engine, project, clip)
        clock.now += 1
        engine.links.poll()

    assert runs(project) == []
    assert engine.links.input_of("tora", "selection") is None
    assert engine.links.status("l9")["state"] == "needs_approval"


def test_each_pick_runs_the_code_with_the_value_and_selects_the_annotation_folder(
    project: Project, approve_link
):
    engine, clock = make_engine(project)
    engine.live.submit(mirror_doc(engine), origin="agent:s1", label="mirror")
    pick(engine, project, "clip1")
    approve_link(engine, "l9")

    assert engine.links.status("l9")["state"] == "resolved"
    assert engine.links.input_of("tora", "selection") == selected("clip1")
    assert runs(project) == [{"root": str(project.root), "paths": [WORLD.format("clip1")]}]

    clock.now += 1  # well after the debounce: the pick runs at once
    pick(engine, project, "clip2")
    assert engine.links.input_of("tora", "selection") == selected("clip2")
    assert len(runs(project)) == 2

    # the same value again is the same resolve key: nothing runs
    engine.links.resolve("l9")
    assert len(runs(project)) == 2


def test_rapid_picks_are_debounced_into_one_run_of_the_latest(project: Project, approve_link):
    engine, clock = make_engine(project)
    engine.live.submit(mirror_doc(engine), origin="agent:s1", label="mirror")
    pick(engine, project, "clip1")
    approve_link(engine, "l9")
    assert len(runs(project)) == 1

    clock.now += 0.05
    pick(engine, project, "clip2")
    clock.now += 0.05
    pick(engine, project, "clip1")
    clock.now += 0.05
    pick(engine, project, "clip2")
    engine.links.poll()  # 50 ms after the last pick: still inside the window
    assert len(runs(project)) == 1
    assert engine.links.input_of("tora", "selection") == selected("clip1")

    clock.now += 0.2
    engine.links.poll()
    assert [r["paths"] for r in runs(project)] == [[WORLD.format("clip1")], [WORLD.format("clip2")]]
    assert engine.links.input_of("tora", "selection") == selected("clip2")


class HeldJobs(JobManager):
    """Holds every job until the test runs it, so a pick can land while one runs."""

    def __init__(self) -> None:
        super().__init__()
        self.held: list = []

    def submit(self, kind, fn, *args, canvas=None, on_done=None, **kwargs) -> JobInfo:
        job = self._new(kind, canvas)
        self.held.append((job, fn, args, kwargs, on_done))
        return job

    def run_next(self) -> None:
        job, fn, args, kwargs, on_done = self.held.pop(0)
        try:
            self._finish(job, result=fn(*args, progress=lambda p, m="": None, **kwargs))
        except Exception as e:  # pragma: no cover - a failing job is a test failure
            self._finish(job, error=f"{type(e).__name__}: {e}")
        if on_done is not None:
            on_done(job)

    def cancel(self, job_id: str) -> JobInfo:
        return self.status(job_id)


def test_a_result_for_an_older_pick_is_dropped_and_only_the_latest_lands(
    project: Project, approve_link
):
    jobs = HeldJobs()
    engine, clock = make_engine(project, jobs)
    engine.live.submit(mirror_doc(engine), origin="agent:s1", label="mirror")
    approve_link(engine, "l9")
    jobs.held.clear()  # whatever the open and the approval queued (nothing picked yet)
    landed: list = []
    engine.links.attach("tora", lambda port, value: landed.append(value))
    landed.clear()  # the tree's own listing, replayed on attach (#231)

    clock.now += 1
    pick(engine, project, "clip1")
    clock.now += 1
    pick(engine, project, "clip2")  # while clip1's run is still in flight
    jobs.run_next()  # clip1's result arrives: stale
    assert landed == []
    assert jobs.held == []
    clock.now += 0.2  # the burst is over: the latest pick runs
    engine.links.poll()
    assert len(jobs.held) == 1
    jobs.run_next()
    assert landed == [selected("clip2")]
    assert engine.links.input_of("tora", "selection") == selected("clip2")


def test_a_content_output_reads_the_returned_paths_for_the_map(project: Project, approve_link):
    engine, _ = make_engine(project)
    engine.live.submit(mirror_doc(engine, map_link=True), origin="agent:s1", label="mirror")
    pick(engine, project, "clip2")
    approve_link(engine, "l10")

    features = engine.links.input_of("mapa", "features")
    assert isinstance(features, ContentValue)
    assert features.root == str(project.root)
    # the folder expands to its readable files; the .bin is not contents
    assert [(e.path, e.format) for e in features.entries] == [
        (f"{GEO.format('clip2')}/a.geojson", "geojson")
    ]
    assert features.entries[0].meta["features"] == 2
    assert features.link == "l10"


@pytest.mark.parametrize(
    ("output", "returned", "says"),
    [
        ("selection", '{"x": 1}', "a key, a list of keys"),
        ("content", "42", '{"paths": ['),
        ("time", '"soon"', "a number of seconds"),
    ],
)
def test_a_wrong_shape_fails_the_link_with_a_fixit(
    project: Project, output, returned, says, approve_link
):
    (project.root / CLIP_TO_GEO).write_text(
        f"def resolve(value, items, params, root):\n    return {returned}\n", encoding="utf-8"
    )
    engine, _ = make_engine(project)
    doc = mirror_doc(engine)
    targets = {"selection": ("tora", "selection"), "content": ("mapa", "features")}
    if output == "time":
        doc["views"]["vida"] = {"type": "video", "label": "Video", "layout": {"position": [0, 600]}}
        targets["time"] = ("vida", "time")
    if output == "content":
        doc["views"]["mapa"] = {"type": "map", "label": "geo", "layout": {"position": [800, 0]}}
    view, port = targets[output]
    doc["links"]["l9"]["target"] = {"view": view, "port": port}
    doc["links"]["l9"]["python"]["output"] = output
    engine.live.submit(doc, origin="agent:s1", label="wrong shape")
    pick(engine, project, "clip1")
    approve_link(engine, "l9")

    status = engine.links.status("l9")
    assert status["state"] == "failed"
    assert f"{CLIP_TO_GEO}:resolve" in status["message"]
    assert says in status["message"]
    assert engine.links.input_of(view, port) is None


def test_a_map_refuses_contents_that_are_not_geojson_with_a_fixit(project: Project, approve_link):
    (project.root / "annotations" / "notes.txt").write_text("hello", encoding="utf-8")
    (project.root / GEO_FILES).write_text(
        'def resolve(value, items, params, root):\n    return ["source/notes.txt"]\n',
        encoding="utf-8",
    )
    engine, _ = make_engine(project)
    engine.live.submit(mirror_doc(engine, map_link=True), origin="agent:s1", label="mirror")
    pick(engine, project, "clip1")
    approve_link(engine, "l10")

    status = engine.links.status("l10")
    assert status["state"] == "failed"
    assert "GeoJSON" in status["message"] and "source/notes.txt" in status["message"]
