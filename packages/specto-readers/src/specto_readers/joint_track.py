"""Joint tracks (SPEC-43.3, the URDF view's ``joints`` port): timestamped joint positions
per joint name, read from a table and sampled at the global clock's time.

- **The table**: a CSV or Parquet file (read through pyarrow) with one time column and
  one numeric column per joint name. Values are URDF units (radians, metres).
- **The time column**: ``options["time_column"]`` when given; else the first column named
  like a time (``time``, ``t``, ``stamp``, ``timestamp``, ``sec``..., with or without a
  unit suffix ``_ns`` / ``_us`` / ``_ms`` / ``_s``); else the first column. A unit
  suffix sets the unit; without one the column's magnitude does (integers or floats of
  at least 1e17 are ns, 1e14 us, 1e11 ms, else seconds: ``timebase.stamp_seconds``'s
  rule), so an epoch-ns stamp lands on the ``stamp`` clock beside stamped images and
  bag messages. The timeline is ``options["timeline"]``, default ``stamp``.
- **The decoder** ``joint_track`` (worker side) returns one float64 ``(rows, 1 + joints)``
  table sorted by time, the time in seconds first, ``NaN`` where a cell was empty;
  ``meta``: ``columns`` (the time column's own name first), ``timeline``, ``skipped``
  (non-numeric columns, left out), ``source``.
- :class:`JointTrack` (GUI side) caches the columns and answers :meth:`JointTrack.sample`:
  per joint, the sample **nearest** ``t``, never an interpolated one (owner decision,
  2026-11-07); a tie goes to the earlier sample, and twins sharing a stamp to the first
  row. Before the first and after the last sample the end samples show. A joint whose
  cell is empty at a row is not reported there: it shows its own nearest sample.
"""

from __future__ import annotations

import io
import re
from typing import Any

import numpy as np

from specto_core.containers import open_source
from specto_core.errors import SpectoError
from specto_core.kinds import DataRef, DecodeRequest, TypedBuffer
from specto_core.timebase import DEFAULT_CLOCK, OWN_TIMELINE, TimeValue

from .decoders import DecodeError

#: column names read as the time column (lower case, unit suffix stripped)
TIME_NAMES = ("time", "t", "stamp", "timestamp", "header.stamp", "sec", "secs", "seconds")
_UNIT = re.compile(r"^(?P<base>.+?)[_.](?P<unit>ns|us|ms|s)$")
_SCALE = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1.0}
PARQUET_EXTS = (".parquet", ".pq")


def _split_unit(name: str) -> tuple[str, str | None]:
    low = name.strip().lower()
    m = _UNIT.match(low)
    return (m.group("base"), m.group("unit")) if m else (low, None)


def _is_time_name(name: str) -> bool:
    low = name.strip().lower()
    return low in TIME_NAMES or _split_unit(low)[0] in TIME_NAMES


def _magnitude_scale(values: np.ndarray) -> float:
    """``stamp_seconds``'s magnitude rule over a whole column."""
    finite = values[np.isfinite(values)]
    top = float(np.max(np.abs(finite))) if finite.size else 0.0
    if top >= 1e17:
        return 1e-9
    if top >= 1e14:
        return 1e-6
    if top >= 1e11:
        return 1e-3
    return 1.0


def _read_table(uri: str) -> Any:
    import pyarrow as pa
    import pyarrow.csv as pcsv
    import pyarrow.parquet as pq

    try:
        with open_source(uri) as f:
            data = f.read()
    except (OSError, SpectoError) as e:
        raise DecodeError(f"cannot read {uri}: {e}") from e
    try:
        if uri.lower().endswith(PARQUET_EXTS):
            return pq.read_table(pa.BufferReader(data))
        return pcsv.read_csv(io.BytesIO(data))
    except (pa.ArrowException, ValueError) as e:
        raise DecodeError(f"cannot read {uri} as a table: {e}") from e


def _numeric(column: Any) -> np.ndarray | None:
    """A pyarrow column as float64 with NaN for nulls; None when it is not numbers."""
    import pyarrow as pa

    if not (pa.types.is_integer(column.type) or pa.types.is_floating(column.type)):
        return None
    if pa.types.is_integer(column.type):
        # an epoch-ns int64 keeps its precision until the one division into seconds
        arr = column.to_numpy(zero_copy_only=False)
        if column.null_count:
            arr = arr.astype(np.float64)
            arr[np.asarray(column.is_null())] = np.nan
        return arr
    return column.cast(pa.float64()).fill_null(np.nan).to_numpy(zero_copy_only=False)


