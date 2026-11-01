"""``specto-mcp``: serve the Specto tools to an MCP client (stdio by default).

    specto-mcp [--project DIR] [--endpoint URL] [--session ID] [--transport stdio|streamable-http]

Each MCP session connects to the running app as ``agent:<session>``; the process's
default session id comes from ``--session`` / ``SPECTO_AGENT_SESSION`` or is generated.
"""

from __future__ import annotations

import argparse
import os
import sys

from specto_sdk import Client

from .server import build_server
from .sessions import AgentSessions


def make_sessions(
    project: str | None = None,
    *,
    endpoint: str | None = None,
    session: str | None = None,
    session_ttl_s: float = 3600.0,
) -> AgentSessions:
    def connect(origin: str) -> Client:
        return Client.open(project, endpoint=endpoint, origin=origin, client="specto-mcp")

    return AgentSessions(connect, default_session=session, session_ttl_s=session_ttl_s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="specto-mcp", description=__doc__)
    ap.add_argument("--project", default=os.environ.get("SPECTO_PROJECT"))
    ap.add_argument("--endpoint", default=os.environ.get("SPECTO_ENDPOINT"))
    ap.add_argument("--session", default=os.environ.get("SPECTO_AGENT_SESSION"))
    ap.add_argument("--session-ttl", type=float, default=3600.0, help="idle seconds per session")
    ap.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    ns = ap.parse_args(argv)
    sessions = make_sessions(
        ns.project, endpoint=ns.endpoint, session=ns.session, session_ttl_s=ns.session_ttl
    )
    server = build_server(sessions)
    print(f"[OK] specto-mcp session {sessions.default_session} ({ns.transport})", file=sys.stderr)
    try:
        server.run(transport=ns.transport)
    finally:
        sessions.close_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
