"""SPEC-48: a submit that keeps a view's id but changes its type gets the new type's
host. The registry handed the patch to the old host (a MissingHost for a type with no
host here, or the previous type's host) and never built the new one."""

from __future__ import annotations

from pathlib import Path

import pytest
import test_shell
from app_doc_helpers import pin
from gallery_helpers import FakeGallery, needs_testdata
from test_shell import submit

from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.hosts.registry import MissingHost
from specto_app.views.gallery import GalleryFactory, GalleryHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager

frames_project = test_shell.frames_project
app = test_shell.app


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine = Engine(project=None, jobs=InlineJobManager())
    clear_pending()
    fakes: list[FakeGallery] = []

    class Factory(GalleryFactory):
        def create(self, ctx, view_id, manifest):
            fakes.append(FakeGallery())
            return GalleryHost(ctx, view_id, manifest, gallery=fakes[-1])

    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)  # no host for "3d" here
    reg.add_factory(Factory())
    yield engine, ctx, reg
    reg.dispose_all()
    engine.close()


def _submit(engine: Engine, vtype: str, label: str = "kobi") -> None:
    doc = engine.live.document()
    view = doc["views"].get("kobi")
    doc["views"]["kobi"] = {**(view or {"layout": {"size": [600, 400]}}), "type": vtype}
    doc["views"]["kobi"]["label"] = label
    if view is None:
        pin(doc, "kobi")  # the project's files feed it, once
    engine.live.submit(doc, origin="agent:s1", label=f"kobi as {vtype}")


def test_a_type_change_builds_the_new_types_host(setup):
    engine, ctx, reg = setup
    _submit(engine, "3d")
    assert isinstance(reg.host("kobi"), MissingHost)
    assert ctx.health_of("kobi")["status"] == "error"

    _submit(engine, "gallery")
    host = reg.host("kobi")
    assert isinstance(host, GalleryHost) and not host.disposed
    assert ctx.health_of("kobi")["status"] != "error"

    _submit(engine, "3d")  # and back: the gallery host goes
    assert host.disposed and isinstance(reg.host("kobi"), MissingHost)


def test_a_change_that_keeps_the_type_keeps_the_host(setup):
    engine, _ctx, reg = setup
    _submit(engine, "gallery")
    host = reg.host("kobi")
    _submit(engine, "gallery", label="renamed")
    assert reg.host("kobi") is host and not host.disposed


# -- in the app's window: the new host takes the view's slot ----------------------------
@needs_testdata
def test_in_the_app_the_new_host_takes_the_views_slot(app, spin):
    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "3d",
        "label": "Cam A",
        "layout": {"position": [300, 0], "size": [640, 400]},
    }
    pin(doc, "kobi")
    submit(app, doc, "3d")
    spin(until=lambda: app.hosts.slot_of("kobi") is not None, timeout=5)
    old = app.hosts.hosts["kobi"]

    doc = app.live.document()
    doc["views"]["kobi"]["type"] = "gallery"
    submit(app, doc, "kobi as gallery")
    spin(until=lambda: isinstance(app.hosts.hosts.get("kobi"), GalleryHost))
    new = app.hosts.hosts["kobi"]
    assert new is not old and getattr(old, "disposed", True)
    slot = app.hosts.slot_of("kobi")
    assert slot is not None and new.item.parentItem() is slot and new.item.isVisible()
