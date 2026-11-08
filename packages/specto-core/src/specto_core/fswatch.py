"""Change notifications for watched folders (SPEC-8, ADR 0027): the operating system says
which paths changed, so a watched link need not list its sources again to find out.

``Notifier`` subscribes to change notices for a few folders and hands every changed path
(both ends of a move) to a callback on watchdog's thread. The notices come from one
`watchdog <https://github.com/gorakhargosh/watchdog>`_ observer per process
(ReadDirectoryChangesW on Windows, inotify on Linux, FSEvents on macOS), with one watch
per folder however many links follow it: each notice fans out to the notifiers that
subscribed to that folder. A folder's own "modified" notice is dropped: it only says
that something in it changed, and that something has its own notice.

On Windows the observer also says when notices were lost: ReadDirectoryChangesW
returns no bytes when its buffer overflowed, which watchdog drops silently; Specto's
emitter turns it into a ``lost`` notice. That emitter uses three watchdog internals; if
a watchdog release moves them, the stock observer is used instead, without the loss
report (the caller's periodic full listing still catches what an overflow loses).

``unavailable(path)`` says why notifications cannot be trusted for a folder, or None:
no native backend (watchdog would fall back to polling or kqueue, which opens every
file), or a network file system on Linux or macOS, where the kernel only sees changes
made by this machine. A Windows network drive keeps notifications (the SMB server sends
them for every client's changes); the caller relists it more often (``is_remote``).

``Notifier.moved()`` says whether a watched folder is still the folder that was armed:
a handle follows a folder renamed away, so a folder renamed or replaced under its path
leaves the watch on the wrong directory until it is armed again.

Nothing here knows about links; ``specto_core.links.LinkWatch`` decides what to do with
the paths.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from enum import StrEnum
from functools import lru_cache
from typing import Any

#: file systems whose changes made on another machine never reach this kernel's
#: inotify or FSEvents (lower-case ``/proc/mounts`` or ``mount`` type names)
REMOTE_FS = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "smbfs",
        "afpfs",
        "webdav",
        "davfs",
        "fuse.sshfs",
        "fuse.rclone",
        "9p",
        "ceph",
        "glusterfs",
        "lustre",
    }
)
_NATIVE_OBSERVERS = ("InotifyObserver", "FSEventsObserver", "WindowsApiObserver")
#: the mount table is read again after this long, or when a path matches no mount
MOUNTS_TTL_S = 60.0
#: a folder nobody follows any more keeps its watch this long before it is dropped
LINGER_S = 5.0
#: a subscriber waits this long for a folder's watch being armed or torn down elsewhere
ARM_TIMEOUT_S = 10.0
#: a watchdog call (schedule, unschedule) is waited for this long; one that never
#: returns costs its observer
WATCHDOG_CALL_TIMEOUT_S = 2.0
#: a shutdown (the exit, the test sweep) takes about this long at most
STOP_TIMEOUT_S = 1.0


class Unavailable(StrEnum):
    """Why notifications could not start: a folder not there yet (worth trying after
    each listing), the system turned the watch down (inotify's watch limit, a share, a
    watch that did not come free: worth trying later on a backoff), or not to be had
    here at all (no backend, a network file system on Linux or macOS)."""

    MISSING = "missing"
    REFUSED = "refused"
    UNSUPPORTED = "unsupported"


class NotifyUnavailable(Exception):
    """Notifications could not start for a folder; the message says why, in words for
    the status bar, and ``reason`` which kind of why (``Unavailable``)."""

    def __init__(self, message: str, *, reason: Unavailable = Unavailable.UNSUPPORTED) -> None:
        super().__init__(message)
        self.reason = reason


@lru_cache(maxsize=1)
def _observer_cls() -> tuple[Any, str | None]:
    """watchdog's native observer class for this platform, or (None, why not)."""
    try:
        from watchdog.observers import Observer
    except Exception as e:  # not installed, or a broken native part
        return None, f"no change notifications on this system ({type(e).__name__})"
    if Observer.__name__ not in _NATIVE_OBSERVERS:
        return None, "no change notifications on this system"
    if Observer.__name__ == "WindowsApiObserver":
        cls = _windows_observer()
        if cls is not None:
            return cls, None
    return Observer, None


