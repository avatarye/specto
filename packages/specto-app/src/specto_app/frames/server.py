"""The source's end of the frame path: the ring plus the wake socket.

``FrameServer`` owns the current :class:`~.ring.FrameRing` and a local-socket listener
(a named pipe on Windows, a Unix socket in the temp directory elsewhere, the same names
``QLocalSocket`` resolves). After each published frame it sends one byte to the
connected reader; wakes are coalesced, so a slow reader gets one byte for many frames
and never stalls the source. The bytes carry nothing: the reader takes the newest slot
from the ring.

A frame larger than the ring's slots makes a successor ring named ``<name>-<n>``; the
old ring is marked closed with the successor's name in its header, so the native item
follows it on its own, and ``on_ring`` tells the host.
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from multiprocessing.connection import Client, Connection, Listener

from .ring import BYTES_PER_PIXEL, FrameRing


def wake_address(name: str) -> tuple[str, str]:
    """The listener address and family for a wake socket ``name``."""
    if os.name == "nt":
        return rf"\\.\pipe\{name}", "AF_PIPE"
    return os.path.join(tempfile.gettempdir(), name), "AF_UNIX"


def connect_wake(name: str) -> Connection:
    """A Python listener's end of the wake socket (tests; the app uses ``FrameItem``)."""
    address, family = wake_address(name)
    return Client(address, family=family)


class FrameServer:
    def __init__(
        self,
        name: str,
        max_width: int,
        max_height: int,
        on_ring: Callable[[str], None] | None = None,
    ) -> None:
        self.name = name
        self.wake = name
        self.on_ring = on_ring
        self.ring = FrameRing.create(name, max_width, max_height)
        self._generation = 0
        self._conn: Connection | None = None
        self._lock = threading.Lock()
        self._pending = threading.Event()
        self._closed = False
        address, family = wake_address(name)
        self._listener = Listener(address, family=family)
        threading.Thread(target=self._accept, name="frame-wake-accept", daemon=True).start()
        threading.Thread(target=self._notify, name="frame-wake-send", daemon=True).start()

    @property
    def ring_name(self) -> str:
        return self.ring.name

    @contextmanager
    def frame(
        self, width: int, height: int, timestamp: float = 0.0, render_ms: float = 0.0
    ) -> Iterator[memoryview]:
        """The free slot's pixels (rows of ``width * 4`` bytes) to fill in place; the
        frame is published and the reader woken on exit."""
        if not self.ring.fits(width, height):
            self._grow(width, height)
        with self.ring.frame(width, height, None, timestamp, render_ms) as dst:
            yield dst
        self._pending.set()

    def publish(
        self, pixels, width: int, height: int, timestamp: float = 0.0, render_ms: float = 0.0
    ) -> int:
        n = width * height * BYTES_PER_PIXEL
        with self.frame(width, height, timestamp, render_ms) as dst:
            dst[:n] = memoryview(pixels).cast("B")[:n]
        return self.ring.seq

    def _grow(self, width: int, height: int) -> None:
        self._generation += 1
        old = self.ring
        new = FrameRing.create(f"{self.name}-{self._generation}", width, height)
        new._seq = old.seq
        old.set_successor(new.name)
        self.ring = new
        old.close()
        if self.on_ring is not None:
            self.on_ring(new.name)

    # -- wake socket (daemon threads) --------------------------------------------------
    def _accept(self) -> None:
        while not self._closed:
            try:
                conn = self._listener.accept()
            except OSError:
                return
            with self._lock:
                old, self._conn = self._conn, conn
            if old is not None:
                old.close()
            self._pending.set()  # a new reader shows the newest frame at once

    def _notify(self) -> None:
        while True:
            self._pending.wait()
            self._pending.clear()
            if self._closed:
                return
            with self._lock:
                conn = self._conn
            if conn is None:
                continue
            try:
                conn.send_bytes(b"\x01")
            except OSError:
                with self._lock:
                    if self._conn is conn:
                        self._conn = None
                conn.close()

    def close(self) -> None:
        self._closed = True
        self._pending.set()
        self.ring.close()
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()
        with suppress(OSError):
            self._listener.close()
