"""specto-core: the canvas document, its commands, links, undo and journal. No Qt.

Public surface (see docs/contracts.md):

- ``Engine``, ``CallContext``: run commands; ``engine.live`` and ``engine.links``.
- ``Project``: the data folder and its ``.specto/`` (one canvas per project).
- ``canvas_document``: the canvas document model, ``validate_canvas`` (raises
  ``CanvasInvalid`` with every problem) and ``canvas_json_schema``.
- ``LiveCanvas`` (``engine.live``): the one running canvas; submit, undo, journal.
- ``AgentTools``: the six agent tools over ``engine.call`` or an SDK client.
- ``registry`` / ``command``: register commands with pydantic ``Args``/``Result``.
- ``links``: the link runtime (port values, resolves as jobs, delivery to hosts).
- ``kinds``: data kinds, ``DataRef``, ``TypedBuffer``, manifests and protocols.
- ``errors``: ``SpectoError`` and its codes.
"""

from .address import Address, format_address, parse_address
from .agent_tools import AGENT_TOOLS, AgentTools
from .canvas_document import CanvasDocument, CanvasInvalid, canvas_json_schema, validate_canvas
from .engine import CallContext, Engine
from .errors import SpectoError
from .live_canvas import LiveCanvas, SubmitResult
from .project import Project
from .registry import API_COMPATIBLE, API_LEVEL, Args, CommandContext, Result, command, registry
from .schema import SCHEMA_VERSION

__version__ = "0.2.0"

__all__ = [
    "AGENT_TOOLS",
    "API_COMPATIBLE",
    "API_LEVEL",
    "SCHEMA_VERSION",
    "Address",
    "AgentTools",
    "Args",
    "CallContext",
    "CanvasDocument",
    "CanvasInvalid",
    "CommandContext",
    "Engine",
    "LiveCanvas",
    "Project",
    "Result",
    "SpectoError",
    "SubmitResult",
    "canvas_json_schema",
    "command",
    "format_address",
    "parse_address",
    "registry",
    "validate_canvas",
]
