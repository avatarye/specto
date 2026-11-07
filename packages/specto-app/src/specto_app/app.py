"""``SpectoApp``: one project, one canvas, one OS window (ADR 0012).

Wires the pieces on the GUI thread:

- ``Engine`` with the readers' ``PoolJobManager`` (N = cores - 2, spawn) and this
  module's ``AppHost`` (the ``snap`` / ``shell.locate`` provider); ``engine.live`` is
  the project's canvas document and ``engine.links`` the link runtime resolving its
  links as jobs on the pool (results land on the GUI thread from the 8 ms pool poll)
- the Qt Quick shell (``qml/Specto/Shell/Main.qml``) with the canvas controller, the
  view-host registry and the notices as context properties
- the ``QLocalServer`` endpoint (writes ``.specto/endpoint.json`` so the CLI, the SDK and
  MCP find this process)
- the prompt bar (``prompt.PromptBar``): the window's one agent session (#44) on a
  qasync loop, which ``run()`` makes the Qt event loop

The GUI reacts only to document patches; every gesture is a submit with origin
``user``. ``SpectoApp`` is also what the tests drive offscreen.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import os
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Q_ARG,
    Q_RETURN_ARG,
    Property,
    QCoreApplication,
    QEvent,
    QMetaObject,
    QObject,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow

from specto_core import CallContext, Engine, Project
from specto_core.errors import SpectoError
from specto_core.gestures import set_window_frame
from specto_core.instances import Instance, instance_name, register, unregister
from specto_core.jobs import InlineJobManager, JobManager

from . import clipboard, recents, testdrive
from . import commands as _commands  # noqa: F401  (registers snap, shell.locate)
from .branding import APP_ARGV0, install_app_icon
from .canvas.controller import PAD, CanvasController
from .canvas.overworld import OverworldController
from .canvas.snapshots import PROVIDER_ID as SNAPS_PROVIDER
from .canvas.snapshots import SnapshotProvider, SnapshotStore, capture
from .canvas.tile_floor import TileFloor
from .canvas.walk_stage import WalkStage, os_reduced_motion
from .endpoint import LocalEndpoint
from .hosts import HostContext, HostRegistry
from .hosts.base import SHELL_QML
from .launchers.book import book_for
from .launchers.watch import LauncherWatch
from .menu.shell import ContextMenus
from .notices import Notices
from .prompt.bar import PromptBar, SessionFactory
from .qt_setup import install_image_provider, install_qml, install_scheme_handler, prepare
from .snap import CANVAS
from .view_settings import ViewSettings
from .walk.controller import WalkController

MAIN_QML = SHELL_QML / "Main.qml"
#: how long closing the window waits for the agent session to end (its own close is
#: bounded shorter: past it the CLI is killed)
AGENT_CLOSE_S = 5.0


def find_item(root: QQuickItem | None, object_name: str) -> QQuickItem | None:
    """Depth-first search of the visual item tree by ``objectName``. A web view's own
    children (Chromium's render delegate) are never searched: on Linux, wrapping them
    for Python segfaults in shiboken's type lookup."""
    if root is None:
        return None
    stack = [root]
    while stack:
        item = stack.pop()
        if item.objectName() == object_name:
            return item
        if not item.metaObject().className().startswith("QQuickWebEngine"):
            stack.extend(item.childItems())
    return None


class ShellSnapProvider:
    """What ``snap`` and ``shell.locate`` ask the shell for (``specto_app.snap``)."""

    def __init__(self, app: SpectoApp) -> None:
        self.app = app

    def _find(self, name: str) -> QQuickItem | None:
        # Loader/Repeater-made items have no QObject parent chain: walk the item tree
        win = self.app.window
        return find_item(win.contentItem(), name) if win is not None else None

    def _item(self, oid: str) -> QQuickItem | None:
        if self.app.window is None:
            return None
        if oid == CANVAS:
            return self._find("canvasArea")
        if self.app.hosts is not None:
            slot = self.app.hosts.slot_of(oid)
            if slot is not None:
                return slot
        return self._find(f"slot:{oid}")

    def rect_of(self, oid: str) -> list[float] | None:
        item = self._item(oid)
        if item is None:
            return None
        r = item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))
        return [round(r.x(), 1), round(r.y(), 1), round(r.width(), 1), round(r.height(), 1)]

    def host_snapshot(self, view_id: str) -> dict[str, Any]:
        return self.app.hosts.snapshot(view_id) if self.app.hosts else {}

    def health_of(self, view_id: str) -> dict[str, str]:
        return self.app.hosts.ctx.health_of(view_id) if self.app.hosts else {"status": "ok"}

    def locate(self, name: str) -> dict[str, Any] | None:
        """``shell.locate``: the window rect of any item by ``objectName`` (popups live in
        the overlay, which is inside the content item too), plus its screen rect, the part
        of it its clipping ancestors leave in view (``shown``, window px; null when none: a
        row scrolled out of a list, #167) and, for a text item, its text."""
        win = self.app.window
        if win is None:
            return None
        item = self._find(name)
        if item is None:
            return self._locate_in_host(name)
        return locate_item(win, item)

    def _locate_in_host(self, name: str) -> dict[str, Any] | None:
        """``tile:<view>:<key>``: a gallery tile, which the native item draws and which is
        no QML item of its own; the host reports its cell rect."""
        kind, _, rest = name.partition(":")
        view_id, _, key = rest.partition(":")
        host = self.app.hosts.hosts.get(view_id) if self.app.hosts and view_id else None
        tile = getattr(host, "tile_rect", None) if kind == "tile" else None
        r = tile(key) if tile is not None else None
        if r is None:
            return None
        g = self.app.window.mapToGlobal(r.topLeft().toPoint())
        rect = [round(r.x(), 1), round(r.y(), 1), round(r.width(), 1), round(r.height(), 1)]
        return {
            "visible": True,
            "rect": rect,
            "screen": [g.x(), g.y(), round(r.width(), 1), round(r.height(), 1)],
            "shown": rect,
        }

    def raise_window(self) -> bool:
        """``window.raise``: show the window on top and ask for focus (``specto open`` on
        a project that is open already, #215). Unminimises first; the system may only
        flash the taskbar entry when another app holds the foreground. False headless."""
        win = self.app.window
        if win is None:
            return False
        if testdrive.enabled():
            # a driver run takes no attention (#237): the window is shown, never raised
            # or activated
            print("[OK] window.raise under the test driver: not raised")
            return True
        if win.windowStates() & Qt.WindowState.WindowMinimized:
            win.showNormal()
        else:
            win.show()
        win.raise_()
        win.requestActivate()
        return True

    def grab(self, oid: str, path: Path) -> dict[str, Any]:
        win = self.app.window
        if win is None:
            return {"error": "no window"}
        image = win.grabWindow()
        if image.isNull():
            return {"error": "window grab produced no image (no render surface?)"}
        rect = self.rect_of(oid)
        if rect is not None and oid != CANVAS:
            dpr = image.devicePixelRatio() or win.devicePixelRatio() or 1.0
            x, y, w, h = (round(v * dpr) for v in rect)
            image = image.copy(QRect(x, y, max(1, w), max(1, h)))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(path), "PNG"):
            return {"error": f"cannot write {path}"}
        return {"path": str(path), "size": [image.width(), image.height()]}


