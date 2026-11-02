"""The usd view's camera (#137): the navigation model (:class:`.nav.Nav`, Unreal Editor's
viewport scheme since #151, orbit mode since #39, driven by :class:`.ticker.NavTicker`)
behind the host's ``CameraControl`` seam (#139). Every change
reaches the host as one ``on_camera(set_camera params, cut)`` call, in the stage's frame,
which the host sends to the source."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .nav import QT_NOTCH, Button, CameraState, Nav
from .ticker import NavTicker

OnCamera = Callable[[dict[str, Any], bool], None]


def set_camera_params(state: CameraState) -> dict[str, Any]:
    """``set_camera``'s params for a camera state."""
    return {
        "position": list(state.eye),
        "target": list(state.target),
        "up": list(state.up),
        "fov_y": state.fov_y,
        "near": state.near,
        "far": state.far,
    }


class NavCamera:
    def __init__(self, on_camera: OnCamera) -> None:
        self._on_camera = on_camera
        self.nav = Nav()
        self.ticker = NavTicker(self.nav, self._changed)

    def _changed(self, state: CameraState, cut: bool) -> None:
        self._on_camera(set_camera_params(state), cut)

    @property
    def camera_speed(self) -> float:
        return self.nav.camera_speed

    @camera_speed.setter
    def camera_speed(self, value: float) -> None:
        self.nav.camera_speed = value

    @property
    def mode(self) -> str:
        return self.nav.mode

    @mode.setter
    def mode(self, value: str) -> None:
        self.nav.set_mode(value)

    def load(self, bounds: list[list[float]] | None, up_axis: str) -> None:
        self.nav.load((bounds[0], bounds[1]) if bounds else None, up_axis)  # type: ignore[arg-type]
        self.ticker.kick()

    def press(self, button: Button, alt: bool) -> None:
        self.nav.press(button, alt)
        self.ticker.kick()

    def release(self, button: Button) -> None:
        self.nav.release(button)
        self.ticker.kick()

    @property
    def pivoting(self) -> bool:
        return self.nav.pivoting

    def set_pivot(self, point: list[float]) -> None:
        self.nav.set_pivot(point)

    def set_anchor(self, point: list[float] | None) -> None:
        self.nav.set_anchor(point)

    def drag(self, dx: float, dy: float) -> None:
        self.nav.drag(dx, dy)
        self.ticker.kick()

    def wheel(self, angle_delta_y: float) -> None:
        self.nav.wheel(angle_delta_y / QT_NOTCH)
        self.ticker.kick()

    def set_key(self, key: str, down: bool) -> None:
        self.nav.set_key(key, down)
        self.ticker.kick()

    def release_keys(self) -> None:
        self.nav.release_keys()
        self.ticker.kick()

    def world_origin(self) -> None:
        self.nav.world_origin()
        self.ticker.kick()

    def frame_all(self) -> None:
        self.nav.frame_all()
        self.ticker.kick()

    def frame_point(self, point: list[float]) -> None:
        self.nav.frame_point(point)
        self.ticker.kick()

    def frame(self, bounds: list[list[float]]) -> None:
        self.nav.frame_bounds((bounds[0], bounds[1]))  # type: ignore[arg-type]
        self.ticker.kick()

    def stop(self) -> None:
        self.ticker.stop()

    def apply_value(self, value: dict[str, Any]) -> None:
        self.nav.apply_camera_value(value)
        self.ticker.kick()

    def value(self) -> dict[str, Any]:
        return self.nav.camera_value()
