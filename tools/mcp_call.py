"""Send one request to ``tools/mcp_bridge.py`` and print the JSON reply.

uv run python tools/mcp_call.py list
uv run python tools/mcp_call.py call <tool> '<json args>' | @args.json
uv run python tools/mcp_call.py stop
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("list", "call", "stop"):
        print(__doc__)
        return 2
    req: dict = {"op": argv[0]}
    if argv[0] == "call":
        req["tool"] = argv[1]
        raw = argv[2] if len(argv) > 2 else "{}"
        if raw.startswith("@"):  # @file.json avoids shell quoting of regexes
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        req["args"] = json.loads(raw)
    port = int(os.environ.get("SPECTO_MCP_BRIDGE_PORT", "8765"))
    with socket.create_connection(("127.0.0.1", port), timeout=600) as s:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
    out = json.loads(buf)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if out.get("isError") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
