# M2 code against the v2 model (for #34)

Read-only inventory of `main` at the time of the UX realignment map (2026-10-17), produced by an exploration agent.

## Totals and shaping facts

- ~69k tracked lines under packages/ and tools/ (~9.5k generated); 447 test functions (app 207, core 117, intake 54, readers 24, sdk 24, cli 15, mcp 6).
- TextureItem exists only in spikes/s1_s2; the shipped native module has GalleryItem and MpvItem.
- No snapping code exists; snapping is new work.
- No document-diff function exists; ADR 0013's reconcile needs a new `make_patch(old, new)`.

## Verdicts by area

| Area | Verdict |
|---|---|
| Canvas document (document.py) | REPLACE with the v2 pydantic model (from the prototype); keep id allocation |
| Schema/migrations, project, address grammar | KEEP with changes (one canvas, port addresses, .specto/links/) |
| Command registry, api-info, codegen, RPC, errors, jobs, journal, containers | KEEP |
| Patch + DocTxn | KEEP, add make_patch for reconcile |
| Engine/transactions/undo | KEEP with changes: one canvas, submit path, checks move to submit validation |
| Channels + links + tick | REPLACE with the directed link runtime (intaker, follow with key-field match, read_contents, time_map, python), resolved at edit time |
| View manifests/kinds | KEEP with changes: PortDecl{inputs, outputs, propagates} |
| Groups/split trees, canvas.place/dock/undock, frame.* | REPLACE with free position/size, new snapping module, one window-frame guide |
| View/col commands | KEEP with changes: drop dock/source; col.* go (collections become runtime results) |
| Locks, quotas/health, events | KEEP with changes: submit-time checks, viewport-intersection on-screen, runtime health |
| Controls spec + view | RETIRE for v1 |
| Intake pipeline (spec, sources, screener, keys, scan, watch) | KEEP with changes: becomes the intaker link's params and resolve; cache keyed by link id |
| Readers, worker pool, shared memory | KEEP; pool also hosts out-of-process Python link resolve |
| Endpoint, SDK, CLI | KEEP (regenerate; one canvas) |
| MCP | KEEP with changes: tools reduce to state / describe / schema / submit_canvas / snap / undo; in-process for the Agent SDK |
| app.py / main | KEEP with changes: one window, prompt-bar session, remove changeset/palette/panels wiring |
| Canvas controller + split/tab QML, parked shelf, FrameWindow, windows.py | REPLACE / RETIRE: pannable 1:1 canvas with snapping |
| ViewSlot chrome | KEEP with changes: header, close, maximise, move/resize, chain-coloured stroke |
| Overworld (cards, snapshots, PauseKeeper) | KEEP with changes: positions from the document, no wire-drag creation, add link nodes + summaries |
| Changeset bar, ghost cards, diff panel, changeset.revert | REPLACE with reply strip + Undo + brief highlight |
| Files/Intake/Links panels, add view, link mode, drop/compose, palette | RETIRE (reuse Files list_folder + ThumbProvider for the file tree view) |
| Welcome/launcher | KEEP with changes: open a data folder only |
| GalleryItem, MpvItem | KEEP |
| View hosts base/registry | KEEP with changes: emit(port) / receive(port), the seam for the channel-to-link swap |
| gallery, image, video, json, markdown, map views | KEEP with changes: ports per #32 (gallery adds sort) |
| timemap | KEEP, moves into time_map link resolve |
| timeline view type, diff view type | RETIRE (timeline bar stays inside video; diff page stays for diff-on-request) |
| file_tree view, 3D view | NEW |
| Web bridge, capabilities, items, diff_data | KEEP with changes: port messages, port capabilities, resolve-output items, diff of one submit |
| Theme | KEEP with changes: link-chain colours, values from the Figma variables |
| gui_drive/gui_session/desktop_lock, benches | KEEP; gui_flows (29) REWRITE; demo_intaker RETIRE; build_openusd PARK |

## Migration risks

1. Channels to directed links: the host seam (publish/chan_set/receive_channel, channels_in/out, chan: capabilities) spans ~15 files; channel types pan_zoom2d, scalar, scroll, hover_time have no v2 port type. Breaks core test_channels, test_time_channels, test_undoable, app gallery/video/controls/web tests, mcp, sdk.
2. Collections stop being document objects: hosts, web items and the read:col: capability bind via view.source to col ids. Breaks all intake tests and several app tests.
3. Document shape and patch paths: lists give index-based JSON Patch paths, while undo, provenance, highlight and the journal key on /objects/<id>; store id-keyed maps or diff by id. Handler-time checks must move to submit validation.
4. Groups to free placement with snapping: layout, groups, canvas.place/dock/undock, live/on-screen definitions, PauseKeeper and quotas. Breaks many core and app tests (the core `composed` fixture docks into g1).
5. Multi-canvas/multi-window assumptions throughout (Engine.canvases, canvas.new/ls/open, frame.*, windows.py, MCP canvas_new, SPECTO_CANVAS); keeping canvas.new internally as "ensure the one canvas" limits breakage.
6. Changeset UI removal breaks shell tests.
7. Panel retirement breaks files/intaker/drop/watching tests and ~all 29 gui_flows.
8. A v1-to-v2 document migration would be lossy (n-member channels to directed links, synthesized summaries, groups to positions, parked views, journal cannot replay).
9. Generated surfaces must be regenerated after command changes.
10. Prototype gaps: drop window_frame.docked and global_clock as document fields per later decisions, type link params with a tagged union, add file-tree and 3D view types, implement Python-link trust (ADR 0006), build the resolve runtime on the worker pool.
