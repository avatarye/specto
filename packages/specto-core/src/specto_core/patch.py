"""RFC 6902 JSON Patch over plain JSON data, plus ``DocTxn``: a write recorder.

Handlers never mutate the document directly. They write through a ``DocTxn`` which
applies each operation to the live data and records the forward operation and its
exact inverse. Undo is ``apply_patch(doc, txn.inverse_patch())``; redo is the forward
patch. No command implements ``undo()``.

Paths are JSON Pointers (RFC 6901). ``"-"`` as the last token appends to a list.
"""

from __future__ import annotations

import copy
from typing import Any

from .errors import PatchError

JSON = Any
PatchOp = dict[str, Any]


def escape_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def unescape_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def pointer(*tokens: str | int) -> str:
    """Build a JSON Pointer from tokens: ``pointer("objects", "tamu", "label")``."""
    return "".join("/" + escape_token(str(t)) for t in tokens)


def split_pointer(path: str) -> list[str]:
    if path == "":
        return []
    if not path.startswith("/"):
        raise PatchError(f"JSON Pointer must start with '/': {path!r}")
    return [unescape_token(t) for t in path[1:].split("/")]


def _walk(data: JSON, tokens: list[str]) -> JSON:
    cur = data
    for t in tokens:
        if isinstance(cur, dict):
            if t not in cur:
                raise PatchError(f"path not found: {pointer(*tokens)}")
            cur = cur[t]
        elif isinstance(cur, list):
            cur = cur[_index(t, cur, allow_end=False)]
        else:
            raise PatchError(f"cannot descend into scalar at {pointer(*tokens)}")
    return cur


def _index(token: str, seq: list, *, allow_end: bool) -> int:
    if token == "-" and allow_end:
        return len(seq)
    try:
        i = int(token)
    except ValueError as e:
        raise PatchError(f"bad list index {token!r}") from e
    if i < 0 or i > len(seq) or (i == len(seq) and not allow_end):
        raise PatchError(f"list index {i} out of range (len {len(seq)})")
    return i


def get_at(data: JSON, path: str, default: Any = ...) -> JSON:
    """Read the value at ``path``; return ``default`` if given and the path is missing."""
    try:
        return _walk(data, split_pointer(path))
    except PatchError:
        if default is ...:
            raise
        return default


def has_path(data: JSON, path: str) -> bool:
    try:
        _walk(data, split_pointer(path))
        return True
    except PatchError:
        return False


def _parent_and_key(data: JSON, path: str) -> tuple[JSON, str]:
    tokens = split_pointer(path)
    if not tokens:
        raise PatchError("operation on the document root is not allowed")
    return _walk(data, tokens[:-1]), tokens[-1]


def apply_op(data: JSON, op: PatchOp) -> None:
    """Apply one RFC 6902 operation in place. Supports add, remove, replace, test, move, copy."""
    kind = op.get("op")
    path = op.get("path")
    if not isinstance(path, str):
        raise PatchError(f"patch op without path: {op!r}")
    if kind == "add":
        parent, key = _parent_and_key(data, path)
        value = copy.deepcopy(op["value"])
        if isinstance(parent, dict):
            parent[key] = value
        elif isinstance(parent, list):
            parent.insert(_index(key, parent, allow_end=True), value)
        else:
            raise PatchError(f"cannot add into scalar at {path}")
    elif kind == "replace":
        parent, key = _parent_and_key(data, path)
        value = copy.deepcopy(op["value"])
        if isinstance(parent, dict):
            if key not in parent:
                raise PatchError(f"replace on missing path {path}")
            parent[key] = value
        elif isinstance(parent, list):
            parent[_index(key, parent, allow_end=False)] = value
        else:
            raise PatchError(f"cannot replace in scalar at {path}")
    elif kind == "remove":
        parent, key = _parent_and_key(data, path)
        if isinstance(parent, dict):
            if key not in parent:
                raise PatchError(f"remove on missing path {path}")
            del parent[key]
        elif isinstance(parent, list):
            del parent[_index(key, parent, allow_end=False)]
        else:
            raise PatchError(f"cannot remove from scalar at {path}")
    elif kind == "test":
        if get_at(data, path) != op.get("value"):
            raise PatchError(f"test failed at {path}")
    elif kind in ("move", "copy"):
        src = op.get("from")
        if not isinstance(src, str):
            raise PatchError(f"{kind} without 'from': {op!r}")
        value = copy.deepcopy(get_at(data, src))
        if kind == "move":
            apply_op(data, {"op": "remove", "path": src})
        apply_op(data, {"op": "add", "path": path, "value": value})
    else:
        raise PatchError(f"unknown patch op {kind!r}")


def apply_patch(data: JSON, patch: list[PatchOp]) -> JSON:
    """Apply a patch in place and return ``data``. Atomic: on failure the input is untouched."""
    trial = copy.deepcopy(data)
    for op in patch:
        apply_op(trial, op)
    if isinstance(data, dict):
        data.clear()
        data.update(trial)
    elif isinstance(data, list):
        data[:] = trial
    return data


