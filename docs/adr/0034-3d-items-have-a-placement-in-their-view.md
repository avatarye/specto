---
status: accepted
date: 2026-11-27
---

# A 3D item has a placement in its view, kept in the view's params

Amends the `3d` view's params (`views/scene3d_view.py`, contracts section 11); a minor release, since a view's params change.

## Context

The `3d` view shows every item of its collection in one scene. Its only orientation control is the view-wide `up` (`y` or `z`), which turns one shared root; glTF/GLB and DAE are exempt because they declare their own up. That breaks on a collection whose items disagree. Gaussian splats are the common case: trainers and capture apps write different conventions (Y down from COLMAP-style cameras, Z up, Y up), so two splats side by side cannot both stand upright, and nothing can turn one of them. Once an item can be turned, moving and scaling it is the same mechanism, and a user lining up a scan with a mesh needs them too.

Babylon.js offers two ready-made editors, and neither fits:

- **The Babylon.js Editor** (BabylonJS/Editor) is a standalone desktop application with its own project format. It cannot be embedded in a view.
- **The Inspector** (`@babylonjs/inspector`) can be embedded: a scene explorer, a property grid and gizmos. But its edits change only the page's live scene. They would not reach the canvas document, so there would be no undo (ADR 0002), the agent could neither see nor make them (ADRs 0003, 0011, 0013), and they would be lost on reload. Its debugging UI is also far from the calm canvas (ADR 0008).

## Decision

1. **Each item in a `3d` view has a placement**: how it sits in that view's scene. It is an up preset, a rotation, a position and a scale. A placement belongs to the view, not to the data. Another view of the same file places it on its own, and the file is never written.
2. **Placements live in the view's params**, under `params.placements`, keyed by the item's key: `{"up": "+x|-x|+y|-y|+z|-z", "rotation": [x, y, z] (degrees), "position": [x, y, z] (metres), "scale": [x, y, z]}`, each field optional. The canvas document is the only record, so the agent sets placements as it sets any param, a change is one submit, and undo comes from ADR 0002 unchanged.
3. **Order of application, innermost first:** the file's own unit (`mesh_unit` for STL and OBJ) and declared up (glTF, DAE); then the placement's `up`, which replaces the view-wide `up` for that item; then `rotation` (X, then Y, then Z, about the item's own origin); then `scale`; then `position`. An item without a placement looks as it does today. The view-wide `up` stays as the default for items that set no `up` of their own.
4. **One level, no hierarchy.** Each item gets one node between the scene's root and its loaded content. Items are not parented to each other, and the parts inside an item (a glTF's nodes) are not editable. Nesting would turn the viewer into an editor, against DESIGN's "viewing first".
5. **The page edits through gizmos and presets, and writes on release.** Selecting an item (the existing `selection`, `<item key>`) shows Babylon's own gizmos from core (`GizmoManager`: rotate, move, scale) on it. The VIEW panel shows the selected item's up presets and 90-degree turns about each axis. A gizmo drag changes only the page while it moves, then writes the placement once on release, through the page's own `setParams`: one submit and one undo step, as the `urdf` view's joint sliders do (ADR 0029). Turns snap to 15 degrees while Shift is held.
6. **A placement survives what its key survives.** Placements are keyed by item key, so one is kept while its item leaves the collection and comes back, and lost when the file is renamed. Placements for keys not in the collection stay in the params, unused, and `snap` lists them, so the agent can tidy them.
7. **Not a space.** A placement is a display choice made in one view, not a fact about the data. When collections declare spaces (the frame graph, milestone M3), an item's declared frame will apply first and its placement on top; link transforms between views will not read placements.

## Considered options

- **The item's orientation as part of its space**, so it would travel with the item into every view and through link transforms. That is right for a known convention, and the frame graph will carry it. But spaces are not built, and the request is a per-view correction, so it is deferred, not rejected.
- **Separate params per field** (`rotations`, `positions`, ...): rejected; one object per key keeps an item's placement together, and one merge patch changes it.
- **Embedding the Inspector, or the Editor**: rejected for the reasons in Context.

## Consequences

- The `3d` view's state schema and its agent guide gain `placements`; `api-info.json` changes, so `gen_api.py` regenerates the SDK and the TypeScript types.
- Picking (`pick_ray` on the host's kd-tree) must account for a point cloud's placement: the host transforms the page's world-space ray into each item's own frame before querying, or a moved cloud picks the wrong point. The view-wide `up: z` turn may already have this problem: the page sends the ray in world space, the kd-tree holds the file's coordinates, and no test covers picking under `up: z`. Check this first when building.
- The camera's first look and F (frame all) use the placed bounds.
- The `urdf` view is a separate view and does not change.
