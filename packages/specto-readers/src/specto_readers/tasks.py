"""Picklable job entry points for the pool: decode one ref, or a batch, by decoder name.

The worker resolves the decoder from ``default_readers()`` in its own process; nothing
but plain data crosses the pipe.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from specto_core.errors import NotFound
from specto_core.jobs import JobInfo
from specto_core.kinds import DataRef, DecodeRequest, TypedBuffer

from .registry import default_readers

ProgressFn = Callable[[float, str], None]


def decode_task(
    decoder: str, ref: dict[str, Any], request: dict[str, Any], progress: ProgressFn
) -> TypedBuffer:
    reg = default_readers()
    return reg.decoder(decoder).decode(
        DataRef.model_validate(ref), DecodeRequest.model_validate(request)
    )


def map_refs(
    refs: list[Any],
    one: Callable[[DataRef], Any],
    progress: ProgressFn,
    *,
    every: int = 16,
) -> list[Any]:
    """``one(ref)`` for each ref (``DataRef`` dumps or models) of a batch job: a failing
    item becomes ``{"error": ..., "uri": ...}`` in its slot instead of failing the
    batch; progress every ``every`` items."""
    out: list[Any] = []
    n = max(1, len(refs))
    for i, r in enumerate(refs):
        ref = r if isinstance(r, DataRef) else DataRef.model_validate(r)
        try:
            out.append(one(ref))
        except Exception as e:
            out.append({"error": f"{type(e).__name__}: {e}", "uri": ref.uri})
        if (i + 1) % every == 0:
            progress((i + 1) / n, f"{i + 1}/{n}")
    return out


def decode_batch(
    decoder: str, refs: list[dict[str, Any]], request: dict[str, Any], progress: ProgressFn
) -> list[TypedBuffer | dict[str, str]]:
    """Decode several refs in one job; a failing item becomes ``{"error": ...}`` in its
    slot instead of failing the batch."""
    d = default_readers().decoder(decoder)
    req = DecodeRequest.model_validate(request)
    return map_refs(refs, lambda ref: d.decode(ref, req), progress)


def decoder_name_for(ref: DataRef, request: DecodeRequest) -> str:
    d = default_readers().decoder_for(ref.kind, request.purpose)
    if d is None:
        raise NotFound(
            f"no decoder for kind {ref.kind!r} purpose {request.purpose!r}",
            fix=f"decoders: {default_readers().names()['decoders']}",
        )
    return d.name


def submit_decode(
    jobs: Any,
    ref: DataRef,
    request: DecodeRequest | None = None,
    *,
    canvas: str | None = None,
    on_done: Callable[[JobInfo], None] | None = None,
) -> JobInfo:
    """Queue one decode on any ``JobManager`` (the pool in the app, inline in tests)."""
    request = request or DecodeRequest()
    return jobs.submit(
        "decode",
        decode_task,
        decoder_name_for(ref, request),
        ref.model_dump(),
        request.model_dump(),
        canvas=canvas,
        on_done=on_done,
    )


def submit_decode_batch(
    jobs: Any,
    refs: list[DataRef],
    request: DecodeRequest | None = None,
    *,
    canvas: str | None = None,
    on_done: Callable[[JobInfo], None] | None = None,
) -> JobInfo:
    request = request or DecodeRequest()
    if not refs:
        raise ValueError("empty batch")
    return jobs.submit(
        "decode.batch",
        decode_batch,
        decoder_name_for(refs[0], request),
        [r.model_dump() for r in refs],
        request.model_dump(),
        canvas=canvas,
        on_done=on_done,
    )
