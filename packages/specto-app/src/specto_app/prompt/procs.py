"""This process's children, by pid and executable name, and killing a process tree (#150):
how the agent session finds the Claude CLI it started and kills it when it ignores stdin
EOF, without the Agent SDK's private reaper. Windows reads a Toolhelp32 snapshot and
terminates through kernel32; elsewhere ``ps`` and SIGKILL."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from pathlib import PurePath


def _table() -> list[tuple[int, int, str]]:
    """Every process as ``(pid, parent pid, executable stem)``."""
    if sys.platform == "win32":
        return _table_win32()
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), PurePath(parts[2]).stem.lower()))
    return rows


def _table_win32() -> list[tuple[int, int, str]]:
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):  # PROCESSENTRY32W
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        return []
    rows = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            stem = PurePath(entry.szExeFile).stem.lower()
            rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), stem))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return rows


def children(names: tuple[str, ...], parent: int | None = None) -> set[int]:
    """The pids of ``parent``'s (default: this process's) direct children whose executable
    stem is one of ``names`` (lower case)."""
    parent = os.getpid() if parent is None else parent
    return {pid for pid, ppid, stem in _table() if ppid == parent and stem in names}


def kill_tree(pid: int) -> None:
    """Kill ``pid`` and every process under it, the deepest first. Gone already: nothing."""
    table = _table()
    under: dict[int, list[int]] = {}
    for child, ppid, _ in table:
        if child != ppid:
            under.setdefault(ppid, []).append(child)
    order, todo, seen = [], [pid], set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        order.append(p)
        todo += under.get(p, [])
    for p in reversed(order):
        _kill(p)


def _kill(pid: int) -> None:
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)