def _windows_observer() -> Any | None:
    """watchdog's ReadDirectoryChangesW observer, saying when notices were lost (the
    call returns no bytes when its buffer overflowed, which watchdog drops silently):
    the emitter queues a ``lost`` event for the folder instead. None when the watchdog
    internals it relies on are not where they were: the stock observer then."""
    try:
        from watchdog.events import FileSystemEvent
        from watchdog.observers import winapi
        from watchdog.observers.api import DEFAULT_OBSERVER_TIMEOUT, BaseObserver
        from watchdog.observers.read_directory_changes import WindowsApiEmitter
    except Exception:
        return None
    needed = ("read_directory_changes", "_parse_event_buffer", "WinAPINativeEvent")
    if not all(callable(getattr(winapi, n, None)) for n in needed) or not callable(
        getattr(WindowsApiEmitter, "_read_events", None)
    ):
        return None

    class LostEvent(FileSystemEvent):
        event_type = "lost"
        is_directory = True

    class Emitter(WindowsApiEmitter):
        def _read_events(self) -> list[Any]:
            handle = getattr(self, "_whandle", _MISSING)
            if handle is _MISSING:  # the emitter's own field moved: do as watchdog does
                return super()._read_events()
            if not handle:
                return []
            buf, nbytes = winapi.read_directory_changes(
                handle, self.watch.path, recursive=self.watch.is_recursive
            )
            if nbytes == 0:
                if self.should_keep_running():  # not the cancel that stops it
                    self.queue_event(LostEvent(self.watch.path))
                return []
            return [
                winapi.WinAPINativeEvent(action, path)
                for action, path in winapi._parse_event_buffer(buf, nbytes)
            ]

    class LossAwareWindowsObserver(BaseObserver):
        def __init__(self, *, timeout: float = DEFAULT_OBSERVER_TIMEOUT) -> None:
            super().__init__(Emitter, timeout=timeout)

    return LossAwareWindowsObserver


_MISSING = object()


def _posix(path: str) -> str:
    return path.replace("\\", "/")


# -- what kind of file system ------------------------------------------------------------
def fs_type(path: str) -> str:
    """The file system type of the mount holding ``path``: ``remote`` for a Windows
    network drive or UNC path, the mount table's type name on Linux and macOS (``nfs``,
    ``ext4``, ``apfs``), '' when unknown."""
    if sys.platform == "win32":
        p = os.path.abspath(path)
        if p.startswith(("\\\\", "//")):
            return "remote"
        drive = os.path.splitdrive(p)[0]
        if not drive:
            return ""
        try:
            import ctypes

            DRIVE_REMOTE = 4
            remote = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOTE
        except Exception:
            return ""
        return "remote" if remote else "local"
    return _mount_fstype(path)


def is_remote(path: str) -> bool:
    """The folder lives on another machine (a network drive, a UNC path, an NFS or SMB
    mount)."""
    t = fs_type(path).lower()
    return t == "remote" or t in REMOTE_FS


def _mount_fstype(path: str) -> str:
    target = _posix(os.path.realpath(path))
    for fresh in (False, True):
        best, fstype = "", ""
        for mnt, typ in _mounts(refresh=fresh):
            m = mnt.rstrip("/") or "/"
            if (target == m or target.startswith(m.rstrip("/") + "/")) and len(m) > len(best):
                best, fstype = m, typ
        if best not in ("", "/") or fresh:
            return fstype
    return ""


_mount_cache: dict[str, Any] = {"at": -1e9, "rows": ()}
_mount_lock = threading.Lock()


def _mounts(*, refresh: bool = False) -> tuple[tuple[str, str], ...]:
    """The mount table, read again every ``MOUNTS_TTL_S`` or when asked (a path that
    matched only the root mount may be on a share mounted since)."""
    with _mount_lock:
        now = time.monotonic()
        if not refresh and now - _mount_cache["at"] < MOUNTS_TTL_S:
            return _mount_cache["rows"]
        rows = _read_mounts()
        _mount_cache.update(at=now, rows=rows)
        return rows


