"""Seam 2 (#140): selection in the usd view host against the stub USD source, headless. A
click in the viewport picks (the source answers the prim under it), the pick selects:
the source outlines it, the PRIMS outliner reveals it, ``selection`` emits ``<stage
key>#<prim path>`` and ``content`` the prim's properties for a json view. An outliner
click selects the same way; a double-click frames the prim; a ``selection`` received
selects the prims it names."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_usd_host import health, stages, submit_usd

from specto_app.hosts import HostContext
from specto_app.views.usd_view import USD_MANIFEST, UsdHost
from specto_core import Engine
from specto_core.contents import ContentValue
from specto_core.jobs import InlineJobManager
from specto_core.links import Selection

BASE_2 = "/World/Blocks/Block_02/Base"
TOP_3 = "/World/Blocks/Block_03/Top"


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine = Engine(project=None, jobs=InlineJobManager())
    submit_usd(engine)
    h = UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True)
    yield h
    h.dispose()


@pytest.fixture
def shown(host: UsdHost, tmp_path: Path, spin) -> UsdHost:
    """The host with the fake stage loaded and its outliner's first rows in."""
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok" and len(host.outliner_rows()) > 1)
    return host


def emitted(host: UsdHost, port: str):
    return host.ctx.links.output_of(host.view_id, port)


def outlined(host: UsdHost) -> list[str]:
    return host.snapshot()["source"].get("selected") or []


def selected_rows(host: UsdHost) -> list[str]:
    """The outliner rows shown as selected (QML marks a row whose path is selected)."""
    return [
        r["path"]
        for r in host.outliner_rows()
        if r["kind"] == "prim" and r["path"] in host.bridge.selected
    ]


def test_a_click_in_the_viewport_selects_the_prim_under_it(shown: UsdHost, spin):
    # the stub's picture: three blocks left to right, tops above bases, sky on top; NDC +y up
    shown.bridge.pick(0.0, -0.6, False)

    spin(until=lambda: emitted(shown, "selection") is not None)
    key = f"valley#{BASE_2}"
    assert emitted(shown, "selection") == Selection(keys=[key], last=key)
    assert shown.snapshot()["selection"] == {
        "paths": [BASE_2],
        "hit": {"path": BASE_2, "position": [0.0, -6.0, 4.0], "normal": [0.0, -1.0, 0.0]},
    }
    spin(until=lambda: outlined(shown) == [BASE_2])  # the source outlines it
    # the outliner opens the way down and marks the row
    spin(until=lambda: selected_rows(shown) == [BASE_2])
    rows = {r["path"]: r for r in shown.outliner_rows()}
    assert rows["/World/Blocks"]["expanded"] and rows["/World/Blocks/Block_02"]["expanded"]
    assert shown.snapshot()["outliner"]["revealed"] == BASE_2


def test_the_content_port_carries_the_selected_prims_properties(shown: UsdHost, spin):
    shown.bridge.pick(0.8, 0.4, False)

    spin(until=lambda: emitted(shown, "content") is not None)
    content = emitted(shown, "content")
    assert isinstance(content, ContentValue)
    (entry,) = content.entries
    assert entry.key == f"valley#{TOP_3}"
    assert entry.path == f"valley.usda#{TOP_3}"
    assert entry.format == "json"
    assert entry.data["path"] == TOP_3 and entry.data["type"] == "Mesh"
    assert entry.data["properties"]["specto:block"] == "Block_03"


def test_ctrl_click_adds_and_a_click_on_nothing_clears(shown: UsdHost, spin):
    shown.bridge.pick(0.0, -0.6, False)
    shown.bridge.pick(0.8, 0.4, True)
    spin(until=lambda: outlined(shown) == [BASE_2, TOP_3])
    sel = emitted(shown, "selection")
    assert sel.keys == [f"valley#{BASE_2}", f"valley#{TOP_3}"]
    assert sel.last == f"valley#{TOP_3}"
    shown.bridge.pick(0.8, 0.4, True)  # ctrl on a selected prim takes it out
    spin(until=lambda: outlined(shown) == [BASE_2])

    shown.bridge.pick(0.0, 0.9, False)  # the sky
    spin(until=lambda: outlined(shown) == [])
    assert emitted(shown, "selection") == Selection(keys=[], last=None)
    spin(until=lambda: emitted(shown, "content").entries == [])
    assert shown.snapshot()["selection"] == {"paths": [], "hit": None}


def test_a_click_in_the_outliner_selects_and_a_double_click_frames_the_prim(shown: UsdHost, spin):
    shown.bridge.selectRow("/World/Blocks", False)
    spin(until=lambda: outlined(shown) == ["/World/Blocks"])
    key = "valley#/World/Blocks"
    assert emitted(shown, "selection") == Selection(keys=[key], last=key)
    assert selected_rows(shown) == ["/World/Blocks"]

    shown.bridge.frameRow("/World/Blocks/Block_01")
    spin(until=lambda: shown.camera.calls[-1][0] == "frame")
    assert shown.camera.calls[-1] == ("frame", [[-48.0, -6.0, 0.0], [-32.0, 6.0, 12.0]])
    assert outlined(shown) == ["/World/Blocks/Block_01"]  # framing selects it too


def test_a_selection_received_selects_and_reveals_past_the_first_page(shown: UsdHost, spin):
    van = "/World/Vehicles/Van_200"
    shown.receive("selection", Selection(keys=[f"valley#{van}"], last=f"valley#{van}"))

    spin(until=lambda: outlined(shown) == [van])
    spin(until=lambda: selected_rows(shown) == [van])
    rows = [r for r in shown.outliner_rows() if r["path"].startswith("/World/Vehicles")][1:]
    assert len(rows) == 201 + 1 and rows[-1] == {
        "kind": "more", "path": "/World/Vehicles", "depth": 2, "count": 49,
    }  # fmt: skip
    spin(until=lambda: emitted(shown, "content") is not None)
    assert emitted(shown, "content").entries[0].data["path"] == van
    # keys of another stage, or prims this stage lacks, select nothing here
    shown.receive("selection", Selection(keys=["stage-b#/World/Sky"], last="stage-b#/World/Sky"))
    assert outlined(shown) == [van]


def test_stepping_to_another_stage_clears_the_selection(host: UsdHost, tmp_path: Path, spin):
    host.receive("data", stages(tmp_path / "w", "valley", "stage-a"))
    spin(until=lambda: health(host)["status"] == "ok")
    host.bridge.pick(0.0, -0.6, False)
    spin(until=lambda: outlined(host) == [BASE_2])

    host.step_item(1)
    assert host.snapshot()["selection"]["paths"] == []
    assert emitted(host, "selection") == Selection(keys=[], last=None)


def test_a_selection_received_is_shown_not_republished(shown: UsdHost, spin):
    key = f"valley#{BASE_2}"
    shown.receive("selection", Selection(keys=[key], last=key))
    spin(until=lambda: outlined(shown) == [BASE_2])
    spin(until=lambda: emitted(shown, "content") is not None)
    assert emitted(shown, "selection") is None
