"""Joint tracks (SPEC-43.3, the URDF view's ``joints`` port): timestamped joint positions
per joint name, read from a table and sampled at the global clock's time.

- **The table**: a CSV or Parquet file (read through pyarrow) with one time column and
  one numeric column per joint name. Values are URDF units (radians, metres).
- **The time column**: ``options["time_column"]`` when given; else the first column named
  like a time (``time``, ``t``, ``stamp``, ``timestamp``, ``sec``..., with or without a
  unit suffix ``_ns`` / ``_us`` / ``_ms`` / ``_s``); else the **first column**. A unit
  suffix sets the unit; without one the column's largest magnitude sets **one unit for
  the whole column** (``timebase.stamp_divisor``, the rule item stamps use: at least
  1e17 is ns, 1e14 us, 1e11 ms, else seconds), so an epoch-ns stamp lands on the
  ``stamp`` clock beside stamped images and bag messages. The timeline is
  ``options["timeline"]``, default ``stamp``.
- **What counts as a joint track** (:func:`joint_track_problem`, the locator's content
  check): a time column of numbers plus at least one other numeric column, read from
  the CSV header and first row or the Parquet schema. A table that fails it is not
  located as a track (an intaker asking for ``joint_track`` skips it with the reason),
  and a ``.csv`` stays text for every other view. Whether the joint names match the
  URDF is the view's health note (:meth:`JointTrack.unmatched`), not this check.
- **The decoder** ``joint_track`` (worker side) returns one float64 ``(rows, 1 + joints)``
  table sorted by time, the time in seconds first, ``NaN`` where a cell was empty;
  ``meta``: ``columns`` (the time column's own name first), ``timeline``, ``skipped``
  (non-numeric columns, left out), ``source``. Rows without a time are dropped. Memory
  peaks at about the Arrow table plus twice the result (``8 * rows * (1 + joints)``
  bytes, sorted into a copy); past ``MAX_CELLS`` the decoder refuses with the fix.
- :class:`JointTrack` (GUI side) caches the columns and answers :meth:`JointTrack.sample`:
  per joint, the sample **nearest** ``t``, never an interpolated one (owner decision,
  2026-11-07); a tie between two samples goes to the earlier one, and rows sharing a
  stamp to the **first row**. Before the first and after the last sample the end
  samples show. A joint whose cell is empty (``NaN``) at a row is not reported there:
  it shows its own **nearest valid sample**. Values are never clamped into the joint's
  limits (design amendment 17); :meth:`JointTrack.outside` counts the samples beyond them.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping
from pathlib import PurePath
from typing import Any, Literal

import numpy as np

from specto_core.containers import open_source
from specto_core.errors import NotFound, SpectoError
from specto_core.kinds import DataRef, DecodeRequest, Item, TypedBuffer
from specto_core.timebase import DEFAULT_CLOCK, OWN_TIMELINE, TimeValue, stamp_divisor

from .decoders import DecodeError
from .locators import ExtensionLocator

#: column names read as the time column (lower case, unit suffix stripped)
TIME_NAMES = ("time", "t", "stamp", "timestamp", "header.stamp", "sec", "secs", "seconds")
_UNIT = re.compile(r"^(?P<base>.+?)[_.](?P<unit>ns|us|ms|s)$")
_PER_SECOND = {"ns": 10**9, "us": 10**6, "ms": 10**3, "s": 1}
PARQUET_EXTS = (".parquet",)
#: the largest table the decoder takes, in cells (rows x columns): 400 MB as float64
MAX_CELLS = 50_000_000
_SNIFF_BYTES = 64 * 1024
#: the CSV rows the content check reads after the header
SNIFF_ROWS = 5


def _split_unit(name: str) -> tuple[str, str | None]:
    low = name.strip().lower()
    m = _UNIT.match(low)
    return (m.group("base"), m.group("unit")) if m else (low, None)


def _is_time_name(name: str) -> bool:
    low = name.strip().lower()
    return low in TIME_NAMES or _split_unit(low)[0] in TIME_NAMES


def time_column_of(names: list[str], wanted: str | None = None) -> str | None:
    """The time column among ``names`` (module docstring); None when ``wanted`` is not
    one of them or there are no columns."""
    if wanted is not None:
        return wanted if wanted in names else None
    return next((n for n in names if _is_time_name(n)), names[0] if names else None)


def _is_parquet(path: str) -> bool:
    return PurePath(path).suffix.lower() in PARQUET_EXTS


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return text.strip() != ""


def joint_track_problem(path: str, time_column: str | None = None) -> str | None:
    """Why ``path`` is not a joint track, or None when it looks like one: a numeric time
    column plus at least one other numeric column, from the CSV header and first row or
    the Parquet schema (cheap: never the whole table; a Parquet file is read at its footer
    only). A CSV column counts as numeric when any of its first ``SNIFF_ROWS`` cells is a
    number, so an empty first cell does not refuse a track."""
    try:
        with open_source(path) as f:
            if _is_parquet(path):
                import pyarrow as pa
                import pyarrow.parquet as pq

                try:
                    schema = pq.ParquetFile(f).schema_arrow  # the footer, never the data
                except (pa.ArrowException, ValueError, OSError) as e:
                    return f"not a Parquet table: {e}"
                names = [str(n) for n in schema.names]
                numeric = {
                    n
                    for n, t in zip(names, schema.types, strict=True)
                    if pa.types.is_integer(t) or pa.types.is_floating(t)
                }
            else:
                head = f.read(_SNIFF_BYTES)
                rows = list(csv.reader(io.StringIO(head.decode("utf-8", "replace"))))
                if len(rows) < 2:
                    return "no header and data row"
                names = rows[0]
                numeric = {
                    n
                    for row in rows[1 : 1 + SNIFF_ROWS]
                    for n, v in zip(names, row, strict=False)
                    if _is_number(v)
                }
    except (OSError, SpectoError) as e:
        return f"cannot read it: {e}"
    time_name = time_column_of(names, time_column)
    if time_name is None:
        return f"no time column {time_column!r}" if time_column else "no columns"
    if time_name not in numeric:
        return f"its time column {time_name!r} is not numbers"
    if not (numeric - {time_name}):
        return f"no joint columns (numeric columns besides {time_name!r})"
    return None


class JointTrackLocator(ExtensionLocator):
    """The ``joint_track`` locator: by extension, then by content (:func:`joint_track_problem`)
    in ``locate``, so only a table that looks like a track becomes one."""

    def locate(self, item: Item) -> DataRef:
        ref = super().locate(item)
        problem = joint_track_problem(ref.uri)
        if problem is not None:
            raise NotFound(
                f"{PurePath(ref.uri).name} is not a joint track: {problem}",
                fix="a time column plus one numeric column per joint",
            )
        return ref


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
        if _is_parquet(uri):
            return pq.read_table(pa.BufferReader(data))
        return pcsv.read_csv(io.BytesIO(data))
    except (pa.ArrowException, ValueError) as e:
        raise DecodeError(f"cannot read {uri} as a table: {e}") from e


def _numeric(column: Any) -> np.ndarray | None:
    """A pyarrow column as numbers (an int column without nulls stays integer, so an
    epoch-ns stamp keeps its precision; nulls become NaN); None when it is not numbers."""
    import pyarrow as pa

    if not (pa.types.is_integer(column.type) or pa.types.is_floating(column.type)):
        return None
    return np.asarray(column.to_numpy(zero_copy_only=False))


def _time_seconds(name: str, values: np.ndarray) -> np.ndarray:
    unit = _split_unit(name)[1]
    if unit is not None:
        per_second = _PER_SECOND[unit]
    else:
        finite = values[np.isfinite(values)] if values.dtype.kind == "f" else values
        per_second = stamp_divisor(float(np.max(np.abs(finite))) if finite.size else 0.0)
    if values.dtype.kind in "iu":
        whole, part = np.divmod(values, per_second)
        return whole.astype(np.float64) + part.astype(np.float64) / per_second
    return values.astype(np.float64) / per_second


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
        if table.num_rows * len(names) > MAX_CELLS:
            raise DecodeError(
                f"{ref.uri} has {table.num_rows} rows x {len(names)} columns, more than "
                f"{MAX_CELLS} cells",
                fix="downsample the track or drop columns",
            )
        time_name = time_column_of(names, opts.get("time_column"))
        if time_name is None:
            raise DecodeError(
                f"{ref.uri} has no time column {opts.get('time_column')!r}",
                fix=f"columns: {names}",
            )
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
            cols.append(col)
        if not joints:
            raise DecodeError(
                f"{ref.uri} has no joint columns (numeric columns besides {time_name!r})",
                fix=f"columns: {names}",
            )
        keep = ~np.isnan(raw_time) if raw_time.dtype.kind == "f" else slice(None)
        times = _time_seconds(time_name, raw_time[keep])
        if not times.size:
            raise DecodeError(f"{ref.uri} has no rows with a time")
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

    def outside(self, limits: Mapping[str, tuple[float, float]]) -> int:
        """How many samples lie outside their joint's ``(lower, upper)`` limits: a health
        note counts them. Values are never clamped (design amendment 17: a track shows
        what was recorded); joints without limits (continuous) or unknown names count
        nothing."""
        total = 0
        for name, (lower, upper) in limits.items():
            column = self._columns.get(name)
            if column is not None:
                values = column[1]
                total += int(np.count_nonzero((values < lower) | (values > upper)))
        return total

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
    j = int(np.searchsorted(times, times[j], side="left"))  # rows sharing a stamp: the first
    return float(values[j])


__all__ = [
    "MAX_CELLS",
    "TIME_NAMES",
    "JointTrack",
    "JointTrackDecoder",
    "JointTrackLocator",
    "PoseSource",
    "joint_track_problem",
    "resolve_pose",
    "time_column_of",
]


PoseSource = Literal["track", "params", "zero"]


def resolve_pose(
    actuated: Iterable[str],
    track_sample: Mapping[str, float] | None,
    params_joints: Mapping[str, float] | None,
) -> tuple[dict[str, float], PoseSource]:
    """The pose the urdf view shows (design section 6): per actuated joint, the track's
    sample at ``t`` first, then ``params.joints``, then zero. ``source`` is the first of
    those that sets any of this robot's joints: ``track`` (the sliders lock and follow
    it), else ``params``, else ``zero``. Names the robot does not have are ignored here
    (the health note counts a track's, :meth:`JointTrack.unmatched`)."""
    names = list(actuated)
    track = {n: float(v) for n, v in (track_sample or {}).items() if n in names}
    params = {n: float(v) for n, v in (params_joints or {}).items() if n in names}
    joints = {n: track.get(n, params.get(n, 0.0)) for n in names}
    source: PoseSource = "track" if track else "params" if params else "zero"
    return joints, source
