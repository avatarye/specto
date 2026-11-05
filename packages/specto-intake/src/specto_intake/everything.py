"""Everything (voidtools) over its SDK DLL, through ``ctypes`` (#232, Windows only).

The DLL (``Everything64.dll``, MIT-licensed with the SDK, fetched by
``tools/fetch_everything_sdk.py``) talks to the running Everything service over its
inter-process interface: a query costs a few milliseconds and no process. Its state is
global to the process (one search at a time), so :class:`Everything` serialises calls
with a lock and the file search runs it from one worker thread.

Where the DLL is looked for, in order: ``SPECTO_EVERYTHING_DLL`` (a file),
``specto_intake/vendor/Everything64.dll`` (where the fetch script puts it, git-ignored),
``SPECTO_EVERYTHING_SDK_DIR/dll/Everything64.dll`` and the fetch script's default
destination. Without the DLL, or without the service running, ``Everything.available()``
is False and the search falls back to a walk; nothing fails.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

DLL_NAME = "Everything64.dll" if sys.maxsize > 2**32 else "Everything32.dll"
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
_BUILD_ROOT = os.environ.get("SPECTO_BUILD_ROOT", str(Path.home() / "specto-build"))
DEFAULT_SDK_DIR = Path(
    os.environ.get("SPECTO_EVERYTHING_SDK_DIR", str(Path(_BUILD_ROOT) / "everything-sdk"))
)

REQUEST_FULL_PATH = 0x00000004
REQUEST_SIZE = 0x00000010
REQUEST_DATE_MODIFIED = 0x00000040
SORT_NAME_ASCENDING = 1
SORT_PATH_ASCENDING = 3
ERROR_IPC = 2
ERRORS = {
    1: "out of memory",
    2: "Everything is not running",
    3: "unable to register the window class",
    4: "unable to create the listening window",
    5: "unable to create the listening thread",
    6: "invalid index",
    7: "invalid call",
    8: "invalid request data",
    9: "bad parameter",
}
#: FILETIME epoch (1601) to Unix epoch (1970), in 100 ns ticks
_FILETIME_EPOCH = 116444736000000000


@dataclass(frozen=True)
class Hit:
    path: str  # absolute native path
    size: int
    mtime_ns: int
    is_dir: bool = False


def dll_candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("SPECTO_EVERYTHING_DLL")
    if env:
        out.append(Path(env))
    out.append(VENDOR_DIR / DLL_NAME)
    out.append(DEFAULT_SDK_DIR / "dll" / DLL_NAME)
    return out


def find_dll() -> Path | None:
    return next((p for p in dll_candidates() if p.is_file()), None)


class Everything:
    """The SDK's query interface, loaded once per process (``Everything.get()``)."""

    _instance: Everything | None = None
    _instance_lock = threading.Lock()

    def __init__(self, dll_path: Path) -> None:
        self.path = dll_path
        self._lock = threading.Lock()
        lib = ctypes.WinDLL(str(dll_path))  # type: ignore[attr-defined]
        self._lib = lib
        lib.Everything_SetSearchW.argtypes = [wintypes.LPCWSTR]
        lib.Everything_SetSearchW.restype = None
        lib.Everything_SetMax.argtypes = [wintypes.DWORD]
        lib.Everything_SetMax.restype = None
        lib.Everything_SetOffset.argtypes = [wintypes.DWORD]
        lib.Everything_SetOffset.restype = None
        lib.Everything_SetSort.argtypes = [wintypes.DWORD]
        lib.Everything_SetSort.restype = None
        lib.Everything_SetRequestFlags.argtypes = [wintypes.DWORD]
        lib.Everything_SetRequestFlags.restype = None
        lib.Everything_SetMatchPath.argtypes = [wintypes.BOOL]
        lib.Everything_SetMatchPath.restype = None
        lib.Everything_QueryW.argtypes = [wintypes.BOOL]
        lib.Everything_QueryW.restype = wintypes.BOOL
        lib.Everything_GetNumResults.argtypes = []
        lib.Everything_GetNumResults.restype = wintypes.DWORD
        lib.Everything_GetTotResults.argtypes = []
        lib.Everything_GetTotResults.restype = wintypes.DWORD
        lib.Everything_IsFolderResult.argtypes = [wintypes.DWORD]
        lib.Everything_IsFolderResult.restype = wintypes.BOOL
        lib.Everything_GetResultFullPathNameW.argtypes = [
            wintypes.DWORD,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        lib.Everything_GetResultFullPathNameW.restype = wintypes.DWORD
        lib.Everything_GetResultSize.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_longlong)]
        lib.Everything_GetResultSize.restype = wintypes.BOOL
        lib.Everything_GetResultDateModified.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_ulonglong),
        ]
        lib.Everything_GetResultDateModified.restype = wintypes.BOOL
        lib.Everything_GetLastError.argtypes = []
        lib.Everything_GetLastError.restype = wintypes.DWORD
        lib.Everything_IsDBLoaded.argtypes = []
        lib.Everything_IsDBLoaded.restype = wintypes.BOOL
        lib.Everything_GetMajorVersion.argtypes = []
        lib.Everything_GetMajorVersion.restype = wintypes.DWORD
        lib.Everything_GetMinorVersion.argtypes = []
        lib.Everything_GetMinorVersion.restype = wintypes.DWORD
        lib.Everything_GetRevision.argtypes = []
        lib.Everything_GetRevision.restype = wintypes.DWORD
        lib.Everything_Reset.argtypes = []
        lib.Everything_Reset.restype = None
        lib.Everything_CleanUp.argtypes = []
        lib.Everything_CleanUp.restype = None
        self._buf = ctypes.create_unicode_buffer(32768)

    # -- loading -------------------------------------------------------------------------------
    @classmethod
    def get(cls) -> Everything | None:
        """The process's instance, or None when there is no DLL (or not Windows)."""
        if os.name != "nt":
            return None
        with cls._instance_lock:
            if cls._instance is None:
                found = find_dll()
                if found is None:
                    return None
                try:
                    cls._instance = cls(found)
                except OSError:
                    return None
            return cls._instance

    @classmethod
    def forget(cls) -> None:
        """Drop the loaded instance (tests that move the DLL)."""
        with cls._instance_lock:
            cls._instance = None

    # -- the service -------------------------------------------------------------------------
    def available(self) -> bool:
        """Is the Everything service running with its database loaded? (An IPC call;
        cheap.)"""
        with self._lock:
            try:
                return bool(self._lib.Everything_IsDBLoaded())
            except OSError:
                return False

    def version(self) -> str:
        with self._lock:
            lib = self._lib
            major, minor, rev = (
                lib.Everything_GetMajorVersion(),
                lib.Everything_GetMinorVersion(),
                lib.Everything_GetRevision(),
            )
        if major == 0 and minor == 0:
            return ""
        return f"{major}.{minor}.{rev}"

    def last_error(self) -> str:
        code = int(self._lib.Everything_GetLastError())
        return ERRORS.get(code, f"error {code}") if code else ""

    # -- searching -----------------------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int = 0,
        cancelled: Callable[[], bool] | None = None,
        sort: int = SORT_PATH_ASCENDING,
        keep: Callable[[str, bool], bool] | None = None,
    ) -> tuple[list[Hit], int]:
        """Run ``query`` (Everything's own syntax), files and folders alike, with the
        full path, size and modified time of up to ``limit`` results from ``offset`` on
        (a page of the sorted matches); returns the hits and the total number of
        matches. ``keep`` is asked about each result's full path and whether it is a
        folder first, and a result it refuses is left out without reading its size and
        time (two more calls into the DLL each, which is most of the cost of a big
        page). Raises ``OSError`` naming the SDK error when the service is not there."""
        with self._lock:
            lib = self._lib
            lib.Everything_Reset()
            lib.Everything_SetSearchW(query)
            lib.Everything_SetMatchPath(False)
            lib.Everything_SetSort(sort)
            lib.Everything_SetRequestFlags(REQUEST_FULL_PATH | REQUEST_SIZE | REQUEST_DATE_MODIFIED)
            lib.Everything_SetMax(max(1, int(limit)))
            lib.Everything_SetOffset(max(0, int(offset)))
            if not lib.Everything_QueryW(True):
                raise OSError(self.last_error() or "Everything query failed")
            n = int(lib.Everything_GetNumResults())
            total = int(lib.Everything_GetTotResults())
            out: list[Hit] = []
            size = ctypes.c_longlong(0)
            ft = ctypes.c_ulonglong(0)
            for i in range(n):
                if cancelled is not None and i % 256 == 0 and cancelled():
                    break
                got = lib.Everything_GetResultFullPathNameW(i, self._buf, len(self._buf))
                if not got:
                    continue
                path = self._buf.value
                is_dir = bool(lib.Everything_IsFolderResult(i))
                if keep is not None and not keep(path, is_dir):
                    continue
                if not lib.Everything_GetResultSize(i, ctypes.byref(size)):
                    size.value = 0
                mtime_ns = 0
                if lib.Everything_GetResultDateModified(i, ctypes.byref(ft)) and ft.value:
                    mtime_ns = (ft.value - _FILETIME_EPOCH) * 100
                out.append(Hit(path, max(0, size.value), max(0, mtime_ns), is_dir))
            return out, total


