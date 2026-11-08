---
status: accepted
date: 2026-11-08
---

# The usd view reuses its RTX renderer, with the runtime stage cleared between stages

Task SPEC-64, from SPEC-27. Amends the stage-switch rule of #155 (a fresh renderer per stage), which no ADR recorded; `rtx_launcher` and `specto_app.usd.rtx` carry it.

## Context

The usd view renders through `specto-usd-source`, one process per view, which holds one ovrtx renderer. When the user picks another stage the source hands the renderer the new one. In #155 that crashed twice: GPU page faults a few seconds after the second stage was attached to a renderer that had already shown one. The fix then was `--fresh-renderer`: the source closes the renderer and creates a new one for every stage after the first.

SPEC-27 measured what that costs on a public 26k-prim warehouse (Windows, RTX 4070 Ti, ovrtx 0.6.0, warm caches): the renderer swap is 1.0-1.4 s of a 7-10 s reload, and it would be 3-4 s without ovrtx's `keep_system_alive`, which is on by default. It also tried the other route ovrtx offers: keep the renderer and clear its runtime stage with `Renderer.reset_stage()` before the next stage is attached. Nine switches ran clean at the same cost as plain reuse.

ovrtx 0.6 marks `reset_stage` deprecated in favour of "ovstage.population with an ovstage.Stage, attach it with Renderer.attach_ovstage()". That is the path Specto already takes (detach the old ovstage, destroy it, attach a new one); it is the path #155 crashed on. The clear is what that path lacks.

## Decision

1. **One renderer takes the stages in turn.** `rtx_launcher` no longer passes `--fresh-renderer`. `RtxRenderer.load` detaches and destroys the previous ovstage as before and, when there was one, clears the renderer's runtime stage (`reset_stage`, its deprecation warning filtered, since the deprecation points at the path this is) before the new ovstage is attached. A renderer that never had a stage is not cleared.
2. **The evidence is a soak, not the nine switches.** 60 loads of four public stages (the warehouse, its 1x1-texture copy, a forklift prop, a one-cube stage) in one process, 59 switches, ran clean under `tools/gpu_guard.py`: every load reached its first complete frame, no fault or error in the source or the ovrtx log, 395 s of wall time; warehouse reloads took 5.5-9.0 s, drifting upward across rounds the way a fresh renderer did in SPEC-27, and the small stages held steady (forklift 1.2-1.4 s, cube 0.8-1.0 s). The bench, `tools/bench_usd_load.py`, does the same run on demand.
3. **`--fresh-renderer` stays** as the source's opt-in for a machine where the cleared renderer fails, and the bench's `--fresh-renderer` runs it; `load_timings.renderer_s` keeps reporting the swap when it happens.

## Consequences

- A stage switch saves the 1.0-1.4 s swap and the "starting" phase it showed; the sky compiled for the environment stays compiled across switches (`_compiled_skies` is cleared only when a renderer closes).
- A crash on the cleared path would show as the source dying (#155's `failure` handling, with its retry) rather than a wrong picture; the opt-in flag is the fallback, by hand.
- `reset_stage` is deprecated API. An ovrtx release that removes it breaks the switch loudly (`AttributeError` on the second load); the fallback is then the flag, and the fix a new way to clear or the fresh renderer again. The test against the fake ovrtx pins the call order (detach, clear, attach) so the change is visible.
- The restage tests say what each path does: the default clears between stages and never before the first; the fresh renderer never clears.

## Considered options

- Keep the fresh renderer: safe by construction, 1.0-1.4 s a switch forever, and a visible "starting" phase on every pick. Rejected once the soak was clean.
- Plain reuse without the clear: the #155 path; rejected, it is what crashed.
- Clearing in `_drop_stage` for every drop: would also clear a renderer about to be destroyed (`close`), a wait for nothing; rejected for the conditional clear in `load`.
