from __future__ import annotations

import pytest

from specto_core.address import Address, format_address, kind_of_id, parse_address
from specto_core.errors import AddressError


@pytest.mark.parametrize(
    "text",
    [
        "canvas:cv7/view:tamu.camera",
        "canvas:cv7/link:l4",
        "proj:robotA/canvas:cv7/col:c1",
        "canvas:cv1",
        "recipe:compare",
        "job:j12",
        "proj:p/canvas:cv2/chan:ch1.value.key",
    ],
)
def test_roundtrip(text: str):
    a = parse_address(text)
    assert format_address(a) == text
    assert str(a) == text


def test_fields():
    a = parse_address("proj:robotA/canvas:cv7/view:tamu.state.zoom")
    assert a == Address("view", "tamu", "cv7", "robotA", ("state", "zoom"))
    assert a.object_ref == "view:tamu"
    assert a.prop_pointer == "/state/zoom"
    assert not a.is_canvas
    assert parse_address("canvas:cv1").is_canvas


def test_canvas_is_never_implicit():
    with pytest.raises(AddressError) as e:
        parse_address("view:tamu")
    assert "canvas" in e.value.fix
    assert parse_address("view:tamu", default_canvas="cv1").canvas == "cv1"


def test_project_scoped_kinds_may_omit_canvas():
    a = parse_address("recipe:foo")
    assert a.kind == "recipe" and a.canvas is None


def test_bare_prefixed_ids_with_default_canvas():
    a = parse_address("tamu.state", default_canvas="cv1")
    assert (a.kind, a.id, a.props) == ("view", "tamu", ("state",))
    with pytest.raises(AddressError):
        parse_address("tamu")  # no canvas to default to


@pytest.mark.parametrize("bad", ["", "foo", "canvas:cv1/widget:w1", "canvas:cv1.prop", "view:"])
def test_bad_addresses(bad: str):
    with pytest.raises(AddressError):
        parse_address(bad)


def test_kind_of_id():
    assert kind_of_id("tamu") == "view"
    assert kind_of_id("ch12") == "chan"
    assert kind_of_id("x1") is None
