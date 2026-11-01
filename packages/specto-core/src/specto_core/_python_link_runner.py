"""Runs one Python link's entry point in this (isolated, ``python -I -X utf8``) interpreter.

Standard library only: ``-I`` keeps the script directory, the working directory and
``PYTHONPATH`` off ``sys.path``; UTF-8 mode makes text files and the console UTF-8
whatever the system code page, so a script that opens a file without an encoding reads
it as the app does. Reads one JSON request on stdin (``path``, ``sha256``, ``entry``,
``items``, ``params``, ``root``), refuses a file whose content no longer hashes to what
was approved, imports the file as a module, calls ``entry(items, params, root)`` (or,
with ``mode: value``, ``entry(value, items, params, root)`` for the source's current
``value``) and prints one JSON line:
``{"ok": true, "result": ...}`` or ``{"ok": false, "error": "...", "trace": "..."}``
(``trace``: the script's own frames only, paths relative to ``root``; ``kind: result``
when the result itself is the problem). What the script prints goes to stderr, so it
never mixes with the reply.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import traceback

#: the fix-it for a result JSON cannot carry
NOT_JSON = "which is not JSON; return a dict, list, str, number, bool or None"


class _NotJSON(TypeError):
    pass


def _not_json(obj: object) -> object:
    raise _NotJSON(type(obj).__name__)


def _trace(e: BaseException, path: str, shown: str) -> str:
    """The traceback of ``e`` through the script's own frames, as Python prints it."""
    here = os.path.normcase(os.path.abspath(path))
    frames = [
        f
        for f in traceback.extract_tb(e.__traceback__)
        if os.path.normcase(os.path.abspath(f.filename)) == here
    ]
    lines = ["Traceback (most recent call last):\n"] if frames else []
    lines += traceback.format_list(frames) + traceback.format_exception_only(type(e), e)
    return "".join(lines).replace(path, shown)


def main() -> int:
    req = json.load(sys.stdin)
    path, entry = req["path"], req["entry"]
    try:
        shown = os.path.relpath(path, req["root"]).replace("\\", "/")
    except ValueError:  # another drive
        shown = path
    reply = sys.stdout
    sys.stdout = sys.stderr  # the script's prints
    try:
        with open(path, "rb") as f:
            data = f.read()
        if hashlib.sha256(data).hexdigest() != req["sha256"]:
            raise PermissionError("the file changed since it was approved; approve it again")
        spec = importlib.util.spec_from_file_location("specto_link", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, entry, None)
        if not callable(fn):
            raise AttributeError(f"no function {entry!r} in {shown}")
        if req.get("mode") == "value":
            result = fn(req["value"], req["items"], req["params"], req["root"])
        else:
            result = fn(req["items"], req["params"], req["root"])
        try:
            out = json.dumps({"ok": True, "result": result}, default=_not_json)
        except _NotJSON as e:
            error = f"returned a {e}, {NOT_JSON}"
            out = json.dumps({"ok": False, "kind": "result", "error": error})
        except ValueError as e:  # a circular reference
            out = json.dumps(
                {"ok": False, "kind": "result", "error": f"returned a value {NOT_JSON} ({e})"}
            )
    except BaseException as e:  # every failure is reported, never raised
        out = json.dumps(
            {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": _trace(e, path, shown)}
        )
    reply.write(out + "\n")
    reply.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
