# Specto high-level design (HLD), draft 2026-10-12

> **Superseded in part (2026-10-18, v2).** ADRs 0011-0017 (`docs/adr/`) override this
> document wherever they conflict, and `docs/contracts.md` describes what is built. In
> short: one canvas per project driven by one pydantic canvas document that agents write
> and submit (0012, 0013); views with typed ports connected by directed links, intakers
> being links, replacing channels, groups and docking (0014); links resolve at edit time
> off the GUI thread (0016); the prompt bar runs one Claude Code session per window
> (0015); the side panels, palette, changeset bar, controls/timeline/diff views,
> multi-window and the human composition panels of ADR 0007 are gone (0011, 0017); v1
> projects are refused, not migrated (0017). Sections 3.2 (document shape), 3.3
> (addresses beyond `canvas:`), 3.4 (transactions), 3.8 (channels), 5 (frames, groups,
> docking, shelf), 6 (locks on groups and frames) and the M-milestones read as history.

Status: first HLD. Supersedes the architecture parts of `DESIGN.md` where they conflict; resolves every finding in `docs/review/design-review.md` (disposition table in section 9). Nothing is built yet.

**TLDR**

- Shape stays as required: agentic GUI with no built-in main window, intake to any view, two-level canvas with window frames and link wires, multi-window, multi-project, one command schema for CLI/SDK/MCP, recipes, typed channel links, plugins, Qt. Several of these are phased, none removed.
- The three things the agent loop stands on are now pinned: one JSON canvas document with core-assigned stable ids and a `schema_version`; an address grammar that always names the canvas; transactions whose undo is a document patch inverse, so core needs no Qt and no per-command `undo()`.
- Execution model is explicit: GUI thread = Qt + Python + command bus; Qt Quick render thread = C++ only; decoders in a worker process pool over shared memory; ovrtx and Blender out of process; Storm and mpv are C++ items or they are not in-process at all.
- Shell is Qt Quick on the OpenGL RHI backend (Metal on macOS), confirmed or overturned by spike S1 before M1 code starts; the fallback (Widgets + `QGraphicsView` overworld) is designed in, not improvised.
- Trust is "the user clicked Run on this content hash", regardless of who wrote it; agent-written `build.py` always runs out of process.
- M1 proves the loop end to end on two cheap views; USD/Storm, LiDAR streaming, ROS bags, multi-project switching, in-window frame switching and recipes with code land in M2-M4.
- Pushback: in-window frame switching, several live projects in one process, and a git commit per recipe save each cost a subsystem; they are phased last and you are asked to confirm they still earn it.

## 1. Goals, non-goals, principles

**Goals**

- An external agent (Claude Code over MCP, or any client of the CLI/SDK) composes a useful viewer from a self-described vocabulary; the user reviews, edits and undoes that composition on the same surface; both see one document.
- Collections of agent-produced data (image sets, video, USD, point clouds, splats, GeoJSON, JSON, code, markdown, ROS bags) viewed in one desktop app, with any collection assignable to any view that can show it.
- Windows and Linux (X11 and Wayland) primary; macOS secondary without NVIDIA-only backends.

**Non-goals**

- Writing a renderer. Every 3D, video or map view wraps an existing engine.
- Editing data. Read-only everywhere except where an engine gives it for free.
- Multi-user, remote, or networked operation. One user, one machine, one app process per user session.
- A chat UI inside Specto in year one. The agent is external first.

**Principles**

1. A feature is a command first; the GUI calls the same command through the same bus.
2. The canvas document is the single source of truth; the GUI, agents and undo all react to document patches, never to each other.
3. Python owns control, C++ owns per-frame work. Nothing Python runs on the render thread.
4. Fail loud with a fix-it message; list what was skipped and why; never drop silently.
5. Human GUI underneath, agent on top. Specto is a complete human-usable desktop app on its own (project open, file browser, intake with live previews, view creation, docking, linking, undo and diff) with no CLI and no agent. The agent layer sits on top of it. Every human action goes through the same commands an agent calls, so both share one surface, one document and one undo history (user decision, 2026-10-13).
6. One noun per concept: **view** (not widget/panel/pane), **collection** (materialised items), **intake spec** (how a collection is found), **window frame** (canvas rectangle bound to an OS window), **space** (a coordinate frame; the graph of spaces is the frame graph).

## 2. System context and components

