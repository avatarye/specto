"""Camera navigation for the ``usd`` view: Unreal Editor's perspective viewport scheme
(#151), a free camera (an eye with yaw and pitch, no orbit target) that only turns around
a point while Alt is held.

The model works in the 3d view's frame (Babylon's: right-handed, Y up; z-up data is
turned by -90 degrees about X, as the 3d page's ``up: "z"`` root does), so its ``camera``
port value is the 3d view's and the two views can share a camera. Points from the stage
(bounds, picks, stage cameras) come in, and the camera state goes out, in the stage's own
frame, chosen by its ``upAxis``.

The gestures: the table the 3d view's ``web/viewnav.js`` implements (#152), the same
rates, so keep the two in step. ``dx, dy`` are logical pixels since the last move, +x
right, +y down; ``u`` is the reach (the scene radius: half the bounds' diagonal, 10
without bounds) x camera speed; "up" is world up (the stage's up axis). No inertia.

====================  ==============================================================
input                 motion
====================  ==============================================================
LMB drag              yaw by dx x ROTATE (right turns right), then the eye and the focus
                      point move along the ground heading by -dy x WALK x u (up: forward)
LMB click             moved at most CLICK_SLOP px, no other button, no Alt: the host
                      picks (#140); a plain LMB drag starts past the slop, the move that
                      crosses it counting whole (the view's QML holds the slop)
RMB drag              look: yaw by dx x ROTATE, pitch by -dy x ROTATE about the yawed
                      right, within +-PITCH_LIMIT; the eye and the focus distance stay
RMB held + W/S, A/D   fly along the view / the camera's right, and down / up along world
RMB held + Q/E        up, at FLY x u per second (x SHIFT with Shift), ticked per frame
                      from the held keys (key repeat plays no part); nothing without RMB
RMB held + wheel      camera speed: log2 += notches x SPEED_STEP (away: faster), within
                      2^SPEED_MIN .. 2^SPEED_MAX; live, kept by the host on RMB release
MMB, LMB+RMB drag     pan: the eye and the focus point move by right x dx x PAN x u +
                      screen up x -dy x PAN x u (screen up = right x forward): the camera
                      moves with the mouse
wheel                 dolly: the eye and the focus point move along the view by notches x
                      WHEEL x u (a notch is angleDelta QT_NOTCH; away: forward)
Alt + LMB drag        orbit: the look's turn applied to eye - pivot and focus - pivot
Alt + RMB drag        dolly along the view by (dx - dy) x DOLLY x u (right or up: forward)
Alt + MMB drag        pan, as MMB
F                     frame the selection (the union of its bounds), else everything: the
                      view keeps its direction, the box's centre ahead at radius /
                      sin(fov / 2) x 1.05 (radius: half its diagonal)
====================  ==============================================================

Alt is read when the first button goes down and holds until the last comes up; with Alt,
the middle button pans, else the left orbits, else the right dollies. The pivot is taken
at the Alt+LMB press: the selection's picked point (where the click that selected it hit),
else the centre of its bounds, else the point under the cursor (the host answers these
from the source and hands them to :meth:`Nav.set_pivot`, a moment after the press), else,
until then or when nothing is there, the focus point (``focus`` ahead). F frames a
selection that has no bounds but a picked point as a cube POINT_FRAME x reach across.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Vec3 = tuple[float, float, float]
Bounds = tuple[Sequence[float], Sequence[float]]
UpAxis = Literal["Y", "Z"]
Button = Literal["left", "right", "middle"]
Gesture = Literal["walk", "look", "pan", "orbit", "dolly"]

# the 3d view's constants (web/3d/main.js and views/scene3d_view.py)
DEFAULT_FOV_Y = 0.8  # Babylon's ArcRotateCamera default (radians)
FIRST_LOOK = (-0.55, -0.6, 0.58)  # the first look: down from a three-quarter view
DEFAULT_RADIUS = 10.0  # the world's reach when nothing has bounds (moves agree)
SPEED_MIN, SPEED_MAX = -3, 3  # camera speed is 2^SPEED_MIN .. 2^SPEED_MAX (1/8x .. 8x)
LOWER_RADIUS_LIMIT = 0.01  # the focus point stays at least this far ahead of the eye
# Unreal Editor's viewport (#151), web/viewnav.js's rates (#152); u = reach x speed
ROTATE = 0.004  # radians per px: yaw, pitch, orbit
WALK = 0.01  # u per px: LMB forward / back
PAN = 0.004  # u per px
DOLLY = 0.01  # u per px: Alt+RMB
WHEEL = 0.1  # u per notch
FLY = 0.5  # u per second
SHIFT = 4  # Shift flies this much faster
SPEED_STEP = 0.25  # log2 of the camera speed per notch with RMB held
PITCH_LIMIT = math.pi / 2 - 0.01
CLICK_SLOP = 4  # px: a left press that moves no further is a click (UsdView.qml)
POINT_FRAME = 0.05  # F on a picked point frames a cube this many reaches across
FLY_KEYS = "wasdqe"
QT_NOTCH = 120  # Qt's angleDelta of one wheel notch
USD_VERTICAL_APERTURE = 15.2908  # UsdGeomCamera's default verticalAperture (mm)
WORLD_UP: Vec3 = (0.0, 1.0, 0.0)  # in the nav frame


def _add(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Sequence[float], k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(c * c for c in v))


def _unit(v: Sequence[float]) -> Vec3:
    n = _norm(v) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vec(v: object) -> Vec3:
    x, y, z = v  # type: ignore[misc]
    return (float(x), float(y), float(z))


def to_nav(p: Sequence[float], up_axis: UpAxis) -> Vec3:
    """A stage point in the nav frame: Z-up turns -90 degrees about X, as the 3d page's
    ``up: "z"`` root does, so (x, y, z) sits at (x, z, -y)."""
    return (p[0], p[2], -p[1]) if up_axis == "Z" else _vec(p)


def to_stage(p: Sequence[float], up_axis: UpAxis) -> Vec3:
    """A nav-frame point in the stage's frame (the inverse of ``to_nav``)."""
    return (p[0], -p[2], p[1]) if up_axis == "Z" else _vec(p)


