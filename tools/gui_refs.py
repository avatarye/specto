"""View references in gui_drive flow lines (#101): a flow names a view by its type or its
label, and gui_drive swaps in the view's name from the live document before it parses
the line, because view names are generated (``fresh_name``) and a flow that hard-codes
one breaks when anything changes the canvas it was named on.

    @html          the first view of type html, in document order (a new view goes last)
    @gallery#2     the second gallery
    "Front frames" a label, right after a name prefix: slot:"Front frames", header:"Notes"
    @"Front frames"   a label anywhere else (webclick, params, a JSON target); #n works too

The swap is textual and covers the whole line, so a reference works wherever a view name
does: ``tile:@gallery:00003``, ``treeRow:@file_tree:runs/cam-a``, ``{"target": "@json"}``.
A type is lower case, so snap's own ``@<Label>`` target is spelled ``@"Label"`` in a flow.
"""

from __future__ import annotations

import re
from typing import Any

_LABEL_AT = re.compile(r'@"([^"]+)"(?:#(\d+))?')
# a label right after a name prefix (slot:"x"); in JSON ("a":"x") a quote precedes the colon
_LABEL_COLON = re.compile(r'(?<=[A-Za-z]:)"([^"]+)"(?:#(\d+))?')
_TYPE = re.compile(r"(?<![\w@])@([a-z0-9_]+)(?:#(\d+))?")


class Unresolved(LookupError):
    """A reference names no view on the canvas (yet: wait_until polls again)."""


def tokens(line: str) -> list[str]:
    """The line's words, a quoted label staying inside its word (slot:"Front frames")."""
    return re.findall(r'(?:[^\s"]+|"[^"]*")+', line)


def has_refs(line: str) -> bool:
    return any(p.search(line) for p in (_LABEL_AT, _LABEL_COLON, _TYPE))


def _pick(hits: list[str], nth: str | None, what: str, views: dict[str, Any]) -> str:
    n = int(nth or 1)
    if len(hits) >= n >= 1:
        return hits[n - 1]
    have = ", ".join(f"{vid} ({v.get('type')}, {v.get('label')!r})" for vid, v in views.items())
    raise Unresolved(
        f"{what}#{n}: {f'{len(hits)} {what} views' if hits else 'none'}; "
        f"on the canvas: {have or 'nothing'}"
    )


def resolve_refs(line: str, views: dict[str, Any]) -> str:
    """``line`` with every reference replaced by a view name from ``views`` (the
    document's ``views``: name -> {type, label, ...}). Raises ``Unresolved``."""

    def by_label(m: re.Match[str]) -> str:
        label = m.group(1)
        hits = [vid for vid, v in views.items() if v.get("label") == label]
        if not hits:
            raise Unresolved(
                f"no view labelled {label!r}; labels: "
                f"{', '.join(repr(v.get('label')) for v in views.values())}"
            )
        return _pick(hits, m.group(2), f"label {label!r}", views)

    def by_type(m: re.Match[str]) -> str:
        kind = m.group(1)
        hits = [vid for vid, v in views.items() if v.get("type") == kind]
        if not hits:
            raise Unresolved(
                f"no {kind} view; types: "
                f"{', '.join(sorted({str(v.get('type')) for v in views.values()}))}"
            )
        return _pick(hits, m.group(2), kind, views)

    line = _LABEL_AT.sub(by_label, line)
    line = _LABEL_COLON.sub(by_label, line)
    return _TYPE.sub(by_type, line)
