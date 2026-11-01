"""Seam 1 (spec #35 story 15): the detailed diff of a submit is there on request and
quiet otherwise. ``canvas.history`` lists the submits as ``state`` does and, asked for
it, returns a submit's patch and inverse; ``describe_patch`` turns a patch into the
lines a human reads."""

from __future__ import annotations

from specto_core import Engine
from specto_core.diff_text import describe_patch
from specto_core.txn import touched_objects


def relabel_and_add(engine: Engine) -> dict:
    doc = engine.live.document()
    doc["views"]["fege"]["label"] = "Data"
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [300, 0], "size": [600, 400]},
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["."]},
        "summary": {"sentence": "The gallery shows every image."},
    }
    return doc


def test_history_lists_submits_and_returns_a_patch_only_on_request(engine: Engine):
    res = engine.call("canvas.submit", {"document": relabel_and_add(engine), "label": "front"})
    plain = engine.call("canvas.history", {})["history"]
    assert [h["txn"] for h in plain] == [res["txn"]]
    assert "patch" not in plain[0] and plain[0]["label"] == "front"
    assert engine.call("canvas.state", {})["undo"]["history"] == plain

    detailed = engine.call("canvas.history", {"txn": res["txn"], "patch": True})["history"]
    assert len(detailed) == 1
    assert detailed[0]["patch"] == res["patch"] and detailed[0]["inverse"]
    assert set(touched_objects(detailed[0]["inverse"])) == {"fege", "kobi", "l2"}
    assert detailed[0]["diff"] == describe_patch(res["patch"], detailed[0]["inverse"])


def test_describe_patch_reads_as_what_changed(engine: Engine):
    res = engine.call("canvas.submit", {"document": relabel_and_add(engine), "label": "front"})
    rec = engine.live.record(res["txn"])
    lines = describe_patch(rec.patch, rec.inverse)
    assert lines == [
        "~ view fege label: 'demo' -> 'Data'" if engine.live.data["views"]["fege"]["label"] else "",
        "+ view kobi: gallery 'Front' at 300,0 600x400",
        "+ link l2: intaker project.all_files -> kobi.data",
    ] or lines[1:] == [
        "+ view kobi: gallery 'Front' at 300,0 600x400",
        "+ link l2: intaker project.all_files -> kobi.data",
    ]
    assert lines[0].startswith("~ view fege label:") and lines[0].endswith("-> 'Data'")

    doc = engine.live.document()
    doc["views"]["kobi"]["layout"]["position"] = [320, 40]
    doc["links"]["l3"] = doc["links"].pop("l2")  # the link under a new id: one out, one in
    doc["root_view"] = None
    moved = engine.call("canvas.submit", {"document": doc, "label": "moved"})
    rec = engine.live.record(moved["txn"])
    assert describe_patch(rec.patch, rec.inverse) == [
        "~ view kobi layout.position: [300.0, 0.0] -> [320.0, 40.0]",
        "- link l2: intaker project.all_files -> kobi.data",
        "+ link l3: intaker project.all_files -> kobi.data",
    ]