```text
            Claude Code / MCP clients      shell / CI        scripts, build.py (out of process)
                     │ MCP                   │ CLI                │ SDK (remote mode only)
                     ▼                       ▼                    ▼
                specto-mcp  ──────────► specto-sdk ◄──────── specto-cli
                                             │ JSON-RPC over QLocalServer (pipe / unix socket)
┌──────────────────────── specto-app: one process per user ───────────────────────────────┐
│ GUI thread (Python; Qt event loop)                                                       │
│   endpoint ─► specto-core: registry · transactions/undo · canvas document · channels ·   │
│               event stream · plugin registry · data kinds · job manager                  │
│   canvas controller ─► QML item models ─► view hosts (Python glue, no per-frame work)    │
│   specto-intake (scan orchestration) ─► jobs ─► worker process pool (decoders, scans)    │
│                                                   ▲ shared-memory typed buffers          │
│ Qt Quick render thread (C++ only, _spectoqt extension)                                   │
│   GalleryItem · TextureItem (native GL/Metal texture) · MpvItem · StormItem (spike S1)   │
│   WebEngineView items (Chromium IPC) ─► specto-web (TS bridge) ─► Monaco, MapLibre, …    │
└──────────────────────────────────────────────────────────────────────────────────────────┘
          │ shared memory + control socket                 │ custom URL scheme (binary)
   frame-source processes: ovrtx · Blender · Storm-fallback          web panel assets
```

| Package | Depends on | Owns |
|---|---|---|
| `specto-core` | pydantic, numpy; **no Qt** | Canvas document + schema/migrations, address grammar, command registry, transactions/undo/journal, channels and links, spaces, data-kind vocabulary, plugin manifests, job manager interface, event stream |
| `specto-intake` | core | Sources, screener, keys, grouping, sort, containers (M2), watch (M2) |
| `specto-readers` | core | Built-in locators and decoders (images, JSON, text, later LAS/PLY/MCAP) |
| `specto-sdk` | core schemas | Python client, **remote mode only** (socket to a running app) |
| `specto-cli`, `specto-mcp` | sdk | Generated from the registry; thin |
| `specto-app` | core, intake, readers, PySide6 | Shell, canvas, view hosts, `QLocalServer` endpoint, worker pool, `_spectoqt` C++ extension |
| `specto-web` (TS) | generated types | Channel bridge, command client, capability enforcement on the JS side |
| plugins | core plugin API only | Views, locators, decoders, frame sources, channel types, transforms, intake aliases, commands |

Arrows point toward core; plugins never import `specto-app`. `specto-sdk` has one mode (R9, R10, R11): headless is `specto-core` + `intake` + `readers` without a GUI, for tests and caches; `snap` always talks to a running app.

## 3. Core abstractions

### 3.1 Project

A project is a folder containing `.specto/`: `project.toml` (name, id, defaults), `canvases/<id>.json`, `canvases/<id>.journal.jsonl`, `intake/*.toml`, `recipes/`. Project id is a UUID written once into `project.toml`; caches live in the platform cache dir keyed by that id. The CLI resolves the project from `--project` or the nearest `.specto/` above cwd. A registry of known projects with a recent list lives in the user data dir (M3). M1 opens one project per app process; M3 holds several (section 5.6).

### 3.2 Canvas document (session)

One canvas is one session; the words are synonyms and the document is called the **canvas document**. It is a JSON file:

```text
{ schema_version: 3, canvas: {id, label, created, project_id},
  objects: { "v3": {kind:"view", type:"gallery", label, source:"c1", state:{…}, locked:false, provenance:{…}},
             "c1": {kind:"col", spec:{…}, generation: 12, item_count: 1842},
             "g2": {kind:"group", tree:{split:"h", children:[…]}},
             "f1": {kind:"frame", rect:[x,y,w,h], window:{bound:true, monitor:1}},
             "l4": {kind:"link", channel:"time", members:["v3","v7"], mode:"two-way", transform:{…}},
             "r1": {kind:"region", rect, label}, "ch_time": {kind:"chan", value:…, rev: 88, origin:"v3"} },
  layout: { positions: {"g2":[x,y], "r1":[…]}, parked:["v9"] },
  undo: { cursor: 41 } }
```

Rules:

- **Ids** are core-assigned, kind-prefixed, monotonic per canvas (`v3`, `c1`, `g2`, `f1`, `l4`, `r1`), never reused, never user-chosen. Every object also has a mutable `label`. Agents use ids in commands and labels in prose; `resolve "@frames"` maps a label to an id and errors on ambiguity (R2).
- `schema_version` is an integer. Core holds forward-only migrations `n -> n+1`; opening an older file migrates in memory and saves at the new version on first write. Journal entries carry the version they were written under. A newer file than the app understands is refused with the version numbers in the message.
- Collections store their spec, generation counter and a content hash; the materialised item list lives in the cache (it can be 100k entries).
- Channel values are document objects with a revision and origin (R7), so `state get` and undo see them.
- Provenance per object: `{by: "user" | "agent:<session>" | "recipe:<name>@<ver>", txn: 41}`.
- Format is JSON, not TOML, because patches (RFC 6902 JSON Patch) are the undo and diff unit.

### 3.3 Address grammar

```text
address  := [ "proj:" slug "/" ] "canvas:" id "/" object { "." property }
object   := kind ":" id          kind ∈ col view group frame region link chan space job recipe
examples := canvas:c7/view:v3.camera   canvas:c7/link:l4   proj:robotA/canvas:c7/col:c1
```

