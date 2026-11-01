"""Command registry (HLD 3.4).

A command is a pydantic ``Args`` model, a ``Result`` model and a handler, registered
by decorator with metadata. One registry feeds every surface: the CLI, MCP tools, the
SDK, the TypeScript bridge types and ``api-info.json``.

Handler signature::

    @registry.command("canvas.submit", since=1, doc="Submit the canvas document")
    def canvas_submit(ctx: CommandContext, args: CanvasSubmitArgs) -> SubmitResult: ...

Handlers change the canvas only through ``ctx.engine.live.submit`` (ADR 0013).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import InvalidArgs, UnknownCommand

if TYPE_CHECKING:
    from .engine import Engine
    from .report import ConversionReport

API_LEVEL = 1
API_COMPATIBLE = 1

#: Effects a command may declare: things outside the canvas document that undo cannot
#: revert (glossary: effect).
EFFECTS: tuple[str, ...] = ("file", "job", "watch", "process")


class Args(BaseModel):
    """Base for command argument models. Unknown fields are an error (fix-it, not drift)."""

    model_config = ConfigDict(extra="forbid")


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(Result):
    ok: bool = True


class CommandMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    doc: str = ""
    since: int = 1
    deprecated_since: int | None = None
    read_only: bool = False
    destructive: bool = False
    effects: list[str] = Field(default_factory=list)
    #: ``project``: the command reads or changes the project's canvas; ``global``: it
    #: needs no project (discovery, jobs).
    scope: str = "project"  # project | global


@dataclass
class CommandContext:
    """What a handler sees. Built by the engine for each command call."""

    engine: Engine
    origin: str = "user"
    connection: str = "local"
    report: ConversionReport | None = None
    extra: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[CommandContext, Any], Any]


@dataclass(frozen=True)
class CommandSpec:
    meta: CommandMeta
    args_model: type[BaseModel]
    result_model: type[BaseModel]
    handler: Handler

    @property
    def name(self) -> str:
        return self.meta.name

    def parse_args(self, raw: dict[str, Any] | BaseModel | None) -> BaseModel:
        if isinstance(raw, self.args_model):
            return raw
        try:
            return self.args_model.model_validate(raw or {})
        except ValidationError as e:
            errors = [
                f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
                for err in e.errors()
            ]
            raise InvalidArgs(
                f"{self.name}: invalid arguments: {'; '.join(errors)}",
                fix=f"describe {self.name} shows the argument schema",
                errors=errors,
            ) from e

    def summary(self) -> dict[str, Any]:
        m = self.meta
        return {
            "name": m.name,
            "doc": m.doc,
            "read_only": m.read_only,
            "destructive": m.destructive,
            "effects": list(m.effects),
            "since": m.since,
            "deprecated_since": m.deprecated_since,
            "scope": m.scope,
        }

    def describe(self) -> dict[str, Any]:
        out = self.summary()
        out["args_schema"] = self.args_model.model_json_schema()
        out["result_schema"] = self.result_model.model_json_schema()
        return out


class Registry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}

    def command(
        self,
        name: str,
        *,
        doc: str = "",
        since: int = API_LEVEL,
        deprecated_since: int | None = None,
        read_only: bool = False,
        destructive: bool = False,
        effects: list[str] | None = None,
        scope: str = "project",
        args: type[BaseModel] | None = None,
        result: type[BaseModel] | None = None,
    ) -> Callable[[Handler], Handler]:
        """Decorator. ``Args``/``Result`` default to the handler's type hints."""
        for e in effects or []:
            if e not in EFFECTS:
                raise ValueError(f"{name}: unknown effect {e!r}; known: {EFFECTS}")
        meta = CommandMeta(
            name=name,
            doc=doc,
            since=since,
            deprecated_since=deprecated_since,
            read_only=read_only,
            destructive=destructive,
            effects=list(effects or []),
            scope=scope,
        )

        def deco(fn: Handler) -> Handler:
            hints = get_type_hints(fn)
            params = [k for k in hints if k != "return"]
            a = args or (hints[params[1]] if len(params) >= 2 else Args)
            r = result or hints.get("return", Empty)
            if not (isinstance(a, type) and issubclass(a, BaseModel)):
                raise TypeError(f"{name}: Args must be a pydantic model, got {a!r}")
            if not (isinstance(r, type) and issubclass(r, BaseModel)):
                raise TypeError(f"{name}: Result must be a pydantic model, got {r!r}")
            if name in self._commands:
                raise ValueError(f"command {name!r} already registered")
            self._commands[name] = CommandSpec(meta, a, r, fn)
            return fn

        return deco

    def register(self, spec: CommandSpec) -> None:
        if spec.name in self._commands:
            raise ValueError(f"command {spec.name!r} already registered")
        self._commands[spec.name] = spec

    def get(self, name: str) -> CommandSpec:
        spec = self._commands.get(name)
        if spec is None:
            close = [n for n in self._commands if n.split(".")[0] == name.split(".")[0]]
            raise UnknownCommand(
                f"unknown command {name!r}",
                fix="list_commands shows the names"
                + (f"; similar: {', '.join(sorted(close)[:6])}" if close else ""),
            )
        return spec

    def __contains__(self, name: str) -> bool:
        return name in self._commands

    def names(self) -> list[str]:
        return sorted(self._commands)

    def specs(self) -> list[CommandSpec]:
        return [self._commands[n] for n in self.names()]

    def list_commands(self) -> list[dict[str, Any]]:
        return [s.summary() for s in self.specs()]

    def describe(self, name: str) -> dict[str, Any]:
        return self.get(name).describe()

    def api_info(
        self,
        *,
        data_kinds: list[str] | None = None,
        port_types: list[dict[str, Any]] | None = None,
        link_kinds: list[dict[str, Any]] | None = None,
        view_types: list[dict[str, Any]] | None = None,
        theme_tokens: dict[str, Any] | None = None,
        effects: list[str] | None = None,
        canvas_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The ``api-info.json`` document (Neovim-style ``api_level``/``api_compatible``)."""
        from .schema import SCHEMA_VERSION

        return {
            "api_level": API_LEVEL,
            "api_compatible": API_COMPATIBLE,
            "schema_version": SCHEMA_VERSION,
            "canvas_schema": dict(canvas_schema or {}),
            "effects": list(effects or EFFECTS),
            "commands": [s.describe() for s in self.specs()],
            "data_kinds": list(data_kinds or []),
            "port_types": list(port_types or []),
            "link_kinds": list(link_kinds or []),
            "view_types": list(view_types or []),
            "theme_tokens": dict(theme_tokens or {}),
        }


#: The default registry that the built-in commands register into.
registry = Registry()
command = registry.command
