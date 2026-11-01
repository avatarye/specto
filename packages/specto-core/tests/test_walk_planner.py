"""Seam 1 for the view-chain walk-through (#174): the planner orders the hops from the
project source to a view by the fork rule (#159, the user's decision of 2026-10-24),
from the canvas document's links alone. Expected orders come from the approved
storyboard (designs/walkthrough-159/x-forks.png), not from the code."""

from __future__ import annotations

import pytest

from specto_core.walkthrough import WalkError, plan_walk


def link(src: str, src_port: str, dst: str, dst_port: str, kind: str = "follow", **extra) -> dict:
    return {
        "kind": kind,
        "source": {"view": src, "port": src_port},
        "target": {"view": dst, "port": dst_port},
        "summary": {"sentence": f"{src} feeds {dst} here"},
        **extra,
    }


def view(type_: str, x: float = 0) -> dict:
    return {"type": type_, "label": "", "layout": {"position": [x, 0], "size": [300, 200]}}


def storyboard() -> dict:
    """The run-001 storyboard: fege (file tree) -> kobi (gallery) -> zila (gallery, also
    pinned from the project) -> tamu (video, also fed by fege's picks) -> pola (map)."""
    return {
        "schema_version": 2,
        "views": {
            "fege": view("file_tree", 80),
            "kobi": view("gallery", 400),
            "zila": view("gallery", 800),
            "tamu": view("video", 1200),
            "pola": view("map", 1600),
        },
        "links": {
            "l1": link("project", "all_files", "fege", "data", "intaker"),
            "l2": link("fege", "selected_files", "kobi", "data", "intaker", scope="selection"),
            "l3": link("kobi", "selection", "zila", "selection", params={"match": "frame"}),
            "l4": link("project", "all_files", "zila", "data", "intaker", scope="pinned"),
            "l5": link("zila", "time", "tamu", "time", "time_map"),
            "l6": link("fege", "selected_files", "tamu", "data", "intaker", scope="selection"),
            "l7": link("tamu", "time", "pola", "features", "python"),
        },
    }


def order(plan) -> list[str]:
    return [f"{'<' if h.branch_start else ''}{h.source}>{h.target}" for h in plan.hops]


def test_the_storyboard_walks_each_fork_input_right_after_reaching_the_fork():
    plan = plan_walk(storyboard(), "pola")
    # 1 project -> fege, 2 fege -> kobi, 3 kobi -> zila, <- 4 project -> zila,
    # 5 zila -> tamu, <- 6 fege -> tamu, 7 tamu -> pola
    assert [h.link for h in plan.hops] == ["l1", "l2", "l3", "l4", "l5", "l6", "l7"]
    assert order(plan) == [
        "project>fege",
        "fege>kobi",
        "kobi>zila",
        "<project>zila",
        "zila>tamu",
        "<fege>tamu",
        "tamu>pola",
    ]
    assert [h.branch for h in plan.hops] == [False, False, False, True, False, True, False]
    assert [h.fork for h in plan.hops] == [None, None, None, "zila", None, "tamu", None]
    assert [h.index for h in plan.hops] == [1, 2, 3, 4, 5, 6, 7]
    assert plan.views == ["project", "fege", "kobi", "zila", "tamu", "pola"]
    assert plan.forks == ["zila", "tamu"]


def test_walking_to_a_view_mid_chain_stops_there():
    plan = plan_walk(storyboard(), "zila")
    assert order(plan) == ["project>fege", "fege>kobi", "kobi>zila", "<project>zila"]


def test_a_longer_branch_starts_from_the_nearest_walked_view_and_ends_at_the_fork():
    # x-forks.png, right: project -> a -> b -> F -> v; project -> c -> d -> F
    doc = {
        "views": {n: view("gallery") for n in ("aaa", "bbb", "ccc", "ddd", "fff", "vvv")},
        "links": {
            "l1": link("project", "all_files", "aaa", "data", "intaker"),
            "l2": link("aaa", "selection", "bbb", "selection"),
            "l3": link("bbb", "selection", "fff", "selection"),
            "l4": link("project", "all_files", "ccc", "data", "intaker"),
            "l5": link("ccc", "selection", "ddd", "selection"),
            "l6": link("ddd", "time", "fff", "time", "time_map"),
            "l7": link("fff", "selection", "vvv", "selection"),
            # bbb takes a second input too: a fork on the main line
            "l8": link("project", "all_files", "bbb", "data", "intaker"),
        },
    }
    plan = plan_walk(doc, "vvv")
    assert order(plan) == [
        "project>aaa",
        "aaa>bbb",
        "<project>bbb",
        "bbb>fff",
        "<project>ccc",
        "ccc>ddd",
        "ddd>fff",
        "fff>vvv",
    ]
    assert [h.branch for h in plan.hops] == [False, False, True, False, True, True, True, False]
    assert [h.fork for h in plan.hops][4:7] == ["fff", "fff", "fff"]


def test_a_tie_between_equally_long_inputs_goes_to_the_upper_port_first():
    # x-forks.png, right, as drawn: both branches are three hops long into F
    doc = {
        "views": {n: view("gallery") for n in ("aaa", "bbb", "ccc", "ddd", "fff")},
        "links": {
            "l1": link("project", "all_files", "aaa", "data", "intaker"),
            "l2": link("aaa", "selection", "bbb", "selection"),
            "l3": link("bbb", "time", "fff", "time", "time_map"),
            "l4": link("project", "all_files", "ccc", "data", "intaker"),
            "l5": link("ccc", "selection", "ddd", "selection"),
            "l6": link("ddd", "selection", "fff", "selection"),
        },
    }
    plan = plan_walk(doc, "fff")
    # gallery inputs top to bottom: data, selection, time -> selection (ddd) comes first
    assert order(plan) == [
        "project>ccc",
        "ccc>ddd",
        "ddd>fff",
        "<project>aaa",
        "aaa>bbb",
        "bbb>fff",
    ]


