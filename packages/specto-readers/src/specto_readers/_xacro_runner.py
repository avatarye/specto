"""Expands one xacro file in this (fresh, ``python -I -X utf8``) interpreter.

Started by ``xacro_expand.xacro_task`` for every expansion, so an approved file's code
never runs in a pool worker. Reads one pickled request on stdin from that parent
(``path``, ``find_package``, ``trusted``, ``args``, ``approved_root_sha``, ``roots``),
calls ``expand_xacro`` and prints one JSON line: ``{urdf, files, warnings}``,
``{needs_approval: {path, sha256, include}}`` or ``{error}``. What xacro or the file's
code prints goes to stderr, so it never mixes with the reply.
"""

from __future__ import annotations

import contextlib
import json
import pickle
import sys


def main() -> int:
    request = pickle.loads(sys.stdin.buffer.read())  # from the parent only
    out = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        try:
            from specto_readers.xacro_expand import (
                XacroError,
                XacroNeedsApproval,
                expand_xacro,
            )

            res = expand_xacro(
                request["path"],
                find_package=request["find_package"],
                trusted=request["trusted"],
                args=request["args"],
                approved_root_sha=request["approved_root_sha"],
                roots=request["roots"],
            )
            reply = {"urdf": res.urdf, "files": res.files, "warnings": res.warnings}
        except XacroNeedsApproval as e:
            reply = {"needs_approval": {"path": e.path, "sha256": e.sha256, "include": e.include}}
        except XacroError as e:
            reply = {"error": str(e)}
        except Exception as e:  # one line back to the parent, whatever it was
            reply = {"error": f"{type(e).__name__}: {e}"}
    out.write(json.dumps(reply) + "\n")
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
