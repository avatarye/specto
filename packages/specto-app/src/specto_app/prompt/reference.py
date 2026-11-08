"""The standing reference the session's system prompt ends with (#73 round trips): every
view type (role, doc, accepted data kinds, params as one line each with choices, default
and a clause, ports) and every link kind (doc, params, an example; the intaker's one
example per scope), built from the running engine through its ``vocabulary`` and
``describe`` commands, so the agent composes a simple change from the turn's context in
one ``submit_canvas`` without calling ``describe`` or ``schema`` first. It is static for
the session, so the CLI's prompt cache carries it after the first turn. Compact JSON, one
line per entry. It carries what picks and sets up a view; a manifest's ``guide`` (the
controls, the long form of the params) is ``describe view:<type>``'s alone (SPEC-56).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from specto_core.ports import VIEW_PORTS

CallFn = Callable[[str, dict[str, Any]], dict[str, Any]]
#: manifest and describe fields the agent does not compose with: ``guide``, ``uses`` and
#: ``machine`` are describe's (``renderer_auto`` says what the usd view runs here)
_VIEW_SKIP = (
    "host",
    "cost",
    "hidden",
    "plugin",
    "state_schema",
    "label",
    "type",
    "guide",
    "uses",
    "machine",
)


#: a state_schema property flag: the param is told by ``describe view:<type>`` only, not
#: in the standing reference's card (its default neither)
DESCRIBE_ONLY = "x-describe"


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _choices(prop: dict[str, Any]) -> str:
    if prop.get("enum"):
        return "|".join(str(v) for v in prop["enum"])
    low, high = prop.get("minimum"), prop.get("maximum")
    if low is not None and high is not None:
        return f"{low:g}..{high:g}"
    if low is not None:
        return f">={low:g}"
    return f"<={high:g}" if high is not None else ""


def _default(value: Any) -> str:
    """A default as the line says it: ``default auto``, ``default null``, ``default 1.0``,
    ``default ""`` (an empty string is quoted, so the line never ends in a bare word)."""
    return "default " + (value if isinstance(value, str) and value else json.dumps(value))


def _param_lines(schema: dict[str, Any], defaults: dict[str, Any]) -> dict[str, str]:
    """``state_schema`` as one line per param: its choices or range with its default
    (the schema's, else the manifest's), then its doc. A param marked
    ``"x-describe": true`` (a cosmetic one, SPEC-43.1) is left to ``describe``."""
    out: dict[str, str] = {}
    for name, prop in (schema.get("properties") or {}).items():
        if prop.get(DESCRIBE_ONLY):
            continue
        default = prop["default"] if "default" in prop else defaults.get(name)
        head = ", ".join(
            p
            for p in (
                _choices(prop),
                _default(default) if default is not None or name in defaults else "",
            )
            if p
        )
        line = ". ".join(p for p in (head, _one_line(prop.get("description", ""))) if p)
        if line:
            out[name] = line
    return out


def _view_card(info: dict[str, Any]) -> dict[str, Any]:
    card: dict[str, Any] = {}
    if info.get("role"):
        card["role"] = info["role"]
    if info.get("doc"):
        card["doc"] = _one_line(info["doc"])
    for key in ("accepts", "example"):
        if info.get(key):
            card[key] = info[key]
    if info.get("ports"):
        # inputs and outputs are what a link needs; which input feeds which output
        # (``propagates``) is the shell's reach rule, describe's to tell
        card["ports"] = {k: v for k, v in info["ports"].items() if k != "propagates"}
    defaults = info.get("defaults") or {}
    params = _param_lines(info.get("state_schema") or {}, defaults)
    if params:
        card["params"] = params
    # defaults ride on their param lines; only the ones no param line names stay here
    props = (info.get("state_schema") or {}).get("properties") or {}
    rest = {
        k: v
        for k, v in defaults.items()
        if k not in params and not (props.get(k) or {}).get(DESCRIBE_ONLY)
    }
    if rest:
        card["defaults"] = rest
    handled = {*card, "defaults", "ports"}
    for key, value in info.items():
        if key not in handled and key not in _VIEW_SKIP and value not in (None, {}, []):
            card[key] = value
    return card


def _link_card(info: dict[str, Any]) -> dict[str, Any]:
    card: dict[str, Any] = {"doc": _one_line(info.get("doc", ""))}
    maps = info.get("maps") or {}
    if maps:
        card["maps"] = f"{maps.get('source')} -> {maps.get('target')}"
    defs = (info.get("schema") or {}).get("$defs") or {}
    params = next((d for name, d in defs.items() if name.endswith("Params")), None)
    if params:
        card["params"] = {
            name: _one_line(prop.get("description") or prop.get("type") or "")
            for name, prop in (params.get("properties") or {}).items()
        }
    # ``scopes`` (the intaker's) is told by its doc and shown by its examples
    if info.get("examples"):
        card["examples"] = info["examples"]
    elif info.get("example"):
        card["example"] = info["example"]
    return card


#: the two worked prompts as submit_canvas edits (#98), on a canvas whose file tree is
#: fege and whose front gallery is kobi
EDITS_EXAMPLES: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "move kobi next to the file tree",
        {
            "edits": {"views": {"kobi": {"layout": {"position": [330, 0]}}}},
            "label": "move kobi next to the file tree",
        },
    ),
    (
        "show the selected folder as a gallery",
        {
            "edits": {
                "views": {
                    "new1": {
                        "type": "gallery",
                        "label": "picked images",
                        "layout": {"position": [330, 0], "size": [540, 400]},
                    }
                },
                "links": {
                    "new2": {
                        "kind": "intaker",
                        "scope": "selection",
                        "label": "picked images",
                        "source": {"view": "fege", "port": "selected_files"},
                        "target": {"view": "new1", "port": "data"},
                        "params": {"sources": ["."], "screener": ["@images"]},
                        "summary": {"sentence": "Shows the images you pick in the file tree."},
                    }
                },
            },
            "label": "add a gallery of the picked images",
        },
    ),
)


def reference_text(call: CallFn) -> str:
    """The reference block: view types (the engine's manifests plus the port-only
    built-ins such as the file tree), then link kinds, then the worked prompts as
    submit_canvas edits."""
    vocab = call("vocabulary", {})
    view_types = sorted(set(vocab.get("view_types") or []) | set(VIEW_PORTS))
    lines = [
        "Reference (do not call describe or schema for anything listed here; the "
        "document in the context is the example of the canvas document shape).",
        "",
        "View types (role: browser, viewer, player or scene; params go in the view's params; "
        "ports: inputs/outputs by name and port type):",
    ]
    for name in view_types:
        info = call("describe", {"target": f"view:{name}"}).get("info") or {}
        lines.append(f"- {name}: {json.dumps(_view_card(info), separators=(',', ':'))}")
    lines += ["", "Link kinds (params go in the link's params):"]
    for kind in vocab.get("link_kinds") or []:
        info = call("describe", {"target": f"link:{kind}"}).get("info") or {}
        lines.append(f"- {kind}: {json.dumps(_link_card(info), separators=(',', ':'))}")
    lines += [
        "",
        "submit_canvas edits, one per worked prompt (fege is the file tree, kobi a gallery: "
        "use the names on your canvas; new1 and new2 are placeholders):",
    ]
    for prompt, args in EDITS_EXAMPLES:
        lines.append(f"edits for {prompt!r}: {json.dumps(args, separators=(',', ':'))}")
    return "\n".join(lines)


__all__ = ["EDITS_EXAMPLES", "reference_text"]
