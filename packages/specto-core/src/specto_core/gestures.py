"""Human gestures as submits (spec #35 story 31, ADR 0013): move, resize, close, lock
and unlock a view, collapse and expand it (#114), float and unfloat it and drag it within
the window (#112), delete a
link, resize the window frame, open a gallery's image view or preview, open a file from
the file tree in a dedicated view or the contextual preview (``open_file``, #43), open
the scratch pad and an item of it in a view beside it (#184). Each one
copies the live document, edits it and submits it with ``origin="user"``, so a gesture
is one undo step on the same history as the agent's submits and the agent always sees
what the user changed. The shell (#40) and the overworld (#45) call these on gesture
end; snapping happens before."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .canvas_document import file_tree_of, fit_fresh_tree, next_id
from .errors import InvalidArgs, NotFound
from .float_layer import (
    anchor_at,
    canvas_point_under,
    frame_origin,
    frame_size,
    is_floating,
    shown_size,
)
from .kinds import PluginRegistry, default_plugins
from .live_canvas import LiveCanvas, SubmitResult
from .names import fresh_name
from .ports import CONTEXT_VIEWER, PROJECT_ID, PROJECT_PORT, file_input_of

IMAGE_GAP = 20.0  # canvas px between a gallery and the image view opened beside it
OPEN_GAP = IMAGE_GAP  # canvas px between the file tree and a view opened from it
OPEN_SIZE = (640.0, 480.0)  # a view opened from the file tree
#: the contextual view a file opens in (ADR 0020): the preview renders it by its kind
OPEN_TYPE = CONTEXT_VIEWER
#: the view a file no view type shows opens in (#43): "Not supported: <name> (<ext>)"
TEXT_TYPE = "text"
#: how ``open_file`` opens a file (#43, ADR 0023): in a dedicated view of its own, fed
#: that file alone, or in the contextual preview, which follows the tree's picks
OPEN_MODES: tuple[str, ...] = ("dedicated", "contextual")
SCRATCH_TYPE = "scratch"
SCRATCH_SIZE = (440.0, 520.0)  # a scratch pad the user opens


def _view(doc: dict[str, Any], view_id: str) -> dict[str, Any]:
    view = doc["views"].get(view_id)
    if view is None:
        raise NotFound(
            f"no view {view_id!r} on the canvas",
            fix=f"view ids: {', '.join(doc['views']) or 'none'}",
        )
    return view


def _name(view_id: str, view: dict[str, Any]) -> str:
    return view.get("label") or view_id


def move_view(live: LiveCanvas, view_id: str, position: tuple[float, float]) -> SubmitResult:
    return move_views(live, {view_id: position})


def move_views(live: LiveCanvas, positions: Mapping[str, tuple[float, float]]) -> SubmitResult:
    """Dragging one view of a multi-selection moves the whole group (#84): every view in
    ``positions`` lands at its new position in one submit, one undo step. Locked views
    move with the rest (a lock binds the agent, not the user)."""
    doc = live.document()
    names = []
    for view_id, position in positions.items():
        view = _view(doc, view_id)
        view["layout"]["position"] = list(position)
        names.append(_name(view_id, view))
    label = f"move {names[0]}" if len(names) == 1 else f"move {len(names)} views"
    return live.submit(doc, origin="user", label=label)


def resize_view(
    live: LiveCanvas,
    view_id: str,
    size: tuple[float, float],
    *,
    position: tuple[float, float] | None = None,
) -> SubmitResult:
    """Dragging a left or top border moves the origin as well: still one resize step."""
    doc = live.document()
    view = _view(doc, view_id)
    view["layout"]["size"] = list(size)
    if position is not None:
        view["layout"]["position"] = list(position)
    return live.submit(doc, origin="user", label=f"resize {_name(view_id, view)}")


def close_view(live: LiveCanvas, view_id: str) -> SubmitResult:
    """Remove a view with every link into or out of it; a closed root view leaves no
    root view."""
    doc = live.document()
    view = _view(doc, view_id)
    del doc["views"][view_id]
    doc["links"] = {
        lid: link
        for lid, link in doc["links"].items()
        if view_id not in (link["source"]["view"], link["target"]["view"])
    }
    if doc["root_view"] == view_id:
        doc["root_view"] = None
    return live.submit(doc, origin="user", label=f"close {_name(view_id, view)}")


def set_locked(live: LiveCanvas, view_id: str, locked: bool) -> SubmitResult:
    """The header's lock (spec #35 story 74): a locked view refuses the agent's moves,
    resizes, relinks and deletions until the user unlocks it. One undo step."""
    doc = live.document()
    view = _view(doc, view_id)
    view["locked"] = bool(locked)
    verb = "lock" if locked else "unlock"
    return live.submit(doc, origin="user", label=f"{verb} {_name(view_id, view)}")


def set_collapsed(live: LiveCanvas, view_id: str, collapsed: bool) -> SubmitResult:
    """The header's collapse (#114): the view shrinks to its title bar at the same width
    (its content paused, its links still delivering) or expands back to its stored size.
    One undo step."""
    doc = live.document()
    view = _view(doc, view_id)
    if collapsed:
        view["layout"]["collapsed"] = True
    else:
        view["layout"].pop("collapsed", None)
    verb = "collapse" if collapsed else "expand"
    return live.submit(doc, origin="user", label=f"{verb} {_name(view_id, view)}")


def _intakers_into(doc: dict[str, Any], view_id: str) -> list[str]:
    return [
        lid
        for lid, link in doc["links"].items()
        if link["kind"] == "intaker" and link["target"]["view"] == view_id
    ]


def auto_update_of(doc: dict[str, Any], view_id: str) -> bool | None:
    """The view's auto-update switch (#102): True when an intaker feeding it watches its
    sources, False when none does, None when no intaker feeds it (no switch)."""
    lids = _intakers_into(doc, view_id)
    if not lids:
        return None
    return any((doc["links"][lid].get("params") or {}).get("watch") for lid in lids)


def set_auto_update(live: LiveCanvas, view_id: str, on: bool) -> SubmitResult:
    """The header's auto-update switch (#102): ``params.watch`` on every intaker feeding
    the view, so files arriving in its sources resolve in the background (on) or wait
    for a refresh (off). One undo step."""
    doc = live.document()
    view = _view(doc, view_id)
    lids = _intakers_into(doc, view_id)
    if not lids:
        raise NotFound(
            f"no intaker feeds {_name(view_id, view)}, so it has no auto-update",
            fix="auto-update follows an intaker's sources; link one into the view's data",
        )
    for lid in lids:
        doc["links"][lid].setdefault("params", {})["watch"] = bool(on)
    state = "on" if on else "off"
    return live.submit(doc, origin="user", label=f"auto-update {_name(view_id, view)} {state}")


def delete_link(live: LiveCanvas, link_id: str) -> SubmitResult:
    doc = live.document()
    link = doc["links"].get(link_id)
    if link is None:
        raise NotFound(
            f"no link {link_id!r} on the canvas",
            fix=f"link ids: {', '.join(doc['links']) or 'none'}",
        )
    del doc["links"][link_id]
    return live.submit(doc, origin="user", label=f"delete link: {link['summary']['sentence']}")


def open_image_view(live: LiveCanvas, gallery_id: str) -> tuple[str, SubmitResult | None]:
    """Double-click or Enter on a gallery tile (spec #35 story 38): the gallery's image
    view. When one already follows this gallery's selection, its id comes back with no
    submit; otherwise one submit adds an image view beside the gallery, fed by a copy of
    the gallery's data link and following its selection both ways."""
    return _open_beside_gallery(live, gallery_id, "image")


def open_gallery_preview(live: LiveCanvas, gallery_id: str) -> tuple[str, SubmitResult | None]:
    """Double-click or Enter on a gallery tile that is not an image (#106): the gallery's
    preview, which renders the picked item by its kind; added and reused like the image
    view (``open_image_view``)."""
    return _open_beside_gallery(live, gallery_id, "preview")


#: what the view a gallery opens beside it is called: (label suffix, what it shows)
_BESIDE: dict[str, tuple[str, str]] = {"image": ("image", "frame"), "preview": ("preview", "item")}


def _open_beside_gallery(
    live: LiveCanvas, gallery_id: str, vtype: str
) -> tuple[str, SubmitResult | None]:
    doc = live.document()
    gallery = _view(doc, gallery_id)
    if gallery["type"] != "gallery":
        raise NotFound(
            f"{gallery_id} is a {gallery['type']}, not a gallery",
            fix=f"open the {vtype} from a gallery",
        )
    for link in doc["links"].values():
        if (
            link["kind"] == "follow"
            and link["source"] == {"view": gallery_id, "port": "selection"}
            and link["target"]["port"] == "selection"
            and doc["views"].get(link["target"]["view"], {}).get("type") == vtype
        ):
            return link["target"]["view"], None
    data = next(
        (
            ln
            for ln in doc["links"].values()
            if ln["target"] == {"view": gallery_id, "port": "data"}
        ),
        None,
    )
    name = _name(gallery_id, gallery)
    word, what = _BESIDE[vtype]
    shown = "The image view" if vtype == "image" else "The preview"
    vid = fresh_name(doc["views"], f"{word} of {gallery_id}")
    size = (float(gallery["layout"]["size"][0]), float(gallery["layout"]["size"][1]))
    doc["views"][vid] = {
        "type": vtype,
        "label": f"{name} {word}",
        # beside the gallery, below whatever already sits there (its image view, preview)
        "layout": {"position": _free_spot_beside(doc, gallery, size), "size": list(size)},
        "params": {},
        "locked": False,
    }
    if data is not None:
        lid = next_id(doc["links"], "l")
        doc["links"][lid] = {
            **copy.deepcopy(data),
            "label": f"{data.get('label') or 'frames'} ({word})",
            "target": {"view": vid, "port": "data"},
            "summary": {"sentence": f"{shown} shows the same items as {name}."},
        }
    doc["links"][next_id(doc["links"], "l")] = {
        "kind": "follow",
        "label": f"{word} of the picked {what}",
        "source": {"view": gallery_id, "port": "selection"},
        "target": {"view": vid, "port": "selection"},
        "two_way": True,
        "params": {"match": None},
        "summary": {"sentence": f"{shown} shows the {what} picked in {name}."},
    }
    return vid, live.submit(doc, origin="user", label=f"open {word} of {name}")


def open_scratch(live: LiveCanvas) -> tuple[str, SubmitResult | None]:
    """The project node's double-click (#184): the canvas's scratch pad. When there is
    one its id comes back with no submit; otherwise one submit adds it beside the file
    tree (at the file tree's place when there is none). It needs no link: the scratch pad
    is a source of its own."""
    doc = live.document()
    for vid, view in doc["views"].items():
        if view["type"] == SCRATCH_TYPE:
            return vid, None
    tree_id = file_tree_of(doc["views"])
    if tree_id is not None:
        position = _free_spot_beside(doc, doc["views"][tree_id], SCRATCH_SIZE)
    else:
        position = [80.0, 0.0]
    vid = fresh_name(doc["views"], "scratch pad")
    doc["views"][vid] = {
        "type": SCRATCH_TYPE,
        "label": "scratch pad",
        "layout": {"position": position, "size": list(SCRATCH_SIZE)},
        "params": {},
        "locked": False,
    }
    return vid, live.submit(doc, origin="user", label="open the scratch pad")


def open_from_scratch(
    live: LiveCanvas, scratch_id: str, vtype: str
) -> tuple[str, SubmitResult | None]:
    """A double-click on a scratch pad item (#184): an image in an image view, a file in a
    preview, beside the scratch pad, fed by a follow link off its items and following its
    selection both ways. One per scratch pad and type: an existing one comes back with no
    submit."""
    if vtype not in _BESIDE:
        raise InvalidArgs(f"a scratch item opens in an image view or a preview, not {vtype}")
    doc = live.document()
    pad = _view(doc, scratch_id)
    if pad["type"] != SCRATCH_TYPE:
        raise NotFound(f"{scratch_id} is a {pad['type']}, not the scratch pad")
    for link in doc["links"].values():
        if (
            link["kind"] == "follow"
            and link["source"] == {"view": scratch_id, "port": "selection"}
            and link["target"]["port"] == "selection"
            and doc["views"].get(link["target"]["view"], {}).get("type") == vtype
        ):
            return link["target"]["view"], None
    name = _name(scratch_id, pad)
    word, _what = _BESIDE[vtype]
    shown = "The image view" if vtype == "image" else "The preview"
    vid = fresh_name(doc["views"], f"{word} of {scratch_id}")
    size = OPEN_SIZE
    doc["views"][vid] = {
        "type": vtype,
        "label": f"{name} {word}",
        "layout": {"position": _free_spot_beside(doc, pad, size), "size": list(size)},
        "params": {},
        "locked": False,
    }
    doc["links"][next_id(doc["links"], "l")] = {
        "kind": "follow",
        "label": "scratch items",
        "source": {"view": scratch_id, "port": "items"},
        "target": {"view": vid, "port": "data"},
        "params": {"match": None},
        "summary": {"sentence": f"{shown} shows the items of {name}."},
    }
    doc["links"][next_id(doc["links"], "l")] = {
        "kind": "follow",
        "label": f"{word} of the picked item",
        "source": {"view": scratch_id, "port": "selection"},
        "target": {"view": vid, "port": "selection"},
        "two_way": True,
        "params": {"match": None},
        "summary": {"sentence": f"{shown} shows the item picked in {name}."},
    }
    return vid, live.submit(doc, origin="user", label=f"open {word} of {name}")


@dataclass(frozen=True)
class OpenedFile:
    """What ``open_file`` did: the view that shows the file and how it got there.
    Dedicated: ``created``, a new view fed that file alone (one submit), or ``focused``,
    the file's dedicated view was on the canvas already (no submit: the shell focuses
    and reveals it). Contextual: ``created``, a new preview on the tree's picks (one
    submit); ``repointed``, an existing preview's link now comes from the tree's picks
    (one submit); ``selected``, an existing preview already follows them; the file is
    picked in the tree each time (navigation). ``mode`` says which way it went: a
    contextual open of a kind the preview cannot show falls back to ``dedicated``."""

    view: str
    action: str
    submit: SubmitResult | None = None
    mode: str = "contextual"


def _link_into(doc: dict[str, Any], view_id: str, port: str) -> tuple[str, dict[str, Any]] | None:
    target = {"view": view_id, "port": port}
    return next(((lid, ln) for lid, ln in doc["links"].items() if ln["target"] == target), None)


def _shown_file_name(found: tuple[str, dict[str, Any]] | None) -> str | None:
    """The name of the one file a view's file link shows (its derived label), if any."""
    sources = found[1].get("params", {}).get("sources") if found is not None else None
    if not sources or len(sources) != 1:
        return None
    return PurePosixPath(sources[0]).name


def _picks_link(tree_id: str, tree: dict[str, Any], view_id: str) -> dict[str, Any]:
    """The selection intaker off ``tree_id``'s picks into ``view_id``'s data (#122)."""
    return {
        "kind": "intaker",
        "scope": "selection",
        "label": "picked files",
        "source": {"view": tree_id, "port": "selected_files"},
        "target": {"view": view_id, "port": "data"},
        "params": {"sources": ["."]},
        "summary": {"sentence": f"The preview shows the file picked in {_name(tree_id, tree)}."},
    }


Rect = tuple[float, float, float, float]


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _free_spot_beside(
    doc: dict[str, Any], tree: dict[str, Any], size: tuple[float, float]
) -> list[float]:
    """The first spot beside the tree (same top, one gap to the right) that no view
    overlaps, walking down past the views in the way."""
    (tx, ty), (tw, _th) = tree["layout"]["position"], tree["layout"]["size"]
    x, y = float(tx + tw + OPEN_GAP), float(ty)
    rects: list[Rect] = [
        (v["layout"]["position"][0], v["layout"]["position"][1], *v["layout"]["size"])
        for v in doc["views"].values()
        if not is_floating(v)  # a floating view takes no room on the canvas
    ]
    for _ in range(len(rects) + 1):
        hit = next((r for r in rects if _overlaps((x, y, *size), r)), None)
        if hit is None:
            break
        y = float(hit[1] + hit[3] + OPEN_GAP)
    return [x, y]


def dedicated_label(path: str, project_name: str | None = None) -> str:
    """The title of a dedicated view (#43): the file's name, a middle dot, its folder
    relative to the project; a file at the root shows the project's name, or "." when
    there is none."""
    p = PurePosixPath(path)
    folder = p.parent.as_posix()
    if folder == ".":
        folder = project_name or "."
    return f"{p.name} · {folder}"


def _file_feed(doc: dict[str, Any], view_id: str) -> dict[str, Any] | None:
    """The link that feeds ``view_id``'s file input (its intaker or read_contents)."""
    found = file_input_of(doc["views"][view_id]["type"])
    if found is None:
        return None
    hit = _link_into(doc, view_id, found[0])
    return hit[1] if hit is not None and hit[1]["kind"] == found[1] else None


def dedicated_view_of(doc: dict[str, Any], path: str) -> str | None:
    """The view dedicated to ``path`` (#43, ADR 0023), if the canvas has one: a view
    that is not the context viewer, whose file input is fed from the project source
    (``project.all_files``) with ``path`` as its one source. Recognised by its link, not
    by a mark on the view, so one the agent composed the same way counts."""
    for vid, view in doc["views"].items():
        if view["type"] == CONTEXT_VIEWER:
            continue
        feed = _file_feed(doc, vid)
        if feed is None or feed["source"] != {"view": PROJECT_ID, "port": PROJECT_PORT}:
            continue
        if feed.get("kind") == "intaker" and feed.get("scope") != "pinned":
            continue
        if (feed.get("params") or {}).get("sources") == [path]:
            return vid
    return None


def is_contextual(doc: dict[str, Any], view_id: str) -> bool:
    """Whether ``view_id`` is a contextual view (#43, ADR 0023): the context viewer
    (a preview) not fed fixed sources from the project source, so it follows a
    selection (the file tree's picks, a gallery's pick) or waits for one. The shell
    tags it "Contextual · follows selection" in its header and on its overworld card."""
    view = doc["views"].get(view_id)
    if view is None or view["type"] != CONTEXT_VIEWER:
        return False
    feed = _file_feed(doc, view_id)
    return feed is None or feed["source"] != {"view": PROJECT_ID, "port": PROJECT_PORT}


def open_file(
    live: LiveCanvas,
    tree_id: str,
    path: str,
    *,
    mode: str = "dedicated",
    prefer: Iterable[str] = (),
    origin: str = "user",
    plugins: PluginRegistry | None = None,
) -> OpenedFile:
    """Open ``path`` (relative to the tree's root) from file tree ``tree_id`` (#43, ADR
    0023), or the agent's ``canvas.open_file``.

    ``mode="dedicated"`` (a double-click): a view of its own for that one file, of the
    view type that shows the file's kind (``plugins.typed_view_for``; the usd view for a
    stage, the map for geojson, never the preview), fed by a pinned link from the
    project source to exactly that file, so later picks never change it; labelled
    ``<name> · <folder>``; placed beside the tree in the first free spot (one submit).
    A file no view type shows opens a ``text`` view saying ``Not supported: <name>
    (<ext>)`` the same way. A file whose dedicated view is on the canvas already comes
    back ``focused`` with no submit (the shell focuses and reveals it). The tree's picks
    are left alone.

    ``mode="contextual"`` (Ctrl+double-click): the preview, which follows the tree's
    picks (#106, #122): fed by a selection intaker off ``selected_files`` and the file
    is picked in the tree (navigation, no undo step), so it shows the file now and every
    later pick. A preview already on the canvas is reused: the first of ``prefer`` (the
    selected views) that is one, else the newest. Already on this tree's picks, the pick
    is the whole gesture; otherwise its data link is re-pointed at the picks (one
    submit). A re-pointed preview whose label is just the name of the file it showed
    (or no label) is named for the picks; a label the agent or user wrote stays (#86).
    Without one, a new preview is placed beside the tree (one submit). A kind the
    preview cannot render (a usd stage) opens dedicated instead, and a file no view
    shows the ``text`` view: the result's ``mode`` says so.

    ``plugins`` is the registry the kinds and view types are read from (the engine's);
    without one core's built-in manifests stand in."""
    if mode not in OPEN_MODES:
        raise InvalidArgs(
            f"open_file mode {mode!r} is not one of {', '.join(OPEN_MODES)}",
            fix="dedicated: a view of its own for the file; contextual: the preview on the picks",
        )
    reg = plugins if plugins is not None else default_plugins()
    doc = live.document()
    tree = _view(doc, tree_id)
    if tree["type"] != "file_tree":
        raise NotFound(
            f"{tree_id} is a {tree['type']}, not a file tree", fix="open files from a file tree"
        )
    if mode == "contextual":
        # the file is picked in the tree first (navigation): a Ctrl+double-click's own
        # taps toggled the row, and the file must end up selected either way
        live.remember(tree_id, {"selected": [path]})
        shown = reg.view(CONTEXT_VIEWER)
        kinds = reg.kinds_for_path(path)
        if shown is None or not kinds or not any(k in shown.accepts for k in kinds):
            tree = live.document()["views"][tree_id]
            return _open_dedicated(live, tree, path, origin=origin, plugins=reg)
        return _open_contextual(live, tree_id, path, prefer=prefer, origin=origin)
    return _open_dedicated(live, tree, path, origin=origin, plugins=reg)


def _open_dedicated(
    live: LiveCanvas,
    tree: dict[str, Any],
    path: str,
    *,
    origin: str,
    plugins: PluginRegistry,
) -> OpenedFile:
    doc = live.document()
    have = dedicated_view_of(doc, path)
    if have is not None:
        return OpenedFile(have, "focused", None, "dedicated")
    name = PurePosixPath(path).name
    label = dedicated_label(path, live.project.name if live.project is not None else None)
    vtype = plugins.typed_view_for(path) or TEXT_TYPE
    params: dict[str, Any] = {}
    if vtype == TEXT_TYPE:
        ext = PurePosixPath(path).suffix or "no extension"
        params["text"] = f"Not supported: {name} ({ext})"
    vid = fresh_name(doc["views"], path)
    doc["views"][vid] = {
        "type": vtype,
        "label": label,
        "layout": {"position": _free_spot_beside(doc, tree, OPEN_SIZE), "size": list(OPEN_SIZE)},
        "params": params,
        "locked": False,
    }
    port, kind = file_input_of(vtype) or ("data", "intaker")
    link: dict[str, Any] = {
        "kind": kind,
        "label": name,
        "source": {"view": PROJECT_ID, "port": PROJECT_PORT},
        "target": {"view": vid, "port": port},
        "params": {"sources": [path]},
        "summary": {"sentence": f"The {vtype} view shows {path}."},
    }
    if kind == "intaker":
        link["scope"] = "pinned"
    doc["links"][next_id(doc["links"], "l")] = link
    res = live.submit(doc, origin=origin, label=f"open {name}")
    return OpenedFile(vid, "created", res, "dedicated")


def _open_contextual(
    live: LiveCanvas,
    tree_id: str,
    path: str,
    *,
    prefer: Iterable[str],
    origin: str,
) -> OpenedFile:
    doc = live.document()
    tree = doc["views"][tree_id]
    label = f"{_name(tree_id, tree)} preview"
    same = [vid for vid, v in doc["views"].items() if v["type"] == OPEN_TYPE]
    target = next((vid for vid in prefer if vid in same), same[-1] if same else None)
    if target is not None:
        found = _link_into(doc, target, "data")
        if found is not None and found[1]["source"] == {"view": tree_id, "port": "selected_files"}:
            return OpenedFile(target, "selected")
        lid = found[0] if found is not None else next_id(doc["links"], "l")
        view = doc["views"][target]
        if not view.get("label") or view["label"] == _shown_file_name(found):
            view["label"] = label
            shown_in = target
        else:
            shown_in = view["label"]
        doc["links"][lid] = _picks_link(tree_id, tree, target)
        what = f"show the picks of {_name(tree_id, tree)} in {shown_in}"
        return OpenedFile(target, "repointed", live.submit(doc, origin=origin, label=what))
    vid = fresh_name(doc["views"], path)
    doc["views"][vid] = {
        "type": OPEN_TYPE,
        "label": label,
        "layout": {"position": _free_spot_beside(doc, tree, OPEN_SIZE), "size": list(OPEN_SIZE)},
        "params": {},
        "locked": False,
    }
    doc["links"][next_id(doc["links"], "l")] = _picks_link(tree_id, tree, vid)
    name = PurePosixPath(path).name
    return OpenedFile(vid, "created", live.submit(doc, origin=origin, label=f"open {name}"))


def float_view(live: LiveCanvas, view_id: str) -> SubmitResult:
    """The header's Float (#112, ADR 0021): the view leaves the canvas for the float
    layer, fixed in the window where it is now (pulled inside the window when it hung
    over an edge), anchored to the nearest window corner. One undo step; the view stays
    the user's to move (``by: user``: the agent may unfloat it, not move it)."""
    doc = live.document()
    view = _view(doc, view_id)
    if is_floating(view):
        raise InvalidArgs(
            f"{view_id} already floats", fix=f"unfloat {view_id}, or drag it within the window"
        )
    lay = view["layout"]
    x, y = lay["position"]
    ox, oy = frame_origin(doc)
    lay["floating"] = {
        **anchor_at((x - ox, y - oy), shown_size(lay), frame_size(doc)),
        "by": "user",
    }
    return live.submit(doc, origin="user", label=f"float {_name(view_id, view)}")


def unfloat_view(live: LiveCanvas, view_id: str) -> SubmitResult:
    """The header's Unfloat (#112): the view drops back onto the canvas at the canvas
    point under where it floated (the window frame's origin, which follows the pan, plus
    its window position). One undo step."""
    doc = live.document()
    view = _view(doc, view_id)
    if not is_floating(view):
        raise InvalidArgs(f"{view_id} is not floating", fix=f"float {view_id} first")
    lay = view["layout"]
    lay["position"] = list(canvas_point_under(doc, view))
    del lay["floating"]
    return live.submit(doc, origin="user", label=f"unfloat {_name(view_id, view)}")


def place_floating(
    live: LiveCanvas,
    view_id: str,
    at: tuple[float, float],
    *,
    size: tuple[float, float] | None = None,
) -> SubmitResult:
    """A floating view dragged (or, with ``size``, resized) within the window (#112):
    ``at`` is its new top-left in window px; it re-anchors to its nearest window corner.
    One user step, ``move <label>`` or ``resize <label>``."""
    doc = live.document()
    view = _view(doc, view_id)
    if not is_floating(view):
        raise InvalidArgs(
            f"{view_id} is not floating", fix=f"move {view_id} on the canvas, or float it first"
        )
    lay = view["layout"]
    if size is not None:
        lay["size"] = [float(size[0]), float(size[1])]
    lay["floating"] = {**anchor_at(at, shown_size(lay), frame_size(doc)), "by": "user"}
    verb = "move" if size is None else "resize"
    return live.submit(doc, origin="user", label=f"{verb} {_name(view_id, view)}")


def set_window_frame(live: LiveCanvas, size: tuple[float, float]) -> SubmitResult:
    """The window-frame guide follows the app window's size (its origin stays); a fresh
    canvas's file tree follows its height in the same step (#125)."""
    doc = live.document()
    doc["window_frame"]["rect"].update({"w": size[0], "h": size[1]})
    fit_fresh_tree(doc)
    return live.submit(doc, origin="user", label="resize window")
