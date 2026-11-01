"""A fake ``nimitz`` for the #187 seam-3 flow (``SPECTO_NIMITZ`` points the app at
``nimitz.cmd`` beside this file, or ``nimitz`` off Windows): ``status --json`` answers
one small fleet, ``help signal`` lists ``--from``, ``signal`` only records its argv. It
never reaches a real fleet member. Every argv is appended to ``%TEMP%/specto-fake-nimitz.jsonl``."""

from __future__ import annotations

import json
import os
import sys
import tempfile


def member(mid: str, role: str, status: str, pid: int | None) -> dict:
    return {"id": mid, "role": role, "status": status, "topic": "", "session": {"pid": pid}}


STATUS = {
    "machine": "fake",
    "fleets": [
        {
            "id": "FLEET",
            "admiral": member("FLEET", "admiral", "Idle", 1),
            "squadrons": [
                {
                    "id": "FLEET/SQUAD",
                    "commodore": member("FLEET/SQUAD", "commodore", "Idle", 2),
                    "ships": [member("FLEET/SQUAD/SHIP", "ship", "Busy", 3)],
                    "issues": [],
                }
            ],
            "issues": [],
        }
    ],
    "issues": [],
}


def main(args: list[str]) -> int:
    log = os.path.join(tempfile.gettempdir(), "specto-fake-nimitz.jsonl")
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(args) + "\n")
    if args[:1] == ["status"]:
        print(json.dumps(STATUS))
    elif args[:2] == ["help", "signal"]:
        print("usage:\n  signal <path> <text> [--inbox] [--from LABEL]")
    elif args[:1] == ["signal"]:
        print(f"[OK] posted to {args[1]}'s inbox as a request from nimitz (fake)")
    else:
        print(f"[ERROR] the fake nimitz does not do {' '.join(args[:2])}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
