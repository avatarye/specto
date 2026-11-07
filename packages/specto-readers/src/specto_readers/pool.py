"""Qt-free worker process pool: a ``JobManager`` over ``spawn`` processes (HLD 4.1-4.3).

- One duplex pipe per worker; one job in flight per worker; a FIFO of pending jobs.
- ``fn(*args, progress=..., **kwargs)`` runs in the worker (it must be importable:
  spawn pickles it by reference). An ``ndarray`` or ``TypedBuffer`` result is copied
  into ``multiprocessing.shared_memory`` and comes back as a ``SharedResult`` the GUI
  thread attaches zero-copy; lists of them work too; anything else is pickled.
- The worker keeps its shared-memory handle until the parent has attached and sent
  ``release`` (Windows frees a segment once the last handle closes). The parent owns the
  segment from then on: ``SharedResult.release()`` closes and unlinks it.
- Workers run below normal priority (``lower_priority``; ``SPECTO_WORKER_PRIORITY=normal``
  keeps them level), so decoding on every core never starves the GUI and render threads.
- Crash isolation: a worker that dies fails only the job it was running (with the exit
  code in the error) and is respawned in place; ``respawns`` counts them.
- ``poll()`` drains worker messages and runs ``on_done`` callbacks on the calling thread
  (the GUI thread in the app, from a timer or a socket notifier). ``wait()`` polls until
  the given jobs finish (tests, CLI, benchmarks).
"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing import shared_memory
from multiprocessing.connection import Connection, wait
from typing import Any

import numpy as np

from specto_core.events import EventStream
from specto_core.jobs import JobFn, JobInfo, JobManager
from specto_core.kinds import BufferSpec, TypedBuffer

DoneFn = Callable[[JobInfo], None]


def default_worker_count() -> int:
    return max(1, (os.cpu_count() or 4) - 2)


class SharedResult:
    """A decoded buffer living in shared memory, attached zero-copy in this process.
    Call ``release()`` when the consumer (texture upload, Monaco) is done with it."""

    def __init__(self, spec: BufferSpec, shm: shared_memory.SharedMemory) -> None:
        self.spec = spec
        self._shm: shared_memory.SharedMemory | None = shm
        self.buffer = TypedBuffer.attach(spec, shm.buf)

    @property
    def array(self) -> np.ndarray:
        if self.buffer.array is None:
            raise ValueError("shared result was released")
        return self.buffer.array

    @property
    def released(self) -> bool:
        return self._shm is None

    def release(self) -> None:
        shm = self._shm
        if shm is None:
            return
        self.buffer.array = None
        try:
            shm.close()
        except BufferError as e:
            # a numpy view handed out by ``array`` is still alive somewhere
            raise RuntimeError(
                f"cannot release {self!r}: a view of the buffer is still referenced"
            ) from e
        self._shm = None
        with contextlib.suppress(FileNotFoundError):
            shm.unlink()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.release()

    def __repr__(self) -> str:
        return f"SharedResult({self.spec.dtype} {self.spec.shape} shm={self.spec.shm!r})"


# -- worker side --------------------------------------------------------------------------


def _export(obj: Any, held: dict[str, shared_memory.SharedMemory]) -> tuple[str, Any]:
    if isinstance(obj, np.ndarray):
        obj = TypedBuffer.from_numpy(obj)
    if isinstance(obj, TypedBuffer):
        arr = np.ascontiguousarray(obj.to_numpy())
        if arr.nbytes == 0:
            return ("array", (obj.spec.model_dump(), b""))
        shm = shared_memory.SharedMemory(create=True, size=arr.nbytes)
        dst = np.ndarray(arr.shape, dtype=arr.dtype, buffer=shm.buf)
        dst[...] = arr
        del dst
        held[shm.name] = shm
        spec = obj.spec.model_copy(update={"shm": shm.name, "offset": 0})
        return ("shm", spec.model_dump())
    if isinstance(obj, BufferSpec):
        return ("spec", obj.model_dump())
    if isinstance(obj, list | tuple):
        return ("list", [_export(x, held) for x in obj])
    return ("value", obj)


def lower_priority() -> bool:
    """Run this process below normal priority (SPEC-1): a pool of decoders on every
    core must never take the CPU from the GUI and render threads, or scrolling stalls
    while thumbnails decode. True when it was lowered."""
    if os.environ.get("SPECTO_WORKER_PRIORITY", "").strip().lower() == "normal":
        return False
    try:
        if os.name == "nt":
            import ctypes

            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            return bool(k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS))
        os.nice(5)
        return True
    except (OSError, AttributeError):
        return False


def _worker_main(conn: Connection, worker_id: int) -> None:
    held: dict[str, shared_memory.SharedMemory] = {}
    lower_priority()
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            break
        op = msg[0]
        if op == "stop":
            break
        if op == "release":
            for name in msg[1]:
                shm = held.pop(name, None)
                if shm is not None:
                    shm.close()
            continue
        if op != "run":
            continue
        _, job_id, fn, args, kwargs = msg

        def progress(p: float, m: str = "", _jid: str = job_id) -> None:
            conn.send(("progress", _jid, float(p), str(m)))

        try:
            result = fn(*args, progress=progress, **kwargs)
            conn.send(("done", job_id, _export(result, held)))
        except Exception as e:
            conn.send(("error", job_id, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))
    for shm in held.values():
        shm.close()


# -- parent side --------------------------------------------------------------------------


@dataclass
class _Worker:
    index: int
    proc: Any
    conn: Connection
    job: JobInfo | None = None
    started: float = 0.0
    cancelled: set[str] = field(default_factory=set)


@dataclass
class _Pending:
    job: JobInfo
    fn: JobFn
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class PoolJobManager(JobManager):
    """``JobManager`` backed by ``spawn`` worker processes. No Qt anywhere."""

    def __init__(
        self,
        workers: int | None = None,
        events: EventStream | None = None,
        *,
        start: bool = True,
    ) -> None:
        super().__init__(events)
        self.size = workers or default_worker_count()
        self.ctx = mp.get_context("spawn")
        self._workers: list[_Worker] = []
        self._pending: deque[_Pending] = deque()
        self._done_cbs: dict[str, DoneFn] = {}
        self._rlock = threading.RLock()
        self.respawns = 0
        self._stopped = False
        if start:
            self.start()

    # -- lifecycle -------------------------------------------------------------------
    def start(self) -> None:
        with self._rlock:
            while len(self._workers) < self.size:
                self._workers.append(self._spawn(len(self._workers)))
            self._stopped = False

    def _spawn(self, index: int) -> _Worker:
        parent, child = self.ctx.Pipe(duplex=True)
        proc = self.ctx.Process(
            target=_worker_main, args=(child, index), name=f"specto-worker-{index}", daemon=True
        )
        proc.start()
        child.close()
        return _Worker(index=index, proc=proc, conn=parent)

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._rlock:
            self._stopped = True
            for p in self._pending:
                self._finish(p.job, cancelled=True)
            self._pending.clear()
            for w in self._workers:
                with contextlib.suppress(OSError, ValueError):
                    w.conn.send(("stop",))
            deadline = time.monotonic() + timeout
            for w in self._workers:
                w.proc.join(max(0.0, deadline - time.monotonic()))
                if w.proc.is_alive():
                    w.proc.terminate()
                    w.proc.join(1.0)
                if w.job is not None and not w.job.done:
                    self._finish(w.job, error="pool shut down")
                w.conn.close()
            self._workers.clear()

    def __enter__(self) -> PoolJobManager:
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()

    @property
    def workers(self) -> list[int]:
        return [w.proc.pid for w in self._workers]

    @property
    def idle(self) -> int:
        return sum(1 for w in self._workers if w.job is None)

    @property
    def queued(self) -> int:
        return len(self._pending)

    # -- JobManager ----------------------------------------------------------------
    def submit(
        self,
        kind: str,
        fn: JobFn,
        *args: Any,
        canvas: str | None = None,
        on_done: DoneFn | None = None,
        **kwargs: Any,
    ) -> JobInfo:
        if self._stopped:
            raise RuntimeError("pool is shut down")
        job = self._new(kind, canvas)
        with self._rlock:
            if on_done is not None:
                self._done_cbs[job.id] = on_done
            self._pending.append(_Pending(job, fn, args, kwargs))
            self._dispatch()
        return job

    def cancel(self, job_id: str) -> JobInfo:
        job = self.status(job_id)
        with self._rlock:
            if job.done:
                return job
            for p in list(self._pending):
                if p.job is job:
                    self._pending.remove(p)
                    self._complete(job, cancelled=True)
                    return job
            for w in self._workers:
                if w.job is job:
                    # cannot interrupt a running decoder; drop its result when it lands
                    w.cancelled.add(job.id)
                    self._complete(job, cancelled=True)
                    return job
        return job

    # -- pumping ---------------------------------------------------------------------
    def poll(self, timeout: float = 0.0) -> int:
        """Handle worker messages for up to ``timeout`` seconds. Returns how many
        messages were handled. Call from the thread that owns the callbacks."""
        handled = 0
        with self._rlock:
            self._reap()
            if not self._workers:
                return 0
            by_conn = {w.conn: w for w in self._workers}
            by_sentinel = {w.proc.sentinel: w for w in self._workers}
            ready = wait(list(by_conn) + list(by_sentinel), timeout)
            for obj in ready:
                if obj in by_sentinel:
                    continue  # reaped below
                w = by_conn[obj]
                try:
                    while True:
                        msg = w.conn.recv()
                        handled += 1
                        self._handle(w, msg)
                        if not w.conn.poll():
                            break
                except (EOFError, OSError):
                    self._worker_died(w, "pipe closed")
            self._reap()
            self._dispatch()
        return handled

    def wait(
        self,
        jobs: list[str] | list[JobInfo] | None = None,
        timeout: float | None = None,
        *,
        poll_interval: float = 0.02,
    ) -> bool:
        """Poll until ``jobs`` (default: every active job) are done. False on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            targets = (
                [j if isinstance(j, JobInfo) else self.status(j) for j in jobs]
                if jobs is not None
                else self.list(active_only=True)
            )
            if all(j.done for j in targets):
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            self.poll(poll_interval)

    # -- internals -------------------------------------------------------------------
    def _dispatch(self) -> None:
        for w in self._workers:
            if not self._pending:
                return
            if w.job is not None:
                continue
            p = self._pending.popleft()
            try:
                w.conn.send(("run", p.job.id, p.fn, p.args, p.kwargs))
            except Exception as e:  # unpicklable fn/args or a broken pipe
                if isinstance(e, OSError | EOFError):
                    self._pending.appendleft(p)
                    self._worker_died(w, f"send failed: {e}")
                    return
                self._complete(p.job, error=f"{type(e).__name__}: {e}")
                continue
            w.job, w.started = p.job, time.monotonic()
            p.job.state = "running"

    def _reap(self) -> None:
        for w in list(self._workers):
            if not w.proc.is_alive():
                self._worker_died(w, f"exit code {w.proc.exitcode}")

    def _worker_died(self, w: _Worker, why: str) -> None:
        if w not in self._workers:
            return
        job, w.job = w.job, None
        with contextlib.suppress(OSError):
            w.conn.close()
        if w.proc.is_alive():
            w.proc.terminate()
        w.proc.join(0.5)
        if job is not None and not job.done:
            self._complete(
                job, error=f"worker {w.proc.pid} died ({why}) while running {job.kind} {job.id}"
            )
        if self._stopped:
            self._workers.remove(w)
            return
        self._workers[self._workers.index(w)] = self._spawn(w.index)
        self.respawns += 1

    def _handle(self, w: _Worker, msg: tuple[Any, ...]) -> None:
        op, job_id = msg[0], msg[1]
        job = w.job if (w.job is not None and w.job.id == job_id) else self._jobs.get(job_id)
        if op == "progress":
            if job is not None and not job.done:
                self._progress(job, msg[2], msg[3])
            return
        if w.job is job:
            w.job = None
        if op == "done":
            result, names = self._import(msg[2])
            if names:
                with contextlib.suppress(OSError):
                    w.conn.send(("release", names))
            if job is None or job.id in w.cancelled:
                w.cancelled.discard(job_id)
                _release_all(result)
                return
            self._complete(job, result=result)
        elif op == "error" and job is not None and not job.done:
            self._complete(job, error=msg[2])

    def _import(self, payload: tuple[str, Any]) -> tuple[Any, list[str]]:
        tag, body = payload
        if tag == "shm":
            spec = BufferSpec.model_validate(body)
            shm = shared_memory.SharedMemory(name=spec.shm)
            return SharedResult(spec, shm), [spec.shm]
        if tag == "array":
            spec = BufferSpec.model_validate(body[0])
            return TypedBuffer.attach(spec, body[1] or b"\0" * max(1, spec.nbytes)), []
        if tag == "spec":
            return BufferSpec.model_validate(body), []
        if tag == "list":
            out, names = [], []
            for x in body:
                v, n = self._import(x)
                out.append(v)
                names.extend(n)
            return out, names
        return body, []

    def _complete(
        self, job: JobInfo, *, result: Any = None, error: str | None = None, cancelled: bool = False
    ) -> None:
        self._finish(job, result=result, error=error, cancelled=cancelled)
        cb = self._done_cbs.pop(job.id, None)
        if cb is not None:
            cb(job)


def _release_all(obj: Any) -> None:
    if isinstance(obj, SharedResult):
        obj.release()
    elif isinstance(obj, list | tuple):
        for x in obj:
            _release_all(x)


def release_result(job: JobInfo) -> None:
    """Free the shared memory of a finished job's result (no-op for plain values)."""
    _release_all(job.result)
