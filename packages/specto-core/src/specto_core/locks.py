"""Locks (HLD section 6, step 5; glossary: lock).

``locked: true`` on a view lives in the canvas document. A submit by anyone but the
user that would move, resize, delete, unlock or relink a locked view is refused at
submit (``lock_problems``, one problem per offence, each with the holder and the way
out: ask the user). The user's own gestures pass: the lock is theirs.

A view the user floated (``layout.floating.by: user``, ADR 0021) is held the same way
for its place: the agent may unfloat it but not move or resize it while it floats,
and only the user's Float gesture writes ``by: user``.

Collapsing and expanding (``layout.collapsed``, #114) are neither a move nor a resize:
the agent may collapse a locked view and expand it again.
"""

from __future__ import annotations

from typing import Any

from .canvas_document import Problem

HOLDER = "the user"


def lock_problems(old: dict[str, Any], new: dict[str, Any], origin: str) -> list[Problem]:
    """What a submit from ``origin`` does to the views locked in the live document ``old``
    that a lock forbids. Empty for the user (``is_user_origin``)."""
    if is_user_origin(origin):
        return []
    out: list[Problem] = _float_problems(old, new)
    new_views = new.get("views") or {}
    new_links = new.get("links") or {}
    for vid, view in (old.get("views") or {}).items():
        if not view.get("locked"):
            continue
        name = f"{vid} ({view.get('label')})" if view.get("label") else vid
        after = new_views.get(vid)
        ask = f"ask the user to unlock {vid} first, or leave {vid} as it is"
        if after is None:
            out.append(
                Problem(
                    path=f"/views/{vid}",
                    message=f"view {name} is locked by {HOLDER}: it cannot be deleted",
                    fix=f"keep {vid} and its links in the document; {ask}",
                )
            )
            continue
        if _placement(after) != _placement(view):
            what = (
                "moved"
                if after.get("layout", {}).get("size") == view["layout"]["size"]
                else (
                    "resized"
                    if after.get("layout", {}).get("position") == view["layout"]["position"]
                    else "moved or resized"
                )
            )
            out.append(
                Problem(
                    path=f"/views/{vid}/layout",
                    message=f"view {name} is locked by {HOLDER}: it cannot be {what}",
                    fix=f"submit {vid} with its layout unchanged "
                    f"(position {view['layout']['position']}, size {view['layout']['size']}); "
                    f"{ask}",
                )
            )
        if not after.get("locked"):
            out.append(
                Problem(
                    path=f"/views/{vid}/locked",
                    message=f"view {name} is locked by {HOLDER}: the lock is not yours to remove",
                    fix=f"keep locked: true on {vid}; {ask}",
                )
            )
        for lid, link in (old.get("links") or {}).items():
            if link["target"]["view"] != vid:
                continue
            link_after = new_links.get(lid)
            if link_after is None:
                out.append(
                    Problem(
                        path=f"/links/{lid}",
                        message=f"link {lid} feeds locked view {name}: it cannot be removed",
                        fix=f"keep {lid} as it is; {ask}",
                    )
                )
            elif (
                link_after.get("source") != link["source"]
                or link_after.get("target") != link["target"]
            ):
                out.append(
                    Problem(
                        path=f"/links/{lid}",
                        message=f"link {lid} feeds locked view {name}: it cannot be relinked",
                        fix=f"keep source {link['source']['view']}.{link['source']['port']} -> "
                        f"target {link['target']['view']}.{link['target']['port']} on {lid}; {ask}",
                    )
                )
    return out


def _placement(view: dict[str, Any]) -> dict[str, Any]:
    """A view's layout as a lock holds it: everything but ``collapsed``."""
    return {k: v for k, v in (view.get("layout") or {}).items() if k != "collapsed"}


def _float_problems(old: dict[str, Any], new: dict[str, Any]) -> list[Problem]:
    """A non-user submit's offences against the float layer: moving or resizing a view
    the user floated (unless a lock already says so), or claiming a float as the user's."""
    out: list[Problem] = []
    old_views = old.get("views") or {}
    for vid, after in (new.get("views") or {}).items():
        lay = after.get("layout") or {}
        floating = lay.get("floating")
        if not floating:
            continue
        view = old_views.get(vid) or {}
        was = (view.get("layout") or {}).get("floating") or {}
        name = f"{vid} ({view.get('label')})" if view.get("label") else vid
        if was.get("by") == "user":
            same_place = {k: floating.get(k) for k in ("corner", "offset")} == {
                k: was.get(k) for k in ("corner", "offset")
            }
            if not view.get("locked") and (
                not same_place or lay.get("size") != view["layout"]["size"]
            ):
                out.append(
                    Problem(
                        path=f"/views/{vid}/layout",
                        message=f"view {name} was floated by {HOLDER}: it cannot be moved "
                        "or resized while it floats",
                        fix=f"submit {vid} with corner {was['corner']}, offset "
                        f"{was['offset']} and size {view['layout']['size']} unchanged; "
                        f"you may unfloat it (floating: null), or ask the user to move it",
                    )
                )
            if floating.get("by") != "user":
                out.append(
                    Problem(
                        path=f"/views/{vid}/layout/floating/by",
                        message=f"view {name} was floated by {HOLDER}: the float stays theirs",
                        fix=f"keep by: user on {vid}'s floating, or unfloat it",
                    )
                )
        elif floating.get("by") == "user":
            out.append(
                Problem(
                    path=f"/views/{vid}/layout/floating/by",
                    message=f"view {name}: only the user floats a view as theirs",
                    fix=f"write by: agent (or leave by out) on {vid}'s floating",
                )
            )
    return out


def is_user_origin(origin: str) -> bool:
    return origin == "user" or origin.startswith("user:")
