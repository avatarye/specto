"""The ``video`` view host: libmpv in a Qt Quick item, on ports (ADR 0014; spec #35
stories 41-43). A video is a **player**: when it is the document's ``root_view``, the
global clock follows its time and other views follow it through ``time_map`` links.

One :class:`VideoHost` per live video view. It owns ``Specto/Video/VideoView.qml``: the
native ``MpvItem`` (``Specto.Native``, C++; libmpv's render API into the item's own GL
texture, frames requested from the GUI thread only, spike S1 rule) above the player's
footer (#128): a full-length progress bar, the transport row and the viewer row. The host:

- receives its collection on the ``data`` input port and plays ``params.selected`` (or
  the first video item; PgUp / PgDn and the strip's previous / next step through them,
  ``hosts.stepping``);
- builds a :class:`TimeMap` of the video's frames from the sidecar
  ``<name>.timeline.json`` (frame -> stamp, pts; ``tools/make_testdata.py`` writes one)
  or, without it, from fps and frame count (the video's own clock, timeline ``video``);
  ``params.timeline`` names the clock explicitly;
- emits ``time`` (``TimeValue(t, timeline)``) on its output port while playing, while
  the bar is dragged, and when a frame step, a scrub or a pause lands; emits
  ``selection`` (the video item it shows) whenever that changes: "its time and itself";
- follows a ``time`` received on its input port (nearest frame, exact seek) and emits
  the landed frame's time once (``ports.py`` propagates ``time`` -> ``time``), so the
  global clock and one-way followers see where the player is; the runtime's echo guard
  keeps that from going back over the two-way link it came from;
- takes **agent parameters** from the document (``params``): ``speed`` (0.25 .. 4),
  ``loop`` (``[a, b]`` seconds of pts, or null), ``hwdec`` (hardware decoding, default
  off: ``auto-safe`` copy modes through mpv's ``hwdec`` property), ``selected``,
  ``timeline``, ``muted`` (default true) and ``volume`` (0 .. 1, default 1); there are
  no buttons for the loop or decoding;
- plays **sound** (#216) through the footer's speaker button and volume slider: muted
  by default; at most one video view per window has sound (unmuting one mutes the
  others, :meth:`set_muted`); the speaker and the slider write ``params.muted`` /
  ``params.volume`` as navigation (``LiveCanvas.remember``, no undo step), so a
  reopened project sounds the same and the agent sees and may set them; sound only
  during normal playback: mpv's ``mute`` is on while paused, scrubbing, stepping or
  seeking (:meth:`effective_muted`), so a scrub or a received time never blips;
- feeds the footer: the frame readout ``"12 / 240 @ 32.48 fps"`` (frames from 1, the
  source's own rate, :attr:`source_fps`) as the controller's ``label``, the progress
  bar's hover tip (clip time, SMPTE timecode), the speed button and slider
  (``VideoBridge.speedLabel`` / ``speedFraction`` / ``pickSpeed``, five stops) and the
  speaker and volume (``VideoBridge.muted`` / ``volume`` / ``hasAudio`` / ``toggleMute``
  / ``pickVolume``);
- answers ``snapshot()`` with the item, position, frame, fps, duration, speed, loop,
  hwdec, timeline, time, muted, volume, the audio track and output and the render
  backend; health from mpv (``loading`` / ``ok`` / ``error``);
- ``pause()`` / ``resume()`` for the shell's pause API (off-screen views): playback is
  paused and resumed, the decoder stays.

Interaction map (``VideoView.qml``): click the picture, the footer's play button or
Space: play / pause; Left / Right: one frame; wheel over the picture: frames; Home /
End; press or drag the progress bar: scrub (hover: the tip); ``-`` ``+`` or the speed
button's slider: 0.25x .. 4x (live, not a submit); M or the speaker button: mute /
unmute; press, drag or wheel on the volume slider: volume; PgUp / PgDn or the viewer
row's chevrons: previous / next video item.
"""

