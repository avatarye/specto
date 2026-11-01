"""Hold one MCP stdio session to ``specto-mcp`` open and relay tool calls to it.

An agent that can only run shell commands (a subagent, a script) uses this to act as an
MCP client with one stable agent session, so transactions it begins stay owned by it
across calls (contracts section 14). Used by the M1 verification of exit criterion 1.

    uv run python tools/mcp_bridge.py --project DIR [--port 8765] [--log t.jsonl]
    uv run python tools/mcp_call.py list                              # MCP tools/list
    uv run python tools/mcp_call.py call <tool> '<json args>'         # MCP tools/call

Protocol on 127.0.0.1:<port>: one JSON object per line in, one per line out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _result(res: Any) -> dict[str, Any]:
    texts = [getattr(c, "text", None) for c in res.content]
    return {
        "isError": bool(res.is_error),
        "text": [t for t in texts if t is not None],
        "structured": res.structured_content,
    }


async def serve(project: str, port: int, session: str | None, log: str | None = None) -> None:
    args = ["-m", "specto_mcp.main", "--project", project]
    if session:
        args += ["--session", session]
    params = StdioServerParameters(command=sys.executable, args=args)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as mcp:
        init = await mcp.initialize()
        print(f"[OK] MCP session to {init.server_info.name} {init.server_info.version}", flush=True)
        lock = asyncio.Lock()
        stop = asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            req: Any = None
            try:
                req = json.loads(await reader.readline())
                async with lock:
                    op = req.get("op")
                    if op == "list":
                        tools = await mcp.list_tools()
                        out: dict[str, Any] = {
                            "tools": [
                                {
                                    "name": t.name,
                                    "description": t.description,
                                    "inputSchema": t.input_schema,
                                    "annotations": t.annotations.model_dump(exclude_none=True)
                                    if t.annotations
                                    else None,
                                }
                                for t in tools.tools
                            ],
                            "instructions": init.instructions,
                        }
                    elif op == "call":
                        res = await mcp.call_tool(req["tool"], req.get("args") or {})
                        out = _result(res)
                    elif op == "stop":
                        out = {"stopped": True}
                        stop.set()
                    else:
                        out = {"isError": True, "text": [f"unknown op {op!r}"]}
            except Exception as e:  # report, keep serving
                out = {"isError": True, "text": [f"{type(e).__name__}: {e}"]}
            if log:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"request": req, "reply": out}, ensure_ascii=False) + "\n")
            writer.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", port, limit=2**24)
        print(f"[OK] bridge listening on 127.0.0.1:{port}", flush=True)
        async with server:
            await stop.wait()
    print("[OK] bridge stopped", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--session", default=None)
    ap.add_argument("--log", default=None, help="append every request/reply as JSON lines")
    ns = ap.parse_args()
    asyncio.run(serve(ns.project, ns.port, ns.session, ns.log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