def _bounds_to_nav(bounds: Bounds, up_axis: UpAxis) -> tuple[Vec3, Vec3]:
    a, b = to_nav(bounds[0], up_axis), to_nav(bounds[1], up_axis)
    return (
        (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2])),
        (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2])),
    )


@dataclass(frozen=True)
class CameraState:
    """What a renderer needs, in the stage's frame: the eye, what it looks at, the up
    direction, the vertical field of view in radians and the clip range."""

    eye: Vec3
    target: Vec3
    up: Vec3
    fov_y: float
    near: float
    far: float


def _forward(yaw: float, pitch: float) -> Vec3:
    """The view direction for a yaw (0 looks down -Z, turning right is positive) and a
    pitch (up is positive), in the nav frame."""
    cp = math.cos(pitch)
    return (cp * math.sin(yaw), math.sin(pitch), -cp * math.cos(yaw))


def _ground(yaw: float) -> Vec3:
    return (math.sin(yaw), 0.0, -math.cos(yaw))


def _right(yaw: float) -> Vec3:
    return (math.cos(yaw), 0.0, math.sin(yaw))


class Nav:
    """One view's camera: a free eye with a yaw and a pitch, driven by the gestures of the
    module docstring, and jumps (first look, World origin, frame all, frame bounds, look
    through, a ``camera`` port value)."""

    def __init__(self, bounds: Bounds | None = None, up_axis: UpAxis = "Y") -> None:
        self.bounds: Bounds | None = None
        self.up_axis: UpAxis = "Y"
        self._nav_bounds: tuple[Vec3, Vec3] | None = None
        self.position: Vec3 = (0.0, 0.0, 0.0)
        self.yaw = self.pitch = 0.0
        self.focus = 20.0  # how far ahead the focus point is
        self.fov_y = DEFAULT_FOV_Y
        self.near = 0.05
        self.far = 5000.0
        self._speed = 1.0
        self._keys: set[str] = set()
        self._buttons: set[Button] = set()
        self._alt = False
        self._pivot: Vec3 | None = None
        self.looking_through: str | None = None  # the stage camera's prim path
        self._through: CameraState | None = None  # its pose, in the stage's frame
        self._taken: CameraState | None = None  # the state take_change last handed out
        self._cut = True  # a jump since then: the renderer drops its history
        self.load(bounds, up_axis)

    def load(self, bounds: Bounds | None, up_axis: UpAxis = "Y") -> None:
        """A stage opened: its bounds (stage frame, None when it has none) and ``upAxis``;
        the camera goes to the first look, which is always the 3d view's origin look."""
        self.bounds = bounds
        self.up_axis = "Z" if str(up_axis).upper() == "Z" else "Y"
        self._nav_bounds = _bounds_to_nav(bounds, self.up_axis) if bounds else None
        self.stop()
        self.world_origin()

    def look_through(
        self,
        path: str | None,
        transform: Sequence[Sequence[float]] | None = None,
        focal_length: float | None = None,
        vertical_aperture: float = USD_VERTICAL_APERTURE,
        clipping_range: Sequence[float] | None = None,
    ) -> None:
        """Look through the stage camera at ``path``: its world ``transform`` (row
        vectors, as ``Gf.Matrix4d``) and lens, sampled at the current time. The source
        sends it every frame, so an animated camera is followed. Any navigation drops
        back to the free camera, which carries on from that pose; None drops it now."""
        if path is None or transform is None:
            self._release()
            return
        eye = _vec(transform[3][:3])
        forward = _scale(_unit(transform[2][:3]), -1)
        target = _add(eye, _scale(forward, self.focus))
        if focal_length:
            self.fov_y = 2 * math.atan(vertical_aperture / (2 * focal_length))
        if clipping_range:
            self.near, self.far = float(clipping_range[0]), float(clipping_range[1])
        # the free camera takes the pose now, so navigating carries on from it
        self._set_pose(to_nav(eye, self.up_axis), to_nav(target, self.up_axis))
        up = _unit(transform[1][:3])
        self._cut = self._cut or path != self.looking_through
        self._through = CameraState(eye, target, up, self.fov_y, self.near, self.far)
        self.looking_through = path

    def _release(self) -> None:
        self.looking_through = None
        self._through = None

    def take_change(self) -> tuple[CameraState, bool] | None:
        """The camera state if it changed since the last call, and whether a jump got it
        there (a cut: the renderer resets temporal history, TAA and DLSS, rather than
        ghosting); None when nothing changed. One call per tick coalesces the input."""
        state, cut = self.state(), self._cut
        self._cut = False
        if state == self._taken:
            return None
        self._taken = state
        return state, cut

    def stop(self) -> None:
        """Drop every held key and button (the view paused or lost the focus)."""
        self._keys.clear()
        self._buttons.clear()
        self._alt = False
        self._pivot = None

    def state(self) -> CameraState:
        """The camera for the renderer, in the stage's frame."""
        if self._through:
            return self._through
        return CameraState(
            eye=to_stage(self.position, self.up_axis),
            target=to_stage(self.target, self.up_axis),
            up=to_stage(WORLD_UP, self.up_axis),
            fov_y=self.fov_y,
            near=self.near,
            far=self.far,
        )

    # -- the pose -------------------------------------------------------------------------------
    @property
    def forward(self) -> Vec3:
        return _forward(self.yaw, self.pitch)

    @property
    def target(self) -> Vec3:
        """The focus point: ``focus`` ahead of the eye."""
        return _add(self.position, _scale(self.forward, self.focus))

    def _set_pose(self, position: Sequence[float], target: Sequence[float]) -> None:
        """The eye at ``position`` looking at ``target`` (nav frame); the pitch is held
        inside the limits."""
        self.position = _vec(position)
        v = _sub(target, position)
        n = _norm(v)
        if n < 1e-12:
            return
        d = _scale(v, 1 / n)
        self.yaw = math.atan2(d[0], -d[2]) if d[0] or d[2] else self.yaw
        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, math.asin(max(-1.0, min(1.0, d[1])))))
        self.focus = max(LOWER_RADIUS_LIMIT, n)

    def _axes(self) -> tuple[Vec3, Vec3, Vec3]:
        """The camera's forward, right and up in the nav frame."""
        forward, right = self.forward, _right(self.yaw)
        return forward, right, _cross(right, forward)

    def scene_radius(self) -> float:
        """Half the bounds' diagonal, the 3d page's ``sceneRadius`` (10 without bounds)."""
        if not self._nav_bounds:
            return DEFAULT_RADIUS
        lo, hi = self._nav_bounds
        return _norm(_sub(hi, lo)) / 2 or DEFAULT_RADIUS

    def _u(self) -> float:
        """The table's ``u``: the reach x camera speed."""
        return self.scene_radius() * self._speed

    def _move(self, by: Vec3) -> None:
        """The eye and the focus point move together."""
        self.position = _add(self.position, by)

    # -- buttons, drags and the wheel ----------------------------------------------------------
    def press(self, button: Button, alt: bool = False) -> None:
        """A mouse button went down over the picture; ``alt``: Alt was held. The first
        button of a gesture decides whether it is an Alt one."""
        if not self._buttons:
            self._alt = bool(alt)
            self._pivot = None
        self._buttons.add(button)

    def release(self, button: Button) -> None:
        """A mouse button came up; the last one ends the gesture (and RMB the flying)."""
        self._buttons.discard(button)
        if not self._buttons:
            self._alt = False
            self._pivot = None

    @property
    def gesture(self) -> Gesture | None:
        """What a drag does with the buttons held now (the module docstring's table)."""
        b = self._buttons
        if not b:
            return None
        if self._alt:
            return "pan" if "middle" in b else "orbit" if "left" in b else "dolly"
        if "middle" in b or {"left", "right"} <= b:
            return "pan"
        return "walk" if "left" in b else "look"

    @property
    def flying(self) -> bool:
        """RMB alone held, no Alt (a look): W A S D Q E fly and the wheel sets the camera
        speed."""
        return self.gesture == "look"

    def set_pivot(self, point: Sequence[float]) -> None:
        """The orbit's pivot (stage frame): the selection's centre or the point picked
        under the cursor at the press. Ignored unless an orbit is on."""
        if self.gesture == "orbit":
            self._pivot = to_nav(point, self.up_axis)

    @property
    def pivot(self) -> Vec3:
        """What Alt+LMB orbits (nav frame)."""
        return self._pivot if self._pivot is not None else self.target

    def drag(self, dx: float, dy: float) -> None:
        """The pointer moved ``dx, dy`` pixels with buttons held."""
        gesture = self.gesture
        if gesture is None or (not dx and not dy):
            return
        self._release()
        if gesture == "walk":
            self.yaw += dx * ROTATE
            self._move(_scale(_ground(self.yaw), -dy * WALK * self._u()))
        elif gesture == "look":
            self._turn(dx * ROTATE, -dy * ROTATE)
        elif gesture == "pan":
            _, right, up = self._axes()
            step = PAN * self._u()
            self._move(_add(_scale(right, dx * step), _scale(up, -dy * step)))
        elif gesture == "orbit":
            # the look's turn, swung around the pivot: where the pivot is on screen stays
            pivot = self.pivot
            forward, right, up = self._axes()
            rel = _sub(pivot, self.position)
            local = (_dot(rel, right), _dot(rel, up), _dot(rel, forward))
            self._turn(dx * ROTATE, -dy * ROTATE)
            forward, right, up = self._axes()
            seen = _add(_scale(right, local[0]), _scale(up, local[1]))
            self.position = _sub(pivot, _add(seen, _scale(forward, local[2])))
        else:  # Alt+RMB: dolly along the view
            self._move(_scale(self.forward, (dx - dy) * DOLLY * self._u()))

    def _turn(self, d_yaw: float, d_pitch: float) -> None:
        self.yaw += d_yaw
        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self.pitch + d_pitch))

    def wheel(self, notches: float) -> None:
        """The wheel turned ``notches`` (+ away from the user, Qt's angleDelta / 120):
        RMB held (a look), the camera speed changes; else the eye and the focus point
        dolly forward or back."""
        if not notches:
            return
        if self.flying:
            self.camera_speed = 2.0 ** (math.log2(self._speed) + notches * SPEED_STEP)
            return
        self._release()
        self._move(_scale(self.forward, notches * WHEEL * self._u()))

    # -- held keys and the tick -----------------------------------------------------------------
    def set_key(self, key: str, down: bool) -> None:
        """A navigation key went down or up: W A S D Q E fly while RMB is held, Shift
        flies faster. Held keys are state: a key down before RMB flies once it is held."""
        k = key.lower()
        if k in FLY_KEYS or k == "shift":
            if down:
                self._keys.add(k)
            else:
                self._keys.discard(k)

    def release_keys(self) -> None:
        """Focus left the view: nothing stays held (keys or buttons)."""
        self.stop()

    @property
    def moving(self) -> bool:
        """True while a tick would move the camera: RMB and a fly key are held."""
        return self.flying and any(k in self._keys for k in FLY_KEYS)

    def tick(self, dt: float) -> bool:
        """One frame of ``dt`` seconds; True when the camera flew."""
        if not self.moving or dt <= 0:
            return False
        self._release()
        step = FLY * self._u() * dt
        if "shift" in self._keys:
            step *= SHIFT
        forward, right, _ = self._axes()
        move = (0.0, 0.0, 0.0)
        for key, axis, sign in (
            ("w", forward, 1), ("s", forward, -1), ("a", right, -1), ("d", right, 1),
            ("q", WORLD_UP, -1), ("e", WORLD_UP, 1),
        ):  # fmt: skip
            if key in self._keys:
                move = _add(move, _scale(axis, sign * step))
        self._move(move)
        return True

    # -- camera speed -------------------------------------------------------------------------
    @property
    def camera_speed(self) -> float:
        """The VIEW panel's speed (view param ``camera_speed``): it scales flying,
        dragging, panning and the wheel; anything not a positive number is 1."""
        return self._speed

    @camera_speed.setter
    def camera_speed(self, value: object) -> None:
        try:
            v = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            v = 0.0
        self._speed = max(2.0**SPEED_MIN, min(2.0**SPEED_MAX, v)) if v > 0 else 1.0

    # -- jumps ----------------------------------------------------------------------------------
    def origin_look(self) -> dict[str, object]:
        """The 3d view's origin look (``scene3d_view.initial_camera``): the world origin
        from ``FIRST_LOOK``, far enough back to hold every corner of the bounds."""
        radius = 0.0
        if self.bounds:
            lo, hi = self.bounds
            for x in (lo[0], hi[0]):
                for y in (lo[1], hi[1]):
                    for z in (lo[2], hi[2]):
                        radius = max(radius, _norm((x, y, z)))
        radius = radius or DEFAULT_RADIUS
        distance = radius / math.sin(DEFAULT_FOV_Y / 2) * 1.05
        n = _norm(FIRST_LOOK)
        return {
            "position": [-c / n * distance for c in FIRST_LOOK],
            "target": [0.0, 0.0, 0.0],
            "up": [0.0, 1.0, 0.0],
            "fov_y": DEFAULT_FOV_Y,
            "near": max(0.001, radius / 5000),
            "far": max(100.0, radius * 50),
        }

    def world_origin(self) -> None:
        """The first look, and the VIEW panel's World origin button."""
        self.apply_camera_value(self.origin_look())

    def frame_all(self) -> None:
        """F with nothing selected, and the footer's target: the 3d page's ``frameBox``
        around the bounds, keeping the viewing direction."""
        self._release()
        if not self._nav_bounds:
            return
        radius = self._frame_box(*self._nav_bounds)
        self.far = max(100.0, radius * 50)
        self.near = max(0.001, radius / 5000)

    def frame_point(self, point: Sequence[float]) -> None:
        """F on a selection with no bounds but a picked point (stage frame): a cube
        ``POINT_FRAME x reach`` across around it, as :meth:`frame_bounds` frames."""
        h = self.scene_radius() * POINT_FRAME / 2
        p = to_nav(point, self.up_axis)
        self._release()
        radius = self._frame_box(_sub(p, (h, h, h)), _add(p, (h, h, h)))
        self.near = max(0.001, radius / 5000)

    def frame_bounds(self, bounds: Bounds) -> None:
        """F on a selection, an outliner double-click (#140): ``frameBox`` around world
        bounds (stage frame), keeping the viewing direction; the far clip stays the
        stage's."""
        self._release()
        radius = self._frame_box(*_bounds_to_nav(bounds, self.up_axis))
        self.near = max(0.001, radius / 5000)
        self.far = max(self.far, radius * 50)

    def _frame_box(self, lo: Vec3, hi: Vec3) -> float:
        """The box's centre ahead at ``radius / sin(fov / 2) x 1.05``, along the viewing
        direction; its radius (half the diagonal)."""
        self._cut = True
        center = _scale(_add(lo, hi), 0.5)
        radius = max(0.01, _norm(_sub(hi, lo)) / 2)
        distance = radius / math.sin(self.fov_y / 2) * 1.05
        self._set_pose(_sub(center, _scale(self.forward, distance)), center)
        return radius

    # -- the camera port ------------------------------------------------------------------------
    def apply_camera_value(self, value: object) -> None:
        """A ``camera`` port value (a ``CameraValue`` or its JSON), as the 3d page's
        ``applyCamera`` takes it: the eye at ``position`` looking at ``target``, then the
        lens if given."""
        self._release()
        v = value.model_dump() if hasattr(value, "model_dump") else dict(value)  # type: ignore[call-overload]
        if not v.get("position") or not v.get("target"):
            return
        self._cut = True
        self._set_pose(_vec(v["position"]), _vec(v["target"]))
        if v.get("fov_y"):
            self.fov_y = float(v["fov_y"])
        if v.get("near"):
            self.near = float(v["near"])
        if v.get("far"):
            self.far = float(v["far"])

    def camera_value(self) -> dict[str, object]:
        """The ``camera`` port value, the 3d view's shape and frame."""
        return {
            "position": list(self.position),
            "target": list(self.target),
            "up": [0.0, 1.0, 0.0],
            "fov_y": self.fov_y,
            "near": self.near,
            "far": self.far,
        }


