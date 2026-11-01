"""Agent sessions: one SDK connection per MCP session, so every call carries
``origin="agent:<session>"`` into the submit's provenance (contracts section 14).

Submits are whole-document and atomic, so a session holds no open transaction; the
server only maps session ids to connections and closes idle ones.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from specto_sdk import Client, TransportError

ConnectFn = Callable[[str], Client]  # origin -> connected client


@dataclass
class SessionState:
    id: str
    client: Client
    last_used: float = field(default_factory=time.monotonic)

    @property
    def origin(self) -> str:
        return f"agent:{self.id}"


class AgentSessions:
    def __init__(
        self,
        connect: ConnectFn,
        *,
        default_session: str | None = None,
        session_ttl_s: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect = connect
        self.default_session = default_session or uuid.uuid4().hex[:8]
        self.session_ttl_s = session_ttl_s
        self.clock = clock
        self._sessions: dict[str, SessionState] = {}

    # -- lookup --------------------------------------------------------------------------
    def key(self, session_id: str | None) -> str:
        return session_id or self.default_session

    def get(self, session_id: str | None = None) -> SessionState:
        self.expire()
        key = self.key(session_id)
        st = self._sessions.get(key)
        if st is None or st.client.closed:
            st = SessionState(id=key, client=self._connect(f"agent:{key}"))
            self._sessions[key] = st
        st.last_used = self.clock()
        return st

    def __len__(self) -> int:
        return len(self._sessions)

    # -- lifecycle ---------------------------------------------------------------------------
    def expire(self) -> list[str]:
        """Close sessions idle longer than ``session_ttl_s``."""
        now = self.clock()
        gone = []
        for key, st in list(self._sessions.items()):
            if now - st.last_used > self.session_ttl_s:
                self.close(key)
                gone.append(key)
        return gone

    def close(self, session_id: str | None = None) -> None:
        st = self._sessions.pop(self.key(session_id), None)
        if st is None:
            return
        with contextlib.suppress(TransportError):
            st.client.close()

    def close_all(self) -> None:
        for key in list(self._sessions):
            self.close(key)

    def info(self) -> list[dict[str, Any]]:
        return [{"session": st.id, "origin": st.origin} for st in self._sessions.values()]
