---
status: accepted
date: 2026-10-12
---

# Python owns control, C++ owns per-frame work; the render thread never runs Python

## Context

Python is the working language and the whole control plane (document, commands, channels, intake orchestration) is Python. Galleries of 10k thumbnails, video and 3D viewports need steady 60 Hz. Review finding R1 asked for an explicit execution model; GIL contention on the render thread would stall every view at once.

## Decision

The GUI thread runs Qt, Python and the command bus. The Qt Quick render thread runs C++ only (`_spectoqt` items: gallery tiles, native texture wrap, mpv render callback, Storm), and the GIL is never taken there. Decoding and scanning run in a worker process pool; ovrtx and Blender run out of process. Hot paths found later move into `_spectoqt`, not into QML JavaScript (HLD principle 3, 4.1; section 10 `runtime`).

## Consequences

- `_spectoqt` is a small set of C++ items (`GalleryItem`, `TextureItem`, `MpvItem`, `StormItem`); everything else in the app is Python. Python configures items; items never call Python (spike S1/S2).
- Channel propagation is coalesced into one tick per rendered frame on the GUI thread, not per event.
- Only `_spectoqt` and PySide6 can take the process down; that is the accepted price of in-process Storm and mpv (HLD 4.3).
- Storm and mpv are C++ items or they are not in process at all: a Storm that cannot be a C++ item becomes an out-of-process frame source.

## Considered options

- **Python render callbacks (for example mpv or Storm driven from Python).** Rejected: GIL on the render thread.
- **Hot paths in QML JavaScript.** Rejected in favour of C++ items.
- **C++ decode thread pool inside `_spectoqt`.** Kept as the spike S3 fallback if process-based decoding cannot keep a gallery at 60 Hz (see ADR 0005).