# -- adapters: the camera as each renderer takes it ---------------------------------------------
Matrix4 = list[list[float]]


def _camera_axes(state: CameraState) -> tuple[Vec3, Vec3, Vec3]:
    """The camera's right, up and back (USD cameras look down -Z with +Y up)."""
    back = _unit(_sub(state.eye, state.target))
    right = _unit(_cross(state.up, back))
    return right, _cross(back, right), back


def usd_camera(
    state: CameraState, aspect: float, vertical_aperture: float = USD_VERTICAL_APERTURE
) -> dict[str, object]:
    """The camera as ``UsdGeomCamera`` values, for ovrtx/ovstage's ``/__specto/Camera``:
    the look-at world transform (row vectors, translation last, as ``Gf.Matrix4d``), the
    focal length that gives ``fov_y`` over ``vertical_aperture``, the horizontal aperture
    for the viewport's ``aspect`` (width / height) and the clipping range."""
    right, up, back = _camera_axes(state)
    focal = vertical_aperture / (2 * math.tan(state.fov_y / 2))
    return {
        "transform": [[*right, 0.0], [*up, 0.0], [*back, 0.0], [*state.eye, 1.0]],
        "focal_length": focal,
        "horizontal_aperture": vertical_aperture * aspect,
        "vertical_aperture": vertical_aperture,
        "clipping_range": (state.near, state.far),
    }


