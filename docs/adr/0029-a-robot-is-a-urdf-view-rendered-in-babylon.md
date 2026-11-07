---
status: accepted
date: 2026-11-07
---

# A robot is a `urdf` view: Babylon.js renders it, Python parses it and finds its meshes

Task SPEC-43 (design approved by the owner on 2026-11-07; slice 1 is SPEC-43.1, the Python side, and SPEC-43.2, the page). Builds on ADR 0014 (ports), ADR 0020 (view roles), ADR 0023 (dedicated views), ADR 0025 (quotas) and SPEC-42 (STL, OBJ and DAE in the 3d view).

## Context

Robot data in a project comes with its description: a URDF naming its links, joints, limits, colliders and meshes, the meshes usually behind `package://<name>/` paths that only a ROS workspace resolves. Users want to see the robot as modelled, move its joints, check its limits and its collision shapes, and later follow a joint track at the global clock's time. Three routes were weighed: a Babylon.js page beside the 3d view, a conversion to USD shown in the usd view, or both.

## Decision

1. **Babylon.js, no USD route, now or later.** A robot viewer is for kinematics, limits and collision; a web page does all three for the cost of a 3d view (`ViewCost(web_views=1, viewports3d=1)`), while the usd view would charge every robot the one RTX view a canvas has and make joint motion the hardest part (the usd view is read-only). The page reuses the 3d view's HDRIs, view modes, `viewnav.js` and side panels.
2. **Its own view type, `urdf`, role viewer, never the root view.** ADR 0020 allows a new type for typed ports a viewer needs: `data` (`.urdf` items, kind `urdf`, alias `@urdf`), `joints` (a collection of joint tracks, SPEC-43.3), `time`, `camera` (the 3d view's `CameraValue`), `selection` (`<item key>#<link name>`) in; `camera`, `selection` and `content` (the selected link's or joint's properties) out. A double-click on a `.urdf` opens a dedicated `urdf` view (ADR 0023: the narrowest view type accepting the kind).
3. **Python parses, the page does the forward kinematics.** The host parses the shown file on the job manager into a small JSON model; a joint move is then one small `pose` message, never a re-parse. The parser is yourdfpy (MIT) in parse-only mode (`build_scene_graph=False, load_meshes=False`), with plain `trimesh` (`[tool.uv] override-dependencies` drops yourdfpy's `trimesh[easy]`): no mesh is loaded in Python.
4. **Specto's own package resolver, no prefix stripping, no network.** A mesh or texture path resolves through `params.package_paths` (`{name: folder}`, or folders each searched for the package), then the nearest `package.xml` naming the package (up from the URDF, then the project's folders, bounded), then `ROS_PACKAGE_PATH` and `AMENT_PREFIX_PATH` (`share/<name>`); `file://`, absolute and relative paths resolve directly, relative ones next to the URDF. yourdfpy's own handler, which guesses by stripping prefixes, is not used: a wrong file picked silently is worse than a placeholder. `http(s)://` is never fetched (the offline rule).
5. **What is not found is said, not hidden.** A missing mesh keeps its geometry with no URL and the page draws a placeholder (the link's collision primitive, else a small magenta wire box); the model's `missing` lists each with its link, kind, URI and cause, `snap` reports them, and health turns `warn` (amber, a new health status) with one note per cause, a package not found naming `params.package_paths` as the fix. `warn` also counts `params.joints` names the robot does not have.
6. **The meshes are served as extra roles of the robot's item.** The host serves the files the resolver found as `.m0`, `.m1`, ... of the shown item, under the link that delivered it (`specto://items/<link>/<key>/m<n>/<file name>`, the file's name last so an OBJ's `.mtl` and textures resolve next to it), through a resolver first in the web runtime's chain; the page reads them under its `read:link` grant like any part. A DAE goes as SPEC-42's `<name>.dae.glb` beside it when that is as new as the DAE, else as the DAE (`ext: "dae"`).
7. **The pose lives in `params.joints`, in URDF units.** `{joint name: position}` in radians and metres; the sliders show degrees and millimetres. The host sends every actuated joint (revolute, continuous, prismatic, not mimic), its value clamped into its limits (a continuous joint wraps on the page), else 0; a slider release is one user submit through the host, so the page holds no `params:joints` grant. Which pose shows: a joint track at the time input first (SPEC-43.3), then `params.joints`, then the zero pose (`pose.source`: `track`, `params`, `zero`). A track shows its nearest sample, never interpolated, and locks the sliders while it drives them.
8. **Up is Z by default** (REP 103), switchable among the six signed axes (`params.up`); the ground grid is on by default; the view-mode pill (`lit`, `unlit`, `wireframe`, `collision`, `visual_collision`) sits in the footer as in the usd and 3d views.
9. **xacro expands only for trusted content** (SPEC-43.3): its `${...}` is a Python `eval` with dunder names refused but `type` and `vars` reachable, not a sandbox, so it waits for the approval ADR 0006 gives a file; a plain URDF always opens.

### The host and page contract

All lengths are metres, angles radians, colours linear RGBA 0..1. Either side changes it only with the other, updating this section and both sides' tests together.

The model (`model {generation, model}`, Python to page; `model: null` when the file cannot be read):

```
{ "name": str, "root": str,
  "links": [ { "name": str,
               "visuals":    [ {"origin": {"xyz": [3], "rpy": [3]}, "geometry": G,
                                "material": {"name": str|null, "color": [4]|null, "texture_url": str|null} | null} ],
               "collisions": [ {"origin": {"xyz": [3], "rpy": [3]}, "geometry": G} ],
               "inertial": {"origin": {...}, "mass": float,
                            "inertia": {"ixx","ixy","ixz","iyy","iyz","izz": float}} | null } ],
  "joints": [ { "name": str, "type": "revolute"|"continuous"|"prismatic"|"fixed"|"floating"|"planar",
                "parent": str, "child": str, "origin": {...}, "axis": [3],
                "limit": {"lower", "upper", "effort", "velocity": float} | null,
                "mimic": {"joint": str, "multiplier": float, "offset": float} | null,
                "dynamics": {"damping": float, "friction": float} | null } ],
  "missing": [ {"link": str, "kind": "visual"|"collision", "uri": str, "cause": str} ] }
G = {"type": "box", "size": [3]} | {"type": "cylinder", "radius": f, "length": f}
  | {"type": "sphere", "radius": f} | {"type": "mesh", "url": str|null, "ext": str|null, "scale": [3]}
```

`ext` names the page's loader (`stl`, `obj`, `glb`, `gltf`, `dae` until SPEC-42's sidecar exists); a `url` is null when the mesh is missing.

- Python to page: `model`; `pose {joints: {name: value}, source}`; `params` (the view's params over the manifest's defaults, without `joints` and `package_paths`); `step {index, count}`; `render {running}`; the `camera` and `selection` ports (never echoed).
- Page to Python: `select {link|null, joint|null, add}` (a joint selects its child link); `joints {name: value}` on a slider release; `camera` (throttled, a `CameraValue`; or `Specto.emit("camera")`); `panels {name: {collapsed, width}}`; `frame {link|null}` (a double-click; the page frames, the host notes it); `step {delta}`.

## Consequences

- A new runtime dependency set: yourdfpy, lxml, six and plain trimesh (`THIRD_PARTY_NOTICES.md` section 4). lxml's binary wheels bundle libiconv (LGPL-2.1): it is installed unmodified from PyPI and never redistributed by Specto, the footing of the PySide6 entry.
- Health gains `warn` (theme colour `warn`, the notices' amber). Other views may use it for "works, but something is off".
- The 3d view keeps its role as a scene of many items; a robot inside a 3d scene, posed by TF, is later work, as are the gallery thumbnail and the preview renderer.
- `canvas.open_file` and a double-click open a `.urdf` in its own view; Ctrl+double-click falls back to it, since the preview has no urdf renderer yet.

## Considered options

- URDF to USD (urdf-usd-converter) in the usd view: rejected (decision 1): the RTX quota, read-only stages and an alpha converter.
- A `3d` view mode instead of a type: rejected; the joints, time and link-selection ports would sit on a scene of many items.
- urdfpy (no release since 2020), urchin (pulls a renderer, networkx and scipy), urdf_parser_py (last release 2021, resolves nothing): rejected for yourdfpy.
- yourdfpy's `filename_handler_magic`: rejected (decision 4).
- Forward kinematics in Python, a transform per link per tick: rejected; the page already holds the scene graph, and a pose is a handful of numbers.
