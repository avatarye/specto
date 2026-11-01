"""A Qt Quick scene rendered with OpenGL into an FBO, with no window on screen: a
``QQuickRenderControl`` on a ``QOffscreenSurface``. Seam-2 tests and the frame bench
use it to drive a ``FrameItem`` without taking the user's desktop.

The ``offscreen`` platform has no OpenGL (Windows, macOS, and the Linux wheel), so with a
display the process runs on the native platform (``windows``, ``cocoa``, ``xcb``) and never
shows a window: the offscreen surface is a hidden pbuffer or window that takes no focus and
no input. macOS gets the 4.1 core profile (:func:`specto_app.qt_setup.use_core_profile`).
Create the ``QGuiApplication`` after :func:`prepare_environment`.

Everything runs on the calling thread: ``updatePaintNode`` (sync) and the window's
``beforeRendering`` (render) both happen inside :meth:`OffscreenQuick.render`.
"""

from __future__ import annotations

import os
import sys

import shiboken6
from PySide6.QtCore import QSize, QUrl
from PySide6.QtGui import QImage, QOffscreenSurface, QOpenGLContext
from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import (
    QQuickGraphicsDevice,
    QQuickItem,
    QQuickRenderControl,
    QQuickRenderTarget,
    QQuickWindow,
    QSGRendererInterface,
)

from specto_app.qt_setup import use_core_profile
from specto_app.views import gallery_native


def prepare_environment() -> None:
    """OpenGL Qt Quick; call before the ``QGuiApplication`` exists."""
    os.environ["QSG_RHI_BACKEND"] = "opengl"
    os.environ.pop("QT_QUICK_BACKEND", None)
    # the offscreen platform has no OpenGL on Windows or macOS, nor in the Linux wheel (Qt
    # Quick falls back to its software adaptation); the native platform renders to an FBO
    # without showing a window. Headless Linux (no display) keeps offscreen.
    has_display = (
        os.name == "nt"
        or sys.platform == "darwin"
        or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    )
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and has_display:
        del os.environ["QT_QPA_PLATFORM"]
    use_core_profile()
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)


class OffscreenQuick:
    def __init__(self, width: int, height: int) -> None:
        self.context = QOpenGLContext()
        if not self.context.create():
            raise RuntimeError("no OpenGL context")
        self.surface = QOffscreenSurface()
        self.surface.setFormat(self.context.format())
        self.surface.create()
        self.control = QQuickRenderControl()
        self.window = QQuickWindow(self.control)
        self.window.setGraphicsDevice(QQuickGraphicsDevice.fromOpenGLContext(self.context))
        self.context.makeCurrent(self.surface)
        if not self.control.initialize():
            raise RuntimeError("QQuickRenderControl.initialize failed")
        self.engine = QQmlEngine()
        gallery_native.ensure_import_path(self.engine)
        self.fbo: QOpenGLFramebufferObject | None = None
        self.resize(width, height)

    def load(self, qml: str) -> QQuickItem:
        """Create ``qml`` (a component's source) as a child of the content item."""
        comp = QQmlComponent(self.engine)
        comp.setData(qml.encode(), QUrl())
        if comp.status() != QQmlComponent.Status.Ready:
            raise RuntimeError(comp.errorString())
        item = comp.create()
        item.setParentItem(self.window.contentItem())
        self._keep = comp
        return item

    def resize(self, width: int, height: int) -> None:
        self.context.makeCurrent(self.surface)
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        self.fbo = QOpenGLFramebufferObject(QSize(width, height), fmt)
        self.window.setRenderTarget(
            QQuickRenderTarget.fromOpenGLTexture(self.fbo.texture(), QSize(width, height))
        )
        self.window.setGeometry(0, 0, width, height)
        self.window.contentItem().setSize(QSize(width, height))

    def render(self) -> None:
        """Polish, sync and render one frame into the FBO."""
        self.context.makeCurrent(self.surface)
        self.control.polishItems()
        self.control.beginFrame()
        self.control.sync()
        self.control.render()
        self.control.endFrame()

    def grab(self) -> QImage:
        self.context.makeCurrent(self.surface)
        return self.fbo.toImage()

    def close(self) -> None:
        """Tear down with the context current, the scene before the render control: left
        to the interpreter's exit, ~QQuickRenderControl's QRhi makes a freed context
        current (a segfault on Linux)."""
        self.context.makeCurrent(self.surface)
        self.control.invalidate()
        self.fbo = None
        self._keep = None
        for obj in (self.engine, self.window, self.control):
            if shiboken6.isValid(obj):
                shiboken6.delete(obj)
        self.context.doneCurrent()
