"""The usd view's failure in the real shell (#155), offscreen with the stub USD source:
a renderer that dies leaves the view saying why, with its last error lines, and Retry
(and Open with Storm where this machine has Storm) over the dead picture."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from specto_core import CallContext, Project

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin

from specto_app.app import find_item
from specto_app.views import gallery_native
from specto_core.machine import MachineCapability

pytestmark = pytest.mark.skipif(not gallery_native.is_built(), reason=gallery_native.BUILD_HINT)

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
STAGE = '#usda 1.0\n(\n    upAxis = "Z"\n)\ndef Xform "World" {}\n'


@pytest.fixture
def project(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "proj", "demo")
    (proj.root / "worlds").mkdir()
    (proj.root / "worlds" / "hillside.usda").write_text(STAGE, encoding="utf-8")
    return proj


def item(app, name: str):
    found = find_item(app.window.contentItem(), name)
    assert found is not None, name
    return found


@pytest.fixture
def usd(app, spin):
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "usd",
        "label": "World",
        "layout": {"position": [380, 40], "size": [760, 420]},
    }
    pin(doc, "kobi", sources=["worlds"])
    app.engine.call("canvas.submit", {"document": doc, "label": "add kobi"}, AGENT)
    spin(until=lambda: "kobi" in app.hosts.hosts)
    spin(until=lambda: app.hosts.ctx.health_of("kobi")["status"] == "ok", timeout=15)
    return app.hosts.hosts["kobi"]


def test_a_dead_renderer_shows_why_and_retry_brings_it_back(app, spin, usd):
    # Storm as present shows Open with Storm; Retry runs on the real ones (the stub), as
    # a renderer chosen from capabilities a process keeps would (#155)
    real = app.engine.capabilities
    app.engine._capabilities = {"storm": MachineCapability(name="storm", present=True)}
    failure = item(app, "failure:kobi")
    assert not failure.isVisible()

    usd.source.send(
        "crash", code=0xC0000005, stderr=["[Error] [gpu.foundation] VkResult: ERROR_DEVICE_LOST"]
    )
    spin(until=lambda: usd.failure is not None, timeout=10)
    spin(20)

    assert failure.isVisible() and not item(app, "phase:kobi").isVisible()
    reason = item(app, "failureReason:kobi").property("text")
    assert reason == (
        "the renderer crashed (access violation, 0xC0000005)"
        if sys.platform == "win32"
        else "the renderer crashed (segmentation fault, SIGSEGV)"
    )
    assert item(app, "storm:kobi").isVisible()

    app.engine._capabilities = real
    retry = item(app, "retry:kobi")
    p = retry.mapToScene(retry.boundingRect().center()).toPoint()
    QTest.mouseClick(app.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    spin(until=lambda: app.hosts.ctx.health_of("kobi")["status"] == "ok", timeout=15)
    assert not failure.isVisible() and usd.source is not None
