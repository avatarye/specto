---
status: accepted
date: 2026-10-17
supersedes: 0007
---

# Agent-first, minimal UX: humans use views, the agent composes

## Context

ADR 0007 put a complete human GUI underneath the agent layer. The M1.5/M2 builds followed it and exposed file browser, intake panel, add-view, link mode, links panel, overworld wiring and a changeset bar. The user's verdict: the flow is unclear and the UX is messy, because choosing which controls are critical enough to expose was done badly and far too much was exposed.

## Decision

Start minimal and add back only what proves necessary. Humans open a data folder, use the prompt bar, interact inside views (pan, zoom, select, scrub, play, compare) and arrange views (overworld, normal view, snapping). Everything else (intakers, view creation, links, low-level controls) is composed by the agent by writing the canvas document. Hand-built composition panels leave the default UX; their commands remain in the API.

## Consequences

- The prompt bar becomes the primary control surface (see the prompt-bar decision).
- Existing panels (Files/Intake/Links side panels, link mode, add view, changeset bar) are removed from the default shell or reduced; each comes back only through a decision on the UX realignment map.
- Supersedes ADR 0007.

## Considered options

- Keep a complete human GUI underneath (ADR 0007): rejected; it produced the exposed-everything shell.
- Agent-only with no human arrangement: rejected; arranging and interacting with views stays human.
