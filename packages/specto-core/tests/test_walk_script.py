"""Seam 1 for the walk-through's script (#174): the planned hops plus what the link
runtime has resolved (counts in and out, the link's params as a note), template captions
for when no agent writes them, and the agent's captions filled in. The JSON shape is the
contract with the walk UI."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_walk_planner import link, storyboard

from specto_core.kinds import Item
from specto_core.links import Collection, FilesValue, Selection
from specto_core.ports import PROJECT_NODE_RECT
from specto_core.timebase import TimeValue
from specto_core.walkthrough import WalkError, caption_script, walk_script


def runtime(**over) -> SimpleNamespace:
    """What a resolved run-001 canvas holds: 2,400 project files, 412 picked in fege,
    128 frames kept in kobi."""
    picked = [f"runs/front/{i:05d}.jpg" for i in range(300)] + [
        f"runs/front/{i:05d}.json" for i in range(112)
    ]
    frames = Collection(
        items=[Item(key=f"{i:05d}", parts={"jpg": f"runs/front/{i:05d}.jpg"}) for i in range(128)]
    )
    outputs = {
        ("project", "all_files"): FilesValue(root="C:/data"),
        ("fege", "selected_files"): FilesValue(root="C:/data", paths=picked),
        ("kobi", "selection"): Selection(keys=["00042"], last="00042"),
        ("zila", "time"): TimeValue(t=83.5),
        ("tamu", "time"): TimeValue(t=83.5),
    }
    inputs = {
        ("fege", "data"): Collection(items=[Item(key="a", parts={"x": "a"})] * 2400),
        ("kobi", "data"): frames,
        ("zila", "selection"): Selection(keys=["00042"], last="00042"),
    }
    statuses = {"l7": {"state": "needs_approval", "message": ""}}
    ns = SimpleNamespace(
        outputs=outputs,
        inputs=inputs,
        project_files=FilesValue(root="C:/data", paths=[f"f{i}" for i in range(2400)]),
        status=lambda lid: statuses.get(lid, {"state": "resolved", "message": ""}),
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def document() -> dict:
    doc = storyboard()
    doc["project"] = {"label": "run-001"}
    doc["links"]["l2"]["params"] = {"screener": ["*.jpg"], "keys": r"(?P<frame>\d{5})"}
    doc["links"]["l2"]["summary"] = {"sentence": "kobi shows the .jpg frames you pick in fege"}
    doc["links"]["l4"]["params"] = {"sources": ["rear/"], "screener": ["*.jpg"]}
    doc["links"]["l5"]["params"] = {
        "from_timeline": "stamp",
        "to_timeline": "video",
        "offset_ms": 120,
    }
    doc["links"]["l7"]["python"] = {
        "path": ".specto/links/track.py",
        "entry": "resolve",
        "resolves_to": "value",
        "output": "content",
    }
    doc["views"]["pola"]["layout"]["floating"] = {"corner": "top_right", "offset": [20, 10]}
    return doc


def test_a_hop_carries_its_ends_kind_in_plain_words_and_the_runtime_counts():
    script = walk_script(document(), "pola", runtime())
    assert script["target"] == "pola" and script["project_label"] == "run-001"
    assert script["captioned"] is False and script["source"] == "template"
    hops = script["hops"]
    assert len(hops) == 7
    h2 = hops[1]
    assert (h2["index"], h2["link"], h2["from"], h2["to"]) == (2, "l2", "fege", "kobi")
    assert (h2["from_port"], h2["to_port"]) == ("selected_files", "data")
    assert h2["kind"] == "intaker"
    assert h2["kind_label"] == "intaker · follows your pick"
    assert h2["count_in"] == {"n": 412, "unit": "files picked"}
    assert h2["count_out"] == {"n": 128, "unit": "items"}
    assert h2["ratio"] == pytest.approx(128 / 412)
    assert h2["note"] == (
        "scope: selection (follows your pick in fege) · screener: *.jpg · key: frame"
    )
    assert h2["summary"] == "kobi shows the .jpg frames you pick in fege"
    assert h2["status"] == "resolved"
    assert (h2["branch"], h2["branch_start"], h2["fork"]) == (False, False, None)


def test_the_kind_labels_and_notes_say_what_each_link_does():
    hops = walk_script(document(), "pola", runtime())["hops"]
    labels = [h["kind_label"] for h in hops]
    assert labels == [
        "intaker · all files",
        "intaker · follows your pick",
        "follow · by frame",
        "intaker · pinned",
        "time map",
        "intaker · follows your pick",
        "python · per pick",
    ]
    assert hops[0]["count_in"] == {"n": 2400, "unit": "files"}
    assert hops[0]["count_out"] == {"n": 2400, "unit": "items"}
    assert hops[2]["note"].startswith("match: frame")
    # the fork note: zila takes a second input, and the walk takes it next
    assert hops[2]["note"].endswith("zila takes a second input; the walk takes it next")
    assert hops[2]["count_out"] == {"n": 1, "unit": "selected", "text": "00042"}
    assert hops[3]["note"] == "scope: pinned · sources: rear/ · screener: *.jpg"
    assert (hops[3]["branch"], hops[3]["branch_start"], hops[3]["fork"]) == (True, True, "zila")
    assert hops[4]["note"].startswith("stamp to video · offset 120 ms")
    assert hops[4]["count_in"] == {"text": "1:23.5", "unit": "time"}
    assert hops[6]["note"] == "code: .specto/links/track.py · per pick"
    assert hops[6]["status"] == "needs_approval"
    # nothing resolved on that end: no count, no ratio
    assert hops[6]["count_out"] is None and hops[6]["ratio"] is None


def test_the_camera_targets_are_the_document_rects_and_the_project_node():
    doc = document()
    script = walk_script(doc, "pola", runtime())
    h1 = script["hops"][0]
    assert h1["from_rect"] == list(PROJECT_NODE_RECT)
    assert h1["to_rect"] == [80, 0, 300, 200]
    assert h1["from_floating"] is False
    assert script["hops"][6]["to_floating"] is True
    assert script["project"]["rect"] == list(PROJECT_NODE_RECT)
    assert script["views"]["kobi"]["rect"] == [400, 0, 300, 200]
    assert list(script["views"]) == ["fege", "kobi", "zila", "tamu", "pola"]
    assert script["views"]["kobi"]["type"] == "gallery"


def test_template_captions_come_from_the_link_summary_and_the_counts():
    script = walk_script(document(), "pola", runtime())
    h2 = script["hops"][1]
    assert h2["caption"] == (
        "kobi shows the .jpg frames you pick in fege. kobi gets **128** items "
        "from **412** files picked."
    )
    assert h2["chip"] == "128 items"
    assert script["project"]["chip"] == "2,400 files"
    assert script["project"]["count"] == {"n": 2400, "unit": "files"}
    assert "**7** links" in script["intro"] and "zila" in script["intro"]
    assert "tamu" in script["intro"]
    assert script["summary"].startswith("**7** links bring the project's files to **pola**")
    # every hop has a caption and a chip, even with nothing resolved
    bare = walk_script(document(), "pola", None)
    assert all(h["caption"] and h["chip"] for h in bare["hops"])
    assert bare["hops"][1]["count_in"] is None and bare["project"]["chip"] == "project folder"


def test_the_agents_captions_replace_the_templates_in_hop_order():
    script = walk_script(document(), "pola", runtime())
    captions = [f"caption {i}" for i in range(1, 8)]
    captions[3] = ""  # an empty caption keeps the template
    out = caption_script(
        script,
        captions,
        intro="pola marks where the car was.",
        summary="From 2,400 files to one position.",
        chips=["412 picked", "128 frames", "", "", "", "", "car position"],
    )
    assert out["captioned"] is True and out["source"] == "agent"
    assert [h["caption"] for h in out["hops"]][:3] == ["caption 1", "caption 2", "caption 3"]
    assert out["hops"][3]["caption"] == script["hops"][3]["caption"]
    assert out["intro"] == "pola marks where the car was."
    assert out["summary"] == "From 2,400 files to one position."
    assert (
        out["hops"][0]["chip"] == "412 picked"
        and out["hops"][2]["chip"] == script["hops"][2]["chip"]
    )
    assert out["views"]["pola"]["chip"] == "car position"
    assert script["captioned"] is False  # the plan's script is not changed in place


def test_captions_that_do_not_match_the_hops_are_refused_with_a_fix():
    script = walk_script(document(), "pola", runtime())
    with pytest.raises(WalkError) as e:
        caption_script(script, ["one", "two"])
    assert "7" in e.value.message and "2" in e.value.message
    assert "one caption per hop" in (e.value.fix or "")


def test_a_walk_with_no_fork_says_so_in_neither_intro_nor_notes():
    doc = storyboard()
    script = walk_script(doc, "kobi", None)
    assert [h["note"].endswith("the walk takes it next") for h in script["hops"]] == [False, False]
    assert "input" not in script["intro"]
    assert script["hops"][0]["caption"]


def test_a_time_value_and_unknown_kinds_still_describe_themselves():
    doc = storyboard()
    doc["links"]["l8"] = link("tamu", "time", "kobi", "time", "time_map")
    del doc["links"]["l5"]  # no zila -> tamu: tamu feeds kobi's time instead
    script = walk_script(doc, "kobi", runtime())
    assert [h["link"] for h in script["hops"]] == ["l1", "l6", "l8", "l2"]
