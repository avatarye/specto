"""The frame ring: three RGBA8 slots in one named shared-memory block.

Layout (native byte order, little-endian on every supported platform)::

    0    global header, HEADER_BYTES
         magic "SPFR", version, slot count, header bytes, slot bytes, capacity,
         latest (newest complete slot, -1 none; written by the source),
         reading (slot the reader is copying, -1 none; written by the reader),
         closed (the source has gone or moved), successor (name of the ring that
         replaced this one, NUL-padded)
    128  slot 0: SLOT_HEADER_BYTES of header, then `capacity` bytes of pixels
         slot 1, slot 2 at HEADER_BYTES + i * slot_bytes

    slot header: guard u64 (odd while the slot is written), seq u64 (frame number,
    from 1), width, height, stride (bytes per row), format (1 = RGBA8), timestamp f64
    (source seconds), render_ms f64

No locks. The writer picks a slot that is neither the latest nor the one the reader
announced, makes its guard odd, writes, makes it even, then publishes it as latest. The
reader announces the latest slot, reads the guard, copies, reads the guard again and
drops the copy when the two differ or are odd (a seqlock), so a slot that was being
written is never shown. Guards and indices are stored through typed memoryviews, which
are single aligned stores; the store order holds on x86-64 (TSO), the only target the
native reader is built for today.

A frame larger than the capacity does not fit: the source makes a bigger ring and
names it in this one's ``successor`` (see ``server.FrameServer``).
"""

from __future__ import annotations

import os
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from multiprocessing import resource_tracker, shared_memory

MAGIC = b"SPFR"
VERSION = 1
SLOTS = 3
HEADER_BYTES = 128
SLOT_HEADER_BYTES = 64
SUCCESSOR_BYTES = 64
FORMAT_RGBA8 = 1
BYTES_PER_PIXEL = 4

# global header: magic, version, slots, header bytes, slot bytes, capacity
_HEAD = struct.Struct("<4sIIIQQ")
# u32 / i32 indices into the global header
_LATEST = 32 // 4
_READING = 36 // 4
_CLOSED = 40 // 4
_SUCCESSOR = 48
# slot header after guard and seq: width, height, stride, format, timestamp, render_ms
_SLOT = struct.Struct("<IIIIdd")
_SLOT_FIELDS = 16


def _align(n: int, to: int = 64) -> int:
    return (n + to - 1) // to * to


@dataclass(frozen=True)
class Frame:
    seq: int
    width: int
    height: int
    stride: int
    timestamp: float
    render_ms: float
    pixels: bytes | memoryview  # a view while held (RingReader.hold)


class RingTooSmall(ValueError):
    """The frame needs more bytes than a slot holds."""


class _Ring:
    def __init__(self, shm: shared_memory.SharedMemory) -> None:
        self._shm = shm
        self.name = shm.name
        buf = shm.buf
        magic, version, slots, header, slot_bytes, capacity = _HEAD.unpack_from(buf, 0)
        if magic != MAGIC or version != VERSION or header != HEADER_BYTES:
            raise ValueError(f"{self.name} is not a version {VERSION} frame ring")
        self.slots = slots
        self.slot_bytes = slot_bytes
        self.capacity = capacity
        self._i32 = buf[:HEADER_BYTES].cast("i")
        self._u64 = [self._slot_view(i).cast("Q") for i in range(slots)]

    def _slot_view(self, slot: int) -> memoryview:
        at = HEADER_BYTES + slot * self.slot_bytes
        return self._shm.buf[at : at + SLOT_HEADER_BYTES]

    def _pixels(self, slot: int) -> memoryview:
        at = HEADER_BYTES + slot * self.slot_bytes + SLOT_HEADER_BYTES
        return self._shm.buf[at : at + self.capacity]

    @property
    def latest_slot(self) -> int:
        return self._i32[_LATEST]

    @property
    def reading_slot(self) -> int:
        return self._i32[_READING]

    @property
    def closed(self) -> bool:
        return self._i32[_CLOSED] != 0

    @property
    def successor(self) -> str:
        raw = bytes(self._shm.buf[_SUCCESSOR : _SUCCESSOR + SUCCESSOR_BYTES])
        return raw.split(b"\0", 1)[0].decode()

    def _release(self) -> None:
        for v in self._u64:
            v.release()
        self._u64 = []
        self._i32.release()


