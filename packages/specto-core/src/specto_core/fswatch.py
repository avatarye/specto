"""Change notifications for watched folders (SPEC-8, ADR 0027): the operating system says
which paths changed, so a watched link need not list its sources again to find out.

``Notifier`` wraps a `watchdog <https://github.com/gorakhargosh/watchdog>`_ observer
(ReadDirectoryChangesW on Windows, inotify on Linux, FSEvents on macOS) over a few
folders and hands every changed path (both ends of a move) to a callback on watchdog's
thread. A folder's own "modified" notice is dropped: it only says that something in it
changed, and that something has its own notice.

``unavailable(path)`` says why notifications cannot be trusted for a folder, or None:
no native backend (watchdog would fall back to polling or kqueue, which opens every
file), or a network file system on Linux or macOS, where the kernel only sees changes
made by this machine. A Windows network drive keeps notifications (the SMB server
sends them for every client's changes); the caller relists it more often as a safety
net (``is_remote``). On Windows the observer also says when notices were lost (a
ReadDirectoryChangesW buffer overflow, which watchdog itself drops silently).

Nothing here knows about links; ``specto_core.links.LinkWatch`` decides what to do with
the paths.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
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
        with contextlib.suppress(Exception):  # watchdog's internals moved: no loss notice
            return _windows_observer(), None
    return Observer, None


def _windows_observer() -> Any:
    """watchdog's ReadDirectoryChangesW observer, but saying when notices were lost:
    the call returns no bytes when its buffer overflowed (a burst the reader did not
    keep up with), and watchdog drops that silently. The emitter queues a ``lost``
    event for the folder instead, and the watcher lists everything again."""
    from watchdog.events import FileSystemEvent
    from watchdog.observers import winapi
    from watchdog.observers.api import DEFAULT_OBSERVER_TIMEOUT, BaseObserver
    from watchdog.observers.read_directory_changes import WindowsApiEmitter

    class LostEvent(FileSystemEvent):
        event_type = "lost"
        is_directory = True

    class Emitter(WindowsApiEmitter):
        def _read_events(self) -> list[Any]:
            if not self._whandle:
                return []
            buf, nbytes = winapi.read_directory_changes(
                self._whandle, self.watch.path, recursive=self.watch.is_recursive
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


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def is_remote(path: str) -> bool:
    """The folder lives on another machine (a network drive, a UNC path, an NFS or SMB
    mount)."""
    if sys.platform == "win32":
        p = os.path.abspath(path)
        if p.startswith(("\\\\", "//")):
            return True
        drive = os.path.splitdrive(p)[0]
        if not drive:
            return False
        try:
            import ctypes

            DRIVE_REMOTE = 4
            return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOTE
        except Exception:
            return False
    return _mount_fstype(path).lower() in REMOTE_FS


def _mount_fstype(path: str) -> str:
    """The file system type of the mount holding ``path`` (Linux, macOS), or ''."""
    target = _posix(os.path.realpath(path))
    best, fstype = "", ""
    for mnt, typ in _mounts():
        m = mnt.rstrip("/") or "/"
        if (target == m or target.startswith(m.rstrip("/") + "/")) and len(m) > len(best):
            best, fstype = m, typ
    return fstype


@lru_cache(maxsize=1)
def _mounts() -> tuple[tuple[str, str], ...]:
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
        fstype = _mount_fstype(path)
        if fstype.lower() in REMOTE_FS:
            return f"network file system ({fstype}): notifications miss other machines' changes"
    return None


class Notifier:
    """Notifications for ``roots`` (``(folder, recursive)`` pairs): ``on_paths(paths)``
    gets the changed paths, native separators, on watchdog's thread; ``on_lost()`` says
    that notices were lost (Windows: a buffer overflow), so only a full listing can
    tell what changed. ``start()`` raises ``NotifyUnavailable`` when they cannot start;
    ``alive`` turns False when a backend thread died later (a share that refuses them,
    the folder deleted)."""

    def __init__(
        self,
        roots: list[tuple[str, bool]],
        on_paths: Callable[[list[str]], None],
        on_lost: Callable[[], None] | None = None,
    ):
        self.roots = _outermost(roots)
        self.on_paths = on_paths
        self.on_lost = on_lost
        self._observer: Any = None
        self._lock = threading.Lock()

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
        handler = _Handler(self.on_paths, self.on_lost)
        observer = cls()
        observer.daemon = True
        try:
            for folder, recursive in self.roots:
                observer.schedule(handler, folder, recursive=recursive)
            observer.start()
        except Exception as e:  # inotify's watch limit, a refused handle
            self._stop_observer(observer)
            raise NotifyUnavailable(f"change notifications refused: {e}") from e
        with self._lock:
            self._observer = observer
        return self

    @property
    def alive(self) -> bool:
        with self._lock:
            obs = self._observer
        if obs is None or not obs.is_alive():
            return False
        return all(e.is_alive() for e in list(obs.emitters))

    def stop(self, *, timeout: float = 2.0) -> None:
        with self._lock:
            obs, self._observer = self._observer, None
        if obs is not None:
            self._stop_observer(obs, timeout=timeout)

    @staticmethod
    def _stop_observer(obs: Any, *, timeout: float = 2.0) -> None:
        with contextlib.suppress(Exception):
            obs.stop()
            if obs.is_alive():
                obs.join(timeout)


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


class _Handler:
    """watchdog's handler: every changed path but a folder's own "modified"; a loss
    notice to ``on_lost``."""

    def __init__(
        self, on_paths: Callable[[list[str]], None], on_lost: Callable[[], None] | None = None
    ) -> None:
        self.on_paths = on_paths
        self.on_lost = on_lost

    def dispatch(self, event: Any) -> None:
        kind = getattr(event, "event_type", "")
        if kind == "lost":
            if self.on_lost is not None:
                with contextlib.suppress(Exception):
                    self.on_lost()
            return
        if kind in ("opened", "closed_no_write"):
            return
        if kind == "modified" and getattr(event, "is_directory", False):
            return
        paths = [os.fsdecode(event.src_path)]
        dest = getattr(event, "dest_path", "")
        if dest:
            paths.append(os.fsdecode(dest))
        with contextlib.suppress(Exception):
            self.on_paths(paths)


__all__ = ["Notifier", "NotifyUnavailable", "is_remote", "unavailable"]
