"""Canvas document v2 (#37): one canvas per project, the pydantic model, its validation
report and its JSON Schema. Seam 1: the headless core's public interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specto_core import Engine, Project
from specto_core.canvas_document import (
    CanvasDocument,
    CanvasInvalid,
    canvas_json_schema,
    validate_canvas,
    validate_canvas_text,
)
from specto_core.canvas_example import worked_example
from specto_core.errors import NotFound, SchemaError


def test_new_project_opens_with_one_canvas_that_validates(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir()
    Project.init(root, "demo")

    proj = Project.open(root)
    doc = proj.canvas_document()

    assert isinstance(doc, CanvasDocument)
    assert doc.schema_version == 2
    assert proj.canvas_file == root / ".specto" / "canvas.json"
    assert proj.canvas_file.exists()
    # a new canvas starts with the file tree, fed from the project folder by an intaker
    assert [v.type for v in doc.views.values()] == ["file_tree"]
    (link,) = doc.links.values()
    assert link.kind == "intaker"
    assert link.source.view == "project"
    assert link.target.view == next(iter(doc.views))
    assert link.summary.sentence


def _v1_project(root: Path) -> None:
    """The M2 layout: project.toml plus .specto/canvases/<id>.json, no canvas.json."""
    (root / ".specto" / "canvases").mkdir(parents=True)
    (root / ".specto" / "project.toml").write_text(
        'name = "old"\nid = "5f0b2c1e-0000-4000-8000-000000000001"\nnext_canvas = 1\n',
        encoding="utf-8",
    )
    (root / ".specto" / "canvases" / "cv1.json").write_text(
        json.dumps({"schema_version": 1, "canvas": {"id": "cv1"}, "objects": {}}),
        encoding="utf-8",
    )


def test_v1_project_is_refused_with_a_clear_message(tmp_path: Path):
    root = tmp_path / "old"
    _v1_project(root)

    with pytest.raises(SchemaError) as e:
        Project.open(root)
    assert "v1" in e.value.message and "canvas.json" in e.value.message
    assert "does not convert" in e.value.fix
    # open_or_init must not quietly re-initialise it either
    with pytest.raises(SchemaError):
        Project.open_or_init(root)
    assert not (root / ".specto" / "canvas.json").exists()


def test_v1_canvas_document_is_refused_with_a_clear_message(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir()
    proj = Project.init(root, "demo")
    raw = json.loads(proj.canvas_file.read_text(encoding="utf-8"))
    raw["schema_version"] = 1
    proj.canvas_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SchemaError) as e:
        Project.open(root).canvas_document()
    assert "v1" in e.value.message and e.value.data == {"file_version": 1, "core_version": 2}
    assert "does not convert" in e.value.fix


def test_worked_example_validates_and_round_trips(tmp_path: Path):
    """The run-001 review canvas: file tree, two galleries keyed on frame, a video as root
    view, a map from GeoJSON, a JSON inspector, an image view of the picked files; every
    link kind appears once at least, and both intaker scopes."""
    data = worked_example()
    doc = validate_canvas(data)

    assert doc.root_view == "rena" and doc.views["rena"].type == "video"
    assert [v.type for v in doc.views.values()] == [
        "file_tree",
        "gallery",
        "gallery",
        "video",
        "map",
        "json",
        "image",
    ]
    assert {link.kind for link in doc.links.values()} == {
        "intaker",
        "read_contents",
        "follow",
        "python",
        "time_map",
    }
    assert doc.links["l7"].two_way is True
    assert doc.links["l9"].python.output == "time"
    assert doc.links["l2"].scope == "pinned" and doc.links["l13"].scope == "selection"
    assert "docked" not in data["window_frame"] and "global_clock" not in data
    # what is written is what is read back
    assert validate_canvas(doc.to_data()).to_data() == doc.to_data()
    proj = Project.init(tmp_path / "p", "demo")
    proj.save_canvas_document(doc)
    assert proj.canvas_document() == doc


def problems_of(data: dict) -> dict[str, dict]:
    """Validate an invalid document; problems keyed by path, printed for -s."""
    with pytest.raises(CanvasInvalid) as e:
        validate_canvas(data)
    print(e.value)
    assert all(p["fix"] for p in e.value.problems), "every problem carries a fix-it"
    return {p["path"]: p for p in e.value.problems}


def test_all_errors_arrive_together_with_fixits():
    """ADR 0013: one reply lists every problem, field-level and graph-level alike, so the
    agent fixes everything in one retry."""
    data = worked_example()
    del data["links"]["l9"]["summary"]  # missing summary (a field error)
    data["links"]["l8"]["source"] = {"view": "rena", "port": "time"}  # time -> selection
    data["links"]["l13"] = {  # a second link into tamu.selection, of the wrong type
        "kind": "follow",
        "source": {"view": "mihu", "port": "content"},
        "target": {"view": "tamu", "port": "selection"},
        "summary": {"sentence": "Nonsense: content into a selection port."},
    }
    data["links"]["l7"]["two_way"] = False  # with l14, a cycle through both galleries
    data["links"]["l14"] = {
        "kind": "follow",
        "source": {"view": "tamu", "port": "selection"},
        "target": {"view": "kobi", "port": "selection"},
        "summary": {"sentence": "Selecting in the rear gallery selects the frame in the front."},
    }
    data["root_view"] = "kobi"  # a gallery is not a player

    p = problems_of(data)

    assert "summary" in p["/links/l9/summary"]["message"]
    assert "sentence" in p["/links/l9/summary"]["fix"]
    assert "'time'" in p["/links/l8"]["message"] and "'selection'" in p["/links/l8"]["message"]
    assert "'content'" in p["/links/l13"]["message"] and "python" in p["/links/l13"]["fix"]
    assert "already fed by link l7" in p["/links/l13/target"]["message"]
    assert "cycle" in p["/links"]["message"] and "two_way" in p["/links"]["fix"]
    assert "kobi.selection" in p["/links"]["message"] and "tamu.selection" in p["/links"]["message"]
    assert "gallery" in p["/root_view"]["message"] and "video" in p["/root_view"]["fix"]
    assert len(p) == 6  # reachability is judged at submit, for the agent only


def test_duplicate_ids_in_the_json_text_are_reported():
    text = json.dumps(worked_example())
    dup = '"tamu": {"type": "gallery", "label": "Twice"}, "tamu":'
    text = text.replace('"tamu":', dup, 1)
    with pytest.raises(CanvasInvalid) as e:
        validate_canvas_text(text)
    (p,) = e.value.problems
    assert p["path"] == "/views" and "'tamu'" in p["message"] and "twice" in p["message"]


def test_one_link_per_input_port_and_unknown_ports_name_what_exists():
    data = worked_example()
    data["links"]["l5"]["target"] = {"view": "pola", "port": "polygons"}
    data["links"]["l14"] = {
        "kind": "time_map",
        "source": {"view": "rena", "port": "time"},
        "target": {"view": "kobi", "port": "time"},
        "params": {"from_timeline": "video", "to_timeline": "stamp"},
        "summary": {"sentence": "A second time map into the front gallery."},
    }
    p = problems_of(data)
    assert p["/links/l5/target"]["message"] == (
        "pola.polygons: map pola has no input port 'polygons' (has: features, selection, time)"
    )
    assert "already fed by link l10" in p["/links/l14/target"]["message"]
    assert len(p) == 2


def test_python_link_rules_live_in_the_schema():
    data = worked_example()
    del data["links"]["l9"]["python"]
    data["links"]["l12"]["two_way"] = True  # the map has no time output to send back
    data["links"]["l2"]["two_way"] = True  # an intaker is never two-way
    data["links"]["l4"]["kind"] = "teleport"
    p = problems_of(data)
    assert "python" in p["/links/l9/python"]["message"]
    assert "resolves_to" in p["/links/l9/python"]["fix"]
    assert "two_way needs pola.time as an output" in p["/links/l12/two_way"]["message"]
    assert "two_way" in p["/links/l2/two_way"]["message"]
    assert "teleport" in p["/links/l4/kind"]["message"]
    assert "intaker, follow, read_contents, time_map, python" in p["/links/l4/kind"]["fix"]
    assert len(p) == 4


def test_schema_is_exported_through_api_info_and_describe(engine: Engine):
    info = engine.call("api.info", {})
    schema = info["canvas_schema"]
    assert info["schema_version"] == 2 and schema == canvas_json_schema()
    assert schema["properties"]["schema_version"]["const"] == 2
    assert set(schema["$defs"]) >= {
        "View",
        "IntakerLink",
        "FollowLink",
        "ReadContentsLink",
        "TimeMapLink",
        "PythonLink",
    }
    assert "docked" not in schema["$defs"]["WindowFrame"]["properties"]
    assert "global_clock" not in schema["properties"]
    # the same schema and the worked example through describe
    d = engine.call("describe", {"target": "canvas"})
    assert d["kind"] == "canvas" and d["info"]["schema"] == schema
    assert validate_canvas(d["info"]["example"]).root_view == "rena"
    # per link kind: its schema (params, python, required summary) and an example link
    k = engine.call("describe", {"target": "link:python"})["info"]
    assert k["kind"] == "python" and "python" in k["schema"]["required"]
    assert k["maps"] == {"source": "any", "target": "python.output"}
    assert k["example"]["python"]["resolves_to"] == "table"
    i = engine.call("describe", {"target": "link:intaker"})["info"]
    assert i["maps"] == {"source": "files", "target": "collection"}
    assert "sources" in i["schema"]["$defs"]["IntakerParams"]["properties"]
    assert i["schema"]["properties"]["scope"]["description"]
    # view types carry their ports; port types say where they appear and if two-way is allowed
    g = engine.call("describe", {"target": "view:gallery"})["info"]
    assert g["ports"]["inputs"] == {"data": "collection", "selection": "selection", "time": "time"}
    assert g["ports"]["propagates"] == {"selection": ["selection"]}
    s = engine.call("describe", {"target": "port:selection"})["info"]
    assert s["two_way"] is True and "gallery" in s["outputs_of"] and "map" in s["inputs_of"]
    assert engine.call("describe", {"target": "port:time"})["info"]["two_way"] is True
    assert engine.call("describe", {"target": "port:files"})["info"]["two_way"] is False
    with pytest.raises(NotFound) as e:
        engine.call("describe", {"target": "link:teleport"})
    assert "intaker" in e.value.fix
    with pytest.raises(NotFound):
        engine.call("describe", {"target": "port:ether"})


def test_an_intaker_off_the_file_tree_declares_its_scope_and_the_port_must_match():
    """ADR 0014 amendment: a view fed from the file tree either follows what the user
    picks (scope selection, off selected_files) or shows fixed sources (scope pinned,
    off all_files); the agent says which, and the port must agree."""
    data = worked_example()
    del data["links"]["l2"]["scope"]  # a pinned intaker off the project source, unsaid
    data["links"]["l3"]["scope"] = "selection"  # selection scope but fed from the project
    data["links"]["l6"]["scope"] = "pinned"  # pinned scope but fed from selected_files
    data["links"]["l6"]["source"] = {"view": "fege", "port": "selected_files"}
    data["links"]["l1"]["scope"] = "selection"  # the project source has no selection
    p = problems_of(data)
    assert "pinned" in p["/links/l2/scope"]["message"] or "fixed" in p["/links/l2/scope"]["message"]
    assert "selection" in p["/links/l2/scope"]["fix"] and "pinned" in p["/links/l2/scope"]["fix"]
    assert "selected_files" in p["/links/l3/scope"]["fix"]
    assert "project.all_files" in p["/links/l6/source"]["fix"]
    assert "project" in p["/links/l1/scope"]["message"]
    assert len(p) == 4


def test_a_selection_intaker_needs_no_sources_and_describe_shows_both_scopes(engine: Engine):
    data = worked_example()
    data["links"]["l2"]["source"] = {"view": "fege", "port": "selected_files"}
    data["links"]["l2"]["scope"] = "selection"
    del data["links"]["l2"]["params"]["sources"]  # sources default to "." (an optional narrowing)
    doc = validate_canvas(data)
    assert doc.links["l2"].params.sources == ["."]
    # the file tree's own intaker needs no scope (it is pinned to the folder by nature)
    assert doc.links["l1"].scope is None
    i = engine.call("describe", {"target": "link:intaker"})["info"]
    assert set(i["scopes"]) == {"selection", "pinned"}
    assert i["examples"]["selection"]["source"]["port"] == "selected_files"
    assert i["examples"]["pinned"]["source"]["port"] == "all_files"
    assert "selection" in i["doc"] and "pinned" in i["doc"]


def test_a_canvas_saved_before_intaker_scopes_still_opens_and_saves_the_inferred_scope(
    tmp_path: Path,
):
    """The user's first-test project: file-tree intakers without a scope (the shape of
    user-test-1-canvas.json). Opening infers the scope from the source port, the next
    save writes it, and an agent submit without one is still refused."""
    from specto_core.live_canvas import LiveCanvas

    root = tmp_path / "sample"
    Project.init(root, "sample")
    old = {
        "schema_version": 2,
        "project": {"label": "example-project"},
        "window_frame": {"rect": {"x": -447.0, "y": -234.0, "w": 1464.0, "h": 1394.0}},
        "root_view": "tamu",
        "views": {
            "fege": {"type": "file_tree", "label": "Files", "params": {"selected": ["01-run"]}},
            "kobi": {"type": "3d", "label": "02 PLY", "params": {"point_size": 2, "up": "y"}},
            "tamu": {"type": "video", "label": "02 video"},
            "rena": {"type": "gallery", "label": "02 images", "params": {"columns": 2}},
            "pola": {"type": "image", "label": "picked"},
        },
        "links": {
            "l1": {
                "kind": "intaker",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "fege", "port": "data"},
                "params": {"sources": ["."], "watch": True},
                "summary": {"sentence": "The file tree shows every file in the project folder."},
            },
            "l2": {
                "kind": "intaker",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "kobi", "port": "data"},
                "params": {"sources": ["02-run"], "screener": ["*.ply"], "watch": True},
                "summary": {"sentence": "The 3D view shows the PLY files of the 02 run."},
            },
            "l3": {
                "kind": "intaker",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "tamu", "port": "data"},
                "params": {"sources": ["02-run"], "screener": ["*.mp4"], "timeline": "video"},
                "summary": {"sentence": "The video player plays the MP4 of the 02 run."},
            },
            "l4": {
                "kind": "intaker",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "rena", "port": "data"},
                "params": {"sources": ["02-run"], "screener": ["@images"]},
                "summary": {"sentence": "The gallery shows the images of the 02 run."},
            },
            "l5": {
                "kind": "intaker",
                "source": {"view": "fege", "port": "selected_files"},
                "target": {"view": "pola", "port": "data"},
                "params": {"sources": ["."], "screener": ["@images"]},
                "summary": {"sentence": "The image view shows the picked image."},
            },
        },
    }
    (root / ".specto" / "canvas.json").write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(CanvasInvalid):
        validate_canvas(json.loads(json.dumps(old)))  # a submit of this shape is refused

    proj = Project.open(root)
    doc = proj.canvas_document()
    assert doc.links["l1"].scope is None
    assert [doc.links[lid].scope for lid in ("l2", "l3", "l4")] == ["pinned"] * 3
    assert doc.links["l5"].scope == "selection"
    assert '"scope"' not in (root / ".specto" / "canvas.json").read_text(encoding="utf-8")

    live = LiveCanvas.open(proj)
    assert live.document()["links"]["l4"]["scope"] == "pinned"
    # the agent must still say it: dropping the scope again is refused with the fix-it
    again = live.document()
    del again["links"]["l4"]["scope"]
    with pytest.raises(CanvasInvalid) as e:
        live.submit(again, origin="agent:s1", label="no scope")
    assert [p["path"] for p in e.value.problems] == ["/links/l4/scope"]
    # the next save writes the inferred scopes
    proj.save_canvas_document(live.document())
    saved = json.loads((root / ".specto" / "canvas.json").read_text(encoding="utf-8"))
    assert saved["links"]["l4"]["scope"] == "pinned"
    assert saved["links"]["l5"]["scope"] == "selection"
    assert proj.canvas_document().links["l2"].scope == "pinned"
