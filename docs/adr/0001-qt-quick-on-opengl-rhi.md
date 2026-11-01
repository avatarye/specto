---
status: accepted
date: 2026-10-12
---

# Qt Quick shell on the OpenGL RHI backend, gated by spike S1

## Context

The shell has to host native renderers (Hydra Storm, mpv, later ovrtx) beside Chromium-based web views in one window, and draw the overworld's cards and wires. Qt Quick on Windows defaults to Direct3D 11, which Storm and CUDA-GL interop do not share textures with. Every view host depends on this choice, so HLD section 9 names it one of the two risky decisions (HLD 5.5, `DESIGN.md` open questions).

## Decision

The shell is Qt Quick with `QSG_RHI_BACKEND=opengl` on Windows and Linux (Metal on macOS). It was gated on the shell-deciding half of spike S1 plus S2 before any M1 code (HLD section 10, `shell`). S1/S2 passed on 2026-10-12 (`docs/spikes/s1-s2.md`): 60 Hz at 1080p for a native GL texture beside WebEngine, GUI thread 98 % idle, and WebEngine unchanged versus the D3D11 baseline.

## Consequences

- Native items are a C++ QML module plugin (`_spectoqt`) built against the Qt SDK matching the PySide6 wheel and loaded by QML import; shiboken bindings are optional, not required (spike finding; the HLD's "shiboken-wrapped" wording is superseded).
- Design rule from the spike: a C++ item never re-arms frames from the render thread. Frames are driven from the GUI thread (one `item.update()` per frame), otherwise WebEngine drops to ~16 fps.
- CUDA interop follows the backend: CUDA-GL, not D3D (HLD 4.5, R21).
- The Storm half of S1 is separate and still open: it decides in-process `StormItem` versus Storm as an out-of-process frame source; either outcome keeps Qt Quick.
- Pinning PySide6 and the matching Qt SDK patch version is a standing build constraint (spike finding).

## Considered options

- **Qt Widgets with `QMainWindow` docks and a `QGraphicsView` overworld of snapshots.** Kept as the designed fallback: removes the D3D11 question and lets usdview's `StageView` drop in. Rejected as default because Qt Quick gives drag-to-pan galleries, GPU-composited cards and wires, and one item model for both canvas levels.
- **Qt Quick on the default D3D11 backend with cross-API texture sharing.** Rejected (inferred from HLD 5.5 / `DESIGN.md`): Storm and CUDA output would need sharing across graphics APIs on every frame.
- **Decide after the spike with no default.** Offered in HLD 5.5; the user chose a default confirmed by the spike.
