"""What a port's hover says (#81, ADR 0018; #127, Figma page 05 section 3): a port tip
is a small box of two sections, each led by an icon. The data section says what flows
(the port's words, with its type when the words do not already say it) and from or to
whom; the filter section says what the link keeps, in the link summary's words, and is
left out when there is no filter (every output, an input whose link has no summary).
View names are links (``parts`` with a ``view``): clicking one jumps to that view, the
project source's name to the root node. A link dot's tip names its ends, also links, and
what a click does. The root node's tip says all files and the count. Qt-free."""

from __future__ import annotations

from specto_app.canvas.ports_info import (
    input_ports,
    link_tip,
    output_linked,
    output_tip,
    project_tip,
    unlinked_input_tip,
)

SENTENCE = "The gallery shows every picture in the pics folder."


def doc() -> dict:
    return {
        "views": {
            "fege": {"type": "file_tree"},
            "kobi": {"type": "gallery"},
            "tamu": {"type": "gallery"},
            "pola": {"type": "gallery"},
        },
        "links": {
            "l1": {
                "kind": "intaker",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "fege", "port": "data"},
            },
            "l2": {
                "kind": "intaker",
                "label": "pictures",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "kobi", "port": "data"},
                "summary": {"sentence": SENTENCE},
            },
            "l3": {
                "kind": "intaker",
                "label": "picked files",
                "source": {"view": "fege", "port": "selected_files"},
                "target": {"view": "tamu", "port": "data"},
            },
            "l4": {
                "kind": "follow",
                "two_way": True,
                "source": {"view": "kobi", "port": "selection"},
                "target": {"view": "tamu", "port": "selection"},
                "summary": {"sentence": "Picking in one gallery picks in the other."},
            },
        },
    }


def text(*parts: str) -> dict:
    return {"text": "".join(parts)}


def view(name: str) -> dict:
    return {"text": name, "view": name}


def test_an_input_circle_per_linked_input_in_document_order_with_the_upstream_view():
    assert input_ports(doc(), "pola") == []
    tree = input_ports(doc(), "fege")
    assert [(p["link"], p["port"], p["type"], p["from"]) for p in tree] == [
        ("l1", "data", "collection", "project")
    ]
    # the project source's files; the words say "files", so no type; no summary: no
    # filter section; the project's name is a link to the root node
    assert tree[0]["tip"] == {
        "what": "all files",
        "parts": [text("from "), view("project")],
        "filter": "",
    }
    kobi = input_ports(doc(), "kobi")
    assert [(p["link"], p["from"], p["twoWay"]) for p in kobi] == [
        ("l2", "project", False),
        ("l4", "tamu", True),  # a two-way link is a filter on both ends
    ]
    # the summary is the filter, its closing full stop gone
    assert kobi[0]["tip"] == {
        "what": "all files",
        "parts": [text("from "), view("project")],
        "filter": "The gallery shows every picture in the pics folder",
    }
    # the words do not say "selection": the type follows in brackets
    assert kobi[1]["tip"] == {
        "what": "picked items (selection)",
        "parts": [text("two-way with "), view("tamu")],
        "filter": "Picking in one gallery picks in the other",
    }
    tamu = input_ports(doc(), "tamu")
    assert [(p["link"], p["from"]) for p in tamu] == [("l3", "fege"), ("l4", "kobi")]
    # a label is not a summary
    assert tamu[0]["tip"] == {
        "what": "picked files",
        "parts": [text("from "), view("fege")],
        "filter": "",
    }
    assert tamu[1]["tip"]["parts"] == [text("two-way with "), view("kobi")]


def test_the_output_circle_says_what_it_emits_and_where_it_goes():
    d = doc()
    assert output_linked(d, "fege") and output_linked(d, "kobi")
    assert output_linked(d, "tamu")  # the two-way link emits from this end too
    assert not output_linked(d, "pola")
    # all files come from the project source (#83): the tree's one output is its picks;
    # an output does not filter: no filter section
    assert output_tip(d, "fege") == {
        "what": "picked files",
        "parts": [text("to "), view("tamu")],
        "filter": "",
    }
    # the linked outputs only; a two-way link's far end counts as a target
    assert output_tip(d, "kobi") == {
        "what": "picked items (selection)",
        "parts": [text("to "), view("tamu")],
        "filter": "",
    }
    assert output_tip(d, "tamu")["parts"] == [text("to "), view("kobi")]
    # nothing linked: every output, and says so
    assert output_tip(d, "pola") == {
        "what": "picked items (selection), time",
        "parts": [text("not linked")],
        "filter": "",
    }
    assert output_tip(d, "nope") == {}


def test_an_output_feeding_several_views_names_them_all():
    d = doc()
    d["views"]["zila"] = {"type": "image"}
    d["links"]["l5"] = {
        "kind": "follow",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "zila", "port": "selection"},
    }
    assert output_tip(d, "kobi")["parts"] == [text("to "), view("tamu"), text(", "), view("zila")]


def test_an_unlinked_input_circle_asks_for_a_link():
    assert unlinked_input_tip(doc(), "pola") == {
        "what": "no input yet",
        "parts": [text("ask the agent to link one")],
        "filter": "",
    }


def test_the_root_node_tip_says_all_files_and_the_count():
    assert project_tip(412) == {
        "what": "all files · 412",
        "parts": [text("the project folder"), text(" · double-click: the scratch pad")],
        "filter": "",
    }
    assert project_tip(12345)["what"] == "all files · 12,345"
    # before the project scan has landed: no count yet
    assert project_tip(None)["what"] == "all files"


def test_a_link_dot_tip_names_its_ends_as_links_and_what_a_click_does():
    d = doc()
    assert link_tip(d, "l3") == {
        "what": "",
        "parts": [view("fege"), text(" → "), view("tamu"), text(" · click to open the link")],
        "filter": "",
    }
    assert link_tip(d, "l4")["parts"][1] == text(" ↔ ")
    assert link_tip(d, "l1")["parts"][0] == view("project")  # the root node
    assert link_tip(d, "l9") == {}
