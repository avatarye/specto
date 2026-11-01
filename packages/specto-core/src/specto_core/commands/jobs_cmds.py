"""job.status / job.cancel / job.list (HLD 4.2)."""

from __future__ import annotations

from typing import Any

from ..registry import Args, CommandContext, Result, command


class JobArgs(Args):
    job: str


class JobResult(Result):
    job: dict[str, Any]


@command("job.status", doc="State, progress and result of a job.", read_only=True, scope="global")
def job_status(ctx: CommandContext, args: JobArgs) -> JobResult:
    return JobResult(job=ctx.engine.jobs.status(args.job).model_dump())


@command("job.cancel", doc="Cancel a job.", scope="global", effects=["job"])
def job_cancel(ctx: CommandContext, args: JobArgs) -> JobResult:
    return JobResult(job=ctx.engine.jobs.cancel(args.job).model_dump())


class JobListArgs(Args):
    canvas: str | None = None
    active_only: bool = False


class JobListResult(Result):
    jobs: list[dict[str, Any]]


@command("job.list", doc="Known jobs.", read_only=True, scope="global")
def job_list(ctx: CommandContext, args: JobListArgs) -> JobListResult:
    jobs = ctx.engine.jobs.list(canvas=args.canvas, active_only=args.active_only)
    return JobListResult(jobs=[j.model_dump() for j in jobs])
