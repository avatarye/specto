"""What a view's port circles show and say (#81, ADR 0018; glossary: port, port type,
identicon, link summary). Qt-free.

``input_ports`` lists one input circle per linked input of a view (the upstream view,
whose identicon the circle shows, and the hover tip); ``output_linked`` and
``output_tip`` do the same for the view's one output circle; ``unlinked_input_tip`` is
the hollow circle's tip, ``project_tip`` the root node's and ``link_tip`` a link node's.
A port tip is a ``Tip`` (#127, Figma page 05 section 3): a data section (``what`` flows,
with its type when the words do not say it, then ``parts``: from or to whom) over a
filter section (``filter``: what the link keeps, in the link summary's words), which is
empty, and not drawn, when there is no filter: every output, an input whose link has no
summary. In ``parts`` a view's name is a link (``{"text", "view"}``): clicking it jumps
to that view, the project source's to the root node. The link summary lives in the
input tip, not on the link node, whose tip names its ends.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, TypedDict

from specto_core.ports import PROJECT_ID, PROJECT_PORTS, VIEW_PORTS

Direction = Literal["in", "out"]

#: what joins a tip line's parts (#121)
SEP = " · "
#: what an output port carries, in the user's words (a port not listed reads its name)
PORT_WORDS = {
    "all_files": "all files",
    "selected_files": "picked files",
    "selection": "picked items",
    "content": "file contents",
}
#: the root node's tip names what it is (#127)
PROJECT_WORDS = "the project folder"
#: and what its double-click opens (#184)
PROJECT_HINT = " · double-click: the scratch pad"
LINK_TIP_ACTION = "click to open the link"


class Part(TypedDict, total=False):
    """A run of a tip line: plain ``text``, or a view's name with its ``view`` id, a
    link to it."""

    text: str
    view: str


class Tip(TypedDict):
    """A port tip (#127): the data section (``what`` over ``parts``) and the filter
    section's one line (``""``: no filter section)."""

    what: str
    parts: list[Part]
    filter: str


def _text(text: str) -> Part:
    return {"text": text}


def _views(head: str, view_ids: Iterable[str]) -> list[Part]:
    """``head`` then the views' names as links, joined by commas."""
    parts: list[Part] = [_text(head)]
    for i, vid in enumerate(view_ids):
        if i:
            parts.append(_text(", "))
        parts.append({"text": vid, "view": vid})
    return parts


UNLINKED_INPUT_TIP: Tip = {
    "what": "no input yet",
    "parts": [_text("ask the agent to link one")],
    "filter": "",
}


def _ports_of(view: dict[str, Any] | None, direction: Direction) -> dict[str, str]:
    if view is None:
        decl = PROJECT_PORTS
    else:
        decl = VIEW_PORTS.get(view.get("type", ""))
        if decl is None:
            return {}
    return dict(decl.inputs if direction == "in" else decl.outputs)


def port_words(port: str) -> str:
    """What a port carries, in a few plain words: ``selected_files`` -> picked files."""
    return PORT_WORDS.get(port, port.replace("_", " "))


def _what(port: str, ptype: str) -> str:
    """A tip's DATA line: the port's words, then its type in brackets unless the words
    already say it (``picked files``, ``time``, ``file contents``)."""
    words = port_words(port)
    return words if not ptype or ptype in words else f"{words} ({ptype})"


# ---------------------------------------------------------------------------------------
# The port circles (#81): one input circle per linked input, one output circle, and what
# their hovers say
# ---------------------------------------------------------------------------------------


def _link_summary(link: dict[str, Any]) -> str:
    """The link's sentence without its closing full stop: the filter in plain words."""
    text = link.get("summary", {}).get("sentence", "").strip()
    return text[:-1] if text.endswith(".") and not text.endswith("..") else text


def _types_of(data: dict[str, Any], view_id: str, direction: Direction) -> dict[str, str]:
    view = data["views"].get(view_id) if view_id != PROJECT_ID else None
    if view is None and view_id != PROJECT_ID:
        return {}
    return _ports_of(view, direction)


def _distinct(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def input_ports(data: dict[str, Any], view_id: str) -> list[dict[str, Any]]:
    """The input circles of a view, in document order of their links: ``{link, port,
    type, from, twoWay, tip}``. A link feeding the view is one; a two-way link whose
    source is the view is one too (a filter on both ends), its upstream the other end.
    The tip (#127): what the upstream port emits ``from <view>`` (``two-way with
    <view>``), filtered by the link summary, if it has one."""
    types = _types_of(data, view_id, "in")
    out: list[dict[str, Any]] = []
    for lid, link in data["links"].items():
        two_way = bool(link.get("two_way"))
        src, dst = link["source"], link["target"]
        if dst["view"] == view_id:
            port, other, carried = dst["port"], src["view"], src["port"]
        elif two_way and src["view"] == view_id:
            port, other, carried = src["port"], dst["view"], dst["port"]
        else:
            continue
        head = "two-way with" if two_way else "from"
        carried_type = _types_of(data, other, "out").get(carried, "")
        tip: Tip = {
            "what": _what(carried, carried_type),
            "parts": _views(f"{head} ", [other]),
            "filter": _link_summary(link),
        }
        out.append(
            {
                "link": lid,
                "port": port,
                "type": types.get(port, ""),
                "from": other,
                "twoWay": two_way,
                "tip": tip,
            }
        )
    return out


def unlinked_input_tip(data: dict[str, Any], view_id: str) -> Tip:
    """The hollow input circle's tip: nothing feeds the view yet."""
    return {**UNLINKED_INPUT_TIP, "parts": list(UNLINKED_INPUT_TIP["parts"])}


def _outgoing(data: dict[str, Any], view_id: str) -> list[tuple[str, str]]:
    """``(port, other view)`` for every link that flows out of the view: a link from
    it, or a two-way link into it (its far end)."""
    out = []
    for ln in data["links"].values():
        if ln["source"]["view"] == view_id:
            out.append((ln["source"]["port"], ln["target"]["view"]))
        elif ln.get("two_way") and ln["target"]["view"] == view_id:
            out.append((ln["target"]["port"], ln["source"]["view"]))
    return out


def output_linked(data: dict[str, Any], view_id: str) -> bool:
    """True when something flows out of the view: a link from it, or a two-way link
    into it."""
    return bool(_outgoing(data, view_id))


def output_tip(data: dict[str, Any], view_id: str) -> Tip | dict[str, str]:
    """What each linked output port emits (every output when none is linked) ``to
    <targets>``, or ``not linked``. An output does not filter (each link into a
    downstream input does): no filter section. ``{}`` for a view with no output."""
    types = _types_of(data, view_id, "out")
    if not types:
        return {}
    outgoing = _outgoing(data, view_id)
    ports = _distinct(p for p, _ in outgoing) or list(types)
    what = ", ".join(_what(p, types.get(p, "")) for p in ports)
    if not outgoing:
        return {"what": what, "parts": [_text("not linked")], "filter": ""}
    return {"what": what, "parts": _views("to ", _distinct(v for _, v in outgoing)), "filter": ""}


def project_tip(count: int | None) -> Tip:
    """The root node's tip (#127): all files and how many, once the project scan has
    landed (``None`` before)."""
    what = "all files" + (f"{SEP}{count:,}" if count is not None else "")
    return {"what": what, "parts": [_text(PROJECT_WORDS), _text(PROJECT_HINT)], "filter": ""}


def link_tip(data: dict[str, Any], link_id: str) -> Tip | dict[str, str]:
    """A link node's tip, one line (#127): ``<source> → <target>`` (``↔`` for a two-way
    link), both names links, and what a click does; no data or filter section (the
    filter is the input port's to say). ``{}`` for a link not in the document."""
    link = data["links"].get(link_id)
    if link is None:
        return {}
    arrow = "↔" if link.get("two_way") else "→"
    s, t = link["source"]["view"], link["target"]["view"]
    parts: list[Part] = [
        {"text": s, "view": s},
        _text(f" {arrow} "),
        {"text": t, "view": t},
        _text(f"{SEP}{LINK_TIP_ACTION}"),
    ]
    return {"what": "", "parts": parts, "filter": ""}


__all__ = [
    "LINK_TIP_ACTION",
    "PORT_WORDS",
    "PROJECT_HINT",
    "PROJECT_WORDS",
    "SEP",
    "UNLINKED_INPUT_TIP",
    "Direction",
    "Part",
    "Tip",
    "input_ports",
    "link_tip",
    "output_linked",
    "output_tip",
    "port_words",
    "project_tip",
    "unlinked_input_tip",
]
