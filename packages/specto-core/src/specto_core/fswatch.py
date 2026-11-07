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

import contextlib
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
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


class NotifyUnavailable(Exception):
    """Notifications could not start for a folder; the message says why, in words
    for the status bar. ``retry``: worth trying again later (a folder not there yet)."""

    def __init__(self, message: str, *, retry: bool = False) -> None:
        super().__init__(message)
        self.retry = retry


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
class _Shared:
    """One watched folder and the notifiers that subscribed to it."""

    def __init__(self, folder: str, recursive: bool) -> None:
        self.folder = folder
        self.recursive = recursive
        self.watch: Any = None
        self.ident: tuple[Any, ...] | None = None  # the folder the watch was armed on
        self.subscribers: list[Notifier] = []

    def dispatch(self, event: Any) -> None:
        """watchdog's thread: the notice to every subscriber, a folder's own "modified"
        dropped."""
        kind = getattr(event, "event_type", "")
        if kind in ("opened", "closed_no_write"):
            return
        if kind == "modified" and getattr(event, "is_directory", False):
            return
        with _HUB.lock:
            subs = list(self.subscribers)
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
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.observer: Any = None
        self.shared: dict[tuple[str, bool], _Shared] = {}

    def _observer(self) -> Any:
        cls, why = _observer_cls()
        if cls is None:
            raise NotifyUnavailable(why or "no change notifications on this system")
        if self.observer is None or not self.observer.is_alive():
            obs = cls()
            obs.daemon = True
            obs.start()
            self.observer = obs
            for s in self.shared.values():  # a dead observer took its watches along
                s.watch = None
        return self.observer

    def _emitter_alive(self, s: _Shared) -> bool:
        obs = self.observer
        if obs is None or not obs.is_alive() or s.watch is None:
            return False
        return any(e.watch == s.watch and e.is_alive() for e in list(obs.emitters))

    def subscribe(self, n: Notifier, folder: str, recursive: bool) -> _Shared:
        key = (os.path.normcase(os.path.abspath(folder)), recursive)
        with self.lock:
            obs = self._observer()
            s = self.shared.get(key)
            if s is None:
                s = self.shared[key] = _Shared(folder, recursive)
            if s.watch is not None and (not self._emitter_alive(s) or identity(folder) != s.ident):
                # its notices stopped (a share that refused them, the folder deleted), or
                # the folder at that path is another one now (the handle followed the old
                # one away): arm it again for whoever subscribes now
                with contextlib.suppress(Exception):
                    obs.unschedule(s.watch)
                s.watch = None
            if s.watch is None:
                try:
                    s.ident = identity(folder)
                    s.watch = obs.schedule(s, folder, recursive=recursive)
                except Exception as e:  # inotify's watch limit, a refused handle
                    if not s.subscribers:
                        self.shared.pop(key, None)
                    raise NotifyUnavailable(f"change notifications refused: {e}") from e
            s.subscribers.append(n)
            return s

    def unsubscribe(self, n: Notifier, s: _Shared) -> None:
        with self.lock:
            if n in s.subscribers:
                s.subscribers.remove(n)
            if s.subscribers:
                return
            key = (os.path.normcase(os.path.abspath(s.folder)), s.recursive)
            if self.shared.get(key) is s:
                del self.shared[key]
            if s.watch is not None and self.observer is not None:
                with contextlib.suppress(Exception):
                    self.observer.unschedule(s.watch)
            s.watch = None

    def alive(self, s: _Shared) -> bool:
        with self.lock:
            return self._emitter_alive(s)

    def watches(self) -> int:
        """Folders watched in this process (tests)."""
        with self.lock:
            return sum(1 for s in self.shared.values() if s.watch is not None)


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
        self._subs: list[_Shared] = []
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
                raise NotifyUnavailable(f"{_posix(folder)} is not there", retry=True)
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

    def stop(self, *, timeout: float = 2.0) -> None:
        subs, self._subs = self._subs, []
        for s in subs:
            _HUB.unsubscribe(self, s)


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
    "case_insensitive",
    "fs_type",
    "identity",
    "is_remote",
    "unavailable",
    "watched_folders",
]