from __future__ import annotations

import bisect
import contextlib
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQuick import QQuickItem

from specto_app.hosts.base import HostContext, QuickViewHost
from specto_app.hosts.stepping import Stepping
from specto_core.errors import SpectoError
from specto_core.kinds import Item, PluginManifest, ViewCost, ViewManifest
from specto_core.links import Collection, Selection, TimeValue
from specto_core.timebase import DEFAULT_CLOCK, OWN_TIMELINE, load_sidecar, video_part

from .. import testdrive
from ..timeline.controller import TimelineController
from ..timeline.readout import (
    SPEED_MAX,
    SPEED_MIN,
    SPEEDS,
    clip_time,
    frame_readout,
    speed_at,
    speed_fraction,
    speed_label,
    timecode,
    volume_label,
)
from ..timeline.timemap import TimeMap
from . import gallery_native

VIDEO_QML = Path(__file__).resolve().parent.parent / "qml" / "Specto" / "Video" / "VideoView.qml"
DATA, TIME, SELECTION = "data", "time", "selection"
#: the clock of a video without a sidecar: seconds of pts from its start
PTS_TIMELINE = "video"

VIDEO_MANIFEST = ViewManifest(
    type="video",
    label="Video",
    host="quick",
    doc="mpv video (libmpv render API): play/pause, frame step, scrub, 0.25x-4x speed, "
    "sound (muted by default; one view with sound per window); emits time on its "
    "timeline (sidecar <name>.timeline.json maps frames to stamps) and itself as a "
    "selection; follows a received time. params: speed, loop [a, b] seconds or null, "
    "hwdec, selected (item key), timeline, muted (bool), volume (0..1).",
    accepts=["video_ref"],
    cost=ViewCost(video_decoders=1),
    example={"type": "video", "label": "Cam-a video", "params": {"speed": 1.0, "loop": None}},
)
VIDEO_PLUGIN = PluginManifest(name="app-video", version="0.3.0", views=[VIDEO_MANIFEST])
#: the volume slider's grid: 5 % steps (a press snaps to the nearest, the wheel moves one)
VOLUME_STEPS = 20


class VideoBridge(QObject):
    """Handed to ``VideoView.qml`` as ``bridge``: user intent from the picture and the
    footer (the progress bar talks to the ``TimelineController`` directly), the speed
    the footer's button and slider show, and the sound state its speaker and volume
    slider show (#216)."""

    speedChanged = Signal()
    audioChanged = Signal()

    def __init__(self, host: VideoHost) -> None:
        super().__init__()
        self._host = host

    @Property(bool, notify=audioChanged)
    def muted(self) -> bool:
        return self._host.muted

    @Property(float, notify=audioChanged)
    def volume(self) -> float:
        return self._host.volume

    @Property(bool, notify=audioChanged)
    def hasAudio(self) -> bool:
        return self._host.has_audio

    @Property(str, notify=audioChanged)
    def volumeLabel(self) -> str:
        return volume_label(self._host.volume)

    @Slot()
    def toggleMute(self) -> None:
        self._host.set_muted(not self._host.muted, remember=True)

    @Slot(float, bool)
    def pickVolume(self, fraction: float, final: bool) -> None:
        """The slider pressed or dragged to ``fraction`` (snapped to the 5 % grid);
        ``final`` on release remembers it."""
        snapped = round(float(fraction) * VOLUME_STEPS) / VOLUME_STEPS
        self._host.set_volume(snapped, remember=bool(final))

    @Slot(int)
    def stepVolume(self, delta: int) -> None:
        """The wheel over the slider: one grid step per notch, remembered."""
        steps = round(self._host.volume * VOLUME_STEPS) + int(delta)
        self._host.set_volume(steps / VOLUME_STEPS, remember=True)

    @Property(str, notify=speedChanged)
    def speedLabel(self) -> str:
        return speed_label(self._host.speed)

    @Property(float, notify=speedChanged)
    def speedFraction(self) -> float:
        return speed_fraction(self._host.speed)

    @Property("QVariantList", constant=True)
    def speedStops(self) -> list[dict[str, Any]]:
        """The slider's stops, top (4x) to foot (0.25x)."""
        return [
            {"speed": s, "label": speed_label(s), "fraction": speed_fraction(s)}
            for s in reversed(SPEEDS)
        ]

    @Slot(float)
    def pickSpeed(self, fraction: float) -> None:
        self._host.set_speed(speed_at(float(fraction)))

    @Slot()
    def togglePlay(self) -> None:
        self._host.toggle_play()

    @Slot(int)
    def step(self, delta: int) -> None:
        self._host.step(int(delta))

    @Slot(float)
    def setSpeed(self, speed: float) -> None:
        self._host.set_speed(float(speed))

    @Slot(int)
    def stepSpeed(self, delta: int) -> None:
        self._host.step_speed(int(delta))

    @Slot(int)
    def selectItem(self, delta: int) -> None:
        self._host.step_item(int(delta))


