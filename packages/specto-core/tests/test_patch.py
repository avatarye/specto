from __future__ import annotations

import copy

import pytest

from specto_core.errors import PatchError
from specto_core.patch import DocTxn, apply_patch, get_at, invert_patch, pointer


def test_pointer_escapes_tokens():
    assert pointer("objects", "a/b", "c~d") == "/objects/a~1b/c~0d"
    assert get_at({"a/b": {"c~d": 1}}, "/a~1b/c~0d") == 1


def test_doctxn_records_exact_inverse():
    data = {"objects": {}, "list": [1, 2, 3], "ids": {}}
    before = copy.deepcopy(data)
    t = DocTxn(data)
    t.add("/objects/files", {"kind": "view", "label": "a"})
    t.replace("/objects/files/label", "b")
    t.set("/ids/v", 1)
    t.set_deep("/objects/files/state/zoom", 2.0)
    t.add("/list/-", 4)
    t.add("/list/0", 0)
    t.remove("/list/2")
    after = copy.deepcopy(data)
    assert data["list"] == [0, 1, 3, 4]
    apply_patch(data, t.inverse_patch())
    assert data == before
    apply_patch(data, t.forward_patch())
    assert data == after


def test_replace_same_value_records_nothing():
    t = DocTxn({"a": 1})
    t.replace("/a", 1)
    assert len(t) == 0


def test_apply_patch_is_atomic():
    data = {"a": 1}
    with pytest.raises(PatchError):
        apply_patch(
            data,
            [{"op": "replace", "path": "/a", "value": 2}, {"op": "remove", "path": "/missing"}],
        )
    assert data == {"a": 1}


def test_test_move_copy_ops():
    data = {"a": {"x": 1}, "b": {}}
    apply_patch(
        data,
        [
            {"op": "test", "path": "/a/x", "value": 1},
            {"op": "copy", "from": "/a/x", "path": "/b/y"},
            {"op": "move", "from": "/a/x", "path": "/b/z"},
        ],
    )
    assert data == {"a": {}, "b": {"y": 1, "z": 1}}
    with pytest.raises(PatchError):
        apply_patch(data, [{"op": "test", "path": "/b/y", "value": 2}])


def test_invert_patch_matches_doctxn():
    before = {"objects": {"files": {"label": "a"}}, "ids": {"v": 1}}
    patch = [
        {"op": "replace", "path": "/objects/files/label", "value": "b"},
        {"op": "add", "path": "/objects/kobi", "value": {"label": "c"}},
        {"op": "remove", "path": "/ids/v"},
    ]
    inv = invert_patch(before, patch)
    data = copy.deepcopy(before)
    apply_patch(data, patch)
    apply_patch(data, inv)
    assert data == before


def test_values_are_copied_not_aliased():
    data = {"objects": {}}
    t = DocTxn(data)
    value = {"state": {"zoom": 1}}
    t.add("/objects/files", value)
    value["state"]["zoom"] = 99
    assert data["objects"]["files"]["state"]["zoom"] == 1
    assert t.forward_patch()[0]["value"]["state"]["zoom"] == 1
