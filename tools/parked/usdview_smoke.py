"""usdview GUI smoke for the Storm build: open a tiny cube stage, wait, screenshot, quit.

    uv run python tools/desktop_lock.py run m2-usd -- ^
        ~/specto-build/openusd-venv/Scripts/python tools/usdview_smoke.py out/verify/m2/usd-usdview.png

Runs usdview from the build at ~/specto-build/openusd with the build venv's
Python (PySide6 6.12.0), waits SECONDS for the first Storm frame, grabs the usdview
window with QScreen.grabWindow, then terminates usdview. Exit 1 if the window never
appears or usdview exits early. Windows only (FindWindow by title).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("SPECTO_BUILD_ROOT", str(Path.home() / "specto-build")))
INST = ROOT / "openusd"
SMOKE = ROOT / "openusd-smoke"
SECONDS = 10

CUBE = """#usda 1.0
(
    defaultPrim = "Cube"
    upAxis = "Y"
)
def Cube "Cube"
{
    double size = 2
    color3f[] primvars:displayColor = [(0.2, 0.6, 0.9)]
}
"""

user32 = ctypes.windll.user32


def find_window(title_part: str) -> int:
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title_part in buf.value:
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rc = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top


def main() -> int:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else SMOKE / "usdview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    SMOKE.mkdir(parents=True, exist_ok=True)
    cube = SMOKE / "cube.usda"
    cube.write_text(CUBE)

    env = dict(os.environ)
    env["PATH"] = f"{INST / 'bin'};{INST / 'lib'};{env['PATH']}"
    env["PYTHONPATH"] = str(INST / "Lib" / "site-packages")
    log = SMOKE / "usdview.log"
    print(f"[RUN] usdview {cube} for {SECONDS}s; log {log}")
    t0 = time.monotonic()
    with log.open("w") as lf:
        proc = subprocess.Popen(
            [sys.executable, str(INST / "bin" / "usdview"), str(cube)],
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
        )
        hwnd = 0
        frame_at = 0.0
        while time.monotonic() - t0 < SECONDS:
            if proc.poll() is not None:
                print(f"[ERROR] usdview exited early with {proc.returncode}; see {log}")
                return 1
            if not hwnd:
                hwnd = find_window("cube.usda")
                if hwnd:
                    print(f"[OK] window after {time.monotonic() - t0:.1f}s")
                    user32.SetForegroundWindow(hwnd)
                    frame_at = time.monotonic() + 4.0
            elif frame_at and time.monotonic() >= frame_at:
                # click the viewport (upper right quarter) and press F: frame all,
                # so the shot is not the first frame's unframed camera
                frame_at = 0.0
                x, y, w, h = window_rect(hwnd)
                user32.SetCursorPos(int(x + w * 0.75), int(y + h * 0.35))
                user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
                user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
                time.sleep(0.3)
                user32.keybd_event(0x46, 0, 0, 0)  # 'F' down
                user32.keybd_event(0x46, 0, 0x0002, 0)  # KEYEVENTF_KEYUP
                time.sleep(0.5)
                # Alt + left drag: tumble the free camera so three faces show
                cx, cy = int(x + w * 0.75), int(y + h * 0.35)
                user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU (Alt) down
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                for i in range(1, 21):
                    user32.SetCursorPos(cx + 5 * i, cy + 3 * i)
                    time.sleep(0.02)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                user32.keybd_event(0x12, 0, 0x0002, 0)
                print(
                    f"[OK] framed and orbited (click, F, Alt-drag) at {time.monotonic() - t0:.1f}s"
                )
            time.sleep(0.25)
        if not hwnd:
            proc.terminate()
            print("[ERROR] usdview window not found; see", log)
            return 1

        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication([])
        x, y, w, h = window_rect(hwnd)
        img = QGuiApplication.primaryScreen().grabWindow(0, x, y, w, h)
        img.save(str(out))
        print(f"[OK] shot {out} ({w}x{h})")
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        del app
    print(f"[OK] usdview ran {time.monotonic() - t0:.1f}s, exit {proc.returncode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
