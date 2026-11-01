# Specto design review (adversarial), 2026-10-12

Reviewed: `DESIGN.md` (draft 2026-10-12) and `docs/landscape.md`. Scope: architecture only; nothing is built, so every finding is cheap to act on now.

**TLDR**

- The doc has no execution/threading model, and the stack as written puts Python on Qt Quick's render thread (Storm, mpv). That is the first thing to fix, not the canvas.
- The reason given for Qt Quick ("one scene, animated transition") is cancelled by the later snapshot decision; the D3D11/Storm problem exists only because of that choice. Decide the shell on evidence from one spike, and do not let USD dictate it.
- The session document, addressing and transaction/undo semantics (the three things the agentic loop stands on) are each described two or three different ways. Pin them down before any view.
- Trust has no threat model; "locally created is trusted" trusts agent-written code, which is the only realistic threat for one user.
- Roughly a third of the doc (projects, multi-frame virtual desktops, git-backed recipe library, headless snap, HMAC trust DB, TS codegen) is weight a single-user tool does not need in year one.
- First milestone should be the agentic loop end to end on two cheap views, not ROS/Lichtblick/LiDAR spikes.

## Blockers

**R1. No execution model; Python in per-frame paths.** (Stack, Views, Frame source interface) "Python stays out of per-frame paths" and "Python is the working language" cannot both hold for Storm and mpv as specified. `UsdImagingGLEngine.Render()` has only a Python binding unless you write C++; a `QQuickFramebufferObject` renders on the scene-graph render thread, so the call takes the GIL on that thread while the GUI thread (Python event loop, command bus) holds it. Same for `python-mpv`'s render context, whose update callback arrives on an mpv thread. The doc never says which thread runs commands, readers, watchers, channel fan-out, the JSON-RPC server or transforms. Recommend a one-page threading section: GUI thread = Qt + Python + command bus; render thread = C++ only; readers and intake scans in a worker *process* pool (shared-memory buffers; also contains segfaults from USD/laspy extensions); frame sources out of process or C++. Accept that `specto-view-usd` and the mpv item are C++ plugins from day one, or demote USD to a frame source over shared memory. Also add an async job model (job id, progress, cancel, `watch` events): commands on the GUI thread cannot block for a 200k-file intake scan.

**R2. Session document, addressing and identity are inconsistent and underspecified.** (Session document and commands, Control plane, Projects, Decisions adopted) The document's contents are listed three ways: "widgets, groups, frames, regions, links"; "collections, views, canvas layout, links"; "canvases/*.json". Nouns drift (widget, panel, pane, view). Addresses are `proj:<name>/view:v3` yet a project holds several canvases and "every tool addresses a session explicitly": there is no session component in the grammar. Identity: `view:v3` and `col:frames` are agent-chosen names; renames break links and journals, collisions are unhandled. Versioning covers the command schema only; session schema version and migrations are an open question. Recommend: one noun (`view`); address grammar `canvas:<name>/view:<id>`, where `<id>` is a stable short id and a mutable `label` exists for agents and humans (tool guidance says readable names; stable ids give both); JSON with `schema_version` and forward-only migrations in core; journal entries carry the same version.

**R3. Transaction, undo and journal semantics contradict each other.** (Vision, Decisions adopted, Modules) Vision: every agent turn is one undoable transaction. Decisions: commands without `undo()` stay off the stack. So a turn containing one such command (intake with `--watch`, `frame.bind`) is partly undoable; the doc does not say what happens. `specto-core` "depends on nothing Qt" but the undo stack is `QUndoStack`. MCP calls are stateless, so Specto cannot see a "turn": who opens and closes the transaction, and what happens if the client dies mid-transaction? Recommend: a plain Python undo stack in core; explicit `txn.begin/commit/abort` commands with an idle timeout and automatic abort; a transaction containing a non-undoable command is refused unless flagged `--irreversible`, and is still journaled and highlighted. Journal replay needs determinism: say which commands are replayable (no `watch` arrivals, no gesture coordinates) and from which snapshot.

