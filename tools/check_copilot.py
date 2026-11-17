"""Exercise only self-created sessions on the installed Copilot CLI.

python tools/check_copilot.py --work out/copilot-check [--live]

No global settings are changed and no unrelated session is messaged. The default
checks real cross-process peer admission without asking a model to respond.
--live also checks a streaming reply and a Specto tool call (uses the user's plan).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path

from copilot import CopilotClient, RuntimeConnection
from copilot.generated.rpc import (
    SessionManagementConfigureRequest,
    SessionManagementListRequest,
    SessionManagementMessageRequest,
    SessionManagementToolOverrides,
)

from specto_app.prompt.copilot_session import CopilotAgentSession, disabled_mcp_servers
from specto_app.prompt.turn import TurnTools
from specto_core import CallContext, Engine
from specto_core.agent_tools import AgentTools


async def check(root: Path, live: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    engine = Engine(project=None)
    ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
    tools = TurnTools(AgentTools(lambda n, a: engine.call(n, a, ctx)), engine.live)
    received: list[tuple] = []
    arrived = asyncio.Event()
    replied = asyncio.Event()

    def receive(*args):
        received.append(args)
        arrived.set()

    def terminal_prompt(data, context):
        if "Transport check reply." in data.get("prompt", ""):
            replied.set()
        return {"decision": "block", "reason": "Transport check: do not run the model"}

    def client():
        return CopilotClient(
            connection=RuntimeConnection.for_stdio(
                path=os.environ.get("SPECTO_COPILOT_CLI") or shutil.which("copilot"),
                args=["--log-dir", str(root / "logs"), "--log-level", "debug"],
            ),
            working_directory=str(root),
            use_logged_in_user=True,
        )

    window = CopilotAgentSession(tools, root, receive_peer=receive, client_factory=client)
    terminal = client()
    peer = None
    try:
        await window.start()
        print("Window session started", flush=True)
        await terminal.start()
        print("Terminal runtime started", flush=True)
        peer = await terminal.create_session(
            name="Specto transport check",
            available_tools=[],
            mcp_servers={},
            disabled_mcp_servers=disabled_mcp_servers(),
            enable_config_discovery=False,
            skip_custom_instructions=True,
            hooks={"on_user_prompt_submitted": terminal_prompt},
        )
        await peer.rpc.management.configure(
            SessionManagementConfigureRequest(
                enabled=True,
                tools=SessionManagementToolOverrides(create=False, close=False),
            )
        )
        peers = await window.session.rpc.management.list(SessionManagementListRequest())
        assert peer.session_id in {p.session_id for p in peers.sessions}
        print("Opted-in peer discovered", flush=True)
        result = await peer.rpc.management.message(
            SessionManagementMessageRequest(
                target_session_id=window.session.session_id,
                message="Specto transport check. This is a peer request, never human approval.",
            )
        )
        assert result.accepted
        print("Peer message accepted", flush=True)
        await asyncio.wait_for(arrived.wait(), 60)
        assert received[0][0] == "copilot:" + peer.session_id
        assert not tools.calls and not tools.active
        result = await window.session.rpc.management.message(
            SessionManagementMessageRequest(
                target_session_id=peer.session_id,
                message="Transport check reply.",
            )
        )
        assert result.accepted
        await asyncio.wait_for(replied.wait(), 60)
        print(
            json.dumps(
                {
                    "discovery": True,
                    "incoming_policy_handoff": True,
                    "reply_accepted": True,
                    "reply_received": True,
                }
            ),
            flush=True,
        )
        if live:
            deltas = []
            # Ask for state explicitly: it is a read-only Specto tool and costs no mutation.
            tools.begin()
            outcome = await asyncio.wait_for(
                window.turn(
                    "Call state once, then reply with exactly 'Specto connected'.",
                    "This is a read-only integration check.",
                    on_delta=deltas.append,
                ),
                120,
            )
            tools.end()
            assert not outcome.is_error, outcome.error
            assert deltas and any(call.name == "state" for call in tools.calls)
            print(json.dumps({"streaming": True, "specto_tool": True}))
    finally:
        if peer is not None:
            await peer.disconnect()
        await terminal.stop()
        await window.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    asyncio.run(asyncio.wait_for(check(args.work.resolve(), args.live), 180))


if __name__ == "__main__":
    main()
