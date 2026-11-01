"""An error the renderer draws past (#153 on a large depot: OpenUSD's GeomSubset verify in
Storm) is a ``warning`` from the source: the usd view stays ok, says the last one in its
health next to the file, keeps them in its snapshot, and forgets them with the stage.
:func:`tf_error_summary` makes one line of a ``Tf.ErrorException``."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_usd_host import health, stages, submit_usd

from specto_app.hosts import HostContext
from specto_app.usd.storm import tf_error_summary
from specto_app.views.usd_view import USD_MANIFEST, UsdHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager

SUBSET_ERRORS = """
\tError in 'pxrInternal_v0_26_8__pxrReserved__::HdStMesh::_UpdateDrawItemsForGeomSubsets' \
at line 420 in file /b/openusd-src/pxr/imaging/hdSt/mesh.cpp : 'Failed verification: ' \
subsetDrawItem ''
\tError in 'pxrInternal_v0_26_8__pxrReserved__::HdStMesh::_UpdateDrawItemsForGeomSubsets' \
at line 420 in file /b/openusd-src/pxr/imaging/hdSt/mesh.cpp : 'Failed verification: ' \
subsetDrawItem ''
\tError in 'pxrInternal_v0_26_8__pxrReserved__::Other::Fn' at line 9 in file /b/x.cpp : \
'bad thing'
"""


def test_a_tf_error_becomes_one_line_per_kind_with_its_count():
    assert tf_error_summary(SUBSET_ERRORS) == (
        "HdStMesh::_UpdateDrawItemsForGeomSubsets: Failed verification: subsetDrawItem (x2); "
        "Other::Fn: bad thing"
    )
    assert tf_error_summary("  something\n odd ") == "something odd"
    assert tf_error_summary("") == "an OpenUSD error"


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine = Engine(project=None, jobs=InlineJobManager())
    submit_usd(engine)
    h = UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True)
    yield h
    h.dispose()


def test_a_warning_keeps_the_view_ok_and_says_itself_until_the_next_stage(
    host: UsdHost, tmp_path: Path, spin
):
    host.receive("data", stages(tmp_path / "w", "valley", "lone_pine"))
    spin(until=lambda: health(host)["status"] == "ok" and host.stage.get("path"))
    first = Path(host.path).name
    host._on_event("warning", {"message": "OpenUSD reported errors, drawing goes on: X"})
    assert health(host) == {
        "status": "ok",
        "message": f"{first}: OpenUSD reported errors, drawing goes on: X",
        "hint": "",
    }
    assert host.snapshot()["renderer_warnings"] == ["OpenUSD reported errors, drawing goes on: X"]
    host._on_event("health", {"state": "ok", "message": first})  # a later ok keeps it
    assert health(host)["message"].endswith("drawing goes on: X")

    host.step_item(1)  # the next stage starts clean
    spin(until=lambda: health(host)["status"] == "ok" and Path(host.path).name != first)
    spin(until=lambda: host.stage.get("path") == host.path)
    assert health(host)["message"] == Path(host.path).name
    assert host.snapshot()["renderer_warnings"] == []