class FrameRing(_Ring):
    """The source's end: create the block, write frames into it."""

    @classmethod
    def create(cls, name: str, max_width: int, max_height: int, slots: int = SLOTS) -> FrameRing:
        capacity = _align(max_width * max_height * BYTES_PER_PIXEL)
        slot_bytes = SLOT_HEADER_BYTES + capacity
        shm = shared_memory.SharedMemory(
            name=name, create=True, size=HEADER_BYTES + slots * slot_bytes
        )
        buf = shm.buf
        buf[:HEADER_BYTES] = bytes(HEADER_BYTES)
        _HEAD.pack_into(buf, 0, MAGIC, VERSION, slots, HEADER_BYTES, slot_bytes, capacity)
        for i in range(slots):
            at = HEADER_BYTES + i * slot_bytes
            buf[at : at + SLOT_HEADER_BYTES] = bytes(SLOT_HEADER_BYTES)
        ring = cls(shm)
        ring._i32[_LATEST] = -1
        ring._i32[_READING] = -1
        ring._seq = 0
        return ring

    def __init__(self, shm: shared_memory.SharedMemory) -> None:
        super().__init__(shm)
        self._seq = 0

    @property
    def seq(self) -> int:
        """The number of the last frame written."""
        return self._seq

    def fits(self, width: int, height: int) -> bool:
        return width * height * BYTES_PER_PIXEL <= self.capacity

    def write(
        self,
        pixels: bytes | bytearray | memoryview,
        width: int,
        height: int,
        stride: int | None = None,
        timestamp: float = 0.0,
        render_ms: float = 0.0,
    ) -> int:
        """Copy one frame into a free slot and publish it; returns its seq."""
        stride = stride or width * BYTES_PER_PIXEL
        with self.frame(width, height, stride, timestamp, render_ms) as dst:
            dst[: stride * height] = memoryview(pixels).cast("B")[: stride * height]
        return self._seq

    def frame(
        self,
        width: int,
        height: int,
        stride: int | None = None,
        timestamp: float = 0.0,
        render_ms: float = 0.0,
    ) -> _SlotWrite:
        """A context manager yielding the free slot's pixel bytes to fill in place
        (``np.copyto`` from a mapped render var); the frame is published on exit."""
        stride = stride or width * BYTES_PER_PIXEL
        if stride * height > self.capacity:
            raise RingTooSmall(f"{width}x{height} needs {stride * height} > {self.capacity}")
        return _SlotWrite(self, self._free_slot(), width, height, stride, timestamp, render_ms)

    def _free_slot(self) -> int:
        busy = {self.latest_slot, self.reading_slot}
        free = [i for i in range(self.slots) if i not in busy]
        return min(free, key=lambda i: self._u64[i][1])  # the oldest frame

    def close(self) -> None:
        """Mark the ring closed and free it (the reader keeps its own mapping)."""
        if self._shm is None:
            return
        self._i32[_CLOSED] = 1
        self._release()
        self._shm.close()
        self._shm.unlink()
        self._shm = None

    def set_successor(self, name: str) -> None:
        raw = name.encode()
        if len(raw) >= SUCCESSOR_BYTES:
            raise ValueError(f"ring name too long: {name}")
        self._shm.buf[_SUCCESSOR : _SUCCESSOR + SUCCESSOR_BYTES] = raw.ljust(SUCCESSOR_BYTES, b"\0")


class _SlotWrite:
    def __init__(self, ring: FrameRing, slot, width, height, stride, timestamp, render_ms):
        self._ring = ring
        self._slot = slot
        self._head = (width, height, stride, FORMAT_RGBA8, timestamp, render_ms)
        self._pixels: memoryview | None = None

    def __enter__(self) -> memoryview:
        ring, slot = self._ring, self._slot
        guard = ring._u64[slot]
        guard[0] = guard[0] + 1  # odd: being written
        _SLOT.pack_into(ring._slot_view(slot), _SLOT_FIELDS, *self._head)
        self._pixels = ring._pixels(slot)
        return self._pixels

    def __exit__(self, exc_type, exc, tb) -> None:
        ring, slot = self._ring, self._slot
        self._pixels.release()
        guard = ring._u64[slot]
        if exc_type is None:
            ring._seq += 1
            guard[1] = ring._seq
        guard[0] = guard[0] + 1  # even: complete
        if exc_type is None:
            ring._i32[_LATEST] = slot


class RingReader(_Ring):
    """The reader's end in Python (tests, bench); the native ``FrameItem`` reads the
    same way on the render thread."""

    @classmethod
    def open(cls, name: str) -> RingReader:
        shm = shared_memory.SharedMemory(name=name, create=False)
        if os.name == "posix":  # a reader must not unlink the source's block at exit
            resource_tracker.unregister(shm._name, "shared_memory")
        return cls(shm)

    def latest(self, attempts: int = 3) -> Frame | None:
        """A copy of the newest complete frame, or None when there is none or every
        attempt found its slot being rewritten."""
        for _ in range(attempts):
            slot = self._announce()
            if slot < 0:
                return None
            try:
                guard = self._u64[slot]
                before = guard[0]
                if before % 2:
                    continue
                frame = self._frame(slot, copy=True)
                if guard[0] == before:
                    return frame
            finally:
                self._i32[_READING] = -1
        return None

    @contextmanager
    def hold(self) -> Iterator[Frame | None]:
        """The newest complete frame without a copy: ``pixels`` is a view of the slot,
        which the source does not rewrite until the block exits."""
        slot = self._announce()
        try:
            if slot < 0 or self._u64[slot][0] % 2:
                yield None
                return
            frame = self._frame(slot, copy=False)
            try:
                yield frame
            finally:
                frame.pixels.release()
        finally:
            self._i32[_READING] = -1

    def _announce(self) -> int:
        """Claim the latest slot; re-read it in case the source published in between."""
        while True:
            slot = self.latest_slot
            if slot < 0:
                return slot
            self._i32[_READING] = slot
            if self.latest_slot == slot:
                return slot

    def _frame(self, slot: int, copy: bool) -> Frame:
        seq = self._u64[slot][1]
        width, height, stride, _fmt, ts, ms = _SLOT.unpack_from(self._slot_view(slot), _SLOT_FIELDS)
        with self._pixels(slot) as px:
            view = px[: stride * height]
        if copy:
            with view:
                return Frame(seq, width, height, stride, ts, ms, bytes(view))
        return Frame(seq, width, height, stride, ts, ms, view)

    def close(self) -> None:
        if self._shm is None:
            return
        self._release()
        self._shm.close()
        self._shm = None
