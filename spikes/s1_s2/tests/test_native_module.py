"""Checks on the built QML module (spikes/s1_s2/qml/Specto/Spike).

The instantiation test loads the C++ plugin into a PySide6 process with the
offscreen platform: it proves the plugin built against the Qt SDK is loadable
by the wheel's Qt and that Python sees the item as a QQuickItem with the
C++ properties reachable through property()/setProperty(). No GPU involved.
Run: uv run pytest spikes/s1_s2/tests
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1]
MODULE = SPIKE / "qml" / "Specto" / "Spike"

pytestmark = pytest.mark.skipif(
    not (MODULE / "qmldir").exists(), reason="native module not built (run spikes/s1_s2/build.py)"
)


def test_qmldir_declares_plugin():
    text = (MODULE / "qmldir").read_text()
    assert re.search(r"^module Specto\.Spike$", text, re.M)
    assert re.search(r"^(optional )?plugin spectospike$", text, re.M)
    assert (MODULE / "spectospike.dll").exists()


def test_qmltypes_lists_textureitem():
    text = (MODULE / "spectospike.qmltypes").read_text()
    assert 'name: "TextureItem"' in text
    for prop in ("mode", "frame", "renderFps", "uploadGpuMs", "glRenderer"):
        assert f'name: "{prop}"' in text


def test_plugin_loads_in_pyside6_offscreen():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray, QUrl, qVersion
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickItem

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    assert qVersion() == "6.12.0", "pin: the plugin was built against 6.12.0"
    engine = QQmlEngine()
    engine.addImportPath(str(SPIKE / "qml"))
    comp = QQmlComponent(engine)
    comp.setData(
        b"import QtQuick\nimport Specto.Spike\n"
        b'TextureItem { objectName: "t"; mode: 1; triangles: 4242 }',
        QUrl(),
    )
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    item = comp.create()
    assert isinstance(item, QQuickItem)  # wrapped as the nearest known base
    assert item.metaObject().className() == "TextureItem"
    assert item.property("mode") == 1
    assert item.property("triangles") == 4242
    assert item.setProperty("mode", 2) and item.property("mode") == 2
    item.setProperty("frame", QByteArray(b"\x00" * 64))
    assert item.property("frame").size() == 64
    del item, comp, engine
    app.processEvents()
