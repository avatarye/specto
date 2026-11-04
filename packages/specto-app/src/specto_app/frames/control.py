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

Neither side waits on the other (SPEC-33). The host queues its requests and a writer
thread writes them, so a source that stops reading never blocks the GUI thread; queued
``set_camera``, ``set_time`` and ``resize`` collapse to the newest. The source reads on
while a request is in hand: a handler with no answer yet returns a ``Future`` and the
answer goes out when it resolves, after any answers to requests read meanwhile.

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
from concurrent.futures import Future
from typing import IO, Any

STDERR_TAIL = 200  # the source's last stderr lines kept by the host


class SourceError(RuntimeError):
    """The source answered a request with an error, or went away."""


# -- source side ---------------------------------------------------------------------
class ControlServer:
    """Reads requests from stdin on a daemon thread and answers through ``handler``
    (``handler(method, params) -> dict``; raising answers with an error). A handler that
    cannot answer at once returns a :class:`~concurrent.futures.Future` instead: the
    answer goes out when it resolves, and the thread reads on meanwhile (SPEC-33: a
    slow command must not stop the camera moves behind it). ``on_eof`` runs when the
    host closes stdin."""

    def __init__(
        self,
        handler: Callable[[str, dict], dict | Future | None],
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
                self._answer(rid, None, exc)
                continue
            if isinstance(result, Future):
                result.add_done_callback(lambda f, rid=rid: self._answer_later(rid, f))
            else:
                self._answer(rid, result, None)
        self._on_eof()

    def _answer(self, rid: Any, result: dict | None, error: BaseException | None) -> None:
        if rid is None:
            return
        if error is not None:
            self._write({"id": rid, "error": {"message": str(error)}})
        else:
            self._write({"id": rid, "result": result or {}})

    def _answer_later(self, rid: Any, future: Future) -> None:
        if future.cancelled():
            self._answer(rid, None, SourceError("cancelled"))
            return
        error = future.exception()
        self._answer(rid, None if error is not None else future.result(), error)


# -- host side -----------------------------------------------------------------------
#: requests that set one piece of the source's state, the newest winning: a queued one
#: not yet written gives way to a newer one of its kind. They touch separate state, so
#: they may pass each other; any other request keeps its place between them
LATEST_WINS = frozenset({"set_camera", "set_time", "resize"})


def _merged(old: dict, new: dict) -> dict:
    """``new``'s params over ``old``'s: a camera cut (a jump) survives the moves after it."""
    return {**new, "cut": True} if old.get("cut") else new


class FrameSourceProcess:
    """The host's handle on one source process.

    Requests go out through a writer thread (SPEC-33): :meth:`send` and :meth:`call`
    queue the request and return at once, so a source busy with a slow command (its
    control pipe full) never blocks the caller, the GUI thread among them. Queued
    :data:`LATEST_WINS` requests from :meth:`send` collapse to the newest."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self._held: list[tuple[str, dict]] = []
        #: requests not written yet: [msg, collapsible]; None closes the source's stdin
        self._out: deque[list | None] = deque()
        self._out_ready = threading.Condition()
        self._gone: str | None = None  # why the source's stdin took no more
        self._stderr: deque[str] = deque(maxlen=STDERR_TAIL)
        self._stderr_done: threading.Thread | None = None
        if proc.stderr is not None:
            self._stderr_done = threading.Thread(
                target=self._read_stderr, name="frame-source-err", daemon=True
            )
            self._stderr_done.start()
        threading.Thread(target=self._read, name="frame-source-out", daemon=True).start()
        self._writer = threading.Thread(target=self._write, name="frame-source-in", daemon=True)
        self._writer.start()

    @classmethod
    def spawn(cls, argv: Sequence[str], **popen: Any) -> FrameSourceProcess:
        popen.setdefault("stderr", subprocess.PIPE)
        proc = subprocess.Popen(
            list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, **popen
        )
        return cls(proc)

    def send(self, method: str, **params: Any) -> int:
        """Fire a request without waiting (camera moves from the GUI thread); it is
        queued, never written here. A :data:`LATEST_WINS` request replaces a queued one
        of its kind (its id then never answers)."""
        rid = next(self._ids)
        self._send({"id": rid, "method": method, "params": params}, method in LATEST_WINS)
        return rid

    def call(self, method: str, timeout: float = 10.0, **params: Any) -> dict:
        """Send a request and wait for its result."""
        rid = next(self._ids)
        done, box = threading.Event(), []
        self._pending[rid] = (done, box)
        try:
            self._send({"id": rid, "method": method, "params": params})
        except SourceError:
            self._pending.pop(rid, None)
            raise
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
        """Ask the source to stop, close its stdin (after what is queued) and wait; kill
        it if it hangs."""
        if self.proc.poll() is None:
            with contextlib.suppress(SourceError):
                self.send("shutdown")
            with self._out_ready:
                self._out.append(None)  # the writer closes stdin: never under its write
                self._out_ready.notify()
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()  # breaks the pipe under a write that hangs
                self.proc.wait()
        return self.proc.returncode

    def _send(self, msg: dict, collapsible: bool = False) -> None:
        """Queue ``msg`` for the writer; a collapsible one replaces a queued one of its
        method among the collapsible requests at the queue's end."""
        with self._out_ready:
            if self._gone is not None:
                raise SourceError(f"the source is gone: {self._gone}")
            if collapsible:
                for entry in reversed(self._out):
                    if entry is None or not entry[1]:
                        break  # another request: nothing passes it
                    if entry[0]["method"] == msg["method"]:
                        msg = {**msg, "params": _merged(entry[0]["params"], msg["params"])}
                        entry[0] = msg
                        return
            self._out.append([msg, collapsible])
            self._out_ready.notify()

    def _write(self) -> None:
        """The writer thread: the queued requests, oldest first, into the source's stdin."""
        while True:
            with self._out_ready:
                while not self._out:
                    self._out_ready.wait()
                entry = self._out.popleft()
            try:
                if entry is None:
                    self._fail_pending(self._stop_queue("its control channel is closed"))
                    self.proc.stdin.close()
                    return
                line = json.dumps(entry[0], separators=(",", ":")).encode() + b"\n"
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                dropped = self._stop_queue(str(exc) or type(exc).__name__)
                self._fail_pending([entry[0], *dropped] if entry is not None else dropped)
                return

    def _stop_queue(self, why: str) -> list[dict]:
        """Take no more requests (``why``); the ones still queued, never to be written."""
        with self._out_ready:
            self._gone = why
            dropped = [e[0] for e in self._out if e is not None]
            self._out.clear()
        return dropped

    def _fail_pending(self, msgs: list[dict]) -> None:
        """Answer the waiters of requests that never reached the source."""
        for msg in msgs:
            waiter = self._pending.pop(msg.get("id"), None)
            if waiter is not None:
                waiter[1].append({"error": {"message": f"the source is gone: {self._gone}"}})
                waiter[0].set()

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
        with self._out_ready:
            self._out.append(None)  # the writer ends too
            self._out_ready.notify()
        self._events.put(("exited", {"code": code}))

    def _read_stderr(self) -> None:
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            self._stderr.append(line)
            with contextlib.suppress(Exception):  # no console (a windowed app): kept only
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
