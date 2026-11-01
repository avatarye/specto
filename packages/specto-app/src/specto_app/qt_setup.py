"""Process-level Qt setup shared by the app, the demos and the tests (spike S1/S2 verdict).

``prepare()`` runs before ``QGuiApplication`` exists: it selects the OpenGL RHI backend,
the Basic controls style, declares the process's AppUserModelID (``branding``), and (when
the web package is present) registers ``specto://`` and initialises QtWebEngine for Qt
Quick. ``install_qml(engine)`` runs after the
application exists: the QML import path for ``Specto.Shell`` / ``Specto.Web`` /
``Specto.Controls`` and the theme context properties.

Context properties every QML file may rely on:

- ``theme``: the canonical ``ThemeObject`` (``theme.colors.surface``, ``theme.glyph("added")``)
- ``SpectoTheme``: the same tokens flattened (``SpectoTheme.surface``) for web views that
  prefer one map
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .branding import set_app_user_model_id
from .theme import flat_theme

if TYPE_CHECKING:
    from PySide6.QtQml import QQmlEngine

QML_DIR = Path(__file__).resolve().parent / "qml"

_prepared: str | None = None  # "web" | "plain" once prepare() ran


def have_webengine() -> bool:
    try:
        import PySide6.QtWebEngineQuick  # noqa: F401
    except ImportError:
        return False
    return True


def prepare(*, backend: str = "opengl", web: bool | None = None, offscreen: bool = False) -> bool:
    """Set the environment and initialise WebEngine. Returns True when web views are
    available in this process. Idempotent."""
    global _prepared
    os.environ.setdefault("QSG_RHI_BACKEND", backend)
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if _prepared is not None:
        return _prepared == "web"
    use_core_profile()
    _prepare_settings()
    set_app_user_model_id()  # before the first window: the taskbar groups us as Specto
    want_web = have_webengine() if web is None else web
    ok = False
    if want_web:
        try:
            from PySide6.QtWebEngineQuick import QtWebEngineQuick

            try:
                from .web.scheme import register_scheme  # the web builder's package

                register_scheme()
            except ImportError:
                pass
            QtWebEngineQuick.initialize()
            ok = True
        except ImportError as e:  # pragma: no cover - depends on the wheel
            print(f"[WARN] QtWebEngine unavailable, web views disabled: {e}")
    _prepared = "web" if ok else "plain"
    return ok


def use_core_profile() -> None:
    """On macOS, OpenGL 4.1 core for every context; call before the ``QGuiApplication``
    exists. Without it macOS hands out legacy 2.1, which has no ``glMapBufferRange``
    (the native frame item's upload). Elsewhere the default context already has it."""
    if sys.platform != "darwin":
        return
    from PySide6.QtGui import QSurfaceFormat

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setVersion(4, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    QSurfaceFormat.setDefaultFormat(fmt)


def _prepare_settings() -> None:
    """GUI-only state (rail width, open page) lives in an ini file under the user data
    dir (``SPECTO_USER_DIR`` in tests), never in the registry or the project."""
    from PySide6.QtCore import QCoreApplication, QSettings

    from .recents import user_data_dir

    QCoreApplication.setOrganizationName("specto")
    QCoreApplication.setApplicationName("specto-app")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(user_data_dir() / "settings")
    )


def install_scheme_handler(*, debug: bool = False) -> Any:
    """Install the ``specto://`` handler on the default profile through the web runtime
    (``web.runtime``) when the web package is present; returns the handler (keep a
    reference) or None."""
    try:
        from .web.runtime import runtime
    except ImportError:
        return None
    return runtime().install(debug=debug)


def install_qml(engine: QQmlEngine, theme: Any | None = None) -> Any:
    """Import path plus the ``theme``, ``SpectoTheme`` and ``logoArt`` (the S character's
    parts and poses, read from ``assets/logo``, #170) context properties. Returns the
    ``ThemeObject`` (keep a reference: context properties are not owned)."""
    from .prompt.icon_pose import art_for_qml
    from .theme import ThemeObject

    engine.addImportPath(str(QML_DIR))
    obj = theme or ThemeObject()
    ctx = engine.rootContext()
    ctx.setContextProperty("theme", obj)
    ctx.setContextProperty("SpectoTheme", flat_theme())
    ctx.setContextProperty("logoArt", art_for_qml())
    install_icons(engine)
    install_identicons(engine)
    return obj


_providers: list[Any] = []  # the engine takes ownership in C++; keep the wrappers alive


def install_icons(engine: QQmlEngine) -> None:
    """The ``image://icons`` provider (theme-tinted SVG line icons, ``Icon.qml``)."""
    from .icons import PROVIDER_ID, IconProvider

    if engine.imageProvider(PROVIDER_ID) is not None:
        return
    provider = IconProvider()
    _providers.append(provider)
    engine.addImageProvider(PROVIDER_ID, provider)


def install_identicons(engine: QQmlEngine) -> None:
    """The ``image://identicon`` provider (view identicons on ports, #79)."""
    from .canvas.identicon import PROVIDER_ID, IdenticonProvider

    if engine.imageProvider(PROVIDER_ID) is not None:
        return
    provider = IdenticonProvider()
    _providers.append(provider)
    engine.addImageProvider(PROVIDER_ID, provider)


def install_image_provider(engine: QQmlEngine, name: str, provider: Any) -> None:
    _providers.append(provider)
    engine.addImageProvider(name, provider)


def raise_timer_resolution() -> None:
    """Qt 6 no longer raises the Windows timer resolution; without this a 16 ms timer
    fires every 15.6 ms at best and a 4 ms one every 15.6 ms (spike S1/S2)."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)


__all__ = [
    "QML_DIR",
    "have_webengine",
    "install_qml",
    "install_scheme_handler",
    "prepare",
    "raise_timer_resolution",
]
