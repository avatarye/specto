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

## Amendment 2026-11-18: the gallery's prepare thread (SPEC-1)

`GalleryItem` owns one more C++ thread, its **prepare thread**. Before, the render thread turned every pushed thumbnail into atlas cells in its sync, with the GUI thread blocked: each tier's downsample, the cell with its gutter, the cell's mip chain. That sync averaged about 4 ms a frame while scrolling through new thumbnails and reached 15-23 ms on a burst (`tools/bench_gallery.py --before`); with the prepare thread it averages about 1.2 ms with a worst of about 5 ms.

- **The thread.** One `QThread` per item, started in its constructor. It runs C++ only, never Python, and touches neither GL nor the item's layout: it reads a pushed thumbnail and writes upload-ready cells (`atlascell.h`, `prepareCell`).
- **Its queues.** The GUI thread's `pushThumb` / `pushThumbShm` copy the pixels into the raw queue (`RawThumb`, under its own mutex; a wait condition wakes the thread). The thread moves each prepared thumbnail (`PreparedThumb`) into the prepared queue and asks for a frame through a queued `update()` on the GUI thread, so frames are still requested from the GUI thread only (spike S1). The render thread drains the prepared queue in its sync, at most 24 tiles and 3 ms of uploads a frame. `clearThumbs` bumps a generation under the raw queue's lock and every thumbnail carries the one it was pushed with, so a thumbnail of the old collection that was mid-prepare is dropped, never uploaded.
- **Shutdown.** The destructor sets a stop flag under the raw queue's lock, drops the waiting thumbnails, wakes the thread and waits for it. A thumbnail mid-prepare finishes first (milliseconds) and is dropped.
- **Why not the worker pool.** The pool's workers are processes without Qt (ADR 0005). Building cells there would send each thumbnail's cells (about 1 MB for a large one, all tiers and mips) back through shared memory and the GUI thread, which copies them into the item: more copies and more GUI-thread work for what is a few milliseconds of C++. A shared C++ pool across items (the last considered option below) was not needed: one thread per item keeps the queues and the shutdown per item, and a window holds a handful of galleries.
- `SPECTO_GALLERY_INLINE_PREPARE=1` starts no prepare thread: the render thread prepares raw thumbnails in its sync with no time budget, as before. It exists to measure the change (`tools/bench_gallery.py --before`).

## Considered options

- **Python render callbacks (for example mpv or Storm driven from Python).** Rejected: GIL on the render thread.
- **Hot paths in QML JavaScript.** Rejected in favour of C++ items.
- **C++ decode thread pool inside `_spectoqt`.** Kept as the spike S3 fallback if process-based decoding cannot keep a gallery at 60 Hz (see ADR 0005).
