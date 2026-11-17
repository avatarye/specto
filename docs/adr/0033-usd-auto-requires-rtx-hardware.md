---
status: accepted
date: 2026-11-14
---

# Auto USD rendering requires RTX hardware, not just installed RTX packages

Complements ADRs 0024 and 0031. RTX renderer reuse and the environment light's
sky → HDRI → stage-light fallback are unchanged.

## Decision

The `ovrtx` machine capability requires both a confirmed RT-core GPU from the NVIDIA
driver inventory and importable `ovrtx`/`ovstage`. The inventory runs bounded
`nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader`, without creating a
renderer or Vulkan context. Compute capability alone is insufficient: GTX Turing and
compute-only GPUs must not select RTX. Unknown devices, missing inventory and errors
fail closed to Storm. New NVIDIA product families may require an inventory update;
an explicit renderer choice remains explicit.

Auto continues to choose Storm on non-RTX machines, including when the RTX packages
are installed. Storm requires the OpenUSD imaging build and PyOpenGL; pip `usd-core`
alone is not a renderer.

Renderer construction errors carry `initialization_failed` in the source's health
event. `renderer_ready` distinguishes successful construction from the process's IPC
`ready`; a native exit before construction follows the same fallback path.
Auto falls back once from RTX to Storm, then to the diagnostic stub if Storm
also fails, reporting the cause through health, notices and snapshots. This is a
runtime choice, not a document edit. Explicit renderer errors and failures after a
successful initialization retain the existing failure/retry behavior.

## Setup

Plain `uv sync` includes Storm's Python dependencies. Build OpenUSD with imaging or
set `SPECTO_OPENUSD` to an existing imaging install. Install the large `rtx` extra
only on machines intended to use RTX. A stub is a reported unavailable renderer,
never evidence that Storm rendered successfully.
