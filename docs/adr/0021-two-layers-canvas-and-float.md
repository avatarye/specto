---
status: accepted
date: 2026-10-22
---

# Two layers: the canvas and the float layer

Agreed with the user on 2026-10-22, issue #112 (part of #51). Builds on ADR 0012 (one canvas driven by the canvas document), ADR 0013 (submits, locks) and ADR 0018 (the wiring layer).

## Context

The user asked for "a pin button that makes a view float on top and stay at the same position on the top layer, but movable within the window". Until now every view lived on one pannable canvas: a legend, a player or the viewer the user keeps checking scrolled away with everything else. "Pin" was already taken: a pinned intaker is one with fixed sources.

## Decision

1. **Two layers.** The canvas pans under the window; the **float layer** lies above it, fixed in the window. A view on the float layer is a **floating view**; the header's **Float** button puts it there and **Unfloat** takes it back. "Pin" stays the intaker scope term.
2. **The document records the float, anchored to a window corner.** `layout.floating = {corner, offset, by}`: `corner` is the window corner nearest the view's centre (`top_left`, `top_right`, `bottom_left`, `bottom_right`), `offset` the pixels from that corner to the view's matching corner, inward, so the view stays put when the window resizes (kept inside a window too small for the offset). `layout.size` still applies; `layout.position` is kept for unfloating. A view on the canvas writes no `floating` key. Geometry lives in `specto_core.float_layer`, shared by gestures, quotas and the app.
3. **Float and Unfloat are user gestures, one submit each** (`gestures.float_view`, `unfloat_view`). Float keeps the view where it is on screen (pulled inside the window if it hung over an edge). Unfloat drops it on the canvas point under it: the window frame's origin (which follows the pan) plus its window position.
4. **Drag and resize inside the window.** A floating view's header and border drags stay inside the window and snap to the window edges and to other floating views, never to canvas views (and canvas views never snap to floating ones); the drop re-anchors it to its nearest corner (`gestures.place_floating`, one submit).
5. **The agent may float and unfloat, but not move a view the user floated.** `by` records who floated it. A non-user submit that changes the corner, offset or size of a view with `by: user`, that turns a user's float into the agent's, or that writes `by: user` itself, is refused at submit (`locks.lock_problems`), with a fix-it. Unfloating a user's float is allowed. The user's own gestures always pass.
6. **Never panned, never culled, always counted.** Panning moves the canvas under the float layer; a floating view never pauses, and it always counts against the quotas (`quotas.in_window_frame` is true for it).
7. **Curves cross the layers.** The controller gives a floating view the canvas rect under it at the current pan, so the wiring layer's curves run from a floating view to canvas views (and back) and follow the pan.
8. **Overworld: a "floating" strip.** Floating views are not cards on the overworld's canvas; a strip along its top shows them (a click goes back to the normal view and flashes the view), and a faint outline marks where each floats over the window-frame guide so its wires still land.

## Consequences

- The agent learns the layer from the schema (`Float`, `Layout.floating`), `describe canvas` (`float`), the `submit_canvas` tool text and the prompt bar's system prompt.
- To unfloat, the agent sets `layout.position` itself; the canvas point under a floating view is `window_frame.rect` x, y plus its window position.
- A floating view's place is window-relative and so depends on the window size: two windows of different sizes show it at different canvas points. The window frame guide in the document keeps the size the user last had.
- A multi-selection header drag moves only the canvas views of the selection; a floating view drags alone.

## Considered options

- **Name it "pin":** rejected; a pinned intaker already means fixed sources.
- **Store a window position (x, y) instead of a corner offset:** rejected; a view at the right edge would drift inward or off screen when the window resizes.
- **Let floating views snap to canvas views:** rejected; the canvas moves under them, so the snap would hold only until the next pan.
- **Treat a user's float as a lock (the agent may not even unfloat it):** rejected; the user's decision lets the agent unfloat, only moving and resizing stay the user's.

## Amendment 2026-10-23: a view collapses to its title bar (#114)

The user: "add collapse button and status to all views, collapse into a title bar". A collapse is a third way to keep a view without its content in the way, beside panning it off screen and floating it.

1. **The document records it.** `layout.collapsed: true`; an expanded view writes no key. `layout.size` keeps the expanded size, so expanding restores the height. The header's collapse button is a user gesture, one submit per toggle (`gestures.set_collapsed`); the agent may set the key too. A lock does not bind it: collapsing is neither a move nor a resize (`locks.lock_problems` compares the layout without `collapsed`).
2. **The title bar is the view's rect.** A collapsed view is its header at the same width (theme metric `collapsed_h`, 36 px with the frame and gutter). The controller's `rect_of` returns that rect, so snapping, the marquee, hover, culling and the wires use it; a resize changes its width only. Its ports sit on the bar's middle, extra input circles line up rightwards along the bar with the title after them (`chains.port_anchor`; leftwards they would cover the output circle of a view snapped beside it). On the float layer the same holds: the bar keeps the floating view's top-left.
3. **Paused, not cut off.** Its host pauses like an off-screen view (health `paused`, message `collapsed`) while its links keep delivering, so expanding shows the latest data. It costs no quota (`quotas.quota_problems` skips it). The status bar says "<name>: collapsed · expand".
4. **Overworld.** Its card is the title bar too, with a collapsed mark and no snapshot.

The same ticket hides the header's health dot while a view is healthy (`ok`): only resolving, paused, error, needs approval and not connected show one, since those carry information. A collapsed view paused by its collapse shows none; the collapse button says it.
