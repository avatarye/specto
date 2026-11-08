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
target, up, fov_y, near, far, cut}`` (``cut``: a jump, not a move; a source may reset what
it accumulates over frames), ``set_time {t}``, ``pause``, ``resume``, ``shutdown``.
A source adds its own (``load``, ``pick``, ``query_tree`` ...).

Neither side waits on the other (SPEC-33). The host queues its requests and a writer
thread writes them, so a source that stops reading never blocks the GUI thread; queued
``set_camera``, ``set_time`` and ``resize`` collapse to the newest (a ``cut`` among them
survives: the newest goes out as a cut), and the id of a request given way never answers.
The source reads on while a request is in hand: a handler with no answer yet returns a
``Future`` and the answer goes out when it resolves, so answers may come out of request
order (ADR 0026).

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
from dataclasses import dataclass
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
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        self._reader = threading.Thread(target=self._read, name="frame-control", daemon=True)
        self._reader.start()

    def wait_eof(self, timeout: float = 2.0) -> None:
        """Before the source exits: wait (up to ``timeout``) for the host to close stdin
        and the reader to end. A host may close stdin a moment after ``shutdown`` (it closes
        in the background, SPEC-87); finalizing with the reader still blocked on a buffered
        stdin aborts Python ("Fatal Python error: _enter_buffered_busy")."""
        if self._reader is not None:
            self._reader.join(timeout)

    def emit(self, event: str, **params: Any) -> None:
        self._write({"event": event, "params": params})

    def _write(self, msg: dict) -> None:
        self._write_line(json.dumps(msg, separators=(",", ":")).encode() + b"\n")

    def _write_line(self, line: bytes) -> None:
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
        """Answer request ``rid``; a result that does not serialise answers an error, so
        the host never waits one out."""
        if rid is None:
            return
        if error is None:
            try:
                line = json.dumps({"id": rid, "result": result or {}}, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                error = exc
            else:
                self._write_line(line.encode() + b"\n")
                return
        self._write({"id": rid, "error": {"message": str(error) or type(error).__name__}})

    def _answer_later(self, rid: Any, future: Future) -> None:
        """A future's answer, on whatever thread resolved it: nothing may escape into the
        future's callback, where it would be dropped and the host would wait it out."""
        try:
            if future.cancelled():
                self._answer(rid, None, SourceError("cancelled"))
                return
            error = future.exception()
            self._answer(rid, None if error is not None else future.result(), error)
        except Exception as exc:
            self._answer(rid, None, exc)


# -- host side -----------------------------------------------------------------------
#: requests that set one piece of the source's state, the newest winning: a queued one
#: not yet written gives way to a newer one of its kind. They touch separate state, so
#: they may pass each other; any other request keeps its place between them
LATEST_WINS = frozenset({"set_camera", "set_time", "resize"})


def _merged(old: dict, new: dict) -> dict:
    """``new``'s params over ``old``'s: a ``cut`` (the protocol's camera jump) survives the
    moves after it."""
    return {**new, "cut": True} if old.get("cut") else new


@dataclass
class Outgoing:
    """A request queued for the writer thread; ``collapsible``: a newer one of its method
    may take its place (:data:`LATEST_WINS`, from :meth:`FrameSourceProcess.send`)."""

    msg: dict
    collapsible: bool = False


class CloseStdin:
    """Queued last: the writer closes the source's stdin (never under its own write)."""


CLOSE_STDIN = CloseStdin()


class FrameSourceProcess:
    """The host's handle on one source process.

    Requests go out through a writer thread (SPEC-33): :meth:`send` and :meth:`call`
    queue the request and return at once, so a source busy with a slow command (its
    control pipe full) never blocks the caller, the GUI thread among them. Queued
    :data:`LATEST_WINS` requests from :meth:`send` collapse to the newest."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._ids = itertools.count(1)
        #: calls waiting for their answer, by id; callers, the reader and the writer
        #: touch it, always under ``_pending_lock``
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._pending_lock = threading.Lock()
        self._events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self._held: list[tuple[str, dict]] = []
        #: requests not written yet, oldest first
        self._out: deque[Outgoing | CloseStdin] = deque()
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
        with self._pending_lock:
            self._pending[rid] = (done, box)
        try:
            self._send({"id": rid, "method": method, "params": params})
        except SourceError:
            self._take_waiter(rid)
            raise
        if not done.wait(timeout):
            self._take_waiter(rid)
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
            self._close_stdin()
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()  # breaks the pipe under a write that hangs
                self.proc.wait()
        return self.proc.returncode

    def close_in_background(self, timeout: float = 5.0) -> threading.Thread:
        """:meth:`close` on a thread of its own, so the caller (the GUI thread) never
        waits for a source that finishes its step first (SPEC-87: a first frame may take
        seconds). The thread is not a daemon: an interpreter that exits meanwhile waits
        for it, so the source still stops or is killed and no process outlives the app."""
        reaper = threading.Thread(
            target=self.close, args=(timeout,), name=f"frame-source-close-{self.proc.pid}"
        )
        reaper.start()
        return reaper

    def _send(self, msg: dict, collapsible: bool = False) -> None:
        """Queue ``msg`` for the writer; a collapsible one replaces a queued one of its
        method among the collapsible requests at the queue's end."""
        with self._out_ready:
            if self._gone is not None:
                raise SourceError(f"the source is gone: {self._gone}")
            if collapsible:
                for entry in reversed(self._out):
                    if not isinstance(entry, Outgoing) or not entry.collapsible:
                        break  # another request: nothing passes it
                    if entry.msg["method"] == msg["method"]:
                        entry.msg = {**msg, "params": _merged(entry.msg["params"], msg["params"])}
                        return
            self._out.append(Outgoing(msg, collapsible))
            self._out_ready.notify()

    def _close_stdin(self) -> None:
        with self._out_ready:
            self._out.append(CLOSE_STDIN)
            self._out_ready.notify()

    def _take_waiter(self, rid: Any) -> tuple[threading.Event, list] | None:
        with self._pending_lock:
            return self._pending.pop(rid, None)

    def _write(self) -> None:
        """The writer thread: the queued requests, oldest first, into the source's stdin."""
        while True:
            with self._out_ready:
                while not self._out:
                    self._out_ready.wait()
                entry = self._out.popleft()
            try:
                if isinstance(entry, CloseStdin):
                    self._fail_pending(self._stop_queue("its control channel is closed"))
                    self.proc.stdin.close()
                    return
                line = json.dumps(entry.msg, separators=(",", ":")).encode() + b"\n"
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                dropped = self._stop_queue(str(exc) or type(exc).__name__)
                lost = [entry.msg, *dropped] if isinstance(entry, Outgoing) else dropped
                self._fail_pending(lost)
                return

    def _stop_queue(self, why: str) -> list[dict]:
        """Take no more requests (``why``); the ones still queued, never to be written."""
        with self._out_ready:
            self._gone = why
            dropped = [e.msg for e in self._out if isinstance(e, Outgoing)]
            self._out.clear()
        return dropped

    def _fail_pending(self, msgs: list[dict]) -> None:
        """Answer the waiters of requests that never reached the source."""
        for msg in msgs:
            waiter = self._take_waiter(msg.get("id"))
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
            waiter = self._take_waiter(msg.get("id"))
            if waiter is not None:
                waiter[1].append(msg)
                waiter[0].set()
        code = self.proc.wait()
        if self._stderr_done is not None:
            self._stderr_done.join(2.0)  # its last words before ``exited``
        with self._pending_lock:
            waiting, self._pending = list(self._pending.values()), {}
        for done, box in waiting:
            box.append({"error": {"message": f"the source exited ({code})"}})
            done.set()
        self._close_stdin()  # the writer ends too
        self._events.put(("exited", {"code": code}))

    def _read_stderr(self) -> None:
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            self._stderr.append(line)
            with contextlib.suppress(Exception):  # no console (a windowed app): kept only
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
