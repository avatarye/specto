"""``edits.three_way`` (SPEC-18): a field is a view's or link's top-level key, or one key
of its layout or params; anything inside a field is one value, so two sides' halves of a
link end or a float anchor never merge into an object neither side wrote."""

from __future__ import annotations

from specto_core.edits import MISSING, three_way


def test_fields_only_one_side_changed_merge():
    base = {"label": "a", "layout": {"position": [0, 0], "size": [10, 10]}, "params": {"tile": 1}}
    live = {"label": "a", "layout": {"position": [5, 5], "size": [10, 10]}, "params": {"tile": 1}}
    agent = {"label": "b", "layout": {"position": [0, 0], "size": [20, 20]}, "params": {"tile": 2}}
    merged, conflicts = three_way(base, live, agent)
    assert conflicts == []
    assert merged == {
        "label": "b",
        "layout": {"position": [5, 5], "size": [20, 20]},
        "params": {"tile": 2},
    }


def test_a_link_end_is_one_value():
    base = {"kind": "follow", "source": {"view": "kobi", "port": "selection"}}
    live = {"kind": "follow", "source": {"view": "kobi", "port": "time"}}
    agent = {"kind": "follow", "source": {"view": "tamu", "port": "selection"}}
    merged, conflicts = three_way(base, live, agent)
    assert conflicts == ["source"] and merged == live


def test_a_float_anchor_is_one_value():
    floating = {"corner": "top_left", "offset": [10, 10], "by": "agent"}
    base = {"layout": {"floating": floating}}
    live = {"layout": {"floating": {**floating, "offset": [40, 40], "by": "user"}}}
    agent = {"layout": {"floating": {**floating, "corner": "bottom_right"}}}
    merged, conflicts = three_way(base, live, agent)
    assert conflicts == ["layout.floating"] and merged == live


def test_a_param_value_is_one_value():
    base = {"params": {"spec": {"keys": "a", "sources": ["."]}}}
    live = {"params": {"spec": {"keys": "b", "sources": ["."]}}}
    agent = {"params": {"spec": {"keys": "a", "sources": ["x"]}}}
    assert three_way(base, live, agent)[1] == ["params.spec"]


def test_removal_against_a_change_conflicts_and_alike_changes_do_not():
    assert three_way({"a": 1}, MISSING, {"a": 2}) == (MISSING, [""])
    assert three_way({"a": 1}, {"a": 2}, MISSING) == ({"a": 2}, [""])
    assert three_way({"a": 1}, MISSING, {"a": 1}) == (MISSING, [])
    assert three_way({"a": 1}, {"a": 2}, {"a": 2}) == ({"a": 2}, [])
