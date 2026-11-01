---
status: accepted
date: 2026-10-17
---

# v2 evolves the M2 codebase in place; no v1 migration; retired code is deleted

## Context

The agent-first v2 model (ADRs 0011-0016) replaces the canvas document, the channel-based links, docked groups and the hand-built panels. The M2 inventory (docs/review/m2-v2-inventory.md) found about half the code reusable behind narrow seams: command registry, transactions, undo, journal, intake pipeline, readers and worker pool, transport, SDK/CLI, native gallery and mpv items, web views.

## Decision

- Evolve in place on one integration branch rather than starting a new codebase.
- Persist views and links as maps keyed by id, so patch paths stay stable for undo, journal and highlight.
- Skip any v1-to-v2 document migration: v1 canvases are test projects; v2 projects start fresh. The schema-version machinery stays for later versions.
- Drop channel types with no v2 port type (pan_zoom2d, scalar, scroll, hover_time); add them back as port types on real need (synced pan/zoom returns with the v2 compare view).
- Delete retired code (side panels, palette, changeset bar, controls view, timeline view type, diff view type, multi-window, drop-to-compose); git history keeps it. Park only the OpenUSD build tooling.
- Test v2 at three agreed seams: submit to resolved state (headless core), view-host port emit/receive, and scripted human flows. Delete obsolete tests rather than port them.
- The MCP and in-process agent tool set is state, describe, schema, submit_canvas, snap and undo.

## Consequences

- A v1 project opened in v2 is refused with a clear message.
- Large test churn: obsolete tests go, new tests are written at the three seams first.

## Considered options

- Fresh v2 codebase: rejected; discards working, tested subsystems.
- Lossy v1 migration: rejected; no real v1 data worth migrating.