class AppHost:
    """``specto_core.hosts.AppHost`` for the running app (``shell`` is the snap provider;
    ``approvals`` the prompt bar, which shows ``link.approve`` requests as a card, #203)."""

    def __init__(self, app: SpectoApp) -> None:
        self.app = app
        self.shell = ShellSnapProvider(app)

    @property
    def approvals(self) -> Any:
        return self.app.prompt_bar  # None before the shell is up: no one to ask


def make_jobs(kind: str, *, workers: int | None = None) -> JobManager:
    """``pool`` (contracts 11: spawn workers, N = cores - 2) or ``inline`` (tests)."""
    if kind == "inline":
        return InlineJobManager()
    from specto_readers.pool import PoolJobManager

    return PoolJobManager(workers=workers)


class SpectoApp(QObject):
    """The running app. ``run()`` enters the event loop; tests call ``start()`` and spin
    the loop themselves."""

    windowReady = Signal()
    statusChanged = Signal()
    openProjectRequested = Signal()  # QML shows the native folder dialog (#217: open or make)

    def __init__(
        self,
        project: Project,
        *,
        jobs: str | JobManager = "pool",
        workers: int | None = None,
        web: bool | None = None,
        host_modules: tuple[str, ...] = (),
        offscreen: bool = False,
        serve: bool = True,
        argv: list[str] | None = None,
        launch_flags: list[str] | None = None,
        remember: bool = True,
        agent: str | SessionFactory = "claude",
        loop: asyncio.AbstractEventLoop | None = None,
        watch_interval_s: float | None = None,
        test_driver: bool = False,
    ) -> None:
        self.web_enabled = prepare(web=web, offscreen=offscreen)
        self.qapp = QGuiApplication.instance() or QGuiApplication(argv or [APP_ARGV0])
        install_app_icon(self.qapp)
        if test_driver:
            testdrive.enable()  # ``--test-driver`` (#237); never on without the flag
        testdrive.start()
        super().__init__()
        self.project = project
        self.launch_flags = list(launch_flags or [])
        #: ``claude`` (Claude Code over the Agent SDK), ``scripted:<script.json>`` (a
        #: scripted agent, GUI flows) or a session factory (tests)
        self.agent = agent
        self.loop = loop  # the qasync loop; ``run()`` makes one when none was given
        self.prompt_bar: PromptBar | None = None
        self.walk_stage: WalkStage | None = None
        self.tile_floor: TileFloor | None = None
        self._closing_bar: PromptBar | None = None  # its session ends after the loop
        if remember:
            recents.remember(project.root, project.name)
        #: this window's instance name (#187): the address fleet agents message, stable
        #: per project on this machine; registered while the endpoint serves
        self.instance_name = instance_name(project.root)
        self._registered = False
        self.host = AppHost(self)
        self.jobs: JobManager = (
            jobs if isinstance(jobs, JobManager) else make_jobs(jobs, workers=workers)
        )
        watch_kw = {} if watch_interval_s is None else {"watch_interval_s": watch_interval_s}
        self.engine = Engine(project=project, jobs=self.jobs, host=self.host, **watch_kw)
        self.jobs.events = self.engine.app_events
        self.serve = serve
        self.host_modules = host_modules
        self.notices = Notices(self)
        self.endpoint: LocalEndpoint | None = None
        self.qml: QQmlApplicationEngine | None = None
        self.window: QQuickWindow | None = None
        self.hosts: HostRegistry | None = None
        self.context_menus: ContextMenus | None = None
        #: the launchers' file watch (#186): a saved launchers.toml reloads, its problems
        #: show as notices
        self.launcher_watch: LauncherWatch | None = None
        self.controller: CanvasController | None = None
        self.overworld: OverworldController | None = None
        #: the view-chain walk-through (#174), QML's ``walkthrough``
        self.walkthrough: WalkController | None = None
        self.snapshots = SnapshotStore()
        self._snap_provider: SnapshotProvider | None = None
        self._theme: Any = None
        self._scheme_handler: Any = None
        self._pool_timer = QTimer(self)
        self._pool_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._pool_timer.setInterval(8)
        # a window resize writes the window-frame guide back once it settles
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.setInterval(350)
        self._frame_timer.timeout.connect(self._write_window_frame)
        self._closed = False
        self._off_canvas: Callable[[], None] = lambda: None
        self._open_canvas()

    # -- canvas --------------------------------------------------------------------------
    def _open_canvas(self) -> None:
        live = self.engine.live  # opens .specto/canvas.json and restores the undo history
        n = len(live.data["views"])
        print(f"[OK] opened canvas of {self.project.root} ({n} view{'s' if n != 1 else ''})")
        if live.history_note:
            self.notices.warn(live.history_note, fix="the document itself is intact")
        elif live.history:
            print(f"[OK] undo history restored: {live.cursor}/{len(live.history)}")
        # a save the disk refused (a sync client holding canvas.json): the change is kept
        # in memory and the canvas warns once per streak; the user sees it here
        self._off_canvas = live.events.subscribe(self._on_canvas_event)
        self.engine.links  # the link runtime starts resolving the document's links  # noqa: B018

    def _on_canvas_event(self, ev: Any) -> None:
        if ev.kind != "canvas.save_failed":
            return
        p = ev.payload
        self.notices.warn(
            f"{p.get('what', 'the canvas')} was not saved to {p.get('path', '')}; "
            "your changes are kept in memory and saved with the next one",
            fix=p.get("fix"),
        )

    @property
    def live(self):
        return self.engine.live

    def _install_views(self) -> None:
        """The built-in view hosts (the web ones need QtWebEngine; without it their
        views show a card saying so)."""
        try:
            from .views import install as install_views
        except ImportError as e:
            self.notices.error(f"built-in views unavailable: {e}")
            return
        install_views()

    # -- lifecycle ----------------------------------------------------------------------
    def start(self) -> SpectoApp:
        """Load the shell, start the endpoint and the job pump. Returns self."""
        self.qml = QQmlApplicationEngine()
        self._theme = install_qml(self.qml)
        if self.web_enabled:
            self._scheme_handler = install_scheme_handler()
        self._install_views()
        ctx = HostContext(self.engine, self.qml, notices=self.notices)
        self.hosts = HostRegistry(ctx)
        for mod in self.host_modules:
            try:
                self.hosts.load_module(mod)
            except Exception as e:
                self.notices.error(f"host module {mod}: {type(e).__name__}: {e}")
        self.controller = CanvasController(self.engine, hosts=self.hosts, notices=self.notices)
        # the one context menu (#154): views open it through their host context
        self.context_menus = ContextMenus(
            self.hosts, focused=self._focused_view, notices=self.notices, parent=self
        )
        ctx.context_menus = self.context_menus
        self.launcher_watch = LauncherWatch(book_for(self.project.root), self.notices, parent=self)
        self.overworld = OverworldController(
            self.controller, self.snapshots, capture=self.capture_views
        )
        self._snap_provider = SnapshotProvider(self.snapshots)
        install_image_provider(self.qml, SNAPS_PROVIDER, self._snap_provider)
        root = self.qml.rootContext()
        root.setContextProperty("app", self)
        root.setContextProperty("controller", self.controller)
        root.setContextProperty("overworld", self.overworld)
        root.setContextProperty("hosts", self.hosts)
        root.setContextProperty("contextMenus", self.context_menus)
        root.setContextProperty("notices", self.notices)
        self.view_settings = ViewSettings(parent=self)
        root.setContextProperty("viewSettings", self.view_settings)
        self.walkthrough = WalkController(
            lambda: (self.engine.live.data, self.engine.links), parent=self
        )
        root.setContextProperty("walkthrough", self.walkthrough)
        self.prompt_bar = PromptBar(self, self._session_factory(), parent=self)
        root.setContextProperty("promptBar", self.prompt_bar)
        # the walk-through on stage (#174): follows the walk controller, moves the camera
        self.walk_stage = WalkStage(
            self.controller,
            self.overworld,
            motion=not os_reduced_motion(),
            panel=self.prompt_bar.panel,
            parent=self,
        )
        self.walk_stage.attach(self.walkthrough)
        root.setContextProperty("walkStage", self.walk_stage)
        # the canvas tile floor (#191): the agent's status, new views and the selection
        self.tile_floor = TileFloor(
            self.controller,
            prompt_bar=self.prompt_bar,
            overworld=self.overworld,
            theme=self._theme,
            reduced_motion=os_reduced_motion(),
            parent=self,
        )
        root.setContextProperty("tileFloor", self.tile_floor)
        # the test driver's windows never take the focus off the offscreen platform (#237)
        flags = int(testdrive.window_flags(Qt.WindowType.Window))
        root.setContextProperty("shellWindowFlags", flags)
        self.qml.load(QUrl.fromLocalFile(str(MAIN_QML)))
        if not self.qml.rootObjects():
            raise SpectoError(
                f"the shell failed to load: {MAIN_QML}", fix="see the QML errors above"
            )
        self.window = self.qml.rootObjects()[0]
        self.window.installEventFilter(self.controller)  # Space held: drags pan
        self.window.installEventFilter(self.context_menus)  # the Menu key, Shift+F10
        self.window.installEventFilter(self.walk_stage)  # a walk's keys, ahead of the canvas's
        self.context_menus.keep_f10()  # Windows: F10 enters no menu mode (#165)
        parking = self.window.findChild(QQuickItem, "parking")
        self.hosts.set_parking(parking)
        self._fit_window()
        self.controller.patched.connect(self._on_patched)
        # worker results land on the GUI thread from here (contracts 11); 8 ms so a
        # decoded thumbnail or a resolved link never waits a whole frame. The same timer
        # pumps the link runtime's watchers (arrivals in watched folders, ADR 0016).
        self._pool_timer.timeout.connect(self._pump)
        self._pool_timer.start()
        if self.serve:
            self.endpoint = LocalEndpoint(self.engine, project=self.project, parent=self)
            info = self.endpoint.start()
            print(f"[OK] endpoint {info.kind} {info.address} (pid {info.pid})")
            self._register_instance(info.model_dump())
        self.controller.rebuild()
        self.statusChanged.emit()
        self.windowReady.emit()
        if testdrive.enabled():
            testdrive.attach(self)
        return self

    def _register_instance(self, endpoint: dict[str, Any]) -> None:
        """The registry entry other tools find this window by (``specto instances``)."""
        inst = Instance(
            name=self.instance_name,
            project=str(self.project.root),
            project_name=self.project.name,
            pid=os.getpid(),
            endpoint=endpoint,
        )
        try:
            register(inst)
            self._registered = True
            print(f"[OK] instance {self.instance_name}")
        except OSError as e:
            print(f"[WARN] instance {self.instance_name} not registered: {e}")

    def _session_factory(self) -> SessionFactory:
        """How the prompt bar makes the window's agent session (``agent``)."""
        if callable(self.agent):
            return self.agent
        spec = str(self.agent)
        if spec.startswith("scripted:"):
            from .prompt.scripted import ScriptedAgentSession

            path = Path(spec.partition(":")[2]).expanduser().resolve()
            return lambda tools: ScriptedAgentSession.from_file(path, tools)
        if spec != "claude":
            raise SpectoError(
                f"unknown agent {spec!r}", fix="--agent claude or --agent scripted:<script.json>"
            )
        from .prompt.reference import reference_text
        from .prompt.session import ClaudeAgentSession

        def make(tools: Any) -> ClaudeAgentSession:
            # the standing reference (view types, link kinds) from this app's engine
            ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
            ref = reference_text(lambda name, args: self.engine.call(name, args, ctx))
            return ClaudeAgentSession(tools, self.project.root, reference=ref)

        # the agent icon shows the error from the start when no session can run (#162)
        if importlib.util.find_spec("claude_agent_sdk") is None:
            make.unavailable = "the Claude Agent SDK is not installed"  # type: ignore[attr-defined]
        return make

    def run(self) -> int:
        """Enter the event loop: a qasync loop over the Qt one, so the agent session's
        asyncio code shares the GUI thread (research #30)."""
        import qasync

        if self.window is None:
            self.start()
        if self.loop is None:
            self.loop = qasync.QEventLoop(self.qapp)
        asyncio.set_event_loop(self.loop)
        self.qapp.aboutToQuit.connect(self.close)
        try:
            with self.loop:
                try:
                    return int(self.loop.run_forever())
                finally:
                    self.close_agent()  # while the loop is still open
        finally:
            self.close()

    def _mute_about_to_quit(self) -> None:
        """Disconnect every receiver of ``aboutToQuit`` once the main loop returned: it
        fired then, and the nested loop that closes the agent session emits it again
        when it exits (Qt 6), reaching receivers the first one tore down (QtWebEngine's;
        a segfault on Linux)."""
        with warnings.catch_warnings(), contextlib.suppress(RuntimeError, TypeError):
            warnings.simplefilter("ignore", RuntimeWarning)
            self.qapp.aboutToQuit.disconnect()

    def close_agent(self) -> None:
        """Disconnect the agent session (its CLI subprocess) once the Qt loop returned:
        ``close()`` ran on aboutToQuit with the loop still running and handed the prompt
        bar over here. A CLI left running keeps pipe reads pending on the proactor, whose
        close then waits for them forever: the window is gone, the process stays (#118).
        Both ways out of the loop call this (``run()`` and the welcome launcher), so the
        second-aboutToQuit mute lives here, ahead of the nested loop that would emit it."""
        self._mute_about_to_quit()
        bar, self._closing_bar = self._closing_bar, None
        if bar is None:
            return
        try:
            if self.loop is not None and not self.loop.is_closed():
                self.loop.run_until_complete(asyncio.wait_for(bar.aclose(), AGENT_CLOSE_S))
        except Exception as e:
            print(f"[WARN] agent session close: {type(e).__name__}: {e}")
        finally:
            bar.release()

    @Slot()
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pool_timer.stop()
        self._frame_timer.stop()
        if self.prompt_bar is not None:
            self.prompt_bar.close()
        if self.endpoint is not None:
            self.endpoint.stop()
            self.endpoint = None
            self.statusChanged.emit()
        if self._registered:
            unregister(self.instance_name, pid=os.getpid())
            self._registered = False
        if self.walk_stage is not None:
            self.walk_stage.close()
        if self.tile_floor is not None:
            self.tile_floor.close()
        if self.controller is not None:
            self.controller.close()
        if self.hosts is not None:
            self.hosts.dispose_all()
        with contextlib.suppress(SpectoError, OSError):
            self.engine.live.save()
        self.engine.close()
        shutdown = getattr(self.jobs, "shutdown", None)
        if callable(shutdown):
            shutdown()
        self._release()
        print("[OK] specto-app closed")

    def _release(self) -> None:
        """Destroy the shell here and now, then drop every reference that points back
        at this app. Not left to the cyclic GC: it would free the QML engine, window,
        models and controllers in an arbitrary order inside whichever Qt loop happens
        to trigger it next (another test's), a hard crash with no frame to name."""
        if self.window is not None:
            self.window.hide()
        if self.qml is not None:
            # the engine owns the window and every item in it; a DeferredDelete sent to
            # this one receiver is delivered at once, whatever the loop level
            self.qml.deleteLater()
            QCoreApplication.sendPostedEvents(self.qml, QEvent.Type.DeferredDelete)
        self.window = self.qml = None
        self._theme = self._scheme_handler = self._snap_provider = None
        bar = self.prompt_bar
        if bar is not None:
            bar.setParent(None)
            if bar.session is not None and self.loop is not None and self.loop.is_running():
                self._closing_bar = bar  # run() closes its session once the loop returned
            else:
                bar.release()
        if self.overworld is not None:
            self.overworld.capture = None
        walk = self.walkthrough
        if walk is not None:
            walk.release()
            walk.setParent(None)
        self.walkthrough = None
        self._off_canvas()
        self._off_canvas = lambda: None
        self.host.app = None
        self.host.shell.app = None
        self.agent = None  # type: ignore[assignment]  # a caller's factory closes over the app
        if self.context_menus is not None:
            self.context_menus.close()
            self.context_menus.setParent(None)
        self.context_menus = None
        if self.launcher_watch is not None:
            self.launcher_watch.close()
            self.launcher_watch.setParent(None)
        self.launcher_watch = None
        if self.walk_stage is not None:
            self.walk_stage.setParent(None)
        self.walk_stage = None
        if self.tile_floor is not None:
            self.tile_floor.setParent(None)
        self.tile_floor = None
        self.prompt_bar = self.overworld = self.controller = self.hosts = None

    def _focused_view(self) -> str:
        """The view holding the keyboard focus (its item or one inside it; the keys' rule,
        #113), else the one the canvas focused (a header or border gesture)."""
        hosts = self.hosts.hosts if self.hosts is not None else {}
        pairs = [(vid, h.item) for vid, h in hosts.items() if getattr(h, "item", None)]
        if self.window is not None and pairs:
            # the window walks up from its focus item (Main.qml): wrapping that item for
            # Python segfaults when it is a web view's render delegate (Linux)
            found = QMetaObject.invokeMethod(
                self.window,
                "focusedIndexOf",
                Q_RETURN_ARG("QVariant"),
                Q_ARG("QVariant", [item for _, item in pairs]),
            )
            if isinstance(found, (int, float)) and 0 <= int(found) < len(pairs):
                return pairs[int(found)][0]
        return self.controller.focusedView if self.controller is not None else ""

    # -- window frame ----------------------------------------------------------------------
    def _chrome(self) -> tuple[int, int]:
        """What the window adds around the canvas area (the bottom bar) plus the area's
        padding."""
        # the bar's height is known before the window is exposed; the area's is not
        assert self.window is not None
        bar = find_item(self.window.contentItem(), "statusBar")
        return int(2 * PAD), int((bar.height() if bar is not None else 0.0) + 2 * PAD)

    def _fit_window(self) -> None:
        """The window opens so that the document's window-frame guide (the canvas area
        inside its padding) fits exactly; from then on a resize writes the guide back.
        Once the window settles the guide is written back once anyway: the system may
        have clamped the window to the screen, and a fresh canvas's file tree fits the
        area it got (#125)."""
        assert self.window is not None
        rect = self.live.data["window_frame"]["rect"]
        cw, ch = self._chrome()
        w, h = int(rect["w"]) + cw, int(rect["h"]) + ch
        wanted = (w, h) if rect["w"] > 0 and rect["h"] > 0 else None
        if wanted is not None and wanted != (self.window.width(), self.window.height()):
            self.window.resize(w, h)
        self.window.widthChanged.connect(self._frame_timer.start)
        self.window.heightChanged.connect(self._frame_timer.start)
        self._frame_timer.start()

    @Slot()
    def _write_window_frame(self) -> None:
        """After a resize settles: the guide takes the canvas area's size and a fresh
        canvas's file tree its height (one user step, none when both already match)."""
        if self.window is None or self._closed:
            return
        cw, ch = self._chrome()
        w, h = self.window.width() - cw, self.window.height() - ch
        if w <= 0 or h <= 0:
            return
        try:
            set_window_frame(self.live, (float(w), float(h)))
        except SpectoError as e:
            self.notices.error(e.message, fix=e.fix, code=e.code, command="resize window")

    @Slot(str)
    def _on_patched(self, kind: str) -> None:
        self.statusChanged.emit()

    @Slot()
    def _pump(self) -> None:
        poll = getattr(self.jobs, "poll", None)
        if callable(poll):
            poll(0)
        self.engine.links.poll()

    # -- snapshots (contracts sections 11, 12; the overworld's cards, #45) -----------------------
    def capture_views(
        self, view_ids: list[str] | None = None, on_done: Callable[[str], None] | None = None
    ) -> list[str]:
        """Grab each view's slot on its own (the host's own ``snapshot_image`` wins when
        it returns one); grabs land a frame later, ``on_done(view_id)`` says when.
        Returns the views a snapshot was requested for."""
        if self.hosts is None or self.controller is None:
            return []
        ids = list(view_ids) if view_ids is not None else list(self.live.data["views"])
        items: dict[str, Any] = {}
        own: dict[str, Any] = {}
        for vid in ids:
            slot = self.hosts.slot_of(vid)
            if slot is not None:
                items[vid] = (self.window, slot)
            img = self.hosts.snapshot_image(vid)
            if img is not None:
                own[vid] = img
        return capture(self.snapshots, items, own, on_done)

    # -- project switching (one project per process: relaunch) ----------------------------
    @Property("QVariantList", notify=statusChanged)
    def recents(self) -> list[dict[str, Any]]:
        here = str(self.project.root)
        return [
            {**e, "current": _same_path(e["root"], here), "short": _short_path(e["root"])}
            for e in recents.load_recents()
        ]

    @Slot(str)
    def forgetRecent(self, root: str) -> None:
        recents.forget(root)
        self.statusChanged.emit()

    @Slot()
    def requestOpenProject(self) -> None:
        self.openProjectRequested.emit()

    @Slot(str, bool, result=bool)
    def openProject(self, text: str, create: bool = False) -> bool:
        """Open another project in a new process and quit this one. With ``create`` (the
        folder dialog) a plain folder becomes a project first; a recent-list row passes
        False, so a project that lost its ``.specto/`` is refused with a fix-it."""
        from .launcher import open_project, relaunch, resolve_folder

        folder = resolve_folder(text)
        if folder is None:
            return False
        if _same_path(str(folder), str(self.project.root)):
            self.notices.info(f"{self.project.name} is already open here")
            return False
        try:
            proj = open_project(folder, create=create)
            relaunch(proj.root, self.launch_flags)
        except (SpectoError, OSError) as e:
            msg = e.message if isinstance(e, SpectoError) else str(e)
            self.notices.error(msg, fix=getattr(e, "fix", None))
            return False
        self.notices.info(f"opening {proj.name} in a new window; this one closes")
        QTimer.singleShot(150, self.quit)
        return True

    # -- QML-facing bits ------------------------------------------------------------------
    @Slot(result=str)
    def projectName(self) -> str:
        return self.project.name

    @Slot(result=str)
    def projectRoot(self) -> str:
        return str(self.project.root)

    @Property(str, constant=True)
    def instanceName(self) -> str:
        """This window's instance name (#187): status bar, panel header, title, details."""
        return self.instance_name

    @Slot(str)
    def copyText(self, text: str) -> None:
        """The details panel's Copy."""
        clipboard.set_text(str(text))

    @Property(str, notify=statusChanged)
    def endpointAddress(self) -> str:
        return self.endpoint.info.address if self.endpoint and self.endpoint.info else ""

    @Slot(result="QVariantMap")
    def status(self) -> dict[str, Any]:
        jobs = self.jobs
        return {
            "web": self.web_enabled,
            "workers": len(getattr(jobs, "workers", []) or []),
            "jobs_active": len(jobs.list(active_only=True)),
            "endpoint": self.endpointAddress,
        }

    @Slot()
    def quit(self) -> None:
        self.qapp.quit()


