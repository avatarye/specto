---
status: accepted
date: 2026-10-12
---

# One command registry generates the CLI, SDK, MCP tools and TypeScript types

## Context

Specto is driven by the GUI, a shell CLI, Python scripts, MCP agents and web views. Hand-wiring each surface would let them drift, and an agent could then do things the user cannot see or undo, or the reverse (`DESIGN.md`, "One command schema, many surfaces"; HLD principle 1).

## Decision

Every operation is defined once as a registered command: a typed `Args` model, a `Result` model and a handler, with metadata (`since`, `read_only`, `destructive`, `effects`, `replayable`, `undoable`). The registry exports `api-info.json`, from which the click CLI, the MCP server, the typed Python SDK and the TypeScript bridge types are generated. All surfaces reach the app over one JSON-RPC transport on `QLocalServer`. A feature is a command first; the GUI calls the same command through the same bus.

## Consequences

- Adding a command adds it to every surface at once; `tools/gen_api.py --check` and a test fail when the checked-in generated files drift from the registry.
- The MCP surface is deliberately not one tool per command: about 17 coarse tools plus a generic `call` and `list_commands`. Plugin commands are reachable only through `call`, never as first-class MCP tools (HLD 3.11, R8d).
- Every canvas-scoped command takes the canvas explicitly (see ADR 0010).
- Command argument models forbid unknown fields, so schema changes are visible to every client at once; versioning is by `api_level` / `api_compatible`.

## Considered options

- **Hand-written CLI, SDK and MCP layers over a shared library.** Rejected: duplication and drift (inferred; `DESIGN.md` says "nothing is hand-wired twice").
- **One MCP tool per command.** Rejected for a small coarse surface with annotations (landscape survey, `DESIGN.md`).
- **A second transport (localhost WebSocket) beside `QLocalServer`.** Left open in `DESIGN.md`; not adopted. A TCP endpoint exists for tests and `SPECTO_ENDPOINT` overrides, with the same handler (contracts section 2).