## Major

**R4. The Qt Quick rationale no longer holds; decide the shell on one spike.** (Stack, Canvas: Rendering and performance, Open questions) Stack justifies Qt Quick with "one scene, so the transition animates"; the overworld later renders snapshots only and the transition is "purely cosmetic". With that, the argument reduces to item-model sharing, which a Widgets shell with a `QGraphicsView` overworld also gives, plus `StageView` drops in and the Windows D3D11 problem vanishes. Qt Quick still wins on touch-free pan/zoom galleries and custom items, but say so. Either way, do not let one view type set the shell: run the scene graph on OpenGL on Windows/Linux (`QSG_RHI_BACKEND=opengl`; verify WebEngine under that backend in the spike) and Metal on macOS, and treat Storm as a GL-texture frame source. Spike: Storm texture in a Qt Quick item at 60 Hz with a WebEngineView beside it, Windows first. Also `QQuickRhiItem` is for RHI-abstracted rendering, not raw GL; the real path is a native texture wrapped via `QSGTexture` from-native, and PySide6 coverage of these classes is unverified.

**R5. Canvas model contradictions.** (Principles, Canvas workspace, Decisions adopted) Principles: "endless canvas, arranged freely"; Canvas workspace: "not a free-zoom canvas, two levels". Decisions adopt Rerun's three layout layers (auto, default, user edits), which compose over a tree; a canvas with absolute positions and docked groups has no defined composition rule for three layers. "Frames do not overlap" plus "resizing a window resizes its frame" has no rule for the collision. Recommend: drop the three-layer model (a single document plus undo is enough for one user, and the journal gives provenance); state that the canvas is a 2D arrangement of groups, each group a split/tab tree, and that frames push or clamp on resize.

**R6. Reader/view contract has no data vocabulary; large data goes through the wrong pipe.** (Principles, Views, Collections, Plugins) "Readers decode items; views show them" and "any intaker can feed any view" need a typed vocabulary of what readers produce and views accept (`image2d`, `pointcloud(xyz, attrs)`, `mesh`, `usd_stage_path`, `video_path`, `geojson`). Video, USD and ovrtx do not decode anything; they take a path, so the reader abstraction is two things (decoder, locator). Name both. Point clouds: a 50 M point cloud at 16 B/pt is 800 MB travelling numpy -> bytes -> Chromium IPC -> JS -> GPU; that is the hot path, in Python, through a browser. Recommend typed buffers (Arrow or numpy with dtype) as the reader output; zero-copy to native panels; an honest cap for web panels (~10 M points), with the "tens of millions" claim moved to a native or COPC-streamed panel. Landscape spike 4 should run before this table is final.