def locate_item(win: QQuickWindow, item: QQuickItem) -> dict[str, Any]:
    """``shell.locate``'s answer for one item of ``win``: its window rect, its screen
    rect, the part its clipping ancestors leave in view (``shown``, null when none: a row
    scrolled out of a list, #167), whether it is visible and, for a text item, its text.
    Also the test driver's (#237), on whichever window it drives."""
    r = item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))
    g = win.mapToGlobal(r.topLeft().toPoint())
    sr = _shown_rect(item, r)
    shown = [round(v, 1) for v in (sr.x(), sr.y(), sr.width(), sr.height())]
    text = item.property("text")  # a Text or a field: what it says (gui_drive assert_text)
    return {
        "visible": bool(_item_visible(item)),
        "rect": [round(r.x(), 1), round(r.y(), 1), round(r.width(), 1), round(r.height(), 1)],
        "screen": [g.x(), g.y(), round(r.width(), 1), round(r.height(), 1)],
        "shown": None if sr.isEmpty() else shown,
        "text": text if isinstance(text, str) else None,
    }


def _item_visible(item: QQuickItem) -> bool:
    visible = item.isVisible()
    p = item.parentItem()
    while visible and p is not None:
        visible = p.isVisible()
        p = p.parentItem()
    return bool(visible)


def _shown_rect(item: QQuickItem, rect: QRectF) -> QRectF:
    """The part of ``rect`` (the item's scene rect) inside every clipping ancestor."""
    p = item.parentItem()
    while p is not None and not rect.isEmpty():
        if p.clip():
            rect = rect.intersected(p.mapRectToScene(QRectF(0, 0, p.width(), p.height())))
        p = p.parentItem()
    return rect


def _same_path(a: str, b: str) -> bool:
    import os

    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _short_path(root: str) -> str:
    home = str(Path.home())
    return "~" + root[len(home) :] if root.startswith(home) else root


__all__ = ["MAIN_QML", "AppHost", "ShellSnapProvider", "SpectoApp", "locate_item", "make_jobs"]