`proj:` defaults to the connection's project. `canvas:` is never implicit in a tool call (there is no "active canvas"); the CLI fills it from `--canvas` or `SPECTO_CANVAS`, MCP tools take a `canvas` argument, and `canvas ls` lists them. `recipe:` and `job:` are project-scoped and may omit the canvas component.

```ask
id: addr
type: confirm
question: Accept core-assigned short ids plus mutable labels, with the canvas always explicit in addresses?
```

### 3.4 Commands, transactions, undo, journal

**Registry.** A command is a pydantic `Args` model, a `Result` model and a handler, registered by decorator with `name`, `since`, `deprecated_since`, `read_only`, `destructive`, `effects: [..]`, `replayable`. The registry exports `api-info.json` (`api_level`, `api_compatible`, commands, data kinds, channel types, view types, theme tokens). CLI (click, dynamic), MCP (FastMCP loop), the typed Python SDK and the TypeScript bridge types are generated from it.

**Transaction model (answers R3).**

- Every command executes inside a transaction. A call outside `txn.begin` is its own single-command transaction.
- `txn.begin(canvas, label, ttl_s=60) -> txn_id`; `txn.commit(txn_id, dry_run=false)`; `txn.abort(txn_id)`. The **connection** that began the transaction owns it. Disconnect or `ttl_s` of idleness aborts it; abort reverts its patch and emits `txn.aborted`. MCP calls are stateless, so the MCP server keeps the mapping agent-session -> open transaction and passes `txn_id` through; `ttl_s` is the safety net when the agent stops talking.
- Commands inside an open transaction apply to the live document immediately; the transaction groups them for undo, journal and review. User GUI commands interleave as their own transactions. The undo stack is linear over transactions, so an older one is undone only after newer ones; that is the whole conflict rule.
- `dry_run=true` on commit runs the queued commands against a copy and returns the forward patch and a **conversion report** (what bound, what was skipped and why, which quota would be hit), then discards (R8a).

**Undo model (Qt-free).** Handlers mutate the document only through a `DocTxn`, which records every write as a JSON Patch operation plus its inverse. Undo applies the inverse patch of the transaction; redo re-applies the forward patch; no command writes an `undo()`. A command that touches the world outside the document (start a watcher, bind a frame to an OS window, write a file, launch a job) declares it in `effects`; a transaction containing one is refused unless begun with `allow_irreversible=true`, after which it is marked irreversible, still journaled and highlighted, and `undo` reverts its document patch and lists the effects it could not revert. The GUI reacts to patches, never to commands, so undo is not a special case for views. `QUndoStack` is not used.

**Journal.** `canvases/<id>.journal.jsonl`, append-only: `{seq, ts, txn, origin, schema_version, commands:[{name,args}], patch, irreversible, replayable}`. Replay runs from a named snapshot forward over replayable transactions; a transaction is not replayable if any command is `replayable=false`. Watch arrivals are events, not commands; a gesture journals one command at its end (R13, R15).

```ask
id: txn
type: choice
question: Undo as document-patch inverse, with effectful commands refused in a transaction unless allow_irreversible. Agree?
options:
  - Yes, patch-based undo
  - Prefer per-command undo() (Kit style) and accept partial undo of a turn
  - Discuss
```

### 3.5 Event stream

`watch(canvas, since_seq)` yields `{seq, kind, payload}`. Kinds: `doc.patched {txn, patch}`, `txn.begun|committed|aborted`, `job.progress|done|failed`, `col.arrivals {col, generation, added, removed}`, `chan.changed {chan, rev}` (coalesced to one per channel per tick), `view.health {view, status, message}`, `app.quota`. A ring buffer holds the last 10k events per canvas; `since_seq` older than the buffer returns a `gap` event and the client re-reads `state`. CLI and SDK stream; MCP, being request-response, gets `events.poll(canvas, since_seq, timeout_s)`.

### 3.6 Intake and collections

An intake spec is `{sources:[{root, pattern, depth, follow_links}], screener:[rules], keys:regex, group_by:[key], sort, watch}`. Rules compile to regex over the POSIX path **relative to the source root that matched the file**; keys are namespaced by source index unless `merge_roots=true`, so roots cannot collide silently (R13). Type aliases (`@images`, `@json`, `@pointclouds`) come from locator manifests. Containers (archives, bags) expand lazily into virtual entries `drive.mcap#/camera/image`; a topic yields one item per message behind a lazy index (M2). `watch` arrivals bump `generation` and emit `col.arrivals` events; they are not commands. `intake explain <col> <path>` names the rule that matched or excluded a path.

### 3.7 Readers: locators and decoders; data kinds

The reader abstraction is two things (R6):

