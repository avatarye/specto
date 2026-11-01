"""The engine: one object that ties the project, its one running canvas document, the
link runtime, the command registry, plugins, quotas and jobs together (ADRs 0012-0017).

The app creates one ``Engine`` per process on the GUI thread; the endpoint calls
``engine.call``; view hosts follow ``engine.live`` (patches) and ``engine.links`` (port
values). Tests drive it directly. No Qt anywhere in here.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .canvas_document import canvas_json_schema, describe_link_kind, describe_port_type
from .events import EventStream
from .hosts import AppHost, HeadlessHost
from .jobs import InlineJobManager, JobManager
from .kinds import DATA_KINDS, PluginRegistry, default_plugins
from .links import DEFAULT_WATCH_DEBOUNCE_S, DEFAULT_WATCH_INTERVAL_S, LinkRuntime, link_env
from .live_canvas import LiveCanvas
from .machine import MachineCapability, machine_capabilities
from .ports import LINK_KINDS, PORT_TYPES, VIEW_ROLES
from .project import Project
from .quotas import Quotas
from .registry import CommandContext, Registry
from .registry import registry as default_registry
from .report import ConversionReport
from .scratch import ScratchStore


@dataclass
class CallContext:
    """Per-call identity the endpoint supplies. ``origin`` lands in provenance."""

    connection: str = "local"
    origin: str = "user"


class Engine:
    def __init__(
        self,
        project: Project | None = None,
        *,
        registry: Registry | None = None,
        plugins: PluginRegistry | None = None,
        quotas: Quotas | None = None,
        jobs: JobManager | None = None,
        host: AppHost | None = None,
        autosave: bool = True,
        journal: bool = True,
        clock: Callable[[], float] = time.monotonic,
        watch_interval_s: float = DEFAULT_WATCH_INTERVAL_S,
        watch_debounce_s: float = DEFAULT_WATCH_DEBOUNCE_S,
        capabilities: Callable[[], dict[str, MachineCapability]] = machine_capabilities,
    ) -> None:
        self.project = project
        self.registry = registry or default_registry
        self.plugins = plugins or default_plugins()
        self.quotas = quotas or Quotas()
        self.host: AppHost = host or HeadlessHost()
        self.autosave = autosave and project is not None
        self.journal_enabled = journal
        self.clock = clock
        #: how often a watched link lists its sources, and how long the folder must be
        #: quiet before the arrivals resolve (ADR 0016)
        self.watch_interval_s = watch_interval_s
        self.watch_debounce_s = watch_debounce_s
        self.app_events = EventStream()  # process-level: jobs
        self.jobs: JobManager = jobs or InlineJobManager(self.app_events)
        self._live: LiveCanvas | None = None
        self._links: LinkRuntime | None = None
        self._closed = False
        self._scratch: ScratchStore | None = None
        self._detect_capabilities = capabilities
        self._capabilities: dict[str, MachineCapability] | None = None
        # make sure the built-in commands are registered
        from . import commands as _commands  # noqa: F401

    # -- the one canvas --------------------------------------------------------------------
    @property
    def live(self) -> LiveCanvas:
        """The project's one running canvas document (ADR 0012): what ``canvas.submit``,
        ``canvas.undo`` and ``canvas.state`` drive. Opened from the project on first
        use; in memory when there is no project."""
        if self._live is None:
            kw: dict[str, Any] = {
                "clock": self.clock,
                "quotas": self.quotas,
                "view_cost": self.view_cost,
            }
            if self.project is None:
                self._live = LiveCanvas.headless(**kw)
            else:
                self._live = LiveCanvas.open(
                    self.project, autosave=self.autosave, journal=self.journal_enabled, **kw
                )
        return self._live

    def close(self) -> None:
        """Stop the link runtime (its watcher threads join here) and let go of the live
        canvas. The app calls this on quit; tests on teardown. Safe to call twice."""
        if self._links is not None:
            self._links.close()
            self._links = None
        self._live = None
        self._closed = True

    @property
    def closed(self) -> bool:
        """``close()`` has run: a host still holding this engine (tests) follows nothing."""
        return self._closed

    @property
    def capabilities(self) -> dict[str, MachineCapability]:
        """This machine's capabilities (``specto_core.machine``), detected on first use
        and kept for the process."""
        if self._capabilities is None:
            self._capabilities = self._detect_capabilities()
        return self._capabilities

    def view_cost(self, view_type: str) -> Any:
        """The plugin registry's cost of one live view of ``view_type`` (None: unknown
        type, free). Looked up at submit time, so plugins registered later count too."""
        m = self.plugins.view(view_type)
        return m.cost if m is not None else None

    @property
    def links(self) -> LinkRuntime:
        """The link runtime over ``live`` (ADR 0016): port values, resolves on ``jobs``,
        delivery to view hosts. Built on first use, after the canvas is open."""
        if self._links is None:
            live = self.live
            root = self.project.root if self.project is not None else None
            self._links = LinkRuntime(
                live,
                self.jobs,
                root=root,
                env=link_env(self.plugins),
                watch_interval_s=self.watch_interval_s,
                watch_debounce_s=self.watch_debounce_s,
                resolve=False,
            )
            # resolve once the attribute is set: an inline job's events reach listeners
            # (the host registry) that read ``engine.links`` while it resolves
            self._links.resolve_all()
        return self._links

    @property
    def scratch(self) -> ScratchStore:
        """The project's scratch pad store (#184, ``.specto/scratch``), opened on first
        use; without a project, one in a temporary folder for the process."""
        if self._scratch is None:
            if self.project is not None:
                root = self.project.root
            else:
                import tempfile

                root = Path(tempfile.mkdtemp(prefix="specto-scratch-"))
            self._scratch = ScratchStore(root)
        return self._scratch

    # -- calling commands ----------------------------------------------------------------
    def call(
        self,
        name: str,
        args: dict[str, Any] | BaseModel | None = None,
        ctx: CallContext | None = None,
    ) -> dict[str, Any]:
        """Run a command. Returns ``Result.model_dump()``. Raises ``SpectoError``
        subclasses. Every command runs on its own: a submit is the one atomic change
        (ADR 0013), so there is no transaction around calls."""
        ctx = ctx or CallContext()
        spec = self.registry.get(name)
        parsed = spec.parse_args(args)
        cctx = CommandContext(
            engine=self,
            origin=ctx.origin,
            connection=ctx.connection,
            report=ConversionReport(),
        )
        result = spec.handler(cctx, parsed)
        return result.model_dump(mode="json") if isinstance(result, BaseModel) else dict(result)

    # -- discovery ---------------------------------------------------------------------------
    def api_info(self, theme_tokens: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.registry.api_info(
            data_kinds=list(DATA_KINDS),
            port_types=[describe_port_type(p) for p in PORT_TYPES],
            link_kinds=[describe_link_kind(k) for k in LINK_KINDS],
            view_types=[
                {"type": m.type, "role": VIEW_ROLES.get(m.type)} | m.model_dump()
                for m in self.plugins.views()
            ],
            theme_tokens=theme_tokens,
            canvas_schema=canvas_json_schema(),
        )

    def export_api_info(self, path: Path, theme_tokens: dict[str, Any] | None = None) -> Path:
        import json

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.api_info(theme_tokens), indent=1) + "\n", encoding="utf-8")
        return path
