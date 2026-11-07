---
status: accepted
date: 2026-11-03
---

# Opening or expanding a view makes room under a quota by collapsing the views in its way

Task SPEC-34. Builds on ADR 0021's collapse amendment (#114) and ADR 0023 (dedicated views).

## Context

A canvas allows one live RTX view (quota `rtx_views=1`, #132), and ceilings on 3D viewports, video decoders and web views. Since SPEC-32 a double-click on a file opens a view of its own beside the file tree, inside the window frame. With one stage's usd view open, a double-click on a second stage was refused whole: "canvas document has 1 problem: quota rtx_views=1 would be exceeded", and nothing opened. A fifth video, a fourth 3D view or a ninth web view failed the same way. The quota's fix-it (collapse one, move it off screen, or switch one to Storm) suits an agent composing a document; a user who double-clicked a file only sees that nothing happened.

## Decision

1. **The view asked for goes live; the views in its way collapse.** When a new view opened from the file tree (dedicated or contextual), or a collapsed view being expanded, would exceed a quota, unlocked live views counting toward an exceeded quota are collapsed in the same submit, so one undo step brings the old layout back. The first to go is the view counting toward the most exceeded quotas (a usd view counts toward both 3D viewports and RTX views, so one collapse can relieve both), then the oldest (document order), floating views last. The view being opened or expanded is never collapsed.
2. **Collapse, not close, re-point or re-render.** A collapsed view keeps its file, its link and its size; it is paused and costs no quota (ADR 0021). The way back is the same gesture: a double-click on its file (or "Open in its own view", or `canvas.open_file` in dedicated mode) expands a collapsed dedicated view instead of only revealing it, as does the header's expand, each making room the same way. Each dedicated view stays the one view of its file (ADR 0023). A new view is placed clear of the collapsed views' stored size, so expanding one later covers nothing.
3. **Locked views are never touched.** A view the user locked is not collapsed to make room. When only locked views (and the view asked for) stand in the way, the submit is refused with the quota's own problem and fix, as before; so is a quota the asked-for view alone exceeds (a quota of zero).
4. **An agent's open can collapse the user's views.** `canvas.open_file` is the gesture as a command: when the agent opens or expands a file's view past a quota, the user's unlocked views in the way collapse, as they would for the user's own double-click, and the result's `collapsed` says which. A lock is how the user keeps a view live.
5. **Said, not silent.** The submit's label names what collapsed ("open two.usd, collapse one.usda · a"); `open_file`'s result and the header's expand carry every collapsed view with every quota it was collapsed for; the shell posts one notice naming them and each quota's ceiling ("at most 1 live RTX view per canvas").
6. **Agents' submits are unchanged.** `canvas.submit` still refuses a document over a quota with the fix-it; only the gestures and `canvas.open_file` make room.

## Consequences

- Known gap (SPEC-36): a usd view with `renderer: auto` counts as an RTX view even where auto resolves to Storm (macOS, no ovrtx), so those machines collapse a stage's view to make room that Storm would not need. _Amended (SPEC-36 follow-up): gap closed. A usd view counts toward `rtx_views` only where its renderer resolves to rtx on this machine._

## Considered options

- Open the second stage with `renderer: storm` (costs no RTX view): rejected; the second stage would render differently from the first for no reason the user chose.
- Re-point the open usd view at the new stage: rejected; a dedicated view is fixed on its file (ADR 0023), and the first stage would be gone from the canvas.
- Place the new view outside the window frame: rejected; the user double-clicked to see the file.
- Keep the refusal and word it better: rejected; the user would still have to collapse a view by hand before every second stage.
- Place the new view in the first free spot after the collapse (under the collapsed view's title bar): rejected; expanding the collapsed view would then cover the new one, header and all.
