"""A closed app leaves nothing for the cyclic GC: the shell (QML engine, window) is
destroyed by ``close()`` itself, and dropping the closed app frees it by reference count.
Otherwise the collector tears the Qt tree down in an arbitrary order inside whichever
later test's Qt loop happens to trigger it (a hard crash with no test named)."""

from __future__ import annotations

import gc
import weakref

import pytest

pytest.importorskip("PySide6.QtQuick")

import shiboken6

from specto_app.app import SpectoApp


def test_close_destroys_the_shell_and_the_closed_app_dies_without_the_cyclic_gc(
    qapp, project, spin
):
    a = SpectoApp(project, jobs="inline", web=False, offscreen=True, serve=True)
    a.start()
    spin(50)
    qml, window = a.qml, a.window
    a.close()
    spin(20)
    assert not shiboken6.isValid(qml), "close() left the QML engine alive"
    assert not shiboken6.isValid(window), "close() left the window alive"
    ref = weakref.ref(a)
    gc.disable()
    try:
        del a
        assert ref() is None, "the closed app is held in a reference cycle"
    finally:
        gc.enable()
