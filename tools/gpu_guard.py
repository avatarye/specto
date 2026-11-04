"""Foreground guard for GPU-window test runs (Windows). Runs a command as a child and, every
250 ms, samples the foreground window, the cursor and the clipboard sequence number. If the
foreground window belongs to the child's process tree, it kills the tree and exits 3. At the
end it logs what changed; the user's own activity moves the cursor and the foreground
between their windows, which is logged, not acted on.

    uv run --with psutil python tools/gpu_guard.py --log <file> -- <command ...>

For example, one GPU-tier entry:

    uv run --with psutil python tools/gpu_guard.py --log guard.log -- \
        uv run python tools/gui_flows/v2_suite.py --work <scratch> --tier gpu --only <entry>
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from typing import TextIO

import psutil

user32 = ctypes.windll.user32
KILLED = 3


def foreground() -> tuple[int, int]:
    hwnd = user32.GetForegroundWindow()
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd, pid.value


def cursor() -> tuple[int, int]:
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def tree(root: psutil.Process) -> set[int]:
    try:
        return {root.pid} | {c.pid for c in root.children(recursive=True)}
    except psutil.NoSuchProcess:
        return {root.pid}


def kill_tree(root: psutil.Process) -> None:
    for child in root.children(recursive=True):
        with contextlib.suppress(psutil.NoSuchProcess):
            child.kill()
    with contextlib.suppress(psutil.NoSuchProcess):
        root.kill()


def guard(cmd: list[str], log: TextIO) -> int:
    def say(msg: str) -> None:
        log.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        log.flush()

    hwnd0, fg0 = foreground()
    cur0, clip0 = cursor(), user32.GetClipboardSequenceNumber()
    say(f"[guard] start fg pid {fg0} hwnd {hwnd0} cursor {cur0} clip {clip0}")
    proc = subprocess.Popen(cmd)
    root = psutil.Process(proc.pid)
    fg_changes = cursor_moves = 0
    last_fg, last_cur = fg0, cur0
    try:
        while proc.poll() is None:
            time.sleep(0.25)
            ours = tree(root)
            hwnd, fg = foreground()
            cur = cursor()
            if fg != last_fg:
                fg_changes += 1
                whose = "OURS" if fg in ours else "not ours"
                say(f"[guard] foreground now pid {fg} hwnd {hwnd} ({whose})")
                last_fg = fg
            if cur != last_cur:
                cursor_moves += 1
                last_cur = cur
            if fg and fg in ours:
                say(f"[guard] our pid {fg} took the foreground: killing the tree")
                kill_tree(root)
                proc.wait(timeout=30)
                return KILLED
    finally:
        clip1 = user32.GetClipboardSequenceNumber()
        say(
            f"[guard] end rc {proc.poll()} fg changes {fg_changes} cursor moves {cursor_moves} "
            f"clipboard {clip0}->{clip1}"
        )
    return proc.returncode or 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        ap.error("no command after --")
    with open(args.log, "a", encoding="utf-8") as log:
        return guard(cmd, log)


if __name__ == "__main__":
    sys.exit(main())
