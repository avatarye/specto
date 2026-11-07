---
status: accepted
date: 2026-11-07
---

# A robot is a `urdf` view: Babylon.js renders it, Python parses it and finds its meshes

Task SPEC-43 (design approved by the owner on 2026-11-07; slice 1 is SPEC-43.1, the Python side, and SPEC-43.2, the page). Builds on ADR 0014 (ports), ADR 0020 (view roles), ADR 0023 (dedicated views), ADR 0025 (quotas) and ADR 0028 (STL, OBJ and DAE in the 3d view, SPEC-42).

## Context

Robot data in a project comes with its description: a URDF naming its links, joints, limits, colliders and meshes, the meshes usually behind `package://<name>/` paths that only a ROS workspace resolves. Users want to see the robot as modelled, move its joints, check its limits and its collision shapes, and later follow a joint track at the global clock's time. Three routes were weighed: a Babylon.js page beside the 3d view, a conversion to USD shown in the usd view, or both.

## Decision

1. **Babylon.js, no USD route, now or later.** A robot viewer is for kinematics, limits and collision; a web page does all three for the cost of a 3d view (`ViewCost(web_views=1, viewports3d=1)`), while the usd view would charge every robot the one RTX view a canvas has and make joint motion the hardest part (the usd view is read-only). The page reuses the 3d view's HDRIs, view modes, `viewnav.js` and side panels; its manifest lives in the app beside the 3d view's.
2. **Its own view type, `urdf`, role viewer, never the root view.** ADR 0020 allows a new type for typed ports a viewer needs: `data` (`.urdf` items, kind `urdf`, alias `@urdf`), `camera` (the 3d view's `CameraValue`) and `selection` (`<item key>#<link name>`) in; `camera`, `selection` and `content` (the selected link's or joint's properties) out. The `joints` input (a collection of joint tracks) and the `time` input come with SPEC-43.3, which feeds them; a port nothing drives is not declared. A double-click on a `.urdf` opens a dedicated `urdf` view (ADR 0023: the narrowest view type accepting the kind).
3. **Python parses with the standard library; the page does the forward kinematics.** The host parses the shown file on the job manager into a small JSON model; a joint move is then one small `pose` message, never a re-parse. The parser is Specto's own, on `xml.etree` (`specto_readers.urdf.parse_urdf`): yourdfpy, the maintained Python URDF parser, needs lxml, whose binary wheels statically bundle libiconv under the LGPL-2.1, and the runtime takes no GPL or LGPL code. A URDF is a small XML format, so the cost is a few hundred lines with their tests, and the model comes out exact: `rpy` as written (yourdfpy keeps only 4x4 matrices), a visual's named material resolved from the robot's global one, numbers coerced strictly with errors naming the element, mimic joints kept. A URDF never needs a DTD, so any document with a DOCTYPE or an entity declaration is refused before it is parsed (no entity expansion); a `package.xml` is read the same way, and neither is read past 64 MB.
4. **Specto's own package resolver, no prefix stripping, no network.** A mesh or texture path resolves through `params.package_paths` (`{name: folder}`, or folders each searched for the package), then the nearest `package.xml` naming the package (up from the URDF, then the project's folders, bounded), then `ROS_PACKAGE_PATH` and `AMENT_PREFIX_PATH` (`share/<name>`); `file://`, absolute and relative paths resolve directly, relative ones next to the URDF. A guess by stripping prefixes (yourdfpy's `filename_handler_magic`) is never made: a wrong file picked silently is worse than a placeholder. `http(s)://` is never fetched (the offline rule). `PackageResolver.folder(name)` is the same lookup for xacro's `$(find)` (SPEC-43.3), picklable with its resolver.
5. **What is not found is said, not hidden, and the host owns the health dot.** A missing mesh keeps its geometry with no URL and the page draws a placeholder (the link's collision primitive, else a small magenta wire box); the model's `missing` lists each with its link, kind (`visual`, `collision`, or `texture` for a texture not found or not an image), URI and cause, `snap` reports them, and health turns `warn` (amber, a new health status) with one note per cause, never per file ("3 meshes missing: package `gripper` not found; set `params.package_paths`"; a file missing from a package found names the step that found it, "not in package `arm` (found by package.xml)"). The page sets only `loading` while its meshes load, then reports `loaded`; the meshes it could not read join the notes. `warn` also counts `params.joints` names the robot does not have.
6. **The meshes are served as extra roles of the robot's item, and nothing else is.** The host serves the files the resolver found as `.m0`, `.m1`, ... of the shown item, under the link that delivered it (`specto://items/<link>/<key>/m<n>/<file name>`, the file's name last so an OBJ's `.mtl` and textures resolve next to it), through a resolver first in the web runtime's chain; the page reads them under its `read:link` grant like any part. A URDF's references can point outside the project (a package found elsewhere, an absolute path, `params.package_paths` from a canvas someone else wrote), so files outside the project can be served, limited to these: a mesh only as an STL, OBJ, glTF or GLB, a texture only as an image, each a regular file of at most 512 MB, nothing out of a package folder (`package://pkg/../..` is refused), and next to a mesh only an OBJ's `.mtl` or a glTF's `.bin` and their images (`side_allowed`). A key file, a config file or a folder named like a mesh is listed missing, never served. A DAE will go as the GLB that ADR 0028's `dae.prepare` returns, which owns its freshness rule and cache fallback; until that is on main a DAE is listed missing ("DAE needs the SPEC-42 converter") and drawn as a placeholder.
7. **The pose lives in `params.joints`, in URDF units.** `{joint name: position}` in radians and metres; the sliders show degrees and millimetres. The host sends every actuated joint (revolute, continuous, prismatic, not mimic), its value clamped into its limits (a continuous joint wraps on the page), else 0. A slider release and Reset pose send the full map, which replaces `params.joints` in one user submit through the host, so the page holds no `params:joints` grant. Which pose shows: a joint track at the time input first (SPEC-43.3), then `params.joints`, then the zero pose (`pose.source`: `track`, `params`, `zero`). A track shows its nearest sample, never interpolated, and locks the sliders while it drives them.
8. **Up is Z by default** (REP 103), switchable among the six signed axes (`params.up`); the camera mode is free (the 3d view's) or orbit (left-drag orbits the pivot, right-drag and the wheel dolly toward it); the ground grid is on by default; the view-mode pill (`lit`, `unlit`, `wireframe`, `collision`, `visual_collision`) sits in the footer as in the usd and 3d views. The page writes these params itself through `Specto.setParams`, one user submit each, as the 3d view's page does.
9. **Selection.** `params.selected` is the shown item's key (which robot), as in the gallery and the image view. The selected links are `params.selected_links` (names, the last picked last), written by the page; the host sends them out on `selection` as `<item key>#<link>` and the last one's (or the joint's) properties on `content`.
10. **xacro expands only for trusted content** (SPEC-43.3): its `${...}` is a Python `eval` with dunder names refused but `type` and `vars` reachable, not a sandbox, so it waits for the approval ADR 0006 gives a file; a plain URDF always opens.

### The host and page contract

All lengths are metres, angles radians, colours linear RGBA 0..1. Either side changes it only with the other, updating this section and both sides' tests together.

The model (`model {generation, model, environments}`, Python to page; `model: null` when the file cannot be read; `environments` is the 3d view's bundled HDRIs, `scene3d_view.environment_entries()`):

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
  "missing": [ {"link": str, "kind": "visual"|"collision"|"texture", "uri": str, "cause": str} ] }
G = {"type": "box", "size": [3]} | {"type": "cylinder", "radius": f, "length": f}
  | {"type": "sphere", "radius": f}
  | {"type": "mesh", "url": str|null, "ext": str|null, "scale": [3], "uri": str}
```

`ext` names the page's loader (`stl`, `obj`, `glb`, `gltf`; null for a format the page has no loader for); a `url` is null when the mesh is missing; `uri` is the URDF's own reference, for the properties panel.

- Python to page: `model`; `pose {joints: {name: value}, source, track?}` (`track`: a source name such as `/joint_states` while a joint track drives the robot); `params` (the view's params over the manifest's defaults, without `joints` and `package_paths`); `step {index, count}`; `render {running}`; the `camera` and `selection` ports (never echoed).
- Page to Python: `select {link|null, joint|null, add}` (a joint selects its child link); `joints {name: value}` (the full map, on a slider release or Reset pose); `frame {link|null}` (a double-click; the page frames, the host notes it); `loaded {ms, meshes, failed: [{link, kind, url, error}]}`; `step {delta}`. The camera goes out as the page's port emit (`emit:camera`, throttled); the VIEW panel's, footer's and side panels' params and `selected_links` as its `setParams` writes (`params:<key>` grants).

## Consequences

- No new runtime dependency: the parser is the standard library's.
- Health gains `warn` (theme colour `warn`, the notices' amber). Other views may use it for "works, but something is off".
- The 3d view keeps its role as a scene of many items; a robot inside a 3d scene, posed by TF, is later work, as are the gallery thumbnail and the preview renderer.
- `canvas.open_file` and a double-click open a `.urdf` in its own view; Ctrl+double-click falls back to it, since the preview has no urdf renderer yet.

## Considered options

- URDF to USD (urdf-usd-converter) in the usd view: rejected (decision 1): the RTX quota, read-only stages and an alpha converter.
- A `3d` view mode instead of a type: rejected; the joints, time and link-selection ports would sit on a scene of many items.
- yourdfpy (MIT, maintained) in parse-only mode: rejected (decision 3) for lxml's bundled LGPL-2.1 iconv; its `trimesh[easy]` would also need a dependency override. urdfpy (no release since 2020), urchin (pulls a renderer, networkx and scipy) and urdf_parser_py (last release 2021, resolves nothing): rejected earlier.
- Forward kinematics in Python, a transform per link per tick: rejected; the page already holds the scene graph, and a pose is a handful of numbers.
