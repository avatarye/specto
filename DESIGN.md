# Specto design (draft, 2026-10-12)

Status: draft from the design discussion. Open to refinement; nothing is built yet.

## Vision: agentic GUI

Specto is where the user explores agentic GUI: the app has no predefined shape. The
GUI is formed on the fly by an agent from the user's requirements, out of a small
vocabulary of composable parts. Data viewing is the domain that grounds it.

What follows from that:

- An empty canvas is the default. There is no built-in main window; conventional
  layouts are just recipes.
- The vocabulary is small and fully self-described: panel types, controls, intake
  aliases, channels, link transforms, all listed in `api-info.json` plus a
  generated "vocabulary" document an agent reads before composing.
- Controls are parts too, not only viewers: a declarative control panel (a JSON
  spec of sliders, toggles, pickers, buttons) bound to channel values, parameters
  or commands (FiftyOne's schema-to-form pattern). An agent can give the user a
  time-offset slider or a "next run" button without writing code.
- Agent and user share one surface: whatever the agent builds, the user can
  rearrange, edit or delete by hand on the canvas, and the agent sees those edits in
  the session document.
- Every agent turn is one undoable transaction, and the canvas highlights what it
  changed, so the user can review a composition the way they review a code diff.
- Composition is verified: the agent checks its work with `snap` and the state
  outline before handing it over.
- Compositions that prove useful are saved as recipes; the library is how the app
  "grows" its shapes over time. Throwaway compositions are fine.
- The agent is external first (Claude Code or any MCP client driving the CLI/MCP
  surface). An in-app prompt panel that hands requests to an agent comes later.

## Problem

Viewing agent-produced data needs a different tool per type: image sets, video,
3D (USD, glTF, point clouds, splats), JSON, GeoJSON, source code, markdown. The
existing VS Code extensions are weak (scrollbar-panned galleries, one GeoJSON at a
time, clunky video). Editing is rare; the user mostly views. Specto is one desktop
viewer for all of it, with drag-and-drop of files into any view.

Single-file viewing is not enough: agent output is collections (runs, frame
sequences, tiles, topic streams). Specto loads collections, not files.

## Principles

- Reuse existing engines and widgets; do not write a renderer.
- Viewing first. Editing is out of scope except where free (e.g. Monaco read-only off).
- Data flows intake -> reader -> view. An intaker finds and screens files into a
  collection; readers decode items; views show them. Any intaker can feed any view.
- The workspace is an endless canvas. Panels dock into groups that look like a
  conventional layout, but every panel lives on the canvas and can be arranged freely.
- Multiple projects, switched instantly; each has its own canvases, intakers and recipes.
- Viewports that render elsewhere (ovrtx, Blender, later Godot) plug in through one
  "frame source" interface.
- Three platforms. Windows and Linux are primary (ovrtx is Windows and Linux
  only); macOS is supported without the NVIDIA-only backends. See Platforms.

## Stack

- Shell: PySide6 (Qt 6) with a Qt Quick canvas: the normal view and the overworld
  are one scene, so the transition animates and panels share one item model. The
  canvas and render items are C++/QML; Python stays out of per-frame paths.
- Web content through `WebEngineView` items (Monaco, markdown, MapLibre, Babylon.js).
- Native renderers (Hydra Storm, mpv, ovrtx, Blender) render into textures shown by
  Qt Quick items (`QQuickRhiItem` / `QQuickFramebufferObject`); this is the frame
  source pattern applied everywhere.
- Python is the working language; hot paths may move to C++ later.

## Views

| Data | Component | Notes |
|---|---|---|
| Image sets | Qt Quick gallery | Drag-to-pan, wheel-zoom canvas, lazy thumbnails; no scrollbars |
| Video | mpv via libmpv render API into a Qt Quick item (`python-mpv`) | Frame step, A-B loop, speed, side-by-side compare. Qt WebEngine wheels lack H.264/AAC, so not the web pane |
| JSON | Monaco (web pane) | Folding, outline, search |
| Source code | Monaco (web pane) | VS Code look, read-only, minimap; diff editor for reviewing agent changes |
| Markdown | markdown-it + github-markdown-css (web pane) | Mermaid, KaTeX, Obsidian wikilinks/callouts via plugins; scroll sync with Monaco source |
| GeoJSON / maps | MapLibre GL JS (web pane) | One layer per dropped file, toggles, fit-to-extent. PyQGIS canvas if real GIS is needed |
| USD | Hydra Storm (`UsdImagingGLEngine`) rendered into a Qt Quick texture item; usdview's `StageView` as reference code | Needs a full OpenUSD build (pip `usd-core` has no imaging). `StageView` itself is a Widget, so it is a reference, not a drop-in |
| USD photoreal / sensors / splats | ovstage + ovrtx frame source | ovrtx renders `ParticleField` splats; Windows/Linux NVIDIA only; proprietary pre-release APIs |
| glTF / PLY / OBJ / STL, Gaussian splats | Babylon.js (web pane) | Default 3D view: glTF, splats (.ply/.splat/.spz), WebGPU, same channel bridge as other web panels. Binary data served through a custom URL scheme (`QWebEngineUrlSchemeHandler`), not `QWebChannel` JSON |
| Point clouds / LiDAR | Babylon.js up to tens of millions of points; octree streaming (COPC) beyond that | Large-LiDAR LOD is the risk: the mature COPC/Potree viewers are three.js-based, so a separate LiDAR panel may use three.js. PyVista (`pyvistaqt`) only as an optional numpy-heavy analysis view. CloudCompare as design reference |
| ROS bags (`.bag`, `.db3`, MCAP) | `specto-reader-rosbag` on `rosbags.AnyReader` (Apache-2.0, no ROS install) | Topics map to Specto datasets and channels: images to gallery/video, `PointCloud2` to 3D, `NavSatFix` to map, `/tf` to frame graph, stamps to time; a JSON message inspector for other types. Optional later: Lichtblick (MPL-2.0) fallback panel. See docs/landscape.md |
| Blender scenes | Blender frame source | Add-on renders `gpu.offscreen` `draw_view3d` to shared memory; input forwarded over a socket. Window embedding proved tricky for input |

## Frame source interface (sketch)

A viewport pane that accepts any producer of frames: `load(path)`, `set_camera(...)`,
`resize(w, h)`, `render() -> frame`, `pick(x, y)`, input events. Start with CPU frames
(`QImage`); add GPU paths later (CUDA-GL interop for ovrtx). In-process renderers
(Hydra Storm, mpv) skip the CPU copy and render straight into the item's texture.

## Intake: from sources to collections

Intake is its own module (`specto-intake`, no Qt). It turns "where the data is" into
a collection that readers and views consume.

### Intake spec

- Sources: a file, files, a dir, dirs or globs; recursion depth; follow links or not.
- Screener: ordered include/exclude rules, like `.gitignore`. Users write globs,
  extension sets, type aliases (`@images`, `@pointclouds`, `@geo`) or raw regex;
  everything compiles to regex matched on the POSIX relative path. Optional
  predicates: size, mtime, content sniff (magic bytes) when extensions lie.
- Keys: named regex capture groups give each item a key, e.g.
  `(?P<run>run_\w+)/frame_(?P<frame>\d+)`. Keys drive sorting, grouping and linking.
- Grouping: files sharing a key form one item (`frame_001.png` + `frame_001.json` +
  `frame_001.las` is one multi-part item).
- Sort: natural order, by a key field (numeric aware), or by mtime.
- Containers: archives and bags expand into virtual entries
  (`drive.mcap#/camera/image`), so the screener works inside them too.
- Watch: optional live mode. New files that agents write appear in the collection
  (debounced); views update in place.

### Collections

- A collection is an ordered, keyed list of items; each item has its parts, detected
  type and metadata. Collections are datasets in the session document
  (`col:frames`), addressable and shareable by many views.
- Any collection can be assigned to any view. The view asks the reader registry, per
  item, for a reader that produces data it accepts. Items a view cannot show are
  listed as skipped with a reason, never silently dropped.
- Keys make the item channel cross-collection: two galleries fed from `run_A/` and
  `run_B/` linked on `frame` is A/B compare across directories with no special code.
- Drag-and-drop onto a view creates an intaker from the dropped paths plus that
  view's default screener.

### Commands (examples)

`specto intake new frames --dir runs/ --include '@images' --key 'run_(?P<run>\w+)/frame_(?P<frame>\d+)' --watch`,
`specto intake ls frames`, `specto intake explain frames <path>` (which rule matched or
excluded it), `specto view new gallery --from col:frames`.

## Canvas workspace: normal view + overworld

Not a free-zoom canvas. Two levels:

- Normal view: 1:1, pixel-perfect and live. It looks like a conventional app: the
  widgets inside the current window frame, docked into groups (split/tab layouts).
- Overworld (layout view): zoomed out, all content rendering paused. Each widget
  becomes a simplified panel (title, view type, bound collection, link ports) for
  arranging and docking. Window frames show exactly what each window displays.
  Arrange, then drop back into the normal view.

### Window frames

- A window frame is a rectangle on the canvas sized to a real window; the normal
  view of that window shows exactly the frame's contents at 1:1.
- Several frames give several window views: bind each to its own OS window
  (multi-monitor), or switch between them in one window like virtual desktops.
- Frames do not overlap, so every widget has at most one live home.
- Widgets outside every frame are parked: kept in the session, not rendered.
- Resizing a window resizes its frame; docked groups inside reflow to fit.

### In the overworld

- Drag widgets in and out of frames, dock and undock them, move a whole group.
- Link wires: channels between widgets are drawn as wires between link ports;
  dragging a wire creates a link. The link graph is visible, not hidden in menus.
- Labelled regions organise the canvas (e.g. "run 7 review"); parked widgets live
  there.
- An agent's latest changes are highlighted here (see Vision).

### Rendering and performance

- Only widgets inside a frame that is on screen render. Everything else is paused
  and releases GPU resources where it can, keeping its state.
- Entering the overworld captures a snapshot of each live widget for its panel;
  leaving it resumes them. No scaled live rendering is needed, so web panels, text,
  Storm and mpv are always at native resolution when they render.
- The transition can animate (scaling snapshots), purely cosmetic.

### Session document and commands

- The session holds widgets (position, size, view state), groups (a split tree per
  group), window frames, regions and links.
- Commands: `canvas.place`, `canvas.dock`, `canvas.undock`, `canvas.park`,
  `frame.new`, `frame.bind` (to an OS window), `frame.show`, `view.overworld`,
  `view.normal`. `specto snap` takes a widget, a group or a frame; in the overworld
  it returns the layout diagram, so an agent can check its arrangement.

## Projects

- A project is a folder with `.specto/`: `project.toml` (name, defaults),
  `canvases/*.json` (sessions), `intake/*.toml`, `recipes/`. All diffable and
  git-friendly.
- Caches (thumbnails, file indexes, decoded data) live in the platform cache dir keyed
  by project id, never inside the project folder.
- A registry of known projects lives in the user data dir, with a recent list.
- One app process holds several open projects. Switching (palette or project rail)
  swaps the canvas instantly; inactive projects suspend live views and release GPU
  resources, keeping their state, and resume on return. A project can also pop out
  into its own window.
- Addresses are project-scoped (`proj:robotA/view:v3`). The CLI picks the project
  from `--project` or, like git, the nearest `.specto/` above the current directory.

## Platforms

| | Windows | Linux | macOS |
|---|---|---|---|
| Tier | primary | primary | secondary |
| Hydra Storm | OpenGL | OpenGL | Metal |
| ovstage + ovrtx | yes (NVIDIA RTX) | yes (NVIDIA RTX) | no |
| Blender frame source | yes | yes | yes |

Rules that follow:

- Backends are capability-gated plugins: a view or recipe that needs ovrtx says
  so, and degrades or refuses cleanly where it is missing. No platform `if`s in core.
- Linux means Wayland as well as X11. No native window embedding (it fails on
  Wayland): mpv uses libmpv's render API into a GL context, not `wid`; Blender
  stays an offscreen frame source.
- Command API transport is `QLocalServer`: a named pipe on Windows, a Unix socket
  on Linux and macOS, one code path.
- Paths via `platformdirs` (recipe library, thumbnail cache, config).
- CI on all three; GPU-backend tests run only on machines that have the backend.

## Architecture: modules and the common CLI/SDK layer

### One command schema, many surfaces

Every operation is defined once, as a command with a JSON Schema for its arguments
and result. From that single registry Specto generates:

- the Python SDK methods (typed),
- the `specto` CLI subcommands (`--json` output for agents),
- the MCP tools,
- TypeScript types for web panels,
- the command reference docs.

Nothing is hand-wired twice. A feature is a command first; the GUI calls the same
command, never a private path. Adding a command adds it to every surface at once.

### Modules (uv workspace monorepo)

| Package | Depends on | Role |
|---|---|---|
| `specto-core` | nothing Qt | Session document, addressing, command registry, channels and links, frame graph, recipe model, plugin registry, event bus. Headless and unit-testable |
| `specto-intake` | core | Sources, screener, keys, grouping, containers, watch; produces collections. No Qt |
| `specto-sdk` | core schemas | Public Python client. Same API in three modes: remote (socket to a running app), in-process (scripts and `build.py` run by the app), headless (starts a core with no GUI, for tests and batch jobs) |
| `specto-cli` | sdk | Thin; subcommands generated from the registry |
| `specto-mcp` | sdk | MCP server generated from the registry |
| `specto-app` | core, intake, Qt | GUI shell: Qt Quick canvas, docking groups, project switching, view hosting, `QLocalServer` command endpoint |
| `specto-web` (TS) | command schemas | Channel bridge and command client for web panels, built-in and agent-written |
| plugins | core plugin API only | Readers, views, frame sources, channel types, transforms |

Dependency rule: arrows point toward core. Plugins see only the core plugin API,
never app internals, so a plugin cannot break when the shell changes.

### Plugins

- Discovered through Python entry points with a manifest (napari's npe2 pattern):
  what it contributes (readers, view types, frame sources, channels, transforms,
  intake type aliases, container expanders, commands), what it needs (capabilities such as `ovrtx`, `cuda`), API version range.
- Built-ins ship as plugins too, in the same form: `specto-view-web` (Monaco,
  markdown, MapLibre, Babylon.js), `specto-view-gallery`, `specto-view-video`,
  `specto-view-usd`, `specto-source-ovrtx`, `specto-source-blender`.
- Plugins may register their own commands; they appear in the CLI, SDK and MCP
  automatically.
- A capability a machine lacks disables that plugin cleanly (see Platforms).

### Versioning

The command schema carries a semantic version. Plugins, recipes and SDK clients
declare the range they support; the app refuses incompatible ones with a clear
message instead of failing at runtime.

### Headless

`specto-core` plus plugins run without a GUI for batch work: thumbnail caches,
`specto recipe check`, conversions, CI. Views that render (web, Storm, frame
sources) use offscreen rendering, so `specto snap` works headless too.

## Decisions adopted from the landscape survey

See docs/landscape.md for sources and the patterns behind each.

- Command registry: pydantic models per command, registered by a decorator carrying
  `name`, `since`, `deprecated_since`, `undoable`, `read_only`, `destructive`. Export
  one `api-info.json` (Neovim style, with `api_level` / `api_compatible`). CLI built
  dynamically with click; MCP tools registered in a loop with FastMCP; TS types via
  `json-schema-to-typescript`; a generated typed Python SDK.
- One transport: JSON-RPC over `QLocalServer`. CLI, SDK, MCP and the `QWebChannel`
  bridge all reach the same `call(name, args)` and `list_commands`.
- Undo: commands with `undo()` go on a `QUndoStack`; ones without stay off it (Kit).
  A transaction primitive makes one agent batch one undo step.
- State vs journal: the session document is the snapshot; an append-only command
  journal is replayable and can be promoted into a recipe (ParaView's split, done
  right by recording commands rather than tracing).
- Layout has three layers: automatic (from data), default (from a recipe or code),
  user edits. Agents patch layouts; applying a whole layout is explicit, with a
  visible notice (Rerun's warning).
- Time has a cursor and a separate hover/preview time (Foxglove).
- Selections are named predicates in the session document (glue / Vega-Lite); a view
  draws what it can map and silently skips the rest; the brushed view does not
  filter itself (crossfilter).
- Reader dispatch: readers declare `can_handle(path)` and a priority (QuickLook
  `IViewer`); manifests are readable without importing plugin code (npe2).
- MCP surface stays small: about 10-20 coarse tools plus generic `call` and
  `list_commands`, with read-only/destructive annotations. Agents get a cheap state
  outline first, detailed state on request, `snap` with structured metadata (camera,
  layers, selection), and `watch` events with sequence numbers for resume. Every
  tool addresses a session explicitly, never "the active tab".
- Errors say how to fix the call; no silent drops (CARTO's deck.gl lesson).
- Trust: a per-user, HMAC-signed trust database of content hashes (Jupyter);
  locally created recipes are trusted. Untrusted sessions open in Restricted Mode:
  data and layout load; recipe code, web-panel JS and expression fields do not run.
  Web panels get only a capability-scoped command bridge, no file or network access
  by default. No "execute arbitrary code" tool by default; if enabled, it is logged.
- Later: MCP Apps (`ui://` HTML) to show Specto views inside agent chat clients.

## Control plane: state-driven, agent-first

Each canvas is one session document: collections, views, canvas layout, links. The GUI,
the `specto` CLI and agents are all clients that change it through one command bus.

- Addressable objects with typed properties: `col:lidar1`, `view:v3`,
  `view:v3.camera`, `link:l2`, `group:g1`, all under `proj:<name>/` (close to
  Blender's RNA paths plus operators).
- Every action is a named command with JSON args, whether from a click, the CLI or
  an agent. Gives undo, a replayable journal and macros.
- The app runs a local server (named pipe / localhost). The CLI is a thin client;
  an MCP wrapper over the same API serves agents.
- Agents can see: `specto snap <view>` (screenshot), `specto state get`,
  `specto watch` (event stream).
- Layout is the canvas (see Canvas workspace) inside the session document. Sessions
  save to a diffable file; `specto open <session>` loads one.
- Collections and views are separate: one collection in many views, many
  collections (layers) in one view.

Prior art to study: Rerun blueprints (layout as data, driven from code), Foxglove
panels (shared timeline, transform tree).

## Links: channels, not pairs

No feature is pairwise. Views publish and subscribe to typed channels; a link is
a channel, its member views, a direction (two-way or follow) and a transform.

- Channels: time, 3D camera pose, map view (lon/lat/zoom/bearing), 2D pan/zoom,
  selection (entity ids), cursor (world point), item (by intake key, so it works
  across collections).
- Each change carries its origin; echoes are dropped so links cannot loop.
- A/B compare is not built in: it is two views sharing camera + time (+ pan/zoom)
  links in a compare layout preset (side-by-side, swipe, overlay, difference).
  Any two views pair the same way.

### Frames

Linking lidar, 3D and maps needs a coordinate-frame graph: each dataset declares
its frame (CRS by EPSG code, local ENU origin, sensor-to-world pose over time).
Link transforms convert channel values between frames (pyproj for CRS). Examples:
time ties lidar sweeps to video frames; the 3D camera footprint draws on the map
and panning the map recentres 3D; lassoing a GeoJSON polygon selects the lidar
points inside it.

## Agent-built views

Built-in views use the same plugin API agents use, so agent-made views are
first-class: linkable, scriptable, addressable. Three tiers, safest first:

1. Compose: write a session/layout document from existing views and links. No code.
2. Script: a Python client that drives the command API (build layout, load data,
   set links, step time). Runs out of process; a bad script cannot crash the app.
3. Custom view: a new view type.
   - Web panel (default): HTML/JS (D3, Plotly, three.js) talking to channels over a
     `QWebChannel` bridge, sandboxed by Chromium.
   - Python/Qt view: only when native speed or GPU access is needed (e.g. a frame source).

View contract: accepted dataset types, channels subscribed and published, render
target (web, Qt, frame source). Specto handles linking, layout and persistence.

Agent loop: write the view, Specto hot-reloads on save, `specto snap` to look,
`specto watch` to read events, iterate.

## Recipes: stored viewport setups

Agent-authored logic for a special viewport setup is saved as a recipe: a
reusable, parameterized package that rebuilds the setup on any matching data.

### Layout of a recipe

A recipe is a folder:

- `recipe.toml`: manifest.
  - name, version, description.
  - Roles (parameters): typed collection slots, e.g. `lidar: pointcloud`,
    `roads: geojson[]`, `video: video?`, each with a default intake spec (screener
    and keys), plus scalar params (e.g. `time_offset: float`).
  - Requirements: view types used, Specto API version.
  - Entry points: `layout.json`, optional `build.py`, `views/*`, `transforms.py`.
  - Provenance: author (user or agent session id), a short intent summary, dates.
- `layout.json`: declarative views, canvas groups and links, bound to roles, not paths.
- `build.py` (optional): script-tier logic for setups that cannot be declarative.
- `views/` (optional): custom view types the recipe ships (web panels, Qt views).
- `transforms.py` (optional): link transforms and derived channels (e.g. a
  lidar-to-video time offset, a custom projection).
- `checks/` (optional): sample bindings plus golden snapshots, so an agent can
  verify the recipe still renders correctly after edits.

### Lifecycle

- Capture: `specto recipe save <name> --from-session` freezes the current layout
  and links (built by hand, by CLI or by an agent) into `layout.json`, turning
  concrete datasets into roles.
- Freeze over code: when `build.py` produces a layout, Specto can snapshot the
  result from its command journal into `layout.json`, so most recipes end up
  code-free and diffable. Code stays only for live logic (custom views, transforms).
- Apply: `specto recipe apply <name> --bind lidar=run7/ --bind roads=maps/`. Each
  binding is a source; the role's default screener and keys turn it into a
  collection (overridable per binding). Dropping files or folders onto a recipe
  auto-binds roles by type, asking only when ambiguous. A recipe lands on the
  canvas as one docked group.
- Edit: agents edit recipe files directly; hot reload applies changes to open sessions.
- Discover: `specto recipe list|show|new|edit|diff|check|rm`.

### Storage and versioning

- User library: the platform data dir (`%APPDATA%/specto/recipes/` on Windows,
  XDG data dir on Linux, Application Support on macOS), its own git repo; every save is a commit, so `specto recipe diff` and rollback are git.
- Project recipes: `.specto/recipes/` inside a dataset or project repo, so a setup
  travels with its data. Project recipes shadow library recipes of the same name.
- Sessions reference recipes by name and version, not by copying them.

### Trust

Recipes with code (`build.py`, `views/`, `transforms.py`) run with the user's
permissions. Trust is per recipe content hash: a recipe the user has not trusted,
or one whose code changed since, loads its declarative parts only and asks before
running code. Pure-`layout.json` recipes need no trust.

## Licensing notes

- PySide6 / Qt: LGPL. Avoid Qt Quick 3D (GPL/commercial).
- Reuse permissive sources: OpenUSD usdviewq (modified Apache 2.0), napari (BSD-3),
  libf3d (BSD-3), Monaco (MIT), markdown-it (MIT), MapLibre (BSD).
- GPL projects (qimgv, CloudCompare, QGIS, ReText) are design references only.
- ovrtx / ovstage: NVIDIA proprietary.
- Name: PyPI `specto` is taken (dormant 0.1 audio package); publish as e.g.
  `specto-viewer` if ever needed.

## Open questions

- Canvas: since content never renders scaled, a Widgets shell (real widgets in the
  normal view, a `QGraphicsView` of snapshots in the overworld) is also possible and
  would let usdview's `StageView` drop in. Keep Qt Quick unless Storm-in-Quick
  proves painful.
- How many live 3D panels in visible frames fit the GPU budget.
- Storm into a Qt Quick texture: Qt Quick on Windows defaults to Direct3D 11, so the
  canvas either runs Qt Quick on OpenGL or Storm output is shared across APIs.
- Intake screener syntax: exact rule grammar, and whether type aliases come from
  readers (a reader declares the patterns it can handle).
- Reader plugin API shape.
- Session document format (JSON vs TOML) and its schema versioning.
- Channel value types and frame-transform API; how dataset poses are supplied.
- Recipe role type system: how specific (e.g. `pointcloud` vs `las`), and matching rules.
- Command API: is a localhost WebSocket needed beside `QLocalServer` (e.g. for
  remote or browser clients), and how is it authenticated?
- Does ovrtx coexist in-process with Qt's GL context, or run as a separate process?
- Video compare and frame-accurate scrubbing requirements.
- Thumbnail cache location and format.
- First milestone scope.