**R7. Channel propagation is under-defined.** (Links) Origin tags drop direct echoes, not cycles through non-inverse transforms (camera -> map -> camera with float drift re-triggers). Landscape lists revision counters and a single source of truth; the design took only origin tags. No coalescing: a 60 Hz camera change fanned to N web panels over `QWebChannel` is N JSON messages per frame. Time is undefined: unit, epoch, per-collection axes (ROS stamps vs frame index vs video pts) vs one session clock. Recommend: channel values live in the session document with a revision; links apply latest-value, one update per frame, on the GUI thread; define `time` as float64 seconds on a named timeline with per-collection offset transforms (Rerun's multiple timelines, which the landscape already notes).

**R8. The agentic loop has gaps where it matters.** (Vision, Decisions adopted, Agent-built views) (a) No dry-run: an agent should be able to submit a composition and get a conversion report (what bound, what skipped, why) before it is applied; the landscape praises CARTO for exactly this and the design keeps only "errors say how to fix". (b) Review: "the canvas highlights what it changed" lives in the overworld, so the user must leave the normal view to review. The session file is diffable; show the per-transaction document diff in the Monaco diff view you already have, and highlight in both views. (c) `snap` returns an image; the layout diagram should be structured text an agent can assert on, image optional. (d) "MCP surface stays 10-20 coarse tools" contradicts "plugin commands appear in MCP automatically": expose plugin commands only via `call`/`list_commands`. (e) Vocabulary discovery is a generated doc plus `api-info.json`; add a `describe <view-type>` command that returns accepted data types, channels, and an example composition, so the agent never reads the whole doc.

**R9. Trust has no threat model and trusts the wrong party.** (Decisions adopted, Recipes: Trust, Agent-built views, Modules) For one user the threats are (1) a session or recipe pulled from someone else's repo and (2) a prompt-injected agent writing recipe code. "Locally created recipes are trusted" trusts (2) by construction, since the agent writes locally. "Runs out of process; a bad script cannot crash the app" (tier 2) contradicts `specto-sdk` in-process mode "run by the app" for `build.py`. "Sandboxed by Chromium" is only as strong as the bridge allow-list, and the custom URL scheme that serves binary data is file access by another name. "Expression fields" appear only in the trust bullet. Recommend: trust = "the user clicked Run on this content hash" regardless of author; `build.py` out of process always; a per-panel capability list (`read:col:*`, `cmd:view.*`) enforced in the bridge; drop the HMAC DB (a plain hash list in the user data dir is equally safe for one user, since nothing can write there without already running). Delete or define expression fields.

**R10. Over-engineering for a single user.** (Projects, Window frames, Recipes: Storage, Headless, Versioning, Decisions) Each of these is a real subsystem: several projects open in one process with suspend/resume; frames as virtual desktops; a git repo per recipe library with a commit per save; TypeScript codegen; SDK in three modes; plugin API version ranges before a second plugin exists; MCP Apps. None is needed to answer the question the Vision asks. Recommend: project = the nearest `.specto/` (cwd rule only, one open at a time); one OS window per frame, no in-window frame switching; recipes are folders, the user's own git handles history; SDK remote mode only; versioning is a `schema_version` integer until a third party exists.

**R11. Headless `snap` is overclaimed.** (Headless) QtWebEngine offscreen needs a `QGuiApplication` with a GL-capable platform; Storm needs a GL context (a window or pbuffer on Windows); ovrtx needs an RTX GPU. "`specto snap` works headless too" is true only for the gallery and maybe Storm on Linux EGL. Recommend: headless = core + intake + readers (unit tests, caches, `recipe check` of declarative parts); `snap` always talks to a running app; render tests run on labelled machines, as Platforms already says.

**R12. Failure modes and quotas are absent.** (Plugins, Agent-built views, Views) In-process readers and Qt views can take the process down (R1 covers containment). Bad agent compositions have no limits: 40 web panels is 40 Chromium pages plus GPU memory; a view's JS error is invisible to the agent. Recommend: per-canvas quotas (live web panels, total points, live 3D viewports), refused with the usual fix-it error; a `health` field per view (ok / error with message) in the state outline and as `watch` events; crash-loop protection that disables a plugin after N failures.

## Minor

**R13. Intake edge cases.** (Intake spec) "POSIX relative path" is relative to what when a spec has several dirs and globs? Keys from different roots can collide. A bag topic as a container yields one item per message (10k items): say so and make readers lazy by contract. Files arriving under `--watch` are not commands, so journals that include a watched collection are not replayable; record arrivals as events, not commands.

**R14. Recipes: the hard part is a sentence.** (Recipes: Lifecycle) "Turning concrete datasets into roles" and "snapshot the journal into layout.json" is where all the difficulty is (which paths become which role, which journaled addresses rewrite). Golden image snapshots flake across GPUs and drivers; prefer structured-state goldens, images with tolerance. "A recipe lands as one docked group" but recipes may define frames and links to views outside the group; define scope.

**R15. Gesture coalescing.** (Control plane) "Every action is a named command" with a journal means a drag emits hundreds of `canvas.place`. Emit one command at gesture end (Kit does this).

**R16. Control panels have no home.** (Vision) The declarative control panel is a Vision bullet and appears in no table, module or plugin list. Add it as a built-in view type with its JSON spec in the vocabulary.

**R17. Selections and the item channel need a namespace.** (Decisions adopted, Links) Predicates over which fields: item metadata, USD prims, GeoJSON features, LiDAR points? These are different id spaces; a selection should carry its space, and views map what they can.

**R18. WebGPU is unverified in QtWebEngine.** (Views) Babylon.js "WebGPU" inside Qt's Chromium may be flag-gated, and on Linux is gated upstream. Baseline WebGL2; WebGPU opportunistic. The H.264 gap claim is correct.

**R19. OpenUSD build weight.** (Views) Imaging needs a full build per platform, matched to the PySide6 Python ABI and a GL-capable Hgi. This is weeks, not a `pip install`; it belongs on the risk list, not in a table note.

## Nits

**R20.** Terminology: widget / panel / pane / view; intaker / intake spec / collection. Pick one each.
**R21.** "CUDA-GL interop for ovrtx" conflicts with the D3D11 default; CUDA-D3D11 interop exists but is a different path. Decide with R4.
**R22.** Licensing note on Qt Quick 3D is right; nothing else needs it.
**R23.** Landscape spikes 1-2 (rosbag, Lichtblick) are not on the critical path for the Vision; spike 3-4 are.

## Things the design gets right

- One command registry generating CLI, SDK and MCP, with `read_only`/`destructive`/`undoable` annotations and explicit session addressing. This is the correct spine.
- Snapshot-based overworld: no scaled live rendering means native resolution always and a tiny GPU budget for layout.
- Collections, not files; keyed items making A/B compare a link, not a feature.
- Frame source as the only way renderers enter the app, with Blender and ovrtx out of process.
- Channels with transforms rather than pairwise links; crossfilter and glue rules for selections.
- Libmpv render API instead of `wid`; Wayland taken seriously.
- Conversion reports and fix-it errors as policy, skipped items listed, never dropped.

## Suggested first milestone: "the loop closes"

The doc implies starting with the architecture (monorepo, registry, canvas with overworld, Storm). Start instead with the thing nobody has shown yet: an external agent composing a useful viewer from a vocabulary, the user reviewing and undoing it, both editing one document.

Scope:
- `specto-core`: session document (JSON, versioned), stable ids plus labels, command registry with pydantic schemas, plain undo stack, transactions, journal, `watch` with sequence numbers, state outline, dry-run report.
- Transport: JSON-RPC over `QLocalServer`; generated CLI and FastMCP server with `call`, `list_commands`, `describe`, `txn.*`, `snap`, `state`, `watch`.
- `specto-intake`: sources, screener with `@images`/`@json` aliases, keys, grouping, sort. No containers, no watch.
- App: Qt Quick canvas, normal view only (one frame, one OS window, groups with split/tab). No overworld, no projects, no recipes, no trust (no code runs).
- Views: Qt Quick gallery (native) and one web pane running Monaco for JSON. Channels: `item` by key and `2d pan/zoom`. Transaction highlight in the normal view plus a document-diff panel.
- Parallel spike (R4), not in the milestone: Storm texture in a Qt Quick item next to a WebEngineView on Windows under the OpenGL RHI backend.

Exit criteria:
1. From a blank canvas, Claude Code over MCP, given `runs/`, builds two galleries linked on `frame` plus a JSON inspector following the selected item, using only `describe`/`list_commands` for vocabulary, and verifies with `state` and `snap`.
2. One `undo` reverts the whole agent turn; the diff panel shows exactly what changed.
3. The user drags a panel by hand; the agent reads the new layout from `state` and continues without conflict.
4. A composition with a bad binding is refused by dry-run with a fix-it message; nothing partial lands.
5. Galleries with 10k thumbnails pan at 60 Hz with the GUI thread free (decoding in worker processes).
6. The Storm spike produces a number (fps, frame copy cost) and a decision: Quick on GL, Widgets, or Storm-as-frame-source.
