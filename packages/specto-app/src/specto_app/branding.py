"""The app's identity on the desktop (#77): the window and taskbar icon.

The icon is the S character (#170) on a tile (``assets/specto.svg``); ``assets/specto.ico``
holds the hand-hinted rasters ``tools/make_icon.py`` packs from it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QGuiApplication, QIcon

ASSETS = Path(__file__).resolve().parent / "assets"
ICON_SVG = ASSETS / "specto.svg"
ICON_ICO = ASSETS / "specto.ico"

#: argv[0] of the app's QGuiApplication: X11 takes the window class (WM_CLASS) from it,
#: which would be ``__main__.py`` under ``python -m specto_app`` (the launcher's relaunch)
APP_ARGV0 = "specto-app"

#: Windows groups taskbar buttons by this id, and a Python process inherits python.exe's
#: (with Python's icon) unless it declares its own before its first window.
APP_USER_MODEL_ID = "avatarye.specto.app"


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Tell Windows this process is Specto. Returns True when the id was set; False off
    Windows (nothing to do) or when the shell refused it."""
    if sys.platform != "win32":
        return False
    import ctypes

    hr = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    if hr != 0:
        print(f"[WARN] AppUserModelID not set (HRESULT {hr:#010x})")
        return False
    return True


def app_icon() -> QIcon:
    """The window icon with every size the .ico holds; Qt picks the hinted 16 and 24 for
    the title bar and the taskbar instead of scaling the 256."""
    from PySide6.QtGui import QIcon

    return QIcon(str(ICON_ICO))


def install_app_icon(qapp: QGuiApplication) -> QIcon:
    """Set the application window icon, which every window created afterwards inherits.
    Call it right after ``QGuiApplication`` exists and before the first window."""
    icon = app_icon()
    if icon.isNull():
        print(f"[WARN] window icon not loaded from {ICON_ICO}")
    qapp.setWindowIcon(icon)
    return icon


__all__ = [
    "APP_ARGV0",
    "APP_USER_MODEL_ID",
    "ASSETS",
    "ICON_ICO",
    "ICON_SVG",
    "app_icon",
    "install_app_icon",
    "set_app_user_model_id",
]
