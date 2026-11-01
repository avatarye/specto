---
status: accepted
date: 2026-10-12
---

# Core-assigned ids and an explicit canvas in every address; there is no active canvas

## Context

Agents, the CLI and the GUI act on the same canvases concurrently. An implicit "active canvas" or "active tab" means a command's target depends on what someone else last clicked, and user-chosen ids collide or get renamed under an agent (review finding R2; the landscape survey's "never the active tab").

## Decision

Object ids are assigned by core, kind-prefixed, monotonic per canvas and never reused (`v3`, `c1`); every object also has a mutable label, and `resolve "@label"` maps a label to an id, failing on ambiguity. Every address names its canvas (`canvas:c7/view:v3.camera`); every canvas-scoped command takes `canvas` explicitly. The CLI fills it from `--canvas` or `SPECTO_CANVAS`, MCP tools take a `canvas` argument (HLD 3.2, 3.3; section 10 `addr`).

## Consequences

- Command schemas, generated surfaces and the journal all carry the canvas, so changing this later touches every command.
- Agents speak in ids and write labels for humans; a renamed object keeps its id.
- Projects may be named in addresses (`proj:`), defaulting to the connection's project.

## Considered options

- **An active canvas per client or per app.** Rejected: ambiguous target under concurrent clients.
- **User- or agent-chosen ids (`col:frames` in `DESIGN.md`).** Rejected for core ids plus labels.

This ADR is an addition beyond the originally requested list.
