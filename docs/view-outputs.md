# View outputs: what each view sends, and what it could (#176)

A Python link (`resolves_to: value`) can read any output port below and produce any
input port's value, so the outputs are what a script can react to. This note lists them
as of #176 (the conformance matrix `packages/specto-app/tests/test_pylink_conformance.py`
drives each one on a real host) and proposes richer ones. Port types are
`specto_core.ports`; value shapes as a script sees them are `python_links.value_json`.

## What goes out today

| View | Port (type) | Carries | Goes out when |
| --- | --- | --- | --- |
| file_tree | `selected_files` (files) | `{root, paths}`: the picked rows, folders expanded to their files, with their stat | the user picks, a received `selection` lands, the data changes |
| gallery | `selection` (selection) | `{keys: [key], last}`: the picked item | a click or a key step |
| gallery | `time` (time) | the picked frame's time on its clock | a click on a frame that has a time |
| image | `selection` | the item shown | a footer step |
| video | `time` (time) | seconds on its timeline (`stamp` from a sidecar, else `video`) | play, a step, a scrub, a received time landing |
| video | `selection` | the video shown, by key | it shows another item |
| json | `content` (content) | `{root, entries: [{key, path, format, data, meta, error}]}`: the shown file parsed | the shown item changes |
| json | `selection` | the item shown | a footer step |
| markdown | `content`, `selection` | the shown note; its key | a wikilink, a footer step |
| map | `selection` | `{keys: ["<entry key>#<feature index>"]}` | a feature click |
| 3d | `camera` (camera) | `{position, target, up, fov_y, near, far}`, Y up | the user navigates (120 ms throttle) |
| 3d | `selection` | `"<item>#<point index>"` or the item | a pick |
| html, preview | `selection` | the page or step shown | a pick inside, a footer step |
| pdf | none | | |
| usd | `selection` | `"<stage key>#<prim path>"` | a viewport or outliner pick |
| usd | `content` | the selected prim's resolved properties | the selection changes |
| usd | `time` | seconds on the `usd` timeline | a scrub, a step, play, a received time landing |
| usd | `camera` | the 3d view's shape and frame (#176) | the user navigates (120 ms throttle) |

No view has a `collection` or a `files` output besides the file tree's picks, and no view
has a `files` input; a Python link can still build a whole `collection` for any view's
`data` (an intaker-grade one, see contracts section 6).

## Done in #176

- **usd `camera`, in and out.** Declared since #132 and in the usd design ("the host
  emits camera throttled, as 3d does; a camera arriving on the input drives the same
  model and is not echoed"), but the host did neither: a link to or from it did nothing.
  Now a received `CameraValue` drives the navigation (taken once the stage opens if it
  came first) and the user's navigation goes out at the 3d page's 120 ms rate. The 3d and
  usd views can share a viewpoint, and a script can place either camera.
- **Bare seconds land on the target's own timeline.** Not a new port: a Python link's
  number now names no timeline (`OWN_TIMELINE`), which the video, the usd stage and a
  clocked gallery take as their own (it was the `stamp` clock, which a usd stage and a
  sidecar-less video ignored).

## Proposed, not built

Each fits an existing port type unless it says otherwise; none is cheap enough to be
"clearly useful" without a use case driving its shape.

| View | Proposed output | Type | Why not now |
| --- | --- | --- | --- |
| file_tree | `selection`: the picked rows as keys (folders unexpanded) | selection | #168 chose no selection output so a received pick is never republished; a script reading `selected_files` can take the folder from the paths. Revisit with a two-way tree-to-tree follow. |
| file_tree | `folder`: the folder of the last pick (or the open folder), `{root, paths: null}` | files | "current folder" needs a definition (last pick, last expanded, or the scrolled-to row). |
| gallery | `items`: the collection as sorted and filtered by its pills | collection | A script already gets the source's collection as `items`; worth it once a view takes another's order (a second gallery mirroring the first). |
| gallery | `visible`: the keys on screen | selection | Changes on every scroll: per-pick links would run on every frame of a fling; needs a settle (like the camera's throttle) first. |
| map | `viewport`: `{west, south, east, north, zoom}` | none fits | A new port type (bounds), or `content` with one entry; would let a script load only the GeoJSON in view. |
| map | `hovered`: the feature under the pointer | selection | The host already keeps it (`snapshot().hovered`); hover is noisy for per-pick runs. |
| video | `frame`: the frame index on `index` | time | `time` on the `index` timeline already exists through `time_map`. |
| usd | `time` at load: the stage's start time once it opens | time | The video does not emit at load either (nothing played yet); keep the two players alike. |
| usd | `stage`: the loaded stage's info (`bounds`, `up_axis`, cameras, time range) | content | Useful for a script that frames or places things; one entry of the loaded event. |
| 3d | `bounds`: the scene's bounds | content | As usd `stage`. |
| pdf | `selection`: `"<item>#page=<n>"` | selection | The pdf has no output at all; a page out would let notes follow the page read. |
| json | `pointer`: the JSON pointer clicked in the tree | selection | The page does not report clicks inside the tree yet. |
