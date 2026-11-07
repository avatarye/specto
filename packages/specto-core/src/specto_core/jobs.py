"""Async job model (HLD 4.2): the interface, without a concrete pool.

Any command that may exceed ~50 ms returns ``{job: "j12"}``; ``job.status``,
``job.cancel`` and ``job.*`` events carry progress. The app provides a process-pool
``JobManager``; ``InlineJobManager`` runs jobs synchronously for tests and headless use.
"""

from __future__ import annotations

import threading
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from .clock import now_iso
from .errors import NotFound
from .events import EventStream

JobState = Literal["queued", "running", "done", "failed", "cancelled"]


class Progress:
    """The ``progress`` a job function gets: ``progress(p, message)`` reports, and
    ``progress.cancelled()`` says whether the job was cancelled, so a long job (a copy,
    a slow read) can stop instead of running to a result nobody takes."""

    def __init__(
        self,
        report: Callable[[float, str], None],
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._report = report
        self._cancelled = cancelled or (lambda: False)

    def __call__(self, p: float, message: str = "") -> None:
        self._report(p, message)

    def cancelled(self) -> bool:
        return bool(self._cancelled())


def jsonable(value: Any) -> Any:
    """A job result as JSON for ``job.status``/``job.list``: models dump, shared-memory
    results report their ``BufferSpec`` (the bytes stay with the GUI-side owner), and
    anything else unknown becomes its type name instead of failing serialisation."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    spec = getattr(value, "spec", None)
    if isinstance(spec, BaseModel):
        return {"buffer": spec.model_dump(mode="json")}
    return {"type": type(value).__name__}


class JobInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str
    state: JobState = "queued"
    progress: float = 0.0  # 0..1
    message: str = ""
    canvas: str | None = None
    created: str = Field(default_factory=now_iso)
    finished: str | None = None
    result: Any = None
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    @field_serializer("result")
    def _result_json(self, value: Any) -> Any:
        return jsonable(value)


ProgressFn = Callable[[float, str], None]
JobFn = Callable[..., Any]  # fn(*args, progress: ProgressFn, **kwargs) -> result


class JobManager(ABC):
    """Submit work, track it, cancel it. Results are delivered on the GUI thread by the
    app's implementation; core never blocks on a job."""

    def __init__(self, events: EventStream | None = None) -> None:
        self.events = events
        self._jobs: dict[str, JobInfo] = {}
        self._n = 0
        self._lock = threading.Lock()

    def _new(self, kind: str, canvas: str | None) -> JobInfo:
        with self._lock:
            self._n += 1
            job = JobInfo(id=f"j{self._n}", kind=kind, canvas=canvas)
            self._jobs[job.id] = job
        return job

    @abstractmethod
    def submit(
        self,
        kind: str,
        fn: JobFn,
        *args: Any,
        canvas: str | None = None,
        on_done: Callable[[JobInfo], None] | None = None,
        **kwargs: Any,
    ) -> JobInfo:
        """Queue ``fn(*args, progress=..., **kwargs)``. Returns immediately."""

    @abstractmethod
    def cancel(self, job_id: str) -> JobInfo: ...

    def status(self, job_id: str) -> JobInfo:
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFound(f"no job {job_id!r}", fix="job.list shows known jobs")
        return job

    def list(self, *, canvas: str | None = None, active_only: bool = False) -> list[JobInfo]:
        out = [j for j in self._jobs.values() if canvas is None or j.canvas == canvas]
        if active_only:
            out = [j for j in out if not j.done]
        return out

    # -- helpers for implementations ----------------------------------------------------
    def _emit(self, kind: str, job: JobInfo) -> None:
        if self.events is not None:
            self.events.emit(
                kind,
                {
                    "job": job.id,
                    "kind": job.kind,
                    "state": job.state,
                    "progress": job.progress,
                    "message": job.message,
                    "canvas": job.canvas,
                    "error": job.error,
                },
            )

    def _progress(self, job: JobInfo, p: float, msg: str = "") -> None:
        job.progress = max(0.0, min(1.0, float(p)))
        if msg:
            job.message = msg
        self._emit("job.progress", job)

    def _finish(
        self, job: JobInfo, *, result: Any = None, error: str | None = None, cancelled: bool = False
    ) -> None:
        job.finished = now_iso()
        if cancelled:
            job.state = "cancelled"
            self._emit("job.failed", job)
        elif error is not None:
            job.state, job.error = "failed", error
            self._emit("job.failed", job)
        else:
            job.state, job.result, job.progress = "done", result, 1.0
            self._emit("job.done", job)


class ThreadJobManager(JobManager):
    """Runs each job on its own thread; ``on_done`` callbacks are delivered on the thread
    that calls ``poll`` (the GUI thread in the app, the test's thread in tests). For
    link resolves (ADR 0016) in headless runs and tests; the app uses the worker pool."""

    def __init__(self, events: EventStream | None = None) -> None:
        super().__init__(events)
        self._threads: dict[str, threading.Thread] = {}
        self._finished: list[tuple[JobInfo, Callable[[JobInfo], None] | None]] = []
        self._ready = threading.Condition()

    def submit(
        self,
        kind: str,
        fn: JobFn,
        *args: Any,
        canvas: str | None = None,
        on_done: Callable[[JobInfo], None] | None = None,
        **kwargs: Any,
    ) -> JobInfo:
        job = self._new(kind, canvas)
        job.state = "running"

        def run() -> None:
            try:
                progress = Progress(
                    lambda p, m="": self._progress(job, p, m), lambda: job.state == "cancelled"
                )
                result = fn(*args, progress=progress, **kwargs)
                error = None
            except Exception as e:
                result, error = None, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            with self._ready:
                if not job.done:  # cancelled jobs stay cancelled
                    self._finish(job, result=result, error=error)
                self._finished.append((job, on_done))
                self._threads.pop(job.id, None)
                self._ready.notify_all()

        t = threading.Thread(target=run, name=f"specto-job-{job.id}", daemon=True)
        self._threads[job.id] = t
        t.start()
        return job

    def cancel(self, job_id: str) -> JobInfo:
        job = self.status(job_id)
        with self._ready:
            if not job.done:
                self._finish(job, cancelled=True)
        return job

    def poll(self, timeout: float = 0.0) -> int:
        """Deliver the ``on_done`` of every finished job here. Returns how many."""
        with self._ready:
            if not self._finished and timeout > 0:
                self._ready.wait(timeout)
            batch, self._finished = self._finished, []
        for job, cb in batch:
            if cb is not None:
                cb(job)
        return len(batch)

    def wait(self, job_ids: list[str] | None = None, timeout: float = 30.0) -> bool:
        """Block until the given jobs (default: all) finished running. False on timeout."""
        import time as _time

        deadline = _time.monotonic() + timeout
        with self._ready:
            while True:
                pending = [
                    j
                    for j in (job_ids if job_ids is not None else list(self._threads))
                    if j in self._threads
                ]
                if not pending:
                    return True
                left = deadline - _time.monotonic()
                if left <= 0:
                    return False
                self._ready.wait(left)

    def shutdown(self, timeout: float = 5.0) -> None:
        self.wait(timeout=timeout)
        self.poll()


class InlineJobManager(JobManager):
    """Runs the job synchronously inside ``submit``. For tests and headless commands."""

    def submit(
        self,
        kind: str,
        fn: JobFn,
        *args: Any,
        canvas: str | None = None,
        on_done: Callable[[JobInfo], None] | None = None,
        **kwargs: Any,
    ) -> JobInfo:
        job = self._new(kind, canvas)
        job.state = "running"
        try:
            progress = Progress(lambda p, m="": self._progress(job, p, m))
            result = fn(*args, progress=progress, **kwargs)
            self._finish(job, result=result)
        except Exception as e:
            self._finish(job, error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        if on_done is not None:
            on_done(job)
        return job

    def cancel(self, job_id: str) -> JobInfo:
        job = self.status(job_id)
        if not job.done:
            self._finish(job, cancelled=True)
        return job