def _quaternion(right: Vec3, up: Vec3, back: Vec3) -> tuple[float, float, float, float]:
    """The unit quaternion (w, x, y, z) of the rotation whose columns are the axes."""
    m = ((right[0], up[0], back[0]), (right[1], up[1], back[1]), (right[2], up[2], back[2]))
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = 2 * math.sqrt(trace + 1)
        q = (s / 4, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = 2 * math.sqrt(1 + m[0][0] - m[1][1] - m[2][2])
        q = ((m[2][1] - m[1][2]) / s, s / 4, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s)
    elif m[1][1] > m[2][2]:
        s = 2 * math.sqrt(1 + m[1][1] - m[0][0] - m[2][2])
        q = ((m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, s / 4, (m[1][2] + m[2][1]) / s)
    else:
        s = 2 * math.sqrt(1 + m[2][2] - m[0][0] - m[1][1])
        q = ((m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, s / 4)
    n = math.sqrt(sum(c * c for c in q))
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def gf_frustum(state: CameraState, aspect: float) -> dict[str, object]:
    """The camera as ``Gf.Frustum`` takes it, for Storm, without importing pxr: position,
    rotation (a ``Gf.Quatd`` as (w, x, y, z)), window on the reference plane at depth 1
    (``SetPerspective``), near/far, projection and view distance; plus the view and
    projection matrices it computes, which ``UsdImagingGLEngine.SetCameraState`` takes
    (row vectors, OpenGL clip space)."""
    right, up, back = _camera_axes(state)
    h = math.tan(state.fov_y / 2)
    n, f = state.near, state.far
    e = state.eye
    view: Matrix4 = [
        [right[0], up[0], back[0], 0.0],
        [right[1], up[1], back[1], 0.0],
        [right[2], up[2], back[2], 0.0],
        [-_dot(e, right), -_dot(e, up), -_dot(e, back), 1.0],
    ]
    projection: Matrix4 = [
        [1 / (aspect * h), 0.0, 0.0, 0.0],
        [0.0, 1 / h, 0.0, 0.0],
        [0.0, 0.0, -(f + n) / (f - n), -1.0],
        [0.0, 0.0, -2 * f * n / (f - n), 0.0],
    ]
    return {
        "position": state.eye,
        "rotation": _quaternion(right, up, back),
        "window": ((-aspect * h, -h), (aspect * h, h)),
        "near_far": (n, f),
        "projection": "perspective",
        "view_distance": _norm(_sub(state.target, state.eye)),
        "view_matrix": view,
        "projection_matrix": projection,
    }