class DocTxn:
    """Records every write to ``data`` as a forward op plus its inverse.

    Writes apply immediately to ``data`` (the live document, or a copy for dry runs).
    ``forward_patch()`` replays the writes; ``inverse_patch()`` undoes them exactly.
    """

    def __init__(self, data: JSON) -> None:
        self.data = data
        self._forward: list[PatchOp] = []
        self._inverse: list[PatchOp] = []

    # -- reads ---------------------------------------------------------------
    def get(self, path: str, default: Any = ...) -> JSON:
        return get_at(self.data, path, default)

    def has(self, path: str) -> bool:
        return has_path(self.data, path)

    # -- writes ----------------------------------------------------------------
    def add(self, path: str, value: JSON) -> None:
        """RFC add: insert into a list (``-`` appends) or set a dict key (overwrites)."""
        parent, key = _parent_and_key(self.data, path)
        if isinstance(parent, list):
            idx = _index(key, parent, allow_end=True)
            resolved = pointer(*split_pointer(path)[:-1], idx)
            self._record(
                {"op": "add", "path": resolved, "value": copy.deepcopy(value)},
                {"op": "remove", "path": resolved},
            )
            parent.insert(idx, copy.deepcopy(value))
            return
        if isinstance(parent, dict) and key in parent:
            self.replace(path, value)
            return
        self._record(
            {"op": "add", "path": path, "value": copy.deepcopy(value)},
            {"op": "remove", "path": path},
        )
        apply_op(self.data, {"op": "add", "path": path, "value": value})

    def replace(self, path: str, value: JSON) -> None:
        old = copy.deepcopy(get_at(self.data, path))
        if old == value:
            return
        self._record(
            {"op": "replace", "path": path, "value": copy.deepcopy(value)},
            {"op": "replace", "path": path, "value": old},
        )
        apply_op(self.data, {"op": "replace", "path": path, "value": value})

    def set(self, path: str, value: JSON) -> None:
        """Add or replace, whichever applies."""
        if has_path(self.data, path):
            self.replace(path, value)
        else:
            self.add(path, value)

    def set_deep(self, path: str, value: JSON) -> None:
        """Like ``set`` but creates missing intermediate dicts (each as its own add op)."""
        tokens = split_pointer(path)
        for i in range(1, len(tokens)):
            prefix = pointer(*tokens[:i])
            if not has_path(self.data, prefix):
                self.add(prefix, {})
        self.set(path, value)

    def remove(self, path: str) -> JSON:
        old = copy.deepcopy(get_at(self.data, path))
        self._record({"op": "remove", "path": path}, {"op": "add", "path": path, "value": old})
        apply_op(self.data, {"op": "remove", "path": path})
        return old

    def ensure(self, path: str, value: JSON) -> None:
        """Create ``path`` with ``value`` if missing; leave an existing value alone."""
        if not has_path(self.data, path):
            self.add(path, value)

    # -- results ---------------------------------------------------------------
    def _record(self, fwd: PatchOp, inv: PatchOp) -> None:
        self._forward.append(fwd)
        self._inverse.append(inv)

    def forward_patch(self) -> list[PatchOp]:
        return copy.deepcopy(self._forward)

    def inverse_patch(self) -> list[PatchOp]:
        return copy.deepcopy(list(reversed(self._inverse)))

    def __len__(self) -> int:
        return len(self._forward)

    def extend_from(self, other: DocTxn) -> None:
        """Append another DocTxn's recorded ops (used to merge per-command txns)."""
        self._forward.extend(other._forward)
        self._inverse.extend(other._inverse)


def make_patch(old: JSON, new: JSON) -> list[PatchOp]:
    """Document diff (ADR 0013's reconcile): the RFC 6902 patch that turns ``old`` into
    ``new``. Dicts are diffed key by key, so the id-keyed maps of the canvas document
    (``views``, ``links``) give one add or remove per id and one replace per changed
    field; lists and scalars are replaced whole. Both roots must be objects."""
    if not (isinstance(old, dict) and isinstance(new, dict)):
        raise PatchError("make_patch diffs two JSON objects")
    out: list[PatchOp] = []
    _diff(old, new, "", out)
    return out


def _diff(a: JSON, b: JSON, path: str, out: list[PatchOp]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k not in b:
                out.append({"op": "remove", "path": f"{path}/{escape_token(k)}"})
        for k, v in b.items():
            p = f"{path}/{escape_token(k)}"
            if k not in a:
                out.append({"op": "add", "path": p, "value": copy.deepcopy(v)})
            else:
                _diff(a[k], v, p, out)
    elif a != b:
        out.append({"op": "replace", "path": path, "value": copy.deepcopy(b)})


def invert_patch(data_before: JSON, patch: list[PatchOp]) -> list[PatchOp]:
    """Compute the inverse of ``patch`` relative to ``data_before`` by replaying it."""
    txn = DocTxn(copy.deepcopy(data_before))
    for op in patch:
        kind = op["op"]
        if kind == "add":
            txn.add(op["path"], op["value"])
        elif kind == "replace":
            txn.replace(op["path"], op["value"])
        elif kind == "remove":
            txn.remove(op["path"])
        else:
            raise PatchError(f"cannot invert op {kind!r}")
    return txn.inverse_patch()