def _time_seconds(name: str, values: np.ndarray) -> np.ndarray:
    unit = _split_unit(name)[1]
    if unit is not None:
        scale = _SCALE[unit]
    else:
        scale = _magnitude_scale(values.astype(np.float64))
    if np.issubdtype(values.dtype, np.integer) and scale != 1.0:
        whole = round(1.0 / scale)
        return (values // whole).astype(np.float64) + (values % whole).astype(np.float64) * scale
    return values.astype(np.float64) * scale


class JointTrackDecoder:
    """A table -> the joint-track buffer (module docstring)."""

    name = "joint_track"
    kinds = ("joint_track",)
    purposes = ("full",)

    def decode(self, ref: DataRef, request: DecodeRequest) -> TypedBuffer:
        opts = request.options
        table = _read_table(ref.uri)
        names = [str(n) for n in table.column_names]
        if not names:
            raise DecodeError(f"{ref.uri} has no columns")
        if table.num_rows == 0:
            raise DecodeError(f"{ref.uri} has no rows")
        wanted = opts.get("time_column")
        if wanted is not None:
            if wanted not in names:
                raise DecodeError(
                    f"{ref.uri} has no time column {wanted!r}", fix=f"columns: {names}"
                )
            time_name = str(wanted)
        else:
            time_name = next((n for n in names if _is_time_name(n)), names[0])
        raw_time = _numeric(table.column(time_name))
        if raw_time is None:
            raise DecodeError(
                f"{ref.uri}: the time column {time_name!r} is not numbers",
                fix="name the time column with params (time_column)",
            )
        joints: list[str] = []
        cols: list[np.ndarray] = []
        skipped: list[str] = []
        for n in names:
            if n == time_name:
                continue
            col = _numeric(table.column(n))
            if col is None:
                skipped.append(n)
                continue
            joints.append(n)
            cols.append(col.astype(np.float64))
        if not joints:
            raise DecodeError(
                f"{ref.uri} has no joint columns (numeric columns besides {time_name!r})",
                fix=f"columns: {names}",
            )
        keep = ~np.isnan(raw_time.astype(np.float64))
        if not keep.any():
            raise DecodeError(f"{ref.uri} has no rows with a time")
        times = _time_seconds(time_name, raw_time[keep])
        out = np.empty((times.size, 1 + len(joints)), dtype=np.float64)
        out[:, 0] = times
        for i, col in enumerate(cols, start=1):
            out[:, i] = col[keep]
        out = out[np.argsort(out[:, 0], kind="stable")]
        return TypedBuffer.from_numpy(
            out,
            layout="table",
            columns=[time_name, *joints],
            timeline=str(opts.get("timeline") or DEFAULT_CLOCK),
            skipped=skipped,
            source=ref.uri,
        )


class JointTrack:
    """Cached joint columns of one track and the nearest-sample lookup (module
    docstring). Built from the decoder's buffer; it copies the columns, so a shared
    buffer can be released right after."""

    def __init__(
        self,
        times: np.ndarray,
        joints: dict[str, np.ndarray],
        *,
        timeline: str = DEFAULT_CLOCK,
        skipped: list[str] | None = None,
        source: str = "",
    ) -> None:
        times = np.asarray(times, dtype=np.float64)
        self.timeline = timeline
        self.skipped = list(skipped or [])
        self.source = source
        self._times = times
        self._columns: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for name, values in joints.items():
            values = np.asarray(values, dtype=np.float64)
            have = ~np.isnan(values)
            if have.all():
                self._columns[name] = (times, values)
            elif have.any():
                self._columns[name] = (times[have], values[have])

    @classmethod
    def from_buffer(cls, buf: TypedBuffer) -> JointTrack:
        arr = buf.to_numpy()
        meta = buf.spec.meta
        columns = list(meta.get("columns") or [])
        joints = {name: np.array(arr[:, i]) for i, name in enumerate(columns[1:], start=1)}
        return cls(
            np.array(arr[:, 0]),
            joints,
            timeline=str(meta.get("timeline") or DEFAULT_CLOCK),
            skipped=list(meta.get("skipped") or []),
            source=str(meta.get("source") or ""),
        )

    @property
    def names(self) -> list[str]:
        return list(self._columns)

    def __len__(self) -> int:
        return int(self._times.size)

    def __repr__(self) -> str:
        return f"JointTrack({len(self._columns)} joints, {len(self)} rows on {self.timeline!r})"

    def range(self) -> tuple[float, float] | None:
        if not self._times.size:
            return None
        return float(self._times[0]), float(self._times[-1])

    def sample(self, value: TimeValue) -> dict[str, float] | None:
        """Each joint's sample nearest ``value.t``; None when the time is on another
        timeline (a bare time, ``OWN_TIMELINE``, is taken on the track's)."""
        if value.timeline not in (self.timeline, OWN_TIMELINE):
            return None
        t = float(value.t)
        return {name: _nearest(times, values, t) for name, (times, values) in self._columns.items()}

    def unmatched(self, joint_names: list[str] | set[str]) -> list[str]:
        """The track's joint names that are not in ``joint_names`` (the URDF's): a health
        note counts them."""
        known = set(joint_names)
        return [n for n in self._columns if n not in known]


def _nearest(times: np.ndarray, values: np.ndarray, t: float) -> float:
    i = int(np.searchsorted(times, t, side="left"))
    if i >= times.size:
        j = times.size - 1
    elif i == 0 or times[i] - t < t - times[i - 1]:
        j = i
    else:
        j = i - 1
    j = int(np.searchsorted(times, times[j], side="left"))  # twins: the first row
    return float(values[j])


__all__ = ["TIME_NAMES", "JointTrack", "JointTrackDecoder"]
