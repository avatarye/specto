"""Edits (#98, ADR 0013 amendment): a partial submit, a JSON Merge Patch (RFC 7396) over
the live canvas document. The edit has the document's own shape: objects merge key by
key, any other value replaces (lists whole), ``null`` removes. Whatever the edit does
not name stays as it is live.

    merged = merge_edits(live.data, {"views": {"kobi": {"layout": {"position": [330, 0]}}}})

``LiveCanvas.submit(edits=...)`` merges and then runs the whole-document submit on the
result, so validation, locks, quotas, the stored patch and undo do not change form.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from typing import Any

from .canvas_document import CanvasInvalid, Problem, parse_canvas_text

#: what an edit may name at the top level; ``project`` only for its label
EDITABLE = ("views", "links", "root_view", "project")
#: refused with their reason: the schema is the app's, the window frame the user's
REFUSED = {
    "schema_version": "the schema version is the app's",
    "window_frame": "the window frame follows the user's window and pan",
}


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7396 MergePatch: a new value, neither argument is changed."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge_patch(out.get(key), value)
    return out


def already_removed(
    live: dict[str, Any], edits: dict[str, Any] | str, retired: Iterable[str]
) -> list[str]:
    """Views and links ``edits`` removes (``null``) that the canvas had once and no longer
    has (``retired``: the user closed it during the turn, say): nothing to do for them,
    so the edit takes them as done rather than refusing them (#169)."""
    if isinstance(edits, str):
        try:
            edits = json.loads(edits)
        except ValueError:
            return []  # merge_edits reports it
    if not isinstance(edits, dict):
        return []
    gone = set(retired)
    out: list[str] = []
    for section in ("views", "links"):
        entries = edits.get(section)
        if not isinstance(entries, dict):
            continue
        have = live.get(section) or {}
        out += [oid for oid, v in entries.items() if v is None and oid not in have and oid in gone]
    return out


def merge_edits(
    live: dict[str, Any], edits: dict[str, Any] | str, *, retired: Iterable[str] = ()
) -> dict[str, Any]:
    """The document ``edits`` makes of ``live`` (a deep copy; ``live`` is untouched).
    A view removed with ``null`` takes along every link into or out of it and the root
    view when it was that view, like the close gesture (``gestures.close_view``); a
    link or root view the edit itself writes is left to validation. JSON text is
    accepted (keys written twice are caught). Raises ``CanvasInvalid`` for an edit that
    is no object or names what an edit may not change (``edit_problems``); removing a
    ``retired`` id (``already_removed``) is no problem, it is already done."""
    if isinstance(edits, str):
        data, problems = parse_canvas_text(edits)
        if data is None:
            raise CanvasInvalid([_not_an_object(problems[0].message)])
        edits = data
    else:
        problems = []
    problems += edit_problems(edits)
    if not problems:
        problems += unknown_ids(live, edits, set(already_removed(live, edits, retired)))
    if problems:
        raise CanvasInvalid(problems)
    merged = merge_patch(copy.deepcopy(live), edits)
    views = edits.get("views")
    gone = {vid for vid, v in views.items() if v is None} if isinstance(views, dict) else set()
    if not gone:
        return merged
    named = edits.get("links") if isinstance(edits.get("links"), dict) else {}
    links = merged.get("links")
    if isinstance(links, dict):
        merged["links"] = {
            lid: link for lid, link in links.items() if lid in named or not (_ends(link) & gone)
        }
    if "root_view" not in edits and merged.get("root_view") in gone:
        merged["root_view"] = None
    return merged


def edit_problems(edits: Any) -> list[Problem]:
    """What an edit may not do: be anything but an object, name a top-level key other
    than views, links, root_view and project.label, or remove every view or link at
    once (``views: null``)."""
    if not isinstance(edits, dict):
        return [_not_an_object("the edit is not a JSON object")]
    out: list[Problem] = []
    for key, value in edits.items():
        if key in REFUSED:
            out.append(
                Problem(
                    path=f"/{key}",
                    message=f"an edit cannot change {key}: {REFUSED[key]}",
                    fix=f"leave {key} out of the edit",
                )
            )
        elif key not in EDITABLE:
            out.append(
                Problem(
                    path=f"/{key}",
                    message=f"{key!r} is not a key of the canvas document",
                    fix="an edit names views, links, root_view or project.label",
                )
            )
        elif key in ("views", "links") and not isinstance(value, dict):
            out.append(
                Problem(
                    path=f"/{key}",
                    message=f"{key}: {json.dumps(value)} would replace every one of them",
                    fix=f'name the {key} that change by id: {{"{key}": {{id: ...}}}}; '
                    "null on one id removes it",
                )
            )
        elif key == "project":
            if not isinstance(value, dict):
                out.append(
                    Problem(
                        path="/project",
                        message="the project entry cannot be removed or replaced",
                        fix='to rename it: {"project": {"label": "..."}}',
                    )
                )
            else:
                out += [
                    Problem(
                        path=f"/project/{k}",
                        message=f"an edit cannot change project.{k}",
                        fix="an edit changes only project.label",
                    )
                    for k in value
                    if k != "label"
                ]
    return out


def unknown_ids(
    live: dict[str, Any], edits: dict[str, Any], done: set[str] | None = None
) -> list[Problem]:
    """Ids an edit names that the canvas does not have, where that cannot be a new
    object: removing one (null), or changing fields of a view without a ``type`` (a
    misspelt name, ``kobu`` for ``kobi``) or a link without a ``kind``. A new object
    goes in whole, under a placeholder key. ``done``: removals already done (the id
    is gone already, ``already_removed``), which are no problem."""
    done = done or set()
    out: list[Problem] = []
    for section, what, required in (("views", "view", "type"), ("links", "link", "kind")):
        entries = edits.get(section)
        if not isinstance(entries, dict):
            continue
        have = list(live.get(section) or {})
        for oid, entry in entries.items():
            if oid in have or (isinstance(entry, dict) and required in entry):
                continue
            if entry is None and oid in done:
                continue
            names = ", ".join(have) or "none"
            out.append(
                Problem(
                    path=f"/{section}/{oid}",
                    message=f"no {what} {oid!r} on the canvas to "
                    + ("remove" if entry is None else "change"),
                    fix=f"{section} on the canvas: {names}; a new {what} goes in whole "
                    f"({required} included) under a placeholder key such as new1",
                )
            )
    return out


def _not_an_object(why: str) -> Problem:
    return Problem(
        path="",
        message=f"{why}; edits are a JSON Merge Patch over the canvas document",
        fix="write an object in the document's own shape naming only what changes, e.g. "
        '{"views": {"kobi": {"layout": {"position": [330, 0]}}}}; null removes',
    )


def _ends(link: Any) -> set[str]:
    """The views a link (JSON form) runs between."""
    if not isinstance(link, dict):
        return set()
    return {
        end["view"]
        for end in (link.get("source"), link.get("target"))
        if isinstance(end, dict) and isinstance(end.get("view"), str)
    }


class _Missing:
    """An absent key or object, apart from ``None`` (a JSON null is a value)."""

    def __repr__(self) -> str:
        return "MISSING"


#: what ``three_way`` takes and returns for an object or key that is not there
MISSING: Any = _Missing()


#: the paths whose keys are fields of their own: the object itself, its layout and its
#: params. Anything deeper (a link end ``{view, port}``, ``layout.floating``, a param's
#: value) is one value: half of one side's and half of the other's would be neither
FIELD_PARENTS = frozenset({"", "layout", "params"})


def field_paths(entry: Any) -> set[str]:
    """The fields an object (or an edit of one) names, as ``three_way`` merges them:
    ``label``, ``layout.position``, ``params.tile``."""
    if not isinstance(entry, dict):
        return set()
    out: set[str] = set()
    for key, value in entry.items():
        if key in FIELD_PARENTS and isinstance(value, dict):
            out |= {f"{key}.{sub}" for sub in value}
        else:
            out.add(key)
    return out


def field_at(entry: Any, path: str) -> Any:
    """The value of one ``field_paths`` field (``MISSING`` when absent)."""
    for key in path.split("."):
        if not isinstance(entry, dict) or key not in entry:
            return MISSING
        entry = entry[key]
    return entry


def three_way(base: Any, live: Any, agent: Any, path: str = "") -> tuple[Any, list[str]]:
    """Merge the agent's copy of one object with the live one against the turn-start copy
    (SPEC-18): a field only one side changed takes that side's value, a field both
    changed alike is kept, the object, its layout and its params merge field by field
    (``FIELD_PARENTS``), and anything else (a value both changed differently, or one side
    removed what the other changed) is a conflict. Returns the merged value (``MISSING``:
    absent) and the conflicting field paths (dotted, ``""`` for the whole object)."""
    if agent == base:
        return live, []
    if live in (base, agent):
        return agent, []
    if path in FIELD_PARENTS and all(isinstance(v, dict) for v in (base, live, agent)):
        merged: dict[str, Any] = {}
        conflicts: list[str] = []
        for key in dict.fromkeys([*base, *live, *agent]):
            value, clash = three_way(
                base.get(key, MISSING),
                live.get(key, MISSING),
                agent.get(key, MISSING),
                f"{path}.{key}" if path else key,
            )
            conflicts += clash
            if value is not MISSING:
                merged[key] = value
        return merged, conflicts
    return live, [path]


__all__ = [
    "EDITABLE",
    "MISSING",
    "REFUSED",
    "already_removed",
    "edit_problems",
    "field_at",
    "field_paths",
    "merge_edits",
    "merge_patch",
    "three_way",
    "unknown_ids",
]
