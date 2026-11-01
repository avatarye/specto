"""A view host's own worker for disk listings (#231, #232): one daemon thread that runs
small jobs in order (a folder's listing, the files under a pick, a search) and hands
each result back on the GUI thread, so the GUI thread never waits on the disk.

``submit(fn, on_done)`` queues ``fn(cancelled)`` and returns a token; ``cancel(token)``
drops its callback and makes ``cancelled()`` true for a run in progress, so a job that
checks it between folders stops early. Under the app's inline job manager (tests,
``--inline-jobs``) jobs run synchronously inside ``submit``, as every other job does
there. The results cross threads as a queued Qt signal, delivered when the GUI thread
next runs its loop.
"""

from __future__ import annotations

import queue
import threading
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot

Job = Callable[[Callable[[], bool]], Any]
Done = Callable[[Any, str | None], None]


class ListingWorker(QObject):
    """One ordered queue of disk jobs off the GUI thread (module doc)."""

    _finished = Signal(int, object, object)

    def __init__(self, *, inline: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # no Qt application (a headless engine test): no loop to deliver on, so inline
        self.inline = inline or QCoreApplication.instance() is None
        self._n = 0
        self._callbacks: dict[int, Done] = {}
        self._cancelled: set[int] = set()
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._closing = False
        self._thread: threading.Thread | None = None
        #: the thread the last job ran on (tests check it was not the GUI thread)
        self.last_thread: int | None = None
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    # -- submitting ------------------------------------------------------------------------------
    def submit(self, fn: Job, on_done: Done) -> int:
        self._n += 1
        token = self._n
        self._callbacks[token] = on_done
        if self.inline:
            result, error = self._run(token, fn)
            self._deliver(token, result, error)
            return token
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._loop, name="specto-tree-listing", daemon=True
            )
            self._thread.start()
        self._queue.put((token, fn))
        return token

    def cancel(self, token: int) -> None:
        """Drop the job's callback; a run in progress sees ``cancelled()`` true."""
        if self._callbacks.pop(token, None) is not None:
            self._cancelled.add(token)

    def cancel_all(self) -> None:
        for token in list(self._callbacks):
            self.cancel(token)

    def is_pending(self, token: int) -> bool:
        return token in self._callbacks

    @property
    def pending(self) -> int:
        """Jobs whose result is still to come."""
        return len(self._callbacks)

    def close(self) -> None:
        """Stop taking jobs; a run in progress sees ``cancelled()`` and its result is
        dropped. Waits briefly for the thread so nothing signals a deleted object."""
        self._closing = True
        self.cancel_all()
        if self._thread is not None:
            self._queue.put(None)
            if self._thread is not threading.current_thread():
                self._thread.join(1.0)

    # -- the thread --------------------------------------------------------------------------------
    def _run(self, token: int, fn: Job) -> tuple[Any, str | None]:
        self.last_thread = threading.get_ident()
        try:
            return fn(lambda: self._closing or token in self._cancelled), None
        except Exception as e:  # a broken listing never takes the view down
            return None, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    def _loop(self) -> None:
        while not self._closing:
            item = self._queue.get()
            if item is None:
                break
            token, fn = item
            if token in self._cancelled:
                self._cancelled.discard(token)
                continue
            result, error = self._run(token, fn)
            if self._closing:
                break
            try:
                self._finished.emit(token, result, error)
            except RuntimeError:  # the owner was deleted while the job ran
                break

    @Slot(int, object, object)
    def _deliver(self, token: int, result: Any, error: str | None) -> None:
        self._cancelled.discard(token)
        cb = self._callbacks.pop(token, None)
        if cb is not None and not self._closing:
            cb(result, error)


__all__ = ["Done", "Job", "ListingWorker"]