class VideoHost(Stepping, QuickViewHost):
    """One live video view; see the module docstring."""

    def __init__(
        self,
        ctx: HostContext,
        view_id: str,
        manifest: ViewManifest,
        *,
        headless: bool = False,
    ) -> None:
        super().__init__(ctx, view_id, manifest)
        self.collection: Collection | None = None
        self.items: list[Item] = []
        self.keys: list[str] = []
        self.index = -1
        self.path: str | None = None
        self.timemap = TimeMap([])
        self.timeline = PTS_TIMELINE
        self.fps = 0.0
        self.duration = 0.0
        self.frame_count = 0
        self.position = 0.0
        self.playback_paused = True
        self.speed = 1.0
        self.loop: list[float] | None = None
        self.hwdec = False
        self.muted = True
        self.volume = 1.0
        self.has_audio = False
        self.loaded = False
        self._following = False  # the last seek came from a received time
        self._final_pending = False  # a gesture ended: emit once mpv lands
        self._was_playing = False  # for the shell's pause / resume
        self._seek_target: int | None = None  # a seek in flight: steps build on it
        self._params: dict[str, Any] = {}
        self.tl = TimelineController(self)
        self.tl.on_scrub = self.scrub
        self.tl.on_step = self.step
        self.tl.on_toggle_play = self.toggle_play
        self.tl.on_hover = self._on_hover
        self.bridge = VideoBridge(self)
        self.mpv: QQuickItem | None = None
        VideoHost._live.append(self)
        if ctx.qml_engine is not None and not headless:
            self._create_item()
        self._apply_params(initial=True)
        self.reload()

    #: every live video host (all windows): the one-sound-per-window rule looks here
    _live: list[VideoHost] = []

    # -- construction ------------------------------------------------------------------
    def _create_item(self) -> None:
        if not gallery_native.is_built():
            raise SpectoError(
                "native plugin Specto.Native is not built", fix=gallery_native.BUILD_HINT
            )
        gallery_native.ensure_import_path(self.ctx.qml_engine)
        self.item = self.ctx.create_item(
            VIDEO_QML, properties={"viewId": self.view_id, "bridge": self.bridge, "tl": self.tl}
        )
        mpv = self.item.findChild(QQuickItem, "mpv")
        if mpv is None:
            raise SpectoError("VideoView.qml has no child named 'mpv'")
        self.mpv = mpv
        mpv.loaded.connect(self._on_loaded)
        mpv.positionChanged.connect(self._on_position)
        mpv.pausedChanged.connect(self._on_paused)
        mpv.speedChanged.connect(self._on_speed)
        mpv.loopChanged.connect(self._on_loop)
        mpv.errorChanged.connect(self._on_error)
        mpv.playbackRestart.connect(self._on_restart)
        mpv.durationChanged.connect(self._on_meta)
        mpv.fpsChanged.connect(self._on_meta)
        mpv.frameChanged.connect(self._on_meta)
        mpv.hasAudioChanged.connect(self._on_audio_track)
        if testdrive.enabled():
            # a driver run makes no sound (#237): the null audio output; the view's own
            # muted / volume state still works and reports as usual
            mpv.setMpvProperty("ao", "null")
        if not bool(mpv.property("ready")):
            self.set_health("error", str(mpv.property("error") or "libmpv not available"))

    # -- ports -------------------------------------------------------------------------
    def receive(self, port: str, value: Any) -> None:
        if port == DATA:
            self.collection = value if isinstance(value, Collection) else None
            self.reload()
        elif port == TIME and isinstance(value, TimeValue) and not self.tl.scrubbing:
            self._seek_to_value(value)

    # -- items -------------------------------------------------------------------------
    def reload(self) -> None:
        """(Re)read the collection and show the selected (or first) video item."""
        self.items = [
            it for it in (self.collection.items if self.collection else []) if video_part(it)
        ]
        self.keys = [it.key for it in self.items]
        sel = self._params.get("selected")
        i = self.keys.index(str(sel)) if sel is not None and str(sel) in self.keys else -1
        if i < 0 and self.items:
            i = 0
        if not self.items:
            self.set_health("paused", "no videos" if self.collection is not None else "no data")
        self._show(i)

    def _show(self, index: int) -> None:
        self.index = index if 0 <= index < len(self.items) else -1
        self.path = video_part(self.items[self.index]) if self.index >= 0 else None
        self.loaded = False
        self.frame_count = 0
        self.fps = 0.0
        self.duration = 0.0
        self.position = 0.0
        self._following = True  # positions reported while the file opens are not ours
        self._seek_target = None
        self._rebuild_timemap()
        if self.item is not None:
            self.item.setProperty("caption", self.caption())
        self.sync_step()
        if self.mpv is not None:
            self._apply_hwdec_to_mpv()
            self.mpv.setProperty("source", self.path or "")
            if self.path:
                self.mpv.setProperty("speed", self.speed)
                self._apply_loop_to_mpv()
                self._apply_audio_to_mpv()
        if self.index >= 0:
            if self.mpv is not None:
                self.set_health("loading", self.keys[self.index])
            else:
                self.set_health("ok", f"{self.keys[self.index]}: {len(self.timemap)} frames")
            self.emit(SELECTION, Selection(keys=[self.key], last=self.key))

    @property
    def key(self) -> str | None:
        return self.keys[self.index] if 0 <= self.index < len(self.keys) else None

    def caption(self) -> str:
        """The viewer row's name: the file's (the row shows "n / N" beside it)."""
        if self.index < 0:
            return f"{len(self.items)} items" if self.items else ""
        return Path(self.path).name if self.path else ""

    # -- stepping (hosts.stepping): PgUp / PgDn, the strip's buttons ----------------------
    # live: the document keeps the video the agent selected
    def step_keys(self) -> list[str]:
        return self.keys

    def step_key(self) -> str | None:
        return self.key

    def show_step(self, key: str) -> bool:
        self._show(self.keys.index(key))
        return True

    @property
    def emits_selection(self) -> bool:
        return False  # _show emits the video it shows, whatever showed it

    # -- the time map ------------------------------------------------------------------
    def _rebuild_timemap(self) -> None:
        declared = self._params.get("timeline")
        if self.path:
            side = load_sidecar(self.path)
            own = (side or {}).get("timeline") or (DEFAULT_CLOCK if side else PTS_TIMELINE)
            self.timeline = str(declared or own)
            self.timemap = TimeMap.from_video(
                self.path, fps=self.fps, frame_count=self.frame_count, timeline=self.timeline
            )
        else:
            self.timemap = TimeMap([])
        r = self.timemap.range(self.timeline) or (0.0, max(self.duration, 1.0))
        self.tl.set_range(r[0], r[1], self.timeline)
        self.tl.set_ticks(self.timemap.ticks(self.timeline))
        self._sync_bar()

    def frame_at(self, position: float) -> int:
        """Frame index for a pts position (nearest, by the sidecar's pts)."""
        pts = self.timemap.pts
        if not pts:
            return round(position * self.fps) if self.fps > 0 else -1
        i = bisect.bisect_left(pts, position + 1e-6)
        cands = [j for j in (i - 1, i) if 0 <= j < len(pts)]
        return min(cands, key=lambda j: abs(pts[j] - position)) if cands else -1

    def pts_of(self, frame: int) -> float | None:
        pts = self.timemap.pts
        if pts and 0 <= frame < len(pts):
            return pts[frame]
        if self.fps > 0 and frame >= 0:
            return frame / self.fps
        return None

    @property
    def frame(self) -> int:
        return self.frame_at(self.position)

    @property
    def source_fps(self) -> float:
        """The source's own frame rate: mpv's (the container's), else the sidecar's, else
        the frames' pts span."""
        if self.fps > 0:
            return self.fps
        side = load_sidecar(self.path) if self.path else None
        fps = float((side or {}).get("fps") or 0.0)
        pts = self.timemap.pts or []
        if fps <= 0 and len(pts) > 1 and pts[-1] > pts[0]:
            fps = (len(pts) - 1) / (pts[-1] - pts[0])
        return fps

    def time_value(self, frame: int | None = None) -> TimeValue | None:
        f = self.frame if frame is None else frame
        t = self.timemap.time_of(f, self.timeline)
        return None if t is None else TimeValue(t=t, timeline=self.timeline)

    def _emit_time(self, frame: int | None = None) -> None:
        value = self.time_value(frame)
        if value is not None:
            self.emit(TIME, value)

    def _sync_bar(self) -> None:
        f = self.frame
        t = self.timemap.time_of(f, self.timeline)
        if t is None:
            t = self.position
        count = len(self.timemap) or self.frame_count
        self.tl.set_t(t, f, count)
        self.tl.set_label(frame_readout(f, count, self.source_fps) if self.path else "")
        self.tl.set_playing(not self.playback_paused)
        a, b = (-1.0, -1.0) if not self.loop else (self.loop[0], self.loop[1])
        self.tl.set_loop(a, b)

    def _on_hover(self, t: float | None) -> None:
        """The bar's hover tip: the frame under the pointer as clip time and timecode."""
        f = -1 if t is None else self.timemap.index_at(t, self.timeline)
        pts = self.pts_of(f) if f >= 0 else None
        if pts is None:
            self.tl.set_hover_tip("", "")
        else:
            self.tl.set_hover_tip(clip_time(pts), timecode(pts, self.source_fps))

    # -- mpv signals (GUI thread) ------------------------------------------------------
    @Slot()
    def _on_loaded(self) -> None:
        if self.mpv is None:
            return
        self.loaded = True
        self._on_meta()
        self.set_health(
            "ok",
            f"{self.mpv.property('videoWidth')}x{self.mpv.property('videoHeight')} "
            f"{self.fps:.3f} fps, {self.frame_count} frames, {self.duration:.3f} s",
        )
        # a view that opens while a time already sits on its port lands on it
        value = self.ctx.links.input_of(self.view_id, TIME)
        if isinstance(value, TimeValue):
            self._seek_to_value(value)
        else:
            self._following = False

    @Slot()
    def _on_meta(self) -> None:
        if self.mpv is None:
            return
        fps = float(self.mpv.property("fps") or 0.0)
        dur = float(self.mpv.property("duration") or 0.0)
        n = int(self.mpv.property("frameCount") or 0)
        if (fps, dur, n) != (self.fps, self.duration, self.frame_count):
            self.fps, self.duration, self.frame_count = fps, dur, n
            if not self.timemap.pts or len(self.timemap) == 0:
                self._rebuild_timemap()
            else:
                self._sync_bar()

    @Slot()
    def _on_position(self) -> None:
        if self.mpv is None:
            return
        self.position = float(self.mpv.property("position") or 0.0)
        self._sync_bar()
        if self._final_pending and self.playback_paused:
            self._final_pending = False
            self._following = False
            self._emit_time()
        elif self._following:
            if self._seek_target is None:  # the landing report of a followed seek
                self._following = False
                self._emit_time()
        else:
            self._emit_time()

    @Slot()
    def _on_paused(self) -> None:
        if self.mpv is None:
            return
        self.playback_paused = bool(self.mpv.property("paused"))
        self.tl.set_playing(not self.playback_paused)
        self._apply_audio_to_mpv()
        if self.playback_paused:
            self._final_pending = True  # the next position report is the resting frame
            if not self._following and self.time_value() is not None:
                self._final_pending = False
                self._emit_time()
        else:
            self._following = False

    @Slot()
    def _on_restart(self) -> None:
        """A seek or step landed: the frame shown is exact. ``_following`` stays set
        until the position report of that landing is consumed, so the frame a received
        time lands on is emitted once, exactly, not every intermediate report."""
        self._seek_target = None
        if self._final_pending and self.playback_paused:
            self._on_position()
        self._apply_audio_to_mpv()

    @Slot()
    def _on_audio_track(self) -> None:
        if self.mpv is not None:
            self.has_audio = bool(self.mpv.property("hasAudio"))
            self.bridge.audioChanged.emit()

    @Slot()
    def _on_speed(self) -> None:
        if self.mpv is not None:
            speed = float(self.mpv.property("speed") or 1.0)
            if speed != self.speed:
                self.speed = speed
                self.bridge.speedChanged.emit()

    @Slot()
    def _on_loop(self) -> None:
        if self.mpv is None:
            return
        a, b = float(self.mpv.property("loopA")), float(self.mpv.property("loopB"))
        self.tl.set_loop(a, b)

    @Slot()
    def _on_error(self) -> None:
        if self.mpv is None:
            return
        err = str(self.mpv.property("error") or "")
        if err:
            self.set_health("error", err)

    # -- user intent -------------------------------------------------------------------
    def toggle_play(self) -> None:
        if self.mpv is None:
            self.playback_paused = not self.playback_paused
            self.tl.set_playing(not self.playback_paused)
            if self.playback_paused:
                self._emit_time()
            return
        if self.playback_paused and self.loaded and self.mpv.property("eof"):
            self.seek_frame(0)
        self.mpv.togglePause()

    def step(self, delta: int) -> int:
        """One frame forward / back (clamped); pauses; emits the time once it lands."""
        base = self._seek_target if self._seek_target is not None else self.frame
        target = max(0, min(max(0, len(self.timemap) - 1), base + delta))
        self.seek_frame(target, final=True)
        return target

    def seek_frame(self, frame: int, *, final: bool = False, following: bool = False) -> None:
        pts = self.pts_of(frame)
        if pts is None:
            return
        self._following = following
        self._final_pending = final and not following
        if self.mpv is not None:
            self._seek_target = frame
            self._apply_audio_to_mpv()  # queued ahead of the seek: silent through it
            if not self.playback_paused:
                self.mpv.pause()
            self.mpv.seek(pts, True, False)
        else:  # headless: land immediately
            self.position = pts
            self.playback_paused = True
            self._sync_bar()
            if final or following:
                self._emit_time(frame)

    def scrub(self, t: float, final: bool) -> None:
        """The bar was dragged to ``t`` on the video's timeline."""
        f = self.timemap.index_at(t, self.timeline)
        if f < 0:
            return
        if final:
            self.seek_frame(f, final=True)
        else:
            self._following = True  # the position reports until release are the drag's
            self.seek_frame(f)
            self._emit_time(f)
        value = self.time_value(f)
        if value is not None:
            self.tl.set_t(value.t, f, len(self.timemap))

    def set_speed(self, speed: float) -> None:
        speed = max(SPEED_MIN, min(SPEED_MAX, float(speed)))
        changed = speed != self.speed
        self.speed = speed
        if self.mpv is not None:
            self.mpv.setProperty("speed", self.speed)
        if changed:
            self.bridge.speedChanged.emit()

    def step_speed(self, delta: int) -> None:
        i = min(range(len(SPEEDS)), key=lambda k: abs(SPEEDS[k] - self.speed))
        self.set_speed(SPEEDS[max(0, min(len(SPEEDS) - 1, i + delta))])

    # -- sound (#216) --------------------------------------------------------------------
    def set_muted(self, muted: bool, *, remember: bool = False) -> None:
        """Mute or unmute this view. Unmuting mutes every other video view of the same
        window (one view with sound at a time), each remembering that as navigation.
        ``remember`` writes this view's own change too (the user's gesture; an agent's
        document already says what it says)."""
        muted = bool(muted)
        if not muted:
            for other in list(VideoHost._live):
                if other is not self and other.ctx is self.ctx and not other.muted:
                    other.set_muted(True, remember=True)
        changed = muted != self.muted
        self.muted = muted
        self._apply_audio_to_mpv()
        if changed:
            self.bridge.audioChanged.emit()
        if remember and changed:
            self.ctx.live.remember(self.view_id, {"muted": muted})

    def set_volume(self, volume: float, *, remember: bool = False) -> None:
        """Volume as a fraction of full (0 .. 1); mpv's 0 .. 100 underneath."""
        volume = max(0.0, min(1.0, float(volume)))
        changed = volume != self.volume
        self.volume = volume
        self._apply_audio_to_mpv()
        if changed:
            self.bridge.audioChanged.emit()
        if remember:
            self.ctx.live.remember(self.view_id, {"volume": round(volume, 3)})

    def effective_muted(self) -> bool:
        """What mpv's ``mute`` is set to: the view's own mute, or any state that is not
        normal playback (paused, scrubbing, a seek or step in flight), so sound plays
        only while the video runs on its own."""
        return (
            self.muted or self.playback_paused or self.tl.scrubbing or self._seek_target is not None
        )

    def _apply_audio_to_mpv(self) -> None:
        if self.mpv is None:
            return
        self.mpv.setProperty("muted", self.effective_muted())
        self.mpv.setProperty("volume", self.volume * 100.0)

    # -- agent parameters (the document) -------------------------------------------------
    def on_patch(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        prefix = f"/views/{self.view_id}"
        if any(
            op.get("path", "").startswith(f"{prefix}/params") or op.get("path") == prefix
            for op in patch
        ):
            self._apply_params()

    def _apply_params(self, *, initial: bool = False) -> None:
        try:
            p = dict(self.params)
        except SpectoError:
            return
        before, self._params = self._params, p
        speed = p.get("speed")
        if isinstance(speed, int | float) and not isinstance(speed, bool):
            self.set_speed(float(speed))
        loop = p.get("loop")
        want = (
            [float(loop[0]), float(loop[1])]
            if isinstance(loop, list | tuple) and len(loop) == 2
            else None
        )
        if want != self.loop:
            self.loop = want
            self._apply_loop_to_mpv()
            self._sync_bar()
        hw = bool(p.get("hwdec", False))
        if hw != self.hwdec:
            self.hwdec = hw
            self._apply_hwdec_to_mpv()
        vol = p.get("volume")
        if isinstance(vol, int | float) and not isinstance(vol, bool):
            self.set_volume(float(vol))
        muted = p.get("muted", True)
        if isinstance(muted, bool) and muted != self.muted:
            self.set_muted(muted)
        if initial:
            return
        sel = p.get("selected")
        if sel != before.get("selected") and sel is not None and str(sel) in self.keys:
            self._show(self.keys.index(str(sel)))
        elif p.get("timeline") != before.get("timeline"):
            self._rebuild_timemap()

    def _apply_loop_to_mpv(self) -> None:
        if self.mpv is None:
            return
        a, b = (-1.0, -1.0) if not self.loop else (self.loop[0], self.loop[1])
        self.mpv.setProperty("loopA", a)
        self.mpv.setProperty("loopB", b)

    def _apply_hwdec_to_mpv(self) -> None:
        if self.mpv is not None:
            self.mpv.setMpvProperty("hwdec", "auto-safe" if self.hwdec else "no")

    # -- shell pause API -----------------------------------------------------------------
    def pause(self) -> None:
        """Off screen: stop decoding work (playback); the decoder stays."""
        self._was_playing = not self.playback_paused
        if self.mpv is not None and not self.playback_paused:
            self.mpv.pause()
        super().pause()

    def resume(self) -> None:
        super().resume()
        if self._was_playing and self.mpv is not None:
            self.mpv.play()
        self._was_playing = False

    # -- following a time ------------------------------------------------------------------
    def _seek_to_value(self, value: TimeValue) -> None:
        if value.timeline not in (self.timeline, OWN_TIMELINE):
            return  # not this video's clock: a time_map link is missing or wrong
        f = self.timemap.index_at(value.t, self.timeline)
        if f < 0:
            return
        if f != self.frame or not self.loaded:
            self.seek_frame(f, following=True)
        self.tl.set_t(value.t, f, len(self.timemap))

    def snapshot(self) -> dict[str, Any]:
        mpv = self.mpv
        value = self.time_value()
        return {
            **super().snapshot(),
            "count": len(self.items),
            "selected": {"index": self.index, "key": self.key},
            "path": self.path,
            "position": self.position,
            "frame": self.frame,
            "frame_count": len(self.timemap) or self.frame_count,
            "fps": self.fps,
            "duration": self.duration,
            "paused": self.playback_paused,
            "speed": self.speed,
            "loop": self.loop,
            "hwdec": self.hwdec,
            "hwdec_current": str(mpv.mpvProperty("hwdec-current") or "") if mpv is not None else "",
            "muted": self.muted,
            "volume": self.volume,
            "has_audio": self.has_audio,
            # mpv's mute right now: off only during normal playback with sound
            "mpv_mute": bool(mpv.mpvProperty("mute")) if mpv else self.effective_muted(),
            "audio_output": str(mpv.mpvProperty("current-ao") or "") if mpv is not None else "",
            "timeline": self.timeline,
            "time": value.model_dump() if value is not None else None,
            "backend": str(mpv.property("backend")) if mpv is not None else "none",
            "render_frames": int(mpv.property("renderFrames")) if mpv is not None else 0,
            "mpv": str(mpv.property("mpvVersion")) if mpv is not None else "",
            "error": str(mpv.property("error") or "") if mpv is not None else "",
        }

    def dispose(self) -> None:
        with contextlib.suppress(ValueError):
            VideoHost._live.remove(self)
        if self.mpv is not None:
            with contextlib.suppress(RuntimeError):
                self.mpv.stop()
            self.mpv = None
        super().dispose()


class VideoFactory:
    type = "video"
    plugin = VIDEO_PLUGIN

    def create(self, ctx: HostContext, view_id: str, manifest: ViewManifest) -> VideoHost:
        return VideoHost(ctx, view_id, manifest)


def factory() -> VideoFactory:
    return VideoFactory()


def register(registry: Any) -> None:
    """Entry point / ``--host-module`` hook: ``registry.add_factory(VideoFactory())``."""
    registry.add_factory(factory())


__all__ = [
    "DATA",
    "PTS_TIMELINE",
    "SELECTION",
    "SPEEDS",
    "TIME",
    "VIDEO_MANIFEST",
    "VIDEO_PLUGIN",
    "VIDEO_QML",
    "VOLUME_STEPS",
    "VideoBridge",
    "VideoFactory",
    "VideoHost",
    "factory",
    "register",
]