- **Locator**: `can_handle(path) -> priority`, `locate(item) -> DataRef{kind, uri, meta}`. Cheap, runs on the GUI thread or a scan worker. Video, USD stages and ovrtx scenes stop here: the view takes the reference.
- **Decoder**: `decode(ref, request) -> TypedBuffer`. Runs in a worker process; the result is a numpy array with dtype and shape (or an Arrow table) in shared memory, handed to the GUI thread zero-copy and to native items by pointer.

Data kinds, the vocabulary views accept and readers produce: `image2d`, `image_seq`, `video_ref`, `pointcloud(xyz, attrs)`, `mesh`, `splat`, `usd_stage_ref`, `geojson`, `json`, `text`, `markdown`, `table`, `message_stream`, `pose_track`. A view's manifest declares `accepts`, `channels_in`, `channels_out`, `host: quick | web | frame`. Assigning a collection to a view runs locators per item and produces a conversion report; items without a usable kind are listed as skipped with the reason. Web panels receive binary over a custom URL scheme with range requests and a hard cap (10 M points, 2 GB); beyond it the view refuses with the cap in the message, and the LiDAR panel in M4 is native or COPC-streamed.

```ask
id: kinds
type: confirm
question: Accept the locator/decoder split and the data-kind list above as the reader/view contract?
```

### 3.8 Channels, links, echo handling

Channel types and value schemas: `time {t: float64 s, timeline}`, `hover_time`, `camera3d {pos, quat, fov, space}`, `map_view {lon, lat, zoom, bearing, pitch}`, `pan_zoom2d {cx, cy, scale}`, `selection {space_id, ids[]}` (R17), `cursor {xyz, space}`, `item {key}`, `scalar {name, value}` (for control panels). A link is `{channel, members, mode: two-way | follow, transform}`.

Propagation (R7): channel values live in the document with `rev` and `origin`. A view publishes by calling `chan.set` with its id; core stores the value, bumps `rev`, and schedules a **tick** on the GUI thread (one per rendered frame, coalesced latest-value). In the tick, each changed channel is propagated once along its links; a member view receives `(value, rev)` and applies it without re-publishing (a received update never sets `origin`). Cycles through non-inverse transforms cannot re-trigger inside a tick because each channel is written at most once per tick; across ticks, a set whose value is within the channel's epsilon of the stored value is dropped, which kills float drift. Web panels get one bridge message per channel per tick, not per event.

