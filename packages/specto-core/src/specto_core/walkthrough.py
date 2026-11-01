"""The view-chain walk-through (#174, wish list #159): the hops from the project source to
a view, in the order the walk plays them, and the script the walk plays.

``plan_walk(data, target)`` orders the links of a canvas document (JSON form) by the
fork rule (the user's decision of 2026-10-24): the walk follows the data from the project
source; at a view with several inputs (a fork) it first arrives along the main line, then
walks each other input into the fork, starting from the nearest view already walked, then
goes on. The main line is the longest chain of links from the project (ties: the upper
input port), so the walk tells the whole story along one line and the shortcuts become
the branches. Extra inputs are walked in input-port order, top to bottom. A view is
arrived at once. Links are followed in their declared direction (a two-way link too);
an input from a view the project does not reach is left out (``not_connected``); a loop
of links upstream of the view, or a view the project does not reach, is refused.

``walk_facts`` reads the link runtime's resolved values for each hop (counts in and out,
the link's params as a note) without resolving anything (ADR 0016); ``build_script``
turns a plan and its facts into the ``WalkScript`` the controller plays, with the agent's
captions or, missing them, ``template_caption``'s. The script's JSON shape is the
contract with the walk UI. No Qt here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import SpectoError
from .ports import PROJECT_ID, PROJECT_NODE_RECT, VIEW_PORTS


class WalkError(SpectoError):
    code = "walk"


@dataclass
class Hop:
    """One link walked: ``source`` -> ``target`` (view ids; ``project`` for the project
    source). ``branch`` hops lead into ``fork``; ``branch_start`` marks the first hop of
    each branch (the hop track draws its return arrow there)."""

    index: int
    link: str
    source: str
    target: str
    source_port: str
    target_port: str
    kind: str
    branch: bool = False
    branch_start: bool = False
    fork: str | None = None


@dataclass
class WalkPlan:
    target: str
    hops: list[Hop] = field(default_factory=list)
    #: every view on the walk in arrival order, the project first
    views: list[str] = field(default_factory=list)
    #: the views with more than one walked input, in arrival order
    forks: list[str] = field(default_factory=list)
    #: links into the walk from views the project source does not reach
    not_connected: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _In:
    link: str
    source: str
    order: int  # the target port's place among its view type's inputs, top to bottom


def _link_no(link_id: str) -> int:
    m = re.match(r"^l(\d+)$", link_id)
    return int(m.group(1)) if m else 1 << 30


def _port_order(views: Mapping[str, Any], view_id: str, port: str) -> int:
    decl = VIEW_PORTS.get((views.get(view_id) or {}).get("type", ""))
    names = list(decl.inputs) if decl is not None else []
    return names.index(port) if port in names else len(names)


def plan_walk(data: Mapping[str, Any], target: str) -> WalkPlan:
    """The hops from the project source to ``target`` by the fork rule. Raises
    ``WalkError`` for an unknown view, a view the project does not reach, and a loop of
    links upstream of the view."""
    views: Mapping[str, Any] = data.get("views") or {}
    links: Mapping[str, Any] = data.get("links") or {}
    if target not in views:
        names = ", ".join(sorted(views)) or "none"
        raise WalkError(
            f"there is no view {target!r} on the canvas", fix=f"walk to one of: {names}"
        )
    ins: dict[str, list[_In]] = {}
    outs: dict[str, list[str]] = {}
    for lid in sorted(links, key=_link_no):
        ln = links[lid]
        src, dst = ln["source"]["view"], ln["target"]["view"]
        if src == dst:
            continue
        ins.setdefault(dst, []).append(_In(lid, src, _port_order(views, dst, ln["target"]["port"])))
        outs.setdefault(src, []).append(dst)
    for lst in ins.values():
        lst.sort(key=lambda i: (i.order, _link_no(i.link)))

    reached = {PROJECT_ID}
    frontier = [PROJECT_ID]
    while frontier:
        for nxt in outs.get(frontier.pop(), ()):
            if nxt not in reached:
                reached.add(nxt)
                frontier.append(nxt)
    if target not in reached:
        raise WalkError(
            f"view {target} is not connected to the project source by links, so there is "
            "no walk to it",
            fix=f"link {target} (or a view feeding it) to the project source first",
        )

    plan = WalkPlan(target)
    # the views upstream of the target that the project reaches
    upstream = {target}
    frontier = [target]
    while frontier:
        for i in ins.get(frontier.pop(), ()):
            if i.source not in reached:
                if i.link not in plan.not_connected:
                    plan.not_connected.append(i.link)
            elif i.source not in upstream:
                upstream.add(i.source)
                frontier.append(i.source)
    plan.not_connected.sort(key=_link_no)

    def inputs(v: str) -> list[_In]:
        return [i for i in ins.get(v, ()) if i.source in reached]

    _refuse_loops(upstream, inputs)

    depth: dict[str, int] = {PROJECT_ID: 0}

    def longest(v: str) -> int:
        if v not in depth:
            depth[v] = 1 + max((longest(i.source) for i in inputs(v)), default=-1)
        return depth[v]

    walked = {PROJECT_ID}
    plan.views.append(PROJECT_ID)

    def gap(v: str) -> int:
        """Hops from the nearest walked view up to ``v``."""
        seen, level, n = {v}, [v], 0
        while level:
            if any(u in walked for u in level):
                return n
            level = [i.source for u in level for i in inputs(u) if i.source not in seen]
            seen.update(level)
            n += 1
        return 1 << 30

    def emit(i: _In, to: str, fork: str | None) -> None:
        ln = links[i.link]
        plan.hops.append(
            Hop(
                index=len(plan.hops) + 1,
                link=i.link,
                source=i.source,
                target=to,
                source_port=ln["source"]["port"],
                target_port=ln["target"]["port"],
                kind=ln["kind"],
                branch=fork is not None,
                fork=fork,
            )
        )

    def arrive(v: str, fork: str | None) -> None:
        """Walk to ``v``: along the main line (``fork`` None) or on a branch into ``fork``."""
        if v in walked:
            return
        feeds = inputs(v)
        if fork is None:
            main = max(feeds, key=lambda i: (longest(i.source), -i.order))
        else:
            main = min(feeds, key=lambda i: (gap(i.source), i.order))
        arrive(main.source, fork)
        emit(main, v, fork)
        walked.add(v)
        plan.views.append(v)
        others = [i for i in feeds if i is not main]
        if others:
            plan.forks.append(v)
        for i in others:
            first = len(plan.hops)
            arrive(i.source, v)
            emit(i, v, v)
            plan.hops[first].branch_start = True

    arrive(target, None)
    return plan


def _refuse_loops(upstream: set[str], inputs: Any) -> None:
    """A loop of links among the views upstream of the walk's target has no order to
    walk in: refused, naming it."""
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(upstream, white)
    stack: list[str] = []

    def visit(v: str) -> list[str] | None:
        color[v] = grey
        stack.append(v)
        for i in inputs(v):
            u = i.source
            if u not in color:
                continue
            if color[u] == grey:
                return [*stack[stack.index(u) :], u]
            if color[u] == white and (found := visit(u)):
                return found
        stack.pop()
        color[v] = black
        return None

    for v in sorted(upstream):
        if color[v] == white and (loop := visit(v)):
            path = " -> ".join(reversed(loop))
            raise WalkError(
                f"the links {path} form a loop, so the walk has no order to follow",
                fix="remove one link of the loop, or walk to a view before it",
            )


# -- the script ------------------------------------------------------------------------------
def _count(value: Any, *, project: Any = None, picked: bool = False) -> dict[str, Any] | None:
    """A port value as the flow meter shows it: ``{n, unit}`` or ``{text, unit}``;
    ``None`` when nothing resolved or it has no count. ``project`` is the runtime's
    ``project_files`` (the project source's all files carry no list of their own)."""
    if value is None:
        return None
    name = type(value).__name__
    if name == "FilesValue":
        paths = value.paths
        if paths is None:
            paths = getattr(project, "paths", None)
            return None if paths is None else {"n": len(paths), "unit": "files"}
        return {"n": len(paths), "unit": "files picked" if picked else "files"}
    if name == "Collection":
        return {"n": len(value.items), "unit": "items"}
    if name == "Selection":
        out: dict[str, Any] = {"n": len(value.keys), "unit": "selected"}
        if value.picked:
            out["text"] = value.picked
        return out
    if name == "TimeValue":
        return {"text": _clock(value.t), "unit": "time"}
    if name == "ContentValue":
        return {"n": len(value.entries), "unit": "entries"}
    if name == "CameraValue":
        return {"text": "camera", "unit": "camera"}
    return None


def _clock(t: float) -> str:
    m, s = divmod(max(0.0, float(t)), 60.0)
    return f"{int(m)}:{s:04.1f}"


def _files_in(value: Any) -> int | None:
    """How many files a collection holds (an item may have several parts)."""
    if type(value).__name__ != "Collection":
        return None
    return sum(len(it.parts) for it in value.items)


def _kind_label(link: Mapping[str, Any]) -> str:
    kind = link["kind"]
    params = link.get("params") or {}
    if kind == "intaker":
        scope = link.get("scope") or ""
        labels = {"selection": "intaker · follows your pick", "pinned": "intaker · pinned"}
        return labels.get(scope, "intaker · all files")
    if kind == "follow":
        if link.get("two_way"):
            return "two-way follow"
        return f"follow · by {params['match']}" if params.get("match") else "follow"
    if kind == "python":
        python = link.get("python") or {}
        return "python · per pick" if python.get("resolves_to") == "value" else "python"
    return {"read_contents": "reads contents", "time_map": "time map"}.get(kind, kind)


def _note(link: Mapping[str, Any]) -> str:
    """The caption card's muted line: what the link's params say, in a few words."""
    kind = link["kind"]
    p = link.get("params") or {}
    parts: list[str] = []
    if kind == "intaker":
        scope = link.get("scope")
        if scope == "selection":
            parts.append(f"scope: selection (follows your pick in {link['source']['view']})")
        elif scope:
            parts.append(f"scope: {scope}")
        if p.get("sources") and list(p["sources"]) != ["."]:
            parts.append("sources: " + ", ".join(p["sources"]))
        if p.get("screener"):
            parts.append("screener: " + " ".join(p["screener"]))
        if p.get("keys"):
            try:
                fields = list(re.compile(p["keys"]).groupindex)
            except re.error:
                fields = []
            if fields:
                parts.append("key: " + ", ".join(fields))
        if p.get("watch"):
            parts.append("auto-update on")
    elif kind == "follow":
        parts.append(f"match: {p['match']}" if p.get("match") else "match: whole key")
        if link.get("two_way"):
            parts.append("both ways")
    elif kind == "time_map":
        line = f"{p.get('from_timeline', '?')} to {p.get('to_timeline', '?')}"
        if p.get("offset_ms"):
            line += f" · offset {p['offset_ms']:g} ms"
        parts.append(line)
    elif kind == "read_contents":
        parts.append("reads: " + ", ".join(p.get("sources") or []))
        if p.get("format"):
            parts.append(f"format: {p['format']}")
    elif kind == "python":
        python = link.get("python") or {}
        parts.append(f"code: {python.get('path', '?')}")
        how = {"value": "per pick", "table": "table", "samples": "samples"}
        if python.get("resolves_to") in how:
            parts.append(how[python["resolves_to"]])
    return " · ".join(parts)


def _amount(count: Mapping[str, Any] | None) -> str:
    if not count:
        return ""
    if "n" in count:
        return f"{count['n']:,} {count['unit']}"
    return str(count.get("text", ""))


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else text + "."


def template_caption(hop: Mapping[str, Any]) -> str:
    """The caption when no agent wrote one: the link summary and what the counts say."""
    base = _sentence(hop.get("summary") or "") or (
        f"{hop['kind_label'].capitalize()} from {hop['from']} to {hop['to']}."
    )
    cin, cout = hop.get("count_in"), hop.get("count_out")
    if cin and cout and "n" in cin and "n" in cout:
        base += (
            f" {hop['to']} gets **{cout['n']:,}** {cout['unit']} from **{cin['n']:,}** "
            f"{cin['unit']}."
        )
    elif cout:
        base += f" {hop['to']} now has **{_amount(cout)}**."
    return base


def _rect(view: Mapping[str, Any] | None) -> list[float]:
    if view is None:
        return list(PROJECT_NODE_RECT)
    layout = view.get("layout") or {}
    (x, y), (w, h) = layout.get("position", (0, 0)), layout.get("size", (400, 300))
    return [x, y, w, h]


def _floating(view: Mapping[str, Any] | None) -> bool:
    return bool(view and (view.get("layout") or {}).get("floating"))


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def walk_script(data: Mapping[str, Any], target: str, links: Any = None) -> dict[str, Any]:
    """The walk's script with template captions (``captioned`` false): the planned hops,
    each with its ends, kind, note, counts read from ``links`` (the ``LinkRuntime``, or
    anything with its ``outputs``, ``inputs``, ``project_files`` and ``status``; ``None``:
    no counts), and the camera targets. Raises ``WalkError`` as ``plan_walk`` does."""
    plan = plan_walk(data, target)
    views: Mapping[str, Any] = data.get("views") or {}
    links_doc: Mapping[str, Any] = data.get("links") or {}
    outputs = getattr(links, "outputs", None) or {}
    inputs = getattr(links, "inputs", None) or {}
    project_files = getattr(links, "project_files", None)
    status = getattr(links, "status", None)
    forks = set(plan.forks)
    hops: list[dict[str, Any]] = []
    for h in plan.hops:
        ln = links_doc[h.link]
        source_value = outputs.get((h.source, h.source_port))
        target_value = inputs.get((h.target, h.target_port))
        picked = (views.get(h.source) or {}).get("type") == "file_tree"
        count_in = _count(source_value, project=project_files, picked=picked)
        count_out = _count(target_value)
        files_out = _files_in(target_value)
        ratio = None
        if count_in and count_in.get("n") and files_out is not None:
            ratio = min(1.0, files_out / count_in["n"])
        note = _note(ln)
        if h.target in forks and h.fork != h.target:
            # the main arrival at a fork: its other inputs come next
            extra = sum(1 for o in plan.hops if o.target == h.target) - 1
            more = "a second input" if extra == 1 else f"{extra} more inputs"
            fork_note = f"{h.target} takes {more}; the walk takes it next"
            note = f"{note} · {fork_note}" if note else fork_note
        state = (status(h.link) or {}).get("state", "") if callable(status) else ""
        hop: dict[str, Any] = {
            "index": h.index,
            "link": h.link,
            "from": h.source,
            "to": h.target,
            "from_port": h.source_port,
            "to_port": h.target_port,
            "kind": h.kind,
            "kind_label": _kind_label(ln),
            "summary": (ln.get("summary") or {}).get("sentence") or "",
            "caption": "",
            "chip": "",
            "note": note,
            "count_in": count_in,
            "count_out": count_out,
            "ratio": ratio,
            "branch": h.branch,
            "branch_start": h.branch_start,
            "fork": h.fork,
            "from_rect": _rect(views.get(h.source)),
            "to_rect": _rect(views.get(h.target)),
            "from_floating": _floating(views.get(h.source)),
            "to_floating": _floating(views.get(h.target)),
            "status": state,
            "params": dict(ln.get("params") or {}),
        }
        hop["caption"] = template_caption(hop)
        hop["chip"] = _amount(count_out) or hop["kind_label"]
        hops.append(hop)

    project_count = _count(outputs.get((PROJECT_ID, "all_files")), project=project_files)
    n = len(hops)
    intro = (
        f"{target}'s data starts at the project folder and reaches it along "
        f"**{n}** link{'s' if n != 1 else ''}."
    )
    if plan.forks:
        verb = "takes" if len(plan.forks) == 1 else "take"
        intro += (
            f" {_names(plan.forks)} {verb} more than one input; the walk takes each right "
            "after reaching it."
        )
    walked_views: dict[str, Any] = {}
    for hop in hops:
        v = views.get(hop["to"]) or {}
        walked_views.setdefault(
            hop["to"],
            {
                "type": v.get("type", ""),
                "label": v.get("label", ""),
                "chip": hop["chip"],
                "rect": hop["to_rect"],
                "floating": hop["to_floating"],
            },
        )
    script: dict[str, Any] = {
        "target": target,
        "project_label": (data.get("project") or {}).get("label") or "",
        "intro": intro,
        "summary": "",
        "captioned": False,
        "source": "template",
        "project": {
            "chip": _amount(project_count) or "project folder",
            "count": project_count,
            "rect": list(PROJECT_NODE_RECT),
        },
        "views": walked_views,
        "hops": hops,
        "forks": list(plan.forks),
        "not_connected": list(plan.not_connected),
    }
    script["summary"] = _template_summary(script)
    return script


def _template_summary(script: Mapping[str, Any]) -> str:
    n = len(script["hops"])
    chips = ", ".join(f"{vid} ({v['chip']})" for vid, v in script["views"].items())
    return (
        f"**{n}** link{'s' if n != 1 else ''} bring the project's files to "
        f"**{script['target']}**: {chips}."
    )


def caption_script(
    script: Mapping[str, Any],
    captions: list[str],
    *,
    intro: str | None = None,
    summary: str | None = None,
    chips: list[str] | None = None,
) -> dict[str, Any]:
    """A copy of ``script`` with the agent's captions: one per hop in hop order (an empty
    one keeps the template), and optionally the intro, the summary and the chips (what
    arrives at each hop's view, a few words). Raises ``WalkError`` when the captions or
    chips do not match the hops."""
    n = len(script["hops"])
    for name, values in (("captions", captions), ("chips", chips)):
        if values is not None and len(values) != n:
            raise WalkError(
                f"{name} has {len(values)} entries, but the walk has {n} hops",
                fix=f"send one caption per hop, in hop order ({n}); chips likewise, or none",
            )
    out = dict(script)
    out["hops"] = [dict(h) for h in script["hops"]]
    out["views"] = {vid: dict(v) for vid, v in script["views"].items()}
    arrived: set[str] = set()
    for i, hop in enumerate(out["hops"]):
        if str(captions[i] or "").strip():
            hop["caption"] = str(captions[i]).strip()
        if chips and str(chips[i] or "").strip():
            hop["chip"] = str(chips[i]).strip()
        if hop["to"] not in arrived:
            arrived.add(hop["to"])
            out["views"][hop["to"]]["chip"] = hop["chip"]
    if intro and intro.strip():
        out["intro"] = intro.strip()
    out["summary"] = summary.strip() if summary and summary.strip() else _template_summary(out)
    out["captioned"] = True
    out["source"] = "agent"
    return out


__all__ = [
    "Hop",
    "WalkError",
    "WalkPlan",
    "caption_script",
    "plan_walk",
    "template_caption",
    "walk_script",
]