def test_extra_inputs_are_walked_in_input_port_order_top_to_bottom():
    doc = {
        "views": {"tree": view("file_tree"), "gal": view("gallery"), "vid": view("video")},
        "links": {
            "l1": link("project", "all_files", "tree", "data", "intaker"),
            "l2": link("tree", "selected_files", "gal", "data", "intaker"),
            "l3": link("vid", "time", "gal", "time", "time_map"),
            "l4": link("tree", "selected_files", "vid", "data", "intaker"),
            "l5": link("project", "all_files", "zzz", "data", "intaker"),
        },
    }
    doc["views"]["zzz"] = view("image")
    doc["links"]["l6"] = link("zzz", "selection", "gal", "selection")
    plan = plan_walk(doc, "gal")
    # gal's inputs: data (tree, depth 1), selection (zzz, depth 1), time (vid, depth 2)
    # main = vid (longest); then data, then selection, top to bottom
    assert order(plan) == [
        "project>tree",
        "tree>vid",
        "vid>gal",
        "<tree>gal",
        "<project>zzz",
        "zzz>gal",
    ]


def test_a_nested_fork_on_a_branch_walks_its_own_branch_first():
    # the branch into F passes through G, which takes a second input of its own
    doc = {
        "views": {n: view("gallery") for n in ("aaa", "bbb", "ggg", "hhh", "fff")},
        "links": {
            "l1": link("project", "all_files", "aaa", "data", "intaker"),
            "l2": link("aaa", "selection", "bbb", "selection"),
            "l3": link("bbb", "selection", "fff", "selection"),
            "l4": link("project", "all_files", "ggg", "data", "intaker"),
            "l5": link("project", "all_files", "hhh", "data", "intaker"),
            "l6": link("hhh", "selection", "ggg", "selection"),
            "l7": link("ggg", "time", "fff", "time", "time_map"),
            "l8": link("aaa", "selection", "fff", "data", "follow"),
        },
    }
    plan = plan_walk(doc, "fff")
    assert order(plan) == [
        "project>aaa",
        "aaa>bbb",
        "bbb>fff",
        "<aaa>fff",  # data port: the nearest walked view is aaa itself
        "<project>ggg",  # time port: ggg's nearest walked view is the project
        "<project>hhh",  # ggg is a fork on the branch: its other input next
        "hhh>ggg",
        "ggg>fff",
    ]
    assert [h.fork for h in plan.hops][4:] == ["fff", "ggg", "ggg", "fff"]
    assert plan.forks == ["fff", "ggg"]


def test_a_diamond_arrives_at_each_view_once():
    doc = {
        "views": {n: view("gallery") for n in ("aaa", "bbb", "ccc", "ddd")},
        "links": {
            "l1": link("project", "all_files", "aaa", "data", "intaker"),
            "l2": link("aaa", "selection", "bbb", "selection"),
            "l3": link("aaa", "selection", "ccc", "selection"),
            "l4": link("bbb", "selection", "ddd", "selection"),
            "l5": link("ccc", "time", "ddd", "time", "time_map"),
        },
    }
    plan = plan_walk(doc, "ddd")
    assert order(plan) == ["project>aaa", "aaa>bbb", "bbb>ddd", "<aaa>ccc", "ccc>ddd"]


def test_views_off_the_path_and_downstream_links_are_not_walked():
    doc = storyboard()
    doc["views"]["fufe"] = view("markdown")
    doc["links"]["l8"] = link("fege", "selected_files", "fufe", "data", "intaker")
    plan = plan_walk(doc, "kobi")
    assert order(plan) == ["project>fege", "fege>kobi"]
    assert "fufe" not in plan.views


def test_an_input_from_a_view_that_is_not_connected_is_left_out_and_named():
    doc = storyboard()
    doc["views"]["lone"] = view("gallery")
    doc["links"]["l8"] = link("lone", "time", "kobi", "time", "time_map")
    plan = plan_walk(doc, "kobi")
    assert order(plan) == ["project>fege", "fege>kobi"]
    assert plan.not_connected == ["l8"]


def test_a_view_that_is_not_connected_is_refused_with_a_fix():
    doc = storyboard()
    doc["views"]["lone"] = view("gallery")
    with pytest.raises(WalkError) as e:
        plan_walk(doc, "lone")
    assert "lone" in e.value.message and "not connected" in e.value.message
    assert e.value.fix


def test_an_unknown_view_is_refused_naming_the_views():
    with pytest.raises(WalkError) as e:
        plan_walk(storyboard(), "nope")
    assert "nope" in e.value.message and "pola" in (e.value.fix or "")


def test_the_project_source_itself_has_no_walk():
    with pytest.raises(WalkError):
        plan_walk(storyboard(), "project")


def test_a_loop_of_links_upstream_of_the_view_is_refused_naming_it():
    doc = storyboard()
    # kobi -> zila exists (l3); zila -> kobi closes a loop at the view level
    doc["links"]["l8"] = link("zila", "time", "kobi", "time", "time_map")
    with pytest.raises(WalkError) as e:
        plan_walk(doc, "pola")
    assert "loop" in e.value.message
    assert "kobi" in e.value.message and "zila" in e.value.message


def test_a_two_way_link_is_walked_in_its_declared_direction_and_is_no_loop():
    doc = storyboard()
    doc["links"]["l3"]["two_way"] = True
    plan = plan_walk(doc, "pola")
    assert [h.link for h in plan.hops] == ["l1", "l2", "l3", "l4", "l5", "l6", "l7"]
