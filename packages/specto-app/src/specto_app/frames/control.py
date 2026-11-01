"""The control channel: JSON lines on the source process's stdin and stdout.

Why stdio and not a named pipe: the host spawns one source per view and owns it, so the
pipes need no name, cannot be reached by another process, and close when the host dies,
which ends the source (stdin EOF) instead of leaving an orphan holding VRAM. Every
renderer environment (ovrtx's venv, Blender's Python, the Storm build) has stdio. The
source writes its logs to stderr; stdout carries the protocol only.

Messages, one JSON object per line:

- host -> source: ``{"id": 3, "method": "resize", "params": {"width": 1280, ...}}``
- source -> host: ``{"id": 3, "result": {...}}`` or ``{"id": 3, "error": {"message": ...}}``
- source -> host events: ``{"event": "ready", "params": {...}}``

Methods every source answers: ``resize {width, height}``, ``set_camera {position,
target, up, fov_y, near, far}``, ``set_time {t}``, ``pause``, ``resume``, ``shutdown``.
A source adds its own (``load``, ``pick``, ``query_tree`` ...).

Events: ``ready {ring, wake, width, height, pid}`` once the first ring exists,
``ring {name}`` when frames moved to a bigger ring, ``health {state, message}``, and
``exited {code}``, made by the host when the source's stdout closes.

The host reads the source's stderr through: each line goes on to the host's own stderr,
and the last ``STDERR_TAIL`` stay for :meth:`FrameSourceProcess.stderr_tail` (what a view
says about a source that died, #155).
"""

from __future__ import annotations

import contextlib
import itertools
import json
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import IO, Any

STDERR_TAIL = 200  # the source's last stderr lines kept by the host


class SourceError(RuntimeError):
    """The source answered a request with an error, or went away."""


# -- source side ---------------------------------------------------------------------
class ControlServer:
    """Reads requests from stdin on a daemon thread and answers through ``handler``
    (``handler(method, params) -> dict``; raising answers with an error). ``on_eof``
    runs when the host closes stdin."""

    def __init__(
        self,
        handler: Callable[[str, dict], dict | None],
        on_eof: Callable[[], None],
        stdin: IO[bytes] | None = None,
        stdout: IO[bytes] | None = None,
    ) -> None:
        self._handler = handler
        self._on_eof = on_eof
        self._in = stdin or sys.stdin.buffer
        self._out = stdout or sys.stdout.buffer
        if stdout is None:
            sys.stdout = sys.stderr  # a stray print must not corrupt the protocol
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._read, name="frame-control", daemon=True).start()

    def emit(self, event: str, **params: Any) -> None:
        self._write({"event": event, "params": params})

    def _write(self, msg: dict) -> None:
        line = json.dumps(msg, separators=(",", ":")).encode() + b"\n"
        with self._lock:
            try:
                self._out.write(line)
                self._out.flush()
            except (OSError, ValueError):
                pass  # the host is gone; stdin EOF follows

    def _read(self) -> None:
        for raw in self._in:
            try:
                msg = json.loads(raw)
            except ValueError:
                print(f"[WARN] bad control line: {raw[:200]!r}", file=sys.stderr)
                continue
            rid = msg.get("id")
            try:
                result = self._handler(msg.get("method", ""), msg.get("params") or {})
            except Exception as exc:
                if rid is not None:
                    self._write({"id": rid, "error": {"message": str(exc)}})
                continue
            if rid is not None:
                self._write({"id": rid, "result": result or {}})
        self._on_eof()


# -- host side -----------------------------------------------------------------------
class FrameSourceProcess:
    """The host's handle on one source process."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self._held: list[tuple[str, dict]] = []
        self._write_lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=STDERR_TAIL)
        self._stderr_done: threading.Thread | None = None
        if proc.stderr is not None:
            self._stderr_done = threading.Thread(
                target=self._read_stderr, name="frame-source-err", daemon=True
            )
            self._stderr_done.start()
        threading.Thread(target=self._read, name="frame-source-out", daemon=True).start()

    @classmethod
    def spawn(cls, argv: Sequence[str], **popen: Any) -> FrameSourceProcess:
        popen.setdefault("stderr", subprocess.PIPE)
        proc = subprocess.Popen(
            list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, **popen
        )
        return cls(proc)

    def send(self, method: str, **params: Any) -> int:
        """Fire a request without waiting (camera moves from the GUI thread)."""
        rid = next(self._ids)
        self._send({"id": rid, "method": method, "params": params})
        return rid

    def call(self, method: str, timeout: float = 10.0, **params: Any) -> dict:
        """Send a request and wait for its result."""
        rid = next(self._ids)
        done, box = threading.Event(), []
        self._pending[rid] = (done, box)
        self._send({"id": rid, "method": method, "params": params})
        if not done.wait(timeout):
            self._pending.pop(rid, None)
            raise SourceError(f"{method}: no answer in {timeout} s")
        msg = box[0]
        if "error" in msg:
            raise SourceError(f"{method}: {msg['error'].get('message', '')}")
        return msg.get("result", {})

    def poll(self) -> list[tuple[str, dict]]:
        """Every event that arrived since the last poll, oldest first."""
        out, self._held = self._held, []
        with contextlib.suppress(queue.Empty):
            while True:
                out.append(self._events.get_nowait())
        return out

    def stderr_tail(self) -> list[str]:
        """The source's last stderr lines, oldest first (complete once ``exited`` came)."""
        return list(self._stderr)

    def wait_event(self, name: str, timeout: float = 10.0) -> dict:
        """The params of the next ``name`` event; other events stay for ``poll``."""
        for i, (ev, params) in enumerate(self._held):
            if ev == name:
                del self._held[i]
                return params
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            try:
                ev, params = self._events.get(timeout=max(0.0, left))
            except queue.Empty:
                raise SourceError(f"no {name!r} event in {timeout} s") from None
            if ev == name:
                return params
            self._held.append((ev, params))
            if ev == "exited":
                raise SourceError(f"the source exited ({params.get('code')}) before {name!r}")

    def close(self, timeout: float = 5.0) -> int | None:
        """Ask the source to stop, close its stdin and wait; kill it if it hangs."""
        if self.proc.poll() is None:
            with contextlib.suppress(SourceError):
                self.send("shutdown")
            with contextlib.suppress(OSError):
                self.proc.stdin.close()
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        return self.proc.returncode

    def _send(self, msg: dict) -> None:
        line = json.dumps(msg, separators=(",", ":")).encode() + b"\n"
        with self._write_lock:
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise SourceError(f"the source is gone: {exc}") from None

    def _read(self) -> None:
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if "event" in msg:
                self._events.put((msg["event"], msg.get("params") or {}))
                continue
            waiter = self._pending.pop(msg.get("id"), None)
            if waiter is not None:
                waiter[1].append(msg)
                waiter[0].set()
        code = self.proc.wait()
        if self._stderr_done is not None:
            self._stderr_done.join(2.0)  # its last words before ``exited``
        for done, box in self._pending.values():
            box.append({"error": {"message": f"the source exited ({code})"}})
            done.set()
        self._pending.clear()
        self._events.put(("exited", {"code": code}))

    def _read_stderr(self) -> None:
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            self._stderr.append(line)
            with contextlib.suppress(Exception):  # no console (a windowed app): kept only
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
