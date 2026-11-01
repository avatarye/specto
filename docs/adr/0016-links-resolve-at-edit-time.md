---
status: accepted
date: 2026-10-17
---

# Links resolve when their logic changes; the normal view only reads results

## Context

Links can carry Python code and file parsing (for example GeoJSON contents for a map, or "which video belongs to this image"). Running that while views render would cost frame time. Links are only created or edited in the overworld (by the user or the agent), where rendering is paused.

## Decision

When a link's logic or inputs change, Specto resolves it: parses files, runs intakers and link code, and builds the lookups the link needs (for example selected item to video, or timestamp to nearest frame). Resolution finishes before views render in the normal view; at runtime a link only looks up resolved results. Python link code runs at resolve time, out of process (ADR 0006), never per rendered frame or per interaction.

## Consequences

- Interactive latency in the normal view no longer depends on Python link code.
- Python link logic must resolve to output data (a table or sampled values); real-time evaluation of link code is not supported in v1.
- Watch-mode arrivals trigger a resolve in the background; results swap in when ready (the next collection generation). The resolve re-runs the whole intaker rather than merging the added and removed files into the previous collection (decided 2026-10-18 at review): an item is the group of files sharing a key, so one arriving sidecar can change an existing item, and sort order and key fields are properties of the whole set; a full listing is one `scandir` pass, which is cheap next to the decoding that follows, and the watcher's own listing already runs every interval. Re-running keeps one code path and one correctness argument; a merge is a later optimisation if a watched folder ever grows past what one pass per arrival can carry.
- Resolving runs on worker threads and processes, never on the GUI or render thread; views appear progressively, each marked as resolving until its results are ready, and nothing blocks the app.
- Links are created by the agent only in v1; the user can read, delete or open a link (link view, read-only).
- If a submitted canvas document fails validation, the agent gets the errors and retries within the same turn; the user sees the final result, or a short failure reply after three attempts.

## Considered options

- Evaluate link code on every change at runtime (ADR 0014's first draft): rejected for rendering performance.