def _read_mounts() -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/mounts", encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        out.append((parts[1].replace("\\040", " "), parts[2]))
        elif sys.platform == "darwin":
            text = subprocess.run(
                ["mount"], capture_output=True, text=True, timeout=5, check=False
            ).stdout
            for line in text.splitlines():
                # "//user@host/share on /Volumes/share (smbfs, nodev, ...)"
                if " on " in line and " (" in line:
                    rest = line.split(" on ", 1)[1]
                    mnt, _, opts = rest.rpartition(" (")
                    out.append((mnt, opts.split(",", 1)[0].strip(" )")))
    except Exception:
        return ()
    return tuple(out)


def unavailable(path: str) -> str | None:
    """Why change notifications cannot be trusted for the folder ``path``, or None."""
    _, why = _observer_cls()
    if why:
        return why
    if sys.platform != "win32":
        t = fs_type(path)
        if t.lower() in REMOTE_FS:
            return f"network file system ({t}): notifications miss other machines' changes"
    return None


def case_insensitive(path: str) -> bool:
    """The file system holding the folder ``path`` matches names regardless of case
    (NTFS, APFS by default), probed on the folder itself; the platform's habit when the
    folder has no letter to swap."""
    p = os.path.abspath(path)
    head, name = os.path.split(p.rstrip("\\/"))
    swapped = name.swapcase()
    if swapped != name:
        try:
            return os.path.samefile(p, os.path.join(head, swapped))
        except OSError:
            return False
    return sys.platform in ("win32", "darwin")


