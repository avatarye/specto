"""Seam 2 (#176): the usd view's ``camera`` ports, against the stub USD source, headless.
The usd design (``camera`` in/out, a ``CameraValue`` as the 3d view's, so the two views
can share a viewpoint): a camera arriving on the input drives the navigation model and
goes to the source, not echoed back; the user's navigation goes out on ``camera``,
throttled as the 3d page's; a camera that arrives before the stage opens is taken once
it has (the stage's first look does not override it)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_usd_host import health, stages, submit_usd

from specto_app.hosts import HostContext
from specto_app.usd.camera import NavCamera
from specto_app.views.usd_view import USD_MANIFEST, UsdHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager
from specto_core.links import CameraValue

#: a 3d view's camera (Y up): ten units out on +x and +z, two up, looking at the origin
SHARED = CameraValue(position=(10.0, 2.0, 10.0), target=(0.0, 0.0, 0.0), fov_y=0.6)


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    engine = Engine(project=None, jobs=InlineJobManager())
    submit_usd(engine)
    h = UsdHost(HostContext(engine, None), "kobi", USD_MANIFEST, headless=True,
                camera_factory=NavCamera)  # fmt: skip
    yield h
    h.dispose()


def source_camera(host: UsdHost) -> dict:
    return host.snapshot()["source"].get("camera") or {}


def emitted(host: UsdHost):
    return host.ctx.links.output_of(host.view_id, "camera")


def test_a_received_camera_drives_the_view_and_is_not_echoed(host: UsdHost, tmp_path, spin):
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok" and source_camera(host))
    spin(200)  # the first look's camera, if any, is out
    first = emitted(host)

    host.receive("camera", SHARED)
    # the stage is Z up: the 3d view's (x, y, z) is the stage's (x, -z, y)
    spin(until=lambda: source_camera(host).get("fov_y") == pytest.approx(0.6))
    cam = source_camera(host)
    assert cam["position"] == pytest.approx([10.0, -10.0, 2.0])
    assert cam["cut"] is True
    spin(300)  # longer than the throttle: still nothing new goes out
    assert emitted(host) == first


def test_the_users_navigation_goes_out_on_camera(host: UsdHost, tmp_path, spin):
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok" and source_camera(host))
    host.receive("camera", SHARED)
    spin(200)

    host.bridge.wheel(120)  # a dolly towards the target
    spin(until=lambda: emitted(host) is not None and emitted(host) != SHARED)
    out = emitted(host)
    assert isinstance(out, CameraValue)
    # still on the line from the shared eye to the origin, nearer to it (3d frame)
    assert out.position[1] / out.position[0] == pytest.approx(0.2)
    assert abs(out.position[0]) < 10.0
    assert out.fov_y == pytest.approx(0.6)


def test_a_camera_received_before_the_stage_opens_is_taken_once_it_has(
    host: UsdHost, tmp_path, spin
):
    host.receive("camera", SHARED)
    host.receive("data", stages(tmp_path / "w", "valley"))
    spin(until=lambda: health(host)["status"] == "ok" and source_camera(host))
    spin(until=lambda: source_camera(host).get("fov_y") == pytest.approx(0.6))
    assert source_camera(host)["position"] == pytest.approx([10.0, -10.0, 2.0])