Time: `time.t` is float64 seconds on a named **timeline**; each collection declares its timeline and an offset; links between timelines are transforms, so ROS stamps, video pts and frame index coexist (Rerun's multiple timelines). `hover_time` is separate (Foxglove).

```ask
id: chan
type: confirm
question: Accept once-per-tick coalesced propagation with revision + epsilon as the echo and cycle rule?
```

### 3.9 Frame graph (spaces)

`space:` objects declare a coordinate space: `{kind: epsg | enu | local, epsg, origin_llh, parent, pose: static | track_ref}`. Collections and views bind to a space; link transforms convert channel values between spaces (pyproj for CRS, numpy for rigid poses, time-indexed when the pose is a track). `/tf` from bags becomes time-varying edges. Phased to M3; M1 has one implicit local space.

### 3.10 Recipes

A recipe is a folder: `recipe.toml` (name, version, roles with typed slots and default intake specs, scalar params, requirements, provenance), `layout.json` (views, groups, links, optional frames, bound to roles), optional `build.py`, `views/`, `transforms.py`, `checks/`.

The hard part (R14), stated: **capture** maps each collection in the captured subgraph to one role, typed by the kinds its items resolved to; every address in the subgraph is rewritten through an id map; links to views outside it are dropped and listed. **Scope** is explicit (`recipe save --views v3,v7,g2` or a group), never "the canvas". `apply` binds sources to roles, runs each role's intake spec, lands as one group (plus frames if declared) and returns the conversion report. Goldens in `checks/` are structured state; images carry a tolerance and are advisory. Storage: user library in the platform data dir, `.specto/recipes/` in projects (project shadows library); **the user's own git** keeps history (R10). Declarative recipes are M3; recipes with code are M4.

### 3.11 Trust

Threat model for one user: (1) a canvas or recipe pulled from someone else's repository; (2) a prompt-injected agent writing recipe or panel code locally. Rules:

- Trust is the user's decision on a **content hash**, regardless of author. Store: a plain hash list in the user data dir (no HMAC; nothing can write there without already running as the user).
- Paths that run code are enumerated: `build.py`, `transforms.py`, `views/*` Python, web-panel JS. There are no expression fields. Untrusted content opens in Restricted Mode: layout and data load, code does not, the canvas says so.
- `build.py` and all SDK scripts run **out of process** through the remote SDK, even when the app launches them.
- Web panels run under Chromium's sandbox plus a per-panel capability list (`read:col:c1`, `chan:time`, `cmd:view.*`) enforced on the Python side of the bridge and in the URL-scheme handler.
- No "execute arbitrary code" tool. Plugin commands are reachable only through `call`, never as first-class MCP tools (R8d).

```ask
id: trust
type: confirm
question: Trust = user-approved content hash, build.py always out of process, per-panel capabilities. Accept?
```

## 4. Runtime architecture

### 4.1 Threads and processes

| Where | Runs | Never runs |
|---|---|---|
| GUI thread (Python) | Qt event loop, `QLocalServer` endpoint, command bus, document, channel ticks, job manager, view-host glue, intake orchestration | Decoding, scanning, blocking I/O over ~10 ms |
| Qt Quick render thread (C++) | Scene graph, `_spectoqt` items: gallery tiles, native texture wrap, mpv render callback, Storm `Render()` | Any Python; the GIL is never taken here |
| Worker process pool (N = cores - 2, `spawn`) | Decoders, intake scans, thumbnail generation, container indexing | Qt imports |
| Frame-source processes | ovrtx, Blender, Storm-as-fallback | |
| Chromium processes | Web panels | File or network access outside the capability list |
| mpv threads (inside libmpv) | Demux, decode | Python callbacks; the update callback only posts to the render thread |

**Python/C++ boundary, explicit.** `_spectoqt` is a small C++ extension (shiboken-wrapped) shipped with `specto-app`: `GalleryItem` (tile layout and texture atlas from shared-memory thumbnails), `TextureItem` (wraps an external GL or Metal texture via `QSGTexture` from-native; also receives shared-memory CPU frames), `MpvItem` (libmpv render API, C++ callbacks), and `StormItem` (`UsdImagingGLEngine` driven in C++, built only when the spike passes). Everything else in the app is Python. Hot paths found later move into `_spectoqt`, not into QML JavaScript.

### 4.2 Async job model

Any command that may exceed ~50 ms returns `{job: "j12"}` immediately; `job.status`, `job.cancel`, and `job.*` events carry progress. Jobs run in the worker pool; results arrive as shared-memory handles the GUI thread adopts. Intake scans, decodes, thumbnail builds, `snap` of a frame-source view and recipe apply are jobs. The CLI blocks on a job by default (`--no-wait` to return the id); MCP tools return the id plus a hint to poll.

### 4.3 Crash isolation and health

A worker that dies takes one job with it: the job fails with the signal, the pool respawns, the item is marked skipped. A frame source that dies freezes its last frame and reports `view.health = error`. Every view has `health` in the state outline and in events (R12); web panels forward uncaught JS errors into it. A plugin failing 3 times in a session is disabled until restart, with the reason in `plugin ls`. Only `_spectoqt` and PySide6 can take the process down; that is the price of in-process Storm and mpv, and S1 measures it.

### 4.4 GPU budget and quotas

Per canvas, enforced at command time with fix-it errors: live web panels (default 8), live 3D viewports (3), resident points (20 M), live video decoders (4). Only views inside an on-screen window frame render; everything else is paused, snapshotted, and releases GPU memory where the engine allows. Nothing renders scaled.

### 4.5 Frame sources

One interface for external renderers: control socket (JSON-RPC: `load`, `set_camera`, `resize`, `pick`, input events) plus a frame channel. M1-M2 frames are CPU in shared memory (`QImage` upload per frame, fine for ovrtx at 1080p30); M3 adds GPU handles: DXGI shared handle on Windows, DMA-BUF on Linux, IOSurface on macOS, consumed by `TextureItem`. In-process renderers skip the copy. CUDA interop follows the RHI decision in section 5.3: on OpenGL it is CUDA-GL (R21).

```ask
id: runtime
type: confirm
question: Accept the thread/process split, with StormItem and MpvItem as C++ items (Storm demoted to a frame source if spike S1 fails)?
```

## 5. Canvas and windows

### 5.1 Model

The canvas is a 2D arrangement of **groups**, each a split/tab tree of views, plus window frames and labelled regions. There is no three-layer layout model (R5): one document, undo and provenance give what Rerun's layers give. Positions are absolute canvas coordinates; groups outside every frame are parked and not rendered.

### 5.2 Normal view

1:1, pixel-perfect, live: the groups inside the current window frame, docked. Chrome: header per view with id, label, health and this-turn change glyph; a command palette over the registry; a timeline bar when a `time` channel exists. Style direction is the subject of `gui-styles.md` and is not decided here.

### 5.3 Overworld

Paused layout view: each view becomes a card (label, type, source, link ports, provenance, snapshot age); frames are heavy rectangles labelled with size and monitor; links are typed, colour-plus-glyph coded wires; dragging a wire creates a link; regions are labelled areas. Toggle on one key (tap toggles, hold peeks). Agent changes of the latest transactions are highlighted here and in the normal view (R8b).

### 5.4 Window frames and multi-window

A frame is a rectangle sized to a real window. Frames do not overlap; when an OS window resize grows a frame, it pushes neighbours along the resize axis (the canvas is unbounded, so the push always succeeds). M1: one frame, one OS window. M2: several frames, each bound to its own OS window. **In-window frame switching** (virtual desktops in one window) is M4 and the first candidate to cut: it adds "which frame is live" state to every command and to `snap`, and one OS window per frame already covers multi-monitor. Drop it unless a concrete workflow needs it.

### 5.5 Qt Quick vs Widgets: spike-gated decision

Default: Qt Quick, `QSG_RHI_BACKEND=opengl` on Windows and Linux, Metal on macOS. The reason that survives the snapshot decision: custom drag-to-pan galleries, GPU-composited cards and wires in the overworld, one item model for both levels. Fallback: Qt Widgets with `QMainWindow` dock areas for the normal view and a `QGraphicsView` of snapshots for the overworld, which lets usdview's `StageView` drop in and removes the D3D11 question.

Spike S1 decides. Exit criteria: a Storm-rendered stage in a `TextureItem` beside a `QWebEngineView` item, Windows first, 60 Hz at 1080p with the GUI thread above 80 % idle, no WebEngine regression under the OpenGL backend, and a measured frame-copy cost if the texture is not shared. Pass keeps Qt Quick with `StormItem` in-process. Partial pass (Quick fine, Storm not) keeps Qt Quick with Storm as an out-of-process frame source. Fail (WebEngine broken under OpenGL RHI, or PySide6 lacks the `QSGTexture` from-native path and the C++ item cannot be built in a week) moves the shell to Widgets.

```ask
id: shell
type: choice
question: Which shell approach should M1 commit to?
options:
  - Qt Quick on OpenGL RHI, confirmed by spike S1 before M1 code
  - Qt Widgets + QGraphicsView overworld now, skip the spike
  - Decide after the spike with no default
```

### 5.6 Project switching

M1: one project per app process; `specto open <path>` switches by reopening. M3: a project rail; several projects open in one process; switching swaps the canvas controller, pauses the previous project's live views and releases their GPU memory, and resumes on return. This rides on the pause mechanism the overworld needs anyway, which is why it is M3 rather than cut. Pop-out into its own window is also M3.

```ask
id: frames
type: multi
question: Which of these phased canvas features still earn their place?
options:
  - Several projects open in one process with instant switching (M3)
  - In-window frame switching like virtual desktops (M4, recommended cut)
  - Project pop-out into its own window (M3)
  - Labelled regions for parked views (M2)
```

## 6. Agent loop

1. **Discover.** `list_commands` (names, one-line docs), `describe <view-type | command | channel>` returning accepted kinds, channels, defaults and an example composition (R8e), and `state get --outline` for a cheap tree of the canvas. The generated vocabulary doc is for humans and prompts; agents never need all of it.
2. **Compose.** `txn.begin`, then `intake new`, `view new --from col:c1`, `canvas dock`, `link new`, `controls new` (the declarative control panel: a built-in `controls` view whose JSON spec of sliders, toggles, pickers and buttons binds to channels, parameters or commands, R16). `txn.commit --dry-run` returns the conversion report; the agent fixes bindings before anything lands.
3. **Verify.** `snap view:v3` returns structured metadata (camera, visible items, selection, health) plus an optional image; `snap frame:f1 --layout` returns the layout as structured text the agent can assert on (R8c). `events.poll` confirms health after commit.
4. **User review.** Each transaction is a changeset: glyphs on headers in both levels, a changeset bar with fly-to, revert per change or per transaction, and the transaction's patch in the Monaco diff view. Proposal mode (ghosted until accepted) is not in the core model; it can be built later as an uncommitted dry run shown on the canvas.
5. **Locks.** `locked: true` on a view, group or frame lives in the document; a command that would move, resize, relink or delete it fails with the lock holder and the unlock command, so the agent must ask. User edits set `provenance.by = user`; the agent reads them from `state`. Nothing merges, because there is one document.
6. **Promote.** `recipe save --views …` turns a proven composition into a recipe (M3).

MCP surface: about 15 coarse tools (`state`, `describe`, `txn_begin/commit/abort`, `intake_new`, `view_new`, `dock`, `link_new`, `controls_new`, `snap`, `events_poll`, `undo`, `recipe_apply`) with read-only/destructive annotations, plus generic `call` and `list_commands`. Plugin commands live behind `call`.

```ask
id: review
type: choice
question: Agent changes apply live and are reviewed as a changeset with per-change revert. Keep, or require proposal mode in M1?
options:
  - Apply live + changeset review (M1)
  - Proposal mode in M1 (adds a proposed state to every command and snap)
  - Apply live in M1, proposal mode as an M2 experiment on top of dry run
```

## 7. Phased roadmap and spikes

### Spikes first (two weeks, parallel)

| Spike | Pass | Fail |
|---|---|---|
| S1 Storm in Qt Quick (section 5.5) | 60 Hz, GUI thread >80 % idle, WebEngine unaffected under OpenGL RHI | Any criterion missed; produces the fps and copy-cost numbers either way |
| S2 PySide6 coverage: `QSGTexture` from-native, `QQuickItem` subclass in C++ via shiboken, `QWebEngineUrlSchemeHandler` range requests | All three callable from a built `_spectoqt` | Missing API means Widgets fallback or pure C++ item host |
| S3 Worker pool: 10k JPEG thumbnails decoded in processes, delivered over shared memory to a Qt Quick gallery | Pan at 60 Hz during decode, GUI thread free | Switch to a C++ decode thread pool inside `_spectoqt` |
| S4 Largest local LiDAR file in Babylon.js under QtWebEngine, WebGL2 only (R18) | Interactive at the cap (10 M points) | Native point-cloud item moves from M4 to M3 |
| S5 H.264 MP4 in `QWebEngineView` | Expected fail; confirms mpv item | |

### M1: the loop closes (exit criteria)

Scope: `specto-core` (document, ids, schema version, registry, patch undo, transactions, journal, events, outline, dry run); transport and generated CLI, MCP, SDK; `specto-intake` sources, screener with aliases, keys, grouping, sort; app with Qt Quick canvas, normal view, one frame, one OS window, groups with split/tab, changeset highlight and diff panel; views: gallery (native) and Monaco JSON (web) plus the `controls` view; channels `item` and `pan_zoom2d`; worker pool decoding; locks; quotas. No overworld, no multi-project, no recipes, no code execution.

Exit criteria: (1) from a blank canvas, Claude Code over MCP, given `runs/`, builds two galleries linked on `frame` and a JSON inspector following the selected item, using only `describe`/`list_commands`, and verifies with `state` and `snap`; (2) one `undo` reverts the whole transaction and the diff panel shows exactly its patch; (3) the user drags a view; the agent reads the new layout from `state` and continues; (4) a composition with a bad binding is refused by dry run with a fix-it message and nothing partial lands; (5) a locked view refuses an agent move with the unlock hint; (6) 10k thumbnails pan at 60 Hz with decoding in workers; (7) S1 has produced its decision.

```ask
id: m1_rank
type: rank
question: Rank M1 scope by priority (top = must be in M1, bottom = first to slip to M2).
options:
  - Transactions, patch undo, journal and changeset review
  - Dry-run conversion report
  - Intake with screener, keys and grouping
  - Native gallery with worker-pool thumbnails
  - Monaco JSON view with item-following link
  - Declarative controls view
  - Locks on user-edited objects
  - Per-canvas quotas and view health
```

### M1.5: the human GUI underneath (user decision after M1, 2026-10-13)

Scope: a real gallery (zoom changes thumbnail size and reflows columns under the cursor, drag-pan, keyboard navigation, aspect-correct cells with captions, hover preview, a full-size image view with zoom/pan and next/prev following the item link, selection persisted in view state); project open/new; a file browser panel (thumbnails, multi-select, drag to canvas); an intaker panel (sources by browse or drop, screener rule editor with live match preview and explain, key regex builder with capture preview, grouping preview, collection list); "open as" on a collection; drop files or folders on the canvas to intake and get a suggested view; add/close views; split/tab docking; linking views by hand; visible undo/redo and diff. Fixes: selection and channel traffic stay out of the changeset bar and the undo stack, window resizes stay off the undo stack, same-axis docking flattens, grouped sibling parts are not reported as skipped.

Exit: a scripted human test with real mouse and keyboard, from the app alone: open the project, browse testdata, create cam-a and cam-b intakes keyed by frame, open two linked galleries and the JSON inspector, use the gallery, open a full image, undo.

### M2: the canvas earns its name

Overworld with cards, wires and regions; multiple frames each bound to an OS window; pause/snapshot/resume for views; mpv video item and markdown view; MapLibre view; `time` and `hover_time` channels with timelines; containers and watch in intake; TypeScript bridge types generated for web panels; per-panel capabilities.

### M3: 3D, spaces, projects, recipes

`StormItem` or Storm frame source per S1; Babylon.js glTF/splat view; spaces and link transforms (pyproj); multi-project in one process with switching and pop-out; declarative recipes (save, apply, roles, goldens); camera, map and selection channels across unlike views.

### M4: heavy data and code

ovrtx and Blender frame sources with GPU handles; native or COPC-streamed LiDAR view; rosbag reader mapping topics to kinds, timelines and spaces; recipes with code under trust; in-window frame switching only if still wanted; MCP Apps only if a client needs it.

```ask
id: roadmap
type: confirm
question: Accept M1-M4 as sequenced, with USD/Storm in M3 and LiDAR/ROS in M4?
```

## 8. Review findings disposition

| Id | Disposition | How the HLD handles it |
|---|---|---|
| R1 | accepted | Section 4: thread/process table, C++ items for Storm and mpv, worker pool, job model |
| R2 | accepted | 3.2-3.3: one noun, core ids + labels, canvas in every address, `schema_version` with migrations |
| R3 | adapted | 3.4: patch-based undo instead of per-command `undo()`; explicit `txn.*` with ttl and connection ownership; irreversible flag |
| R4 | accepted | 5.5: spike S1 with exit criteria; OpenGL RHI; Widgets fallback designed |
| R5 | accepted | 5.1, 5.4: no three-layer model; groups as split trees on a 2D canvas; frames push on resize |
| R6 | accepted | 3.7: locator/decoder split, data kinds, typed shared-memory buffers, web cap, native LiDAR in M4 |
| R7 | accepted | 3.8: values in document with rev, once-per-tick coalescing, epsilon, named timelines |
| R8 | accepted | 6: dry run, review in both levels plus Monaco diff, structured `snap`, plugin commands behind `call`, `describe` |
| R9 | accepted | 3.11: hash-based trust by user decision, `build.py` out of process, capabilities, no HMAC, no expression fields |
| R10 | adapted | Fixed requirements kept but phased: one project in M1, multi-project M3, one window per frame M2, in-window switching M4 with a recommended cut, user's git for recipes, SDK remote only, integer schema version |
| R11 | accepted | 2: headless = core + intake + readers; `snap` needs a running app |
| R12 | accepted | 4.3-4.4: quotas, `health`, crash-loop disable |
| R13 | accepted | 3.6: per-source relative paths, key namespacing, lazy containers, arrivals as events |
| R14 | accepted | 3.10: capture and scope rules, structured goldens, images advisory |
| R15 | accepted | 3.4: one command at gesture end |
| R16 | accepted | 6: `controls` built-in view in M1 |
| R17 | accepted | 3.8: `selection {space_id, ids}` |
| R18 | accepted | S4: WebGL2 baseline, WebGPU opportunistic |
| R19 | accepted | Storm moved to M3 behind S1; full OpenUSD build is a listed risk |
| R20 | accepted | Section 1 principle 5; coordinate frames renamed spaces |
| R21 | accepted | 4.5: interop follows the RHI choice |
| R22 | accepted | No change needed |
| R23 | accepted | Rosbag and Lichtblick spikes deferred to M4; S1-S5 are on the critical path |

## 9. Open decisions

The inline questions are the decisions. Two carry real risk: the shell (`shell`), because every view host depends on it, and the phased cuts (`frames`), because in-window frame switching and multi-live-project are the subsystems most likely to absorb months without moving the agentic question. Risks no spike owns yet: the full OpenUSD imaging build per platform matched to the PySide6 Python ABI (weeks), and ovrtx's pre-release API drift.

```ask
id: missing
type: text
question: Anything missing, wrong, or a constraint I should have used?
placeholder: e.g. a workflow that needs in-window frame switching, a view type to move earlier, a different M1
```

## 10. Decisions recorded (review round 1, 2026-10-12)

| Question | Answer | Effect |
|---|---|---|
| `addr` | yes | Core ids + labels; canvas always explicit |
| `txn` | not answered | Default stands: patch-inverse undo, `allow_irreversible` for effects. Revisit if it hurts |
| `kinds` | yes | Locator/decoder split and data kinds are the reader/view contract |
| `chan` | yes | Once-per-tick coalesced propagation, rev + epsilon |
| `trust` | yes | Content-hash trust, `build.py` out of process, per-panel capabilities |
| `runtime` | yes | Thread/process split; C++ items on the render thread |
| `shell` | Qt Quick on OpenGL RHI, confirmed by S1 before M1 code | The shell-deciding parts of S1 (WebEngine under the OpenGL backend, `TextureItem` with a native GL texture, a buildable `_spectoqt`) and S2 gate M1. The Storm half of S1 waits on a full OpenUSD imaging build; it runs beside M1 and only decides `StormItem` in-process vs Storm frame source (a partial pass keeps Qt Quick) |
| `frames` | only "project pop-out (M3)" kept | In-window frame switching: cut. Labelled regions: cut. Several projects in one process with a switching rail: not kept as worded; multi-project is one OS window per project (pop-out) in M3. To confirm |
| `review` | apply live + changeset review (M1) | No proposal mode in M1 |
| `m1_rank` | 1 transactions/undo/journal/changeset, 2 dry run, 3 intake, 4 native gallery, 5 Monaco JSON, 6 controls view, 7 locks, 8 quotas/health | Slip order is bottom-up |
| `roadmap` | yes | M1-M4 as sequenced |
| M1 review (2026-10-13) | "there is no user flow, and the GUI is buggy" | New principle 5: a complete human GUI underneath, the agent flow on top, one command surface for both. M1.5 inserted before M2 (section 7) |

GUI style (from `gui-styles.md`): **B Calm Canvas** (fit 5; A, C, D rated 1). B's ghosted-proposal idea conflicts with `review = apply live`; resolution: keep B's visual language, show live agent changes with B-style highlights and ghosted before-states for removals, and treat true proposals as the M2 dry-run experiment. Cross-cutting priority: overworld transition, change review, locks, command palette, keyboard model, theme tokens, provenance, non-blank empty canvas.
