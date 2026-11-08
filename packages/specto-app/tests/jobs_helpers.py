"""A job manager that holds every job until the test runs it, so the 'resolving' state
(and anything that happens while a job is in flight) stays observable."""

from __future__ import annotations

from specto_core.jobs import InlineJobManager


class HeldJobs(InlineJobManager):
    """Holds every job until ``run_next``."""

    def __init__(self) -> None:
        super().__init__()
        self.held: list = []

    def submit(self, kind, fn, *args, canvas=None, on_done=None, **kwargs):
        job = self._new(kind, canvas)
        self.held.append((job, fn, args, kwargs, on_done))
        return job

    def count(self, kind: str | None = None) -> int:
        """How many jobs are held (of ``kind``, when given)."""
        return sum(1 for job, *_r in self.held if kind is None or job.kind == kind)

    def run_next(self, kind: str | None = None):
        """Run the oldest held job (the oldest of ``kind``, when given: a gallery's
        thumbnail batch may be held beside a link resolve)."""
        i = next(i for i, (job, *_r) in enumerate(self.held) if kind is None or job.kind == kind)
        job, fn, args, kwargs, on_done = self.held.pop(i)
        result = fn(*args, progress=lambda p, m="": None, **kwargs)
        self._finish(job, result=result)
        if on_done is not None:
            on_done(job)
        return job
