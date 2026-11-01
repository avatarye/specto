"""Seam 1 (#46): a submitted ``read_contents`` link parses the files it is pointed at
into a ``ContentValue`` on the target view's input port: GeoJSON features for a map
(with stable feature ids, counts and bounds), JSON sidecars and text as they are. The
worked values come from the ``testdata/geo`` files (sites coverage: one polygon;
site points: 13 points)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from intake_helpers import TESTDATA, copy_frames, needs_testdata, tree_files

import specto_intake  # noqa: F401  (registers the intaker and read_contents resolvers)
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import ContentValue, FilesValue

GEO = TESTDATA / "geo"

pytestmark = [needs_testdata, pytest.mark.skipif(not GEO.is_dir(), reason="no testdata/geo")]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "geo")
    (proj.root / "geo").mkdir()
    for name in ("sites-coverage.geojson", "sites-points.geojson"):
        shutil.copy2(GEO / name, proj.root / "geo" / name)
    (proj.root / "geo" / "broken.geojson").write_text("{not json", encoding="utf-8")
    copy_frames(proj.root / "runs" / "cam-a", 2, "cam-a")
    (proj.root / "notes").mkdir()
    (proj.root / "notes" / "readme.md").write_text("# Geo notes\n\nhello\n", encoding="utf-8")
    return proj


def map_fed_from(doc: dict, sources: list[str], fmt: str | None = None) -> dict:
    doc["views"]["kobi"] = {
        "type": "map",
        "label": "Candidates",
        "layout": {"position": [300, 0], "size": [800, 600]},
    }
    doc["links"]["l2"] = {
        "kind": "read_contents",
        "label": "geo files",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "features"},
        "params": {"sources": sources, **({"format": fmt} if fmt else {})},
        "summary": {"sentence": "The map draws every GeoJSON file under geo/."},
    }
    return doc


def all_files(engine: Engine) -> FilesValue:
    """What the file tree host emits for a pick of its root (#231)."""
    return tree_files(engine.project.root)


def test_read_contents_feeds_geojson_features_to_the_map(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    res = engine.live.submit(
        map_fed_from(engine.live.document(), ["geo"]), origin="agent:s1", label="map"
    )
    assert res.links["added"] == ["l2"]
    # the project source is the folder itself: the link reads its sources at once
    assert links.status("l2")["state"] == "resolved"

    value = links.input_of("kobi", "features")
    assert isinstance(value, ContentValue) and value.link == "l2"
    assert value.root == str(project.root)
    assert [e.key for e in value.entries] == [
        "geo/broken",
        "geo/sites-coverage",
        "geo/sites-points",
    ]
    broken, coverage, points = value.entries
    assert points.path == "geo/sites-points.geojson" and points.format == "geojson"
    assert points.data["type"] == "FeatureCollection" and len(points.data["features"]) == 13
    # feature ids are their indices, so a selection can name "<entry key>#<index>"
    assert [f["id"] for f in points.data["features"]][:3] == [0, 1, 2]
    assert points.meta["features"] == 13 and points.meta["types"] == ["Point"]
    assert coverage.meta["features"] == 1 and coverage.meta["types"] == ["Polygon"]
    west, south, east, north = coverage.meta["bbox"]  # the polygon's bounds, lon/lat
    assert west < east and south < north
    assert coverage.data["features"][0]["properties"]["name"] == "site coverage"
    # a file that does not parse is reported on its entry; the rest still arrive
    assert broken.data is None and broken.format == "geojson"
    assert broken.error and "broken.geojson" in broken.error
    assert links.status("l2")["state"] == "resolved"
    # only files under the link's sources are read: the frames and the note stay out
    assert all(e.path.startswith("geo/") for e in value.entries)


def test_read_contents_json_and_text_by_extension_and_by_format(project: Project):
    engine = Engine(project=project, jobs=InlineJobManager())
    links = engine.links
    doc = map_fed_from(engine.live.document(), ["runs/cam-a", "notes"])
    engine.live.submit(doc, origin="agent:s1", label="sidecars")
    links.emit("project", "all_files", all_files(engine))
    value = links.input_of("kobi", "features")
    by_path = {e.path: e for e in value.entries}
    note = by_path["notes/readme.md"]
    assert note.format == "text" and note.data == "# Geo notes\n\nhello\n"
    assert note.meta == {"lines": 3, "bytes": 19}
    sidecar = next(e for p, e in by_path.items() if p.endswith(".json"))
    assert sidecar.format == "json" and sidecar.data["frame"] == 0 and sidecar.meta == {}
    assert not any(p.endswith(".jpg") for p in by_path)  # images are not readable contents

    # an explicit format wins over the extension: the note read as plain JSON fails per
    # entry, the sidecar is still JSON
    doc = map_fed_from(engine.live.document(), ["notes"], fmt="json")
    engine.live.submit(doc, origin="agent:s1", label="as json")
    value = links.input_of("kobi", "features")
    assert [e.path for e in value.entries] == ["notes/readme.md"]
    assert value.entries[0].format == "json" and value.entries[0].error


def test_content_value_is_plain_json_for_the_schema_and_the_wire():
    v = ContentValue.model_validate(
        {
            "root": "/p",
            "entries": [
                {"key": "a", "path": "a.json", "format": "json", "data": {"x": 1}, "meta": {}}
            ],
        }
    )
    assert json.loads(v.model_dump_json())["entries"][0]["data"] == {"x": 1}
    assert v.link == "" and v.entries[0].error is None
