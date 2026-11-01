"""tools/gui_refs.py: a flow line names views by type or label (#101), resolved against
the live document's views before gui_drive parses the line."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[3] / "tools" / "gui_refs.py"
_spec = importlib.util.spec_from_file_location("gui_refs", _PATH)
assert _spec is not None and _spec.loader is not None
gui_refs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gui_refs)

VIEWS = {
    "fege": {"type": "file_tree", "label": "Files"},
    "kobi": {"type": "gallery", "label": "Front frames"},
    "tamu": {"type": "json", "label": "Frame sidecar"},
    "rena": {"type": "gallery", "label": "Rear frames"},
}


def test_a_type_names_the_first_view_of_that_type_in_document_order():
    assert gui_refs.resolve_refs("click slot:@gallery 0.5 0.5", VIEWS) == "click slot:kobi 0.5 0.5"
    assert gui_refs.resolve_refs("assert_found slot:@gallery#2", VIEWS) == "assert_found slot:rena"
    assert (
        gui_refs.resolve_refs("webclick @json buttons.fold", VIEWS) == "webclick tamu buttons.fold"
    )


def test_a_reference_works_inside_compound_names_and_json():
    assert gui_refs.resolve_refs("click tile:@gallery#2:00003", VIEWS) == "click tile:rena:00003"
    assert (
        gui_refs.resolve_refs("click treeRow:@file_tree:runs/run-001/cam-a 0 0.5", VIEWS)
        == "click treeRow:fege:runs/run-001/cam-a 0 0.5"
    )
    assert (
        gui_refs.resolve_refs('print snap {"target": "@json"}', VIEWS)
        == 'print snap {"target": "tamu"}'
    )


def test_a_quoted_label_after_a_name_prefix_or_after_an_at_sign():
    assert (
        gui_refs.resolve_refs('hover slot:"Rear frames" 0.5 0.3', VIEWS)
        == "hover slot:rena 0.5 0.3"
    )
    assert (
        gui_refs.resolve_refs('params @"Frame sidecar" {"x": 1}', VIEWS) == 'params tamu {"x": 1}'
    )
    # a JSON key-value pair is not a label: the character before the colon is a quote
    assert gui_refs.resolve_refs('call x {"a":"Files"}', VIEWS) == 'call x {"a":"Files"}'


def test_lines_without_references_are_left_alone():
    assert gui_refs.resolve_refs("click slot:kobi", VIEWS) == "click slot:kobi"
    assert gui_refs.has_refs("click slot:kobi") is False
    assert gui_refs.has_refs("click slot:@gallery") is True


def test_a_reference_to_no_view_raises_with_what_is_there():
    with pytest.raises(gui_refs.Unresolved, match="no html view"):
        gui_refs.resolve_refs("wait_until slot:@html", VIEWS)
    with pytest.raises(gui_refs.Unresolved, match="2 gallery views"):
        gui_refs.resolve_refs("assert_found slot:@gallery#3", VIEWS)
    with pytest.raises(gui_refs.Unresolved, match="no view labelled 'Notes'"):
        gui_refs.resolve_refs('assert_found slot:"Notes"', VIEWS)


def test_tokens_keep_a_quoted_label_in_one_word():
    assert gui_refs.tokens('wait_until slot:"Front frames" 30000') == [
        "wait_until",
        'slot:"Front frames"',
        "30000",
    ]
    assert gui_refs.tokens('assert_missing slot:@"Rear frames"#2') == [
        "assert_missing",
        'slot:@"Rear frames"#2',
    ]