def identity(path: str) -> tuple[Any, ...] | None:
    """What makes a folder the same folder: device and file id (inode, NTFS file index),
    plus its creation time on Windows; None when it is not there."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if sys.platform == "win32":
        return (st.st_dev, st.st_ino, getattr(st, "st_birthtime_ns", None))
    return (st.st_dev, st.st_ino)


# -- one observer, one watch per folder, notices fanned out -------------------------------
class _State(StrEnum):
    """A folder watch's life: ``ARMING`` while a subscriber schedules it outside the
    hub's lock, ``ARMED`` while it delivers, ``REAPING`` while it is unscheduled outside
    the lock (the reaper, or a subscriber tearing a stale one down)."""

    ARMING = "arming"
    ARMED = "armed"
    REAPING = "reaping"


class _FolderWatch:
    """One watched folder and the notifiers that subscribed to it.

    ``subscribers`` is replaced, never changed in place (``attach``/``detach``, under the
    hub's lock), and ``dispatch`` reads it without that lock: watchdog dispatches holding
    its observer's lock, which scheduling takes too, so dispatch must wait for nothing.
    ``state`` (``_State``) says whether the watch is being armed, delivers, or is being
    torn down; a subscriber for a folder whose watch is ``ARMING`` or ``REAPING`` waits
    for that to end before it attaches or arms anew. watchdog keys its emitters by
    (path, recursive): a schedule during the teardown would be handed the emitter being
    stopped, and one during the arming would see no emitter yet and start a second."""

    def __init__(self, folder: str, recursive: bool) -> None:
        self.folder = folder
        self.recursive = recursive
        self.watch: Any = None
        self.ident: tuple[Any, ...] | None = None  # the folder the watch was armed on
        self.idle_since = 0.0  # when its last subscriber left
        self.state = _State.ARMING
        self.subscribers: tuple[Notifier, ...] = ()

    def attach(self, n: Notifier) -> None:
        self.subscribers = (*self.subscribers, n)

    def detach(self, n: Notifier) -> None:
        self.subscribers = tuple(x for x in self.subscribers if x is not n)
        if not self.subscribers:
            self.idle_since = time.monotonic()

    def dispatch(self, event: Any) -> None:
        """watchdog's thread: the notice to every subscriber, a folder's own "modified"
        dropped."""
        kind = getattr(event, "event_type", "")
        if kind in ("opened", "closed_no_write"):
            return
        if kind == "modified" and getattr(event, "is_directory", False):
            return
        subs = self.subscribers
        if kind == "lost":
            for n in subs:
                if n.on_lost is not None:
                    with contextlib.suppress(Exception):
                        n.on_lost()
            return
        paths = [os.fsdecode(event.src_path)]
        dest = getattr(event, "dest_path", "")
        if dest:
            paths.append(os.fsdecode(dest))
        for n in subs:
            with contextlib.suppress(Exception):
                n.on_paths(paths)


class _Hub:
    """The process's observer and its folder watches.

    The hub's lock guards its own state only and is never held across a watchdog call
    that can block: scheduling, unscheduling and stopping take watchdog's observer lock
    and join emitter threads, and on Windows an emitter can hang in its close (a watch
    stopped just after it started). They run outside the lock, with the folder's state
    (``ARMING``, ``REAPING``) keeping others off that folder meanwhile. Every schedule
    and unschedule runs on a helper thread and is waited for ``WATCHDOG_CALL_TIMEOUT_S``:
    one that never returns holds watchdog's observer lock for good, so that observer is
    given up (``_give_up``) and the next subscriber starts a fresh one. ``closed`` is set
    while the hub shuts down (and stays set after the exit's shutdown): nothing arms, and
    a subscriber woken then is refused instead of starting a new observer."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.observer: Any = None
        self.folders: dict[tuple[str, bool], _FolderWatch] = {}
        self.closed = False
        self._epoch = 0  # bumped by every shutdown: a waiter woken across one is refused
        self._atexit = False
        self._reaper: threading.Timer | None = None

    @staticmethod
    def key(folder: str, recursive: bool) -> tuple[str, bool]:
        return (os.path.normcase(os.path.abspath(folder)), recursive)

    def _refused(self, what: str) -> NotifyUnavailable:
        return NotifyUnavailable(
            f"change notifications refused: {what}", reason=Unavailable.REFUSED
        )

    def _observer(self) -> Any:
        """The live observer (a new one when there is none or it died); the hub's lock
        held. A dead one is stopped on a thread of its own."""
        cls, why = _observer_cls()
        if cls is None:
            raise NotifyUnavailable(why or "no change notifications on this system")
        if self.observer is None or not self.observer.is_alive():
            if self.observer is not None:
                _stop_observer_soon(self.observer)
            obs = cls()
            obs.daemon = True
            obs.start()
            self.observer = obs
            for s in self.folders.values():  # a dead observer took its watches along
                s.watch = None
            if not self._atexit:
                atexit.register(self.shutdown, final=True)
                self._atexit = True
        return self.observer

    def _give_up(self, obs: Any) -> None:
        """Give ``obs`` up (a call on it never returned: its lock is held for good, so
        nothing on it dispatches or schedules again); the hub's lock held, in the same
        hold that dropped the watch, so a waiter woken by that never finds ``obs``. Its
        watches are gone with it: their notifiers report not alive, so their links poll
        and try again later, and the next subscriber starts a fresh observer. The caller
        then stops ``obs`` on a thread of its own (``_stop_observer_soon``), which may
        never end: its emitter threads run on, their handles open, until the exit."""
        if self.observer is obs:
            self.observer = None
        for s in self.folders.values():
            s.watch = None
        self.cond.notify_all()

    def _emitter_alive(self, s: _FolderWatch) -> bool:
        obs = self.observer
        if obs is None or not obs.is_alive() or s.watch is None:
            return False
        return any(e.watch == s.watch and e.is_alive() for e in list(obs.emitters))

    def _drop(self, key: tuple[str, bool], s: _FolderWatch) -> None:
        """Forget ``s`` (its watch is gone) and wake whoever waits on its folder; the
        hub's lock held."""
        s.watch = None
        if self.folders.get(key) is s:
            del self.folders[key]
        self.cond.notify_all()

    def subscribe(self, n: Notifier, folder: str, recursive: bool) -> _FolderWatch:
        key = self.key(folder, recursive)
        deadline = time.monotonic() + ARM_TIMEOUT_S
        while True:
            stale: _FolderWatch | None = None
            with self.cond:
                if self.closed:
                    raise self._refused("the watches are shutting down")
                obs = self._observer()
                s = self.folders.get(key)
                if s is not None and s.state is not _State.ARMED:
                    # being armed or torn down by another thread: wait for it
                    epoch = self._epoch
                    if not self.cond.wait(max(0.0, deadline - time.monotonic())):
                        raise self._refused("the watch did not come free")
                    if self._epoch != epoch:
                        raise self._refused("the watches shut down meanwhile")
                    continue
                if s is not None and s.watch is not None:
                    if self._emitter_alive(s) and identity(folder) == s.ident:
                        s.attach(n)
                        return s
                    # its notices stopped (a share that refused them, the folder
                    # deleted), or the folder at that path is another one now (the
                    # handle followed the old one away): tear it down, then arm afresh
                    s.state = _State.REAPING
                    stale = s
                else:
                    if s is not None:  # an observer that died took its watch along
                        del self.folders[key]
                    s = self.folders[key] = _FolderWatch(folder, recursive)
                    s.attach(n)
            if stale is None:
                return self._arm(s, key, obs)
            stopped = _unschedule(obs, stale.watch)
            with self.cond:
                self._drop(key, stale)
                if not stopped:
                    self._give_up(obs)
            if not stopped:
                _stop_observer_soon(obs)
                raise self._refused("the folder's old watch did not stop")

    def _arm(self, s: _FolderWatch, key: tuple[str, bool], obs: Any) -> _FolderWatch:
        """Schedule ``s`` (state ``ARMING``, its subscriber the only one: others wait),
        outside the hub's lock and bounded (``_watchdog_call``): a schedule that never
        returns (the observer's lock held by a wedged unschedule) costs that observer,
        and the subscriber is refused rather than left hanging with the folder stuck in
        ``ARMING``. A shutdown meanwhile leaves the watch unscheduled and the subscriber
        refused."""
        try:
            ident = identity(s.folder)
            watch = _watchdog_call(lambda: obs.schedule(s, s.folder, recursive=s.recursive))
        except _Wedged:
            with self.cond:
                self._drop(key, s)
                self._give_up(obs)
            _stop_observer_soon(obs)
            raise self._refused("the watch could not be scheduled: its observer is stuck") from None
        except Exception as e:  # inotify's watch limit, a refused handle
            with self.cond:
                self._drop(key, s)
            raise self._refused(str(e)) from e
        with self.cond:
            if not self.closed and self.folders.get(key) is s:
                s.watch, s.ident, s.state = watch, ident, _State.ARMED
                self.cond.notify_all()
                return s
            self._drop(key, s)
        _unschedule(obs, watch)
        raise self._refused("the watches are shutting down")

    def unsubscribe(self, n: Notifier, s: _FolderWatch) -> None:
        """The last subscriber leaving a folder keeps its watch for ``LINGER_S`` (a
        watch switched off and on again, a link edited, reuses it), then the reaper
        unschedules it, outside the hub's lock. On Windows a watch stopped just after it
        started can leave watchdog's emitter blocked in a read that was issued after its
        cancel, and its close waits for that read; a watch that has lived a while is
        always in its read, which the cancel ends."""
        with self.cond:
            s.detach(n)
            if not s.subscribers:
                self._arm_reaper(LINGER_S)

    def _arm_reaper(self, delay: float) -> None:
        """Start the reaper's timer to run ``_reap`` in ``delay`` s, unless one is
        pending already (``_reap`` starts the next for whatever it leaves lingering);
        the hub's lock held. Nothing is started once the hub is closed."""
        if self._reaper is not None or self.closed:
            return
        self._reaper = threading.Timer(max(0.0, delay), self._reap)
        self._reaper.daemon = True
        self._reaper.start()

    def _reap(self) -> None:
        """Unschedule the watches no one has subscribed to for ``LINGER_S``: marked
        ``REAPING`` under the lock (a subscriber for that folder waits), unscheduled
        outside it on a helper thread, then dropped. A watch still lingering re-arms the
        reaper for the time it has left. An unschedule that never returns gives the
        observer up (``_give_up``)."""
        now = time.monotonic()
        gone: list[tuple[tuple[str, bool], _FolderWatch]] = []
        with self.cond:
            self._reaper = None
            obs = self.observer
            soonest: float | None = None
            for key, s in self.folders.items():
                if s.subscribers or s.state is not _State.ARMED:
                    continue
                left = s.idle_since + LINGER_S - now
                if left > 0:
                    soonest = left if soonest is None else min(soonest, left)
                    continue
                s.state = _State.REAPING
                gone.append((key, s))
            if soonest is not None:
                self._arm_reaper(soonest)
        watches = [s.watch for _key, s in gone if s.watch is not None]
        stopped = obs is None or not watches or _unschedule(obs, *watches)
        with self.cond:
            for key, s in gone:
                self._drop(key, s)
            if not stopped:
                self._give_up(obs)
        if not stopped:
            _stop_observer_soon(obs)

    def shutdown(self, timeout: float = STOP_TIMEOUT_S, *, final: bool = False) -> None:
        """Stop the observer and drop every watch: the test sweep, and the interpreter's
        exit (``atexit``, ``final``: the hub stays closed). Bounded at about ``timeout``
        in all: the lock is waited for at most that long and the observer is stopped on
        a thread of its own, joined for what is left of it, so a wedged emitter never
        blocks the caller or the exit. While it runs the hub is closed: an arm in flight
        is undone and a subscriber woken by it is refused (whether it wakes before or after
        the hub reopens), so no new observer starts."""
        deadline = time.monotonic() + timeout
        if not self.lock.acquire(timeout=timeout):
            return
        try:
            self.closed = True
            self._epoch += 1
            if self._reaper is not None:
                self._reaper.cancel()
                self._reaper = None
            obs, self.observer = self.observer, None
            for s in list(self.folders.values()):
                s.watch = None
                for n in s.subscribers:
                    s.detach(n)
            self.folders.clear()
            self.cond.notify_all()
        finally:
            self.lock.release()
        try:
            if obs is not None:
                t = _stop_observer_soon(obs)
                t.join(max(0.0, deadline - time.monotonic()))
        finally:
            if not final:  # reopen, bounded like the rest: the lock may be held for good
                got = self.lock.acquire(timeout=max(0.05, deadline - time.monotonic()))
                try:
                    self.closed = False
                finally:
                    if got:
                        self.lock.release()

    def alive(self, s: _FolderWatch) -> bool:
        with self.lock:
            return s.state is _State.ARMED and self._emitter_alive(s)

    def watches(self) -> int:
        """Folders watched for a subscriber in this process (tests)."""
        with self.lock:
            return sum(1 for s in self.folders.values() if s.watch is not None and s.subscribers)


class _Wedged(Exception):
    """A watchdog call did not return within ``WATCHDOG_CALL_TIMEOUT_S``."""


def _watchdog_call(fn: Callable[[], Any]) -> Any:
    """Run ``fn`` (a watchdog call that takes the observer's lock: schedule, unschedule)
    on a daemon thread of its own and wait for it ``WATCHDOG_CALL_TIMEOUT_S``; its
    result, or its exception raised here, or ``_Wedged`` when it did not end in time
    (an unschedule joins the emitter thread with no timeout, and a Windows emitter can
    hang in its close; a schedule then waits for that lock). The thread, and the
    observer lock it holds or waits for, are left behind: the caller gives that
    observer up."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["result"] = fn()
        except BaseException as e:  # handed to the caller, raised there
            box["error"] = e

    t = threading.Thread(target=run, name="specto-watchdog-call", daemon=True)
    t.start()
    t.join(WATCHDOG_CALL_TIMEOUT_S)
    if t.is_alive():
        raise _Wedged()
    if "error" in box:
        raise box["error"]
    return box["result"]


def _unschedule(obs: Any, *watches: Any) -> bool:
    """Unschedule ``watches`` from ``obs``, bounded (``_watchdog_call``); False when the
    call did not end in time."""

    def run() -> None:
        for w in watches:
            with contextlib.suppress(Exception):
                obs.unschedule(w)

    try:
        _watchdog_call(run)
    except _Wedged:
        return False
    return True


def _stop_observer_soon(obs: Any, timeout: float = STOP_TIMEOUT_S) -> threading.Thread:
    """Unschedule everything and stop ``obs`` on a daemon thread of its own (watchdog
    joins emitters with no timeout); returns the thread."""

    def stop() -> None:
        with contextlib.suppress(Exception):
            obs.unschedule_all()
        with contextlib.suppress(Exception):
            obs.stop()
            if obs.is_alive():
                obs.join(timeout)

    t = threading.Thread(target=stop, name="specto-watch-stop", daemon=True)
    t.start()
    return t


_HUB = _Hub()


def watched_folders() -> int:
    """How many folders this process holds a watch on (one per folder, whatever the
    number of links following it)."""
    return _HUB.watches()


class Notifier:
    """Notices for ``roots`` (``(folder, recursive)`` pairs): ``on_paths(paths)`` gets
    the changed paths, native separators, on watchdog's thread; ``on_lost()`` says that
    notices were lost (Windows: a buffer overflow), so only a full listing can tell what
    changed. ``start()`` raises ``NotifyUnavailable`` when they cannot start; ``alive``
    turns False when a folder's notices stopped later (a share that refuses them, the
    folder deleted); ``moved()`` when a folder is not the one armed any more."""

    def __init__(
        self,
        roots: list[tuple[str, bool]],
        on_paths: Callable[[list[str]], None],
        on_lost: Callable[[], None] | None = None,
    ):
        self.roots = _outermost(roots)
        self.on_paths = on_paths
        self.on_lost = on_lost
        self._subs: list[_FolderWatch] = []
        self._ids: dict[str, tuple[Any, ...] | None] = {}

    def start(self) -> Notifier:
        cls, why = _observer_cls()
        if cls is None:
            raise NotifyUnavailable(why or "no change notifications on this system")
        for folder, _ in self.roots:
            reason = unavailable(folder)
            if reason:
                raise NotifyUnavailable(reason)
            if not os.path.isdir(folder):
                raise NotifyUnavailable(
                    f"{_posix(folder)} is not there", reason=Unavailable.MISSING
                )
        try:
            for folder, recursive in self.roots:
                self._ids[folder] = identity(folder)
                self._subs.append(_HUB.subscribe(self, folder, recursive))
        except NotifyUnavailable:
            self.stop()
            raise
        return self

    @property
    def alive(self) -> bool:
        return bool(self._subs) and all(_HUB.alive(s) for s in self._subs)

    def moved(self) -> bool:
        """A watched folder was renamed, replaced or removed since it was armed: the
        watch no longer follows the folder at that path."""
        return any(identity(folder) != ident for folder, ident in self._ids.items())

    def stop(self) -> None:
        subs, self._subs = self._subs, []
        for s in subs:
            _HUB.unsubscribe(self, s)


def shutdown(*, final: bool = False) -> None:
    """Stop the process's observer and every watch: the test sweep
    (``links.stop_all_watches``) and the interpreter's exit (registered with ``atexit``
    when the observer starts, which closes the hub for good). Bounded at about
    ``STOP_TIMEOUT_S``, so it never blocks the exit."""
    _HUB.shutdown(final=final)


def _outermost(roots: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    """One watch per folder: a folder inside a recursively watched one is dropped."""
    best: dict[str, tuple[str, bool]] = {}
    for folder, recursive in roots:
        key = os.path.normcase(os.path.abspath(folder))
        prev = best.get(key)
        best[key] = (folder, recursive or (prev is not None and prev[1]))  # recursive wins
    kept: list[str] = []
    out: list[tuple[str, bool]] = []
    for key in sorted(best, key=len):
        folder, recursive = best[key]
        if any(key.startswith(k.rstrip(os.sep) + os.sep) for k in kept):
            continue
        if recursive:
            kept.append(key)
        out.append((folder, recursive))
    return out


__all__ = [
    "Notifier",
    "NotifyUnavailable",
    "Unavailable",
    "case_insensitive",
    "fs_type",
    "identity",
    "is_remote",
    "shutdown",
    "unavailable",
    "watched_folders",
]