def quote(text: str) -> str:
    """A search term as one quoted Everything token (its syntax takes ``"..."``)."""
    return '"' + text.replace('"', "") + '"'


def path_term(word: str) -> str:
    """A search word as a term matched against each result's full path (Everything's
    ``path:`` modifier), with the word's slashes in Everything's own separator: it
    reads ``docs\\setup`` as a path fragment and ``docs/setup`` as nothing."""
    return "path:" + quote(word.replace("/", "\\"))


def below_term(root: str, word: str) -> str:
    """A search word as a term matched against the part of each result's path below
    ``root`` only: a regular expression anchored at the root's own path (escaped), the
    word after it. For a word that also occurs in the root's path, where
    :func:`path_term` would match every file under the root. Everything's regex is
    case-insensitive like the rest of its syntax; with the root term beside it, it
    runs over the files under the root alone (about 1.5 µs a file)."""
    native = os.path.abspath(root).replace("/", "\\").rstrip("\\") + "\\"
    pattern = "^" + re.escape(native) + ".*" + re.escape(word.replace("/", "\\"))
    return "regex:" + quote(pattern)


def under(root: str) -> str:
    """The Everything term that restricts a search to files below ``root`` (a path with
    a trailing separator matches the start of each result's path: Everything's ``path:``
    is a substring match, so the result is also checked against the root on the way)."""
    native = os.path.abspath(root).replace("/", "\\").rstrip("\\") + "\\"
    return quote(native)


__all__ = [
    "DEFAULT_SDK_DIR",
    "DLL_NAME",
    "VENDOR_DIR",
    "Everything",
    "Hit",
    "below_term",
    "dll_candidates",
    "find_dll",
    "path_term",
    "quote",
    "under",
]
