"""Where the native ``Specto.Native`` QML module lives and how to reach it.

The plugin is built by ``packages/specto-app/native/build.py`` into
``specto_app/qml/Specto/Native`` (git-ignored). Anything that loads it goes through
:func:`ensure_import_path`; anything that can run without it checks :func:`is_built`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtQml import QQmlEngine

QML_DIR = Path(__file__).resolve().parent.parent / "qml"
NATIVE_MODULE_DIR = QML_DIR / "Specto" / "Native"
GALLERY_QML = QML_DIR / "Specto" / "Gallery" / "GalleryView.qml"
BUILD_SCRIPT = Path(__file__).resolve().parents[3] / "native" / "build.py"
BUILD_HINT = f"build the native plugin: uv run python {BUILD_SCRIPT}"


def is_built() -> bool:
    return (NATIVE_MODULE_DIR / "qmldir").exists()


def ensure_import_path(engine: QQmlEngine) -> None:
    """Make ``import Specto.Native`` (and ``Specto.Gallery``) resolvable on ``engine``."""
    path = str(QML_DIR)
    if path not in engine.importPathList():
        engine.addImportPath(path)


__all__ = [
    "BUILD_HINT",
    "BUILD_SCRIPT",
    "GALLERY_QML",
    "NATIVE_MODULE_DIR",
    "QML_DIR",
    "ensure_import_path",
    "is_built",
]
