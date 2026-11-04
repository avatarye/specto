---
status: accepted
date: 2026-11-03
---

# Opening or expanding a view makes room under a quota by collapsing the oldest live views

Task SPEC-34. Builds on ADR 0021's collapse amendment (#114) and ADR 0023 (dedicated views).

## Context

A canvas allows one live RTX view (quota `rtx_views=1`, #132), and ceilings on 3D viewports, video decoders and web views. Since SPEC-32 a double-click on a file opens a view of its own beside the file tree, inside the window frame. With one stage's usd view open, a double-click on a second stage was refused whole: "canvas document has 1 problem: quota rtx_views=1 would be exceeded", and nothing opened. A fifth video, a fourth 3D view or a ninth web view failed the same way. The quota's fix-it (collapse one, move it off screen, or switch one to Storm) suits an agent composing a document; a user who double-clicked a file only sees that nothing happened.

## Decision

1. **The view the user asked for goes live; the oldest views in its way collapse.** When a new view opened from the file tree (dedicated or contextual), or a collapsed view the user expands, would exceed a quota, the live views counting toward that quota are collapsed, oldest first (document order), floating views last, in the same submit: one undo step brings the old layout back. The view being opened or expanded is never collapsed.
2. **Collapse, not close, re-point or re-render.** A collapsed view keeps its file, its link and its size; it is paused and costs no quota (ADR 0021), and expanding it is one click, which in turn collapses the view now in the way. Each dedicated view stays the one view of its file (ADR 0023).
3. **Said, not silent.** The submit's label names what collapsed ("open two.usd, collapse one.usda · a"), `open_file`'s result carries `collapsed` (view to quota), and the shell posts a notice naming the collapsed views.
4. **A quota collapsing cannot meet still refuses.** When the opened view alone exceeds a quota (a quota of zero), the submit fails with the quota's own problem and fix, as before.
5. **Agents' submits are unchanged.** `canvas.submit` still refuses a document over a quota with the fix-it; only the gestures (and `canvas.open_file`, the gesture as a command) make room.

## Considered options

- Open the second stage with `renderer: storm` (costs no RTX view): rejected; the second stage would render differently from the first for no reason the user chose.
- Re-point the open usd view at the new stage: rejected; a dedicated view is fixed on its file (ADR 0023), and the first stage would be gone from the canvas.
- Place the new view outside the window frame: rejected; the user double-clicked to see the file.
- Keep the refusal and word it better: rejected; the user would still have to collapse a view by hand before every second stage.
