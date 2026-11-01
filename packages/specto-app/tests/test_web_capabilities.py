"""Per-view capability lists (HLD 3.11, on ports per ADR 0014): parsing, globs, and the
defaults a web view gets from its type's output ports and the links feeding it."""

from __future__ import annotations

import pytest

from specto_app.web.capabilities import (
    TILE_HOSTS,
    Capabilities,
    CapabilityDenied,
    default_capabilities,
    tile_host_allowed,
)


def test_exact_and_glob_matching():
    caps = Capabilities.of(["read:link:l1", "emit:selection", "cmd:describe", "params:layers"])
    assert caps.can_read("l1") and not caps.can_read("l2")
    assert caps.can_emit("selection") and not caps.can_emit("camera")
    assert caps.can_call("describe") and not caps.can_call("canvas.undo")
    assert caps.can_set_param("layers") and not caps.can_set_param("basemap")
    assert Capabilities.of(["cmd:canvas.*"]).can_call("canvas.undo")
    assert sorted(caps) == ["cmd:describe", "emit:selection", "params:layers", "read:link:l1"]
    assert "emit:selection" in caps and len(caps) == 4


def test_require_raises_with_fix_and_granted_list():
    caps = Capabilities.of(["emit:selection"])
    with pytest.raises(CapabilityDenied) as ei:
        caps.require("cmd", "canvas.undo", page="tamu")
    e = ei.value
    assert e.code == "capability"
    assert "tamu" in e.message and "cmd:canvas.undo" in e.message
    d = e.to_dict()
    assert d["fix"] and d["data"]["granted"] == ["emit:selection"] and d["data"]["kind"] == "cmd"


@pytest.mark.parametrize("bad", ["item", "chan:", "chan:item", "bogus:item", "", "net:"])
def test_malformed_grants_are_rejected(bad: str):
    with pytest.raises(ValueError):
        Capabilities.of([bad])


def test_defaults_come_from_the_ports_and_the_links_in():
    # a map fed by a read_contents link and a follow link: it may read both links'
    # values, emit its one output port, write its layer params, and load tiles
    caps = default_capabilities("map", {"features": "l2", "selection": "l5"})
    assert sorted(caps) == [
        "cmd:describe",
        "cmd:vocabulary",
        "emit:selection",
        "net:tiles",
        "params:basemap",
        "params:layers",
        "params:limit",
        "params:panels",
        "read:link:l2",
        "read:link:l5",
    ]
    # json: content and selection out, no tiles, no layer params
    caps = default_capabilities("json", {"data": "l3"})
    assert caps.can_read("l3") and caps.can_emit("content") and caps.can_emit("selection")
    assert (
        not caps.can_net("tiles")
        and not caps.can_set_param("layers")
        and caps.can_set_param("wrap")
    )
    assert caps.can_call("describe") and not caps.can_call("canvas.submit")
    # an unfed view reads nothing; an unknown type still gets the always-allowed commands
    assert not any(c.startswith("read:") for c in default_capabilities("markdown", {}))
    assert list(default_capabilities("nope", {})) == ["cmd:describe", "cmd:vocabulary"]


def test_tile_hosts():
    assert tile_host_allowed(TILE_HOSTS[0].upper()) and not tile_host_allowed("evil.org")
    assert Capabilities.of(["net:tiles"]).can_net("tiles")
