# Specto v2 contracts (specto-core 0.1, api_level 1)

What other packages implement or call. The code in `packages/specto-core` is the
reference; where this document and the code disagree, the code wins and this document
is the bug. Decisions are cited as ADRs (`docs/adr/0011`-`0017`); the HLD
(`docs/review/hld.md`) is superseded where they conflict. Vocabulary: `GLOSSARY.md`.

Everything here is Qt-free except sections 11-13. `specto-app` is the only package that
imports PySide6.

## 0. Vocabulary

| Term | Means |
|---|---|
| project | A data folder with exactly one canvas (ADR 0012); `.specto/` inside it holds the canvas document, its journal, Python link code and the agent session id. |
| canvas document | The one JSON document that completely describes the canvas: views, links (intakers among them), layout, root view (`specto_core.canvas_document`). |
| view / port | A view is one live display; it declares typed input and output ports in code (`specto_core.ports.VIEW_PORTS`), never in the document (ADR 0014), and one view role, browser, viewer, player or scene (`VIEW_ROLES`, ADR 0020). |
| link | A directed edge from an output port to an input port with a kind (`intaker`, `follow`, `read_contents`, `time_map`, `python`), typed params and a required one-sentence summary. |
| submit | The whole document, or an edit of the live one (a JSON Merge Patch, #98), handed in by an agent or a gesture; the resulting document is validated, diffed against the live document and applied as one undo step (ADR 0013). |
| prompt turn | One request in the prompt bar; its submits are squashed into one undo step when it ends (ADR 0013 amendment). |
| origin | Who did it: `user` (gestures, CLI by default), `agent:prompt` (the prompt bar), `agent:<session>` (MCP). |
| resolve | Working out what a link needs (scan, parse, run code, build lookups) when its logic or inputs change, as a job off the GUI thread (ADR 0016). |

## 1. Engine (what the app, the SDK in-process tests and intake call)

```python
from specto_core import Engine, CallContext, Project

project = Project.open(folder) | Project.init(folder, name) | Project.open_or_init(folder)
engine = Engine(project: Project | None = None, *,
                registry=specto_core.registry.registry,   # the command registry
                plugins: PluginRegistry | None = None,     # default: built-in manifests
                quotas: Quotas | None = None,              # section 9 defaults
                jobs: JobManager | None = None,            # default: InlineJobManager
                autosave: bool = True, journal: bool = True,
                clock=time.monotonic)

engine.live   -> LiveCanvas      # the project's one running canvas (section 3)
engine.links  -> LinkRuntime     # the link runtime (section 6), built lazily from the document
engine.plugins, engine.jobs, engine.quotas
engine.call(name: str, args: dict | Args | None, ctx: CallContext | None = None) -> dict
    # Result.model_dump(); raises specto_core.errors.SpectoError subclasses
engine.api_info(theme_tokens=...) -> dict; engine.export_api_info(path)   # api-info.json
engine.app_events: EventStream   # job.* events (process level)
engine.close()                   # stops watcher threads, saves
```

`CallContext(connection="local", origin="user")`. The endpoint sets `connection` per
socket and `origin` from the client's hello. `CommandContext` (what a command handler
gets) is `engine`, `origin`, `connection`, `report`.

Errors: every `SpectoError` has `.code`, `.message`, `.fix` (may be None), `.data` and
`.to_dict()`. Codes: `invalid_args`, `not_found`, `ambiguous`, `bad_address`, `locked`,
`quota`, `schema`, `unknown_command`, `patch`, `invalid_document`, `capability`.

A project without `.specto/canvas.json` (a v1 / M2 project with `.specto/canvases/`) is
refused on open with code `schema` and a message naming the fix (open another folder or
remove `.specto/`); there is no migration (ADR 0017).

## 2. Canvas document (ADRs 0012, 0013; `specto_core.canvas_document`)

```text
{ schema_version: 2,
  project:      {label},
  window_frame: {rect: {x, y, w, h}},        # the app window on the canvas; see section 3
  root_view:    "rena" | null,               # the player other views follow for time
  views: { "fege": {type, label, layout: {position: [x, y], size: [w, h]}, params, locked}, ... },
  links: { "l1": {kind, label, source: {view, port}, target: {view, port}, two_way,
                  params, summary: {sentence, diagram?},
                  python?: {path, entry, resolves_to, output}}, ... } }
```

- Ids: a view's id is its name (ADR 0018, `specto_core.names`): two CV syllables such
  as `kobi`, handed out by the app, never changed once given; the file tree is named
  like any view (`fege` on a fresh canvas) and code finds it by type `file_tree`, never
  by id. A new view submitted under a placeholder key (`v9`, `gallery2`) gets a
  generated name, reported in `SubmitResult.renamed`; legacy ids (`v<n>`, `files`) are
  renamed once on load (one journal entry, no undo step). Links are `l<n>`; a new link
  submitted under a placeholder key (`new2`) gets the next free `l<n>`, in `renamed` too. The map key
  is the id, so patch paths are stable. `source.view` may be `project` (the project source, port `all_files`), the one link
  source that is not a view; `project` is never a view key. Every view must be
  reachable from `project` by links (ADR 0018 amendment, #83).
- View types: `file_tree`, `gallery`, `image`, `video`, `json`, `markdown`, `map`, `3d`
  (`specto_core.ports.VIEW_TYPES`). `params` is a free dict per type; the manifest's
  `defaults` and `describe view:<type>` document the keys. `params.selected` (gallery:
  the picked key; file tree: the picked rows; video: the selected item) is written by
  the hosts as navigation (section 3) and may be set by an agent; so are the video's
  `params.muted` and `params.volume` (#216).
- Link kinds are a tagged union on `kind`, each with its own params model:
  `intaker` (`sources`, `screener`, `keys`, `group_by`, `sort`, `watch`, `timeline`; and
  `scope` on the link, required on every intaker but the file tree's own: `selection`
  off a file tree's `selected_files` follows the user's picks, `pinned` off
  `project.all_files` shows fixed sources; ADR 0014 amendment),
  `follow` (`match`), `read_contents` (`sources`, `format`), `time_map`
  (`from_timeline`, `to_timeline`, `offset_ms`), `python` (`python: {path:
  .specto/links/<name>.py, entry, resolves_to: table|samples|value, output: <port type>}`
  plus free `params`). `summary.sentence` (>= 8 chars) is required on every link.
- Validation (`validate_canvas(data)` / `validate_canvas_text(text)`) raises
  `CanvasInvalid` (code `invalid_document`) whose `problems` list **every** problem at
  once as `{path, message, fix}`: schema, unknown views or ports, port types per kind
  (`BUILTIN_KINDS`), one link per input port, two-way only on `time` and `selection`
  (`TWO_WAY_PORT_TYPES`), no cycles on the port graph (links plus each view type's
  `propagates` edges), a root view that is a player, duplicate ids in the JSON text,
  Python links that declare their output. At submit time (section 3) locks, quotas and
  unknown files join the same list.
- `canvas_json_schema()` is exported as `api.info().canvas_schema`, `describe canvas`
  and the `schema` agent tool, together with the worked example
  (`specto_core.canvas_example`). `describe link:<kind>`, `describe port:<type>` and
  `describe view:<type>` (with `role` and `ports`) read the same tables.

## 3. Submit, undo and navigation (`specto_core.live_canvas.LiveCanvas`)

```python
live = engine.live                       # LiveCanvas.open(project); LiveCanvas.headless("demo") for tests
live.data                                # the live document dict (read only; never cache across patches)
live.document()                          # a deep copy to edit and submit
live.submit(document, origin="user", label="") -> SubmitResult
live.submit(edits={"views": {"kobi": {"layout": {"position": [330, 0]}}}}, origin=..., since=mark)
live.mark() -> int                       # a turn start's id: submit(since=) merges with what the user changed after it against the agent's copy
live.owner_thread; live.serial_lock      # submits run on the owner thread (the GUI thread) or holding serial_lock
live.check_thread()                      # off both: raises under tests, warns in the app (SPECTO_THREAD_CHECK)
live.undo() / live.redo() -> UndoResult; live.history: list[TxnRecord]; live.cursor; live.top_id
live.squash(first_txn_id, label) -> TxnRecord | None    # one prompt turn -> one step
live.navigate((x, y)) -> bool            # the window frame's origin follows a pan
live.remember(view_id, {"selected": ...}) -> bool       # a view's picks, for reopen
live.on_patch(cb) -> unsubscribe         # cb(patch, meta); meta.kind: submit|undo|redo|load|navigate|squash
live.events: EventStream                 # doc.patched, canvas.submitted|squashed|saved|save_failed, history.restored
```

- `submit` validates (nothing changes on `CanvasInvalid`), diffs against the live
  document with `patch.make_patch(old, new)` (id-keyed JSON Patch: one add/remove per
  view or link, one replace per changed field), applies the difference as one
  `TxnRecord` and one journal entry, and returns `SubmitResult {txn, label, origin,
  empty, patch, touched, views: {added, removed, changed}, links: {...}, renamed:
  {placeholder -> name or l<n>}, cursor, depth}`. An equal document is `empty` (no step).
- `edits` instead of `document` (exactly one; `specto_core.edits.merge_edits`): a JSON
  Merge Patch over the live document that may name `views`, `links`, `root_view` and
  `project.label`; `null` on a view also removes its links and clears `root_view`. The
  merged document then takes the path above unchanged. With `since` (a `mark` id), a
  non-user submit is judged against the agent's copy (the mark's, advanced by each
  accepted submit of the agent's: an edit by the fields it named, an object it created
  whole, a document whole; the agent's own undo rewinds it) for each view or link the
  user changed after it: an edit by the fields it names, a whole document three ways
  (`edits.three_way`, fields per `edits.FIELD_PARENTS`); a field both changed
  differently, or a user's link left without its view, raises `CanvasConflict` (code
  `conflict`) with the live entry and the fields (ADR 0013 amendments, #98, SPEC-18).
  A submit without a label gets `diff_text.patch_label(patch, inverse)`.
- Submit-time rules, all returned together with fix-its: `locks.lock_problems` (a
  non-user origin may not move, resize, delete, unlock or relink a `locked` view),
  `quotas.quota_problems` (section 9), `unknown_files.unknown_file_problems` (an intaker
  source matching nothing in the project folder, a missing Python file).
- The window frame: its size follows the app window as a user submit
  (`gestures.set_window_frame`), its origin follows the user's pan as navigation
  (`navigate`: journal kind `navigate`, no undo step). A non-user submit keeps the live
  frame (origin and size), so a stale agent document never drags the window.
  Culling and quotas both count the views touching this frame.
- `remember` writes navigation-class view state (`params.selected`) the same way: the
  file tree and gallery hosts call it on a pick, and a reopened project picks the same
  rows and frame again (spec #35 story 4); the video host writes `muted` and `volume`
  through it (the speaker button, the volume slider).
- Undo history is rebuilt from the journal (`.specto/canvas.journal.jsonl`) on open, so
  it survives a relaunch; `history_note` says when the journal could not be replayed.
- `save` never raises on a disk error (a sync client holding `canvas.json`): the canvas
  stays dirty in memory, warns once per streak (`[WARN]` and a `canvas.save_failed
  {path, error, what, fix}` event, which the app shows as a notice) and the next change
  tries again (`[OK] ... saved again`).
- Human gestures are submits with `origin="user"`, one undo step each
  (`specto_core.gestures`: `move_view`, `resize_view(position=)`, `close_view`,
  `delete_link`, `set_locked`, `open_image_view`, `set_window_frame`, `open_file(tree,
  path, mode=, plugins=)` with `mode` `dedicated` (a view of the file's kind fed that one
  file by a pinned link; an existing one is `focused`, not duplicated; a kind no view
  shows opens a `text` view) or `contextual` (the preview on the tree's picks, the file
  picked first; a kind the preview cannot render falls back to dedicated), SPEC-32, ADR
  0023).
- Journal entries: `JournalEntry {seq, ts, kind: txn|undo|redo|navigate|squash, txn,
  txn_no, origin, label, patch, inverse, touched}`; `journal.replay` applies them.

## 4. Commands and the agent tool set

Every command has pydantic `Args`/`Result`, `describe <name>` gives its schema and
`list_commands` the names (`api-info.json` carries both). Registering one:

```python
from specto_core import command, Args, Result, CommandContext


@command("snap", doc="...", read_only=True, scope="project")  # scope: project | global
def snap(ctx: CommandContext, args: SnapArgs) -> SnapResult: ...
```

| Command | Args | Returns |
|---|---|---|
| `canvas.state` | | `{document, undo: {cursor, depth, history}, project}` |
| `canvas.submit` | `document` (object or JSON text), `label` | `SubmitResult` |
| `canvas.undo` / `canvas.redo` | | `UndoResult {txn, label, patch, touched, cursor, depth}` |
| `canvas.history` | `last`, `txn`, `patch` | the newest submits one line each; `patch=true` adds each one's `patch`, `inverse` and `diff` (`specto_core.diff_text.describe_patch`) |
| `events.poll` | `since_seq`, `limit` | the canvas events after `since_seq` (a `gap` event first if the ring rolled) |
| `link.approve` | `link`, `from?`, `labels?` | asks the user to approve the Python file a link references, as it is now (#203, ADR 0006): the window shows an approval card naming the requester (verified from the caller's nimitz `labels`, else claimed) and returns `{link, path, hash, target, status: pending, request}`; only the user's click on the card (or the view's Approve) records the hash and the link resolves. `status: approved` when the user approved this content already (no card). Headless: an error. Nothing on this path approves |
| `describe` / `list_commands` / `vocabulary` / `api.info` | `target` / `prefix` | self-description |
| `intake.preview`, `intake.keys_preview`, `intake.aliases` | | what an intaker spec would take, pure |
| `job.status` / `job.list` / `job.cancel` | `job` | jobs (section 8) |
| `snap` (app) | `target`, `image`, `layout`, `path` | section 12 |
| `shell.locate` (app) | `name` | section 12 |
| `message` (app) | `from`, `text`, `reply_to?` | a peer request (#187, section 13): `{id, mode, status, instance}`, status `running`, `queued` or `waiting`; error `refused` when Peer requests is off |

The agent tool set (ADR 0017; `specto_core.agent_tools.AGENT_TOOLS`, `AgentTools(call)`)
is exactly six, identical in-process (the prompt bar, section 13) and over MCP
(section 10): `state` (= `canvas.state`), `describe`, `schema` (the document's JSON
Schema and worked example), `submit_canvas {document | document_file, label}`
(= `canvas.submit`), `snap {target, image?}` (the app's `snap` when the app registered
it, a document-only snapshot otherwise), `undo` (= `canvas.undo`). `ToolSpec.params`
owns each tool's argument schema; the SDK MCP server and `specto-mcp` read it from there.

## 5. Ports (ADR 0014; `specto_core.ports`)

Port types: `files`, `collection`, `item`, `selection`, `time`, `content`, `camera`.
Two-way links only on `time` and `selection`. Per view type (`VIEW_PORTS`):

| View | Inputs | Outputs | Propagates (input -> outputs, for the cycle check) |
|---|---|---|---|
| `file_tree` | `data: collection`, `selection` | `selected_files: files` | `data`, `selection -> selected_files` |
| `gallery` | `data`, `selection`, `time` | `selection`, `time` | `selection -> selection` |
| `image` | `data`, `selection` | `selection` | `selection -> selection` |
| `video` | `data`, `time` | `time`, `selection` | `time -> time` |
| `json`, `markdown` | `data`, `selection` | `content`, `selection` | `data, selection -> content` |
| `map` | `features: content`, `selection`, `time` | `selection` | `selection -> selection` |
| `3d` | `data`, `camera`, `selection` | `camera`, `selection` | `camera -> camera`, `selection -> selection` |
| `html` | `data`, `selection` | `selection` | |
| `pdf` | `data`, `selection` | | |
| `preview` | `data`, `selection` | `selection` | |
| `scratch` | `selection` | `items: collection`, `selection` | |
| `text` | `data` | | |

The project source `project` has one output, `all_files: files` (the whole folder). The
intaker from it into a file tree is never walked (#231, ADR 0022): the tree receives a
collection with the root alone and lists its folders itself, one level at a time on its
own worker (`specto_intake.listing.TreeLister`, over the intaker's sources and screener),
resolves a folder pick to its files when it is picked, and follows the folders it has
loaded while the intaker says `watch`. `LinkRuntime.project_files` lists the whole folder
only once some other whole-folder intaker off the project source resolved it (None until
then: the project node shows no count, the prompt bar walks once, cached). The tree's
type-to-search (#232) is `specto_intake.search.FileSearch`: `search(root, text, limit,
hit_filter, cancelled) -> SearchResult(hits, more, backend)`, Everything over its SDK DLL
on Windows when it can answer for the root (`specto_intake.everything`), else a
cancellable walk; hits pass the tree's `HitFilter` (`TreeLister.hit_filter()`: files
`accepts`; folders, a hit by their own name, `accepts_dir`), and while there is text the tree shows the search tree, built
from the hits alone (SPEC-38, ADR 0022 amendment), whose rows take every gesture a row
does; a picked hit goes out on `selected_files` like a row. Built-in kinds map
(`BUILTIN_KINDS`): `intaker` files -> collection, `follow` same type both sides,
`read_contents` files -> content, `time_map` time -> time; `python` declares its output.

Port values (`specto_core.links`, `timebase`, `contents`):

```python
FilesValue(root, paths | None, stats | None)  # paths relative to root, POSIX; None = the whole folder;
                                        # stats {path: [size, mtime_ns]} the sender already knew (#76)
Collection(items: list[Item], root, generation, link)
Selection(keys, last); .picked          # the selected keys and the last one picked
TimeValue(t, timeline)                  # float seconds on a named timeline ("stamp", "video", "index")
ContentValue(root, entries: [ContentEntry {key, path, format, data | error}])
CameraValue(position, target, up, fov_y, near, far)
```

## 6. Link runtime (ADRs 0014, 0016; `specto_core.links.LinkRuntime`, `engine.links`)

```python
links.attach(view_id, receive)          # receive(port, value); already-resolved inputs are replayed
links.detach(view_id)
links.emit(view_id, port, value) -> [link ids started]
links.input_of(view, port) / output_of(view, port) / inputs_of(view) / collection_of(view)
links.status(link_id) -> {state: idle|resolving|ok|error|needs_approval, message, ...}
links.resolving(link_id); links.resolve(link_id); links.resolve_all()
links.approval_request(link_id) -> {link, path, hash, target, approved}  # what link.approve asks about
links.approve(link_id)                  # Python links (ADR 0006): the USER's gesture only (the view's
                                        # Approve, the approval card; never a command, #203)
links.refresh(view_id=None) -> [link ids]  # rescan: the project's links, or a view's (stat afresh), #102
links.retarget(view_id) -> [link ids]   # the view's type changed: its inputs and outputs forgotten, its links
                                        # resolved again, fresh (a Python table is rebuilt); a resolve in flight
                                        # for the old type never lands (SPEC-48). Once per type change: the
                                        # runtime's own patch listener calls it (host or no host yet, SPEC-84)
                                        # and so does the registry's rebuild; the later call returns []
links.poll() -> [link ids]              # the app's 8 ms timer: delivers job results, watch arrivals
links.watching(); links.watch_status(link_id)
links.global_clock: GlobalClock         # section 7
register_resolver(kind, fn, needs=(), inline=False); register_watcher(kind, fn)
```

- A view emits on an output port; every link whose source is that port resolves and
  the result lands on the target view's input port. Resolution is a job on
  `engine.jobs` (worker threads or processes, never the GUI thread); while one is in
  flight new inputs for the same link coalesce (latest value wins). A kind registered
  `inline` (`time_map`) is a pure lookup on the emitting thread so playback time flows
  at frame rate.
- Resolvers are picklable module-level functions `fn(link, value, root, env)`; the link
  entry carries `target_type` (the target view's type) and, for a two-way link resolved
  from its target end, `reversed`. `needs=("collections",)` hands a resolver the
  collections on both ends: that is how `follow` matches on one key field
  (`params.match`) across collections with different keys; such links re-resolve when a
  collection lands on either view. A value delivered to a two-way port that equals what
  that view last emitted is dropped (echo guard).
- No re-delivery of unchanged results (#102): a result equal to what the link already
  delivered (a collection with the same items in order, key fields, parts and stat per
  part; equal parsed contents) is not delivered again. The event `link.resolved` still
  fires with `unchanged: true`, but the target keeps its collection and generation, so
  nothing downstream (follow, Python links) runs again. A file tree's picks carry the
  stat it holds (`FilesValue.stats`); the same picks with the same stat emitted again
  resolve nothing, and the intaker uses those stats instead of stat'ing the files.
- `specto_intake` registers `intaker` (`resolve_intaker`: sources, screener, keys,
  grouping, sort over the files arriving on the input; `.specto/` never enters a
  collection), its watcher (`watch_intaker`), `read_contents` and the 3D scene buffers
  (`scene3d.attach_scenes`, for intakers feeding a `3d` view). For a `.dae` that step
  writes the converted mesh `<name>.dae.glb` beside it, or in the cache when the folder
  cannot take it (`specto_readers.dae`, ADR 0028); a converted mesh whose DAE is in the
  same collection gets no scene, so the model shows once. `attach_scenes` takes the
  collection's `root`; an OBJ's MTL and textures outside it, or missing, are listed under
  the scene's `missing`. The items host serves a part's trailing path that starts with
  `@root/` from the root of the resolver that answers for the link (`ItemResolver.root`,
  None by default), never outside it, and only an MTL, a buffer or an image
  (`scheme.ROOT_SUFFIXES`). A part's own recorded path passes
  `specto_core.pathguard.output_refusal` against the resolver's `guard_root` before it
  is stat'ed (`scheme.part_refused`, status `refused`; SPEC-75), the backstop for every
  host that serves through items whatever produced the item; a container member is
  judged by its archive. `guard_root` is the root of what delivered the item (a
  collection's or a content value's, else the project's; an intaker table's; the
  scratch store's project), so a project on a share serves its own parts and nothing
  on another share (SPEC-91). A part with a bare leading slash is refused where it is
  no absolute path (Windows: the current drive's root). The one leading-slash rule is
  `pathguard.below_root`. A trailing path's text (and the text after
  `@root/`) passes `specto_core.pathguard` before any filesystem call (SPEC-61, ADR 0006
  amended): an
  allowlist, shared with the page host's asset paths (`scheme.resolve_asset`, whose
  decoded URL path may not be rooted either), the 3d reader's own look-up of the
  references it reads out of a file (`pathguard.reference_refusal`: an OBJ's MTL and
  texture names, SPEC-68, named under `missing` and never looked for, and an MTL is
  opened only when it resolves inside the root, SPEC-70, the OBJ's folder and the root
  compared by their resolved names, SPEC-80; a DAE's image
  names, SPEC-69, refused in front of trimesh's resolver so the model converts without
  that texture) and xacro's includes
  (section 10), that
  accepts only a relative path, a Windows drive path or a POSIX single-slash path, so a
  network path (`\\host\share`, `\??\UNC\host`), an NT object or device path, a reserved
  device name or a stream name is refused by its text and never makes the machine connect
  out or open a device. An intaker's `params.sources` and an intake spec's roots pass the
  same guard (SPEC-72): the canvas document's validation refuses such a source with a
  problem before the submit asks whether it exists, `intake.preview` answers with an
  error (a source string is judged whole before a glob is split out of it, so both agree
  on `\??\UNC\...`), and `specto_intake.sources.resolve_root` refuses it as the backstop
  before any walker, listing or resolver resolves it. The submit's unknown-files check
  never walks an absolute glob: it asks only whether the glob's base folder exists.
  The scratch pad's paths (section 11: its link targets, drops, `markdown_of` and
  `link_to`, and a legacy card's path or file at migration, SPEC-67, SPEC-71) and the
  prompt bar agent's `scratch_append` `image` and `file` arguments (SPEC-73) pass it
  too.
- Watch (story 57): for every link with `params.watch`, a `LinkWatch` thread follows the
  kind's sources; a change posts to the runtime, `poll()` emits the event
  `link.arrivals {link, target, added, removed, changed, arrived}` and re-resolves the
  link once the folder has been quiet for `watch_debounce_s`; the new collection
  generation swaps in. `arrived` names the added entries as the watcher does (the
  intaker's: `<source index>:<rel path>`, what an item's `meta.source` and `meta.rel`
  hold; an archive is one entry, `0:drive.zip`, and its members' items go by it:
  `links.arrival_entry`), at most `ARRIVALS_CAP` (1000) of them; `added` counts them
  all (SPEC-59), whether a listing or a change notice found them (`WatchListing.apply`
  returns them beside its counts). The resolve re-runs the whole intaker (ADR 0016
  note). `engine.close()` stops and joins
  the watchers. ADR 0027 (SPEC-8): a kind that registers a `WatchListing` beside its
  watcher (`register_watcher(kind, fn, listing=...)`; the intaker's is
  `specto_intake.watch_listing.IntakerListing`) is followed through change notifications
  (`specto_core.fswatch`, watchdog; one watch per folder per process): the watcher
  lists everything once, then applies only the paths the notices name, and lists
  everything again every `RELIST_S` (30 s, at least 50 listing durations apart; 10 to
  30 s on a network drive), once a storm of notices settled, after a lost-notice report,
  when a source root itself changed, and when a watched folder was renamed or replaced
  (armed again first). Where notifications cannot start, stop, or never come (two
  safety-net listings in a row finding unreported changes), it lists every
  `watch_interval_s` as before, trying notifications again at a doubling gap up to
  5 minutes. `links.watch_status(id)` reports
  `{running, polls, interval_s, mode: notify|poll, reason, notices, decided}`
  (`decided` false only while the first arming is under way, after the baseline,
  SPEC-83); the event `link.watching {link, target, mode, reason}` fires when a watcher
  settles on its mode (notify only once armed) and when it changes (a watcher that polls
  keeps its mode and reason while a retry arms), and `links.watch_note(view)` is the status bar's note after
  "auto-update on". `SPECTO_WATCH=poll` makes every watcher poll. The notices come from
  one watchdog observer per process (`fswatch._Hub`), one watch per folder fanned out to
  its `Notifier`s. The hub's lock is never held across a watchdog call: a folder's
  watch is `arming` while scheduled, `armed`, or `reaping` while unscheduled, outside
  the lock, and a subscriber for a folder arming or reaping waits `ARM_TIMEOUT_S` (10 s)
  for that to end, then is refused. A folder nobody follows keeps its watch `LINGER_S`
  (5 s) before the reaper unschedules it. Every schedule and unschedule runs on a
  helper thread waited for `WATCHDOG_CALL_TIMEOUT_S` (2 s); one that never returns costs
  its observer (given up, its watches no longer alive, the subscriber refused; the next
  subscriber starts a fresh one; its emitter threads run on until the exit). `fswatch.NotifyUnavailable` carries `reason`
  (`Unavailable.MISSING`: tried again after each listing; `REFUSED`: tried again on the
  backoff; `UNSUPPORTED`: polled for good). `fswatch.shutdown(final=False)` stops the
  observer and drops every watch, bounded at about `STOP_TIMEOUT_S` (1 s) in all (lock
  acquire plus the join of the thread stopping the observer); while it runs the hub is
  closed (an arm in flight is undone, a woken waiter refused, no new observer starts),
  and `final` (the `atexit` registration made when the observer starts) leaves it
  closed. `links.stop_all_watches()` (the test sweep) calls it after stopping the
  watchers.
- Auto-update and refresh (#102): `params.watch` is off by default; the file tree's intaker
  is on unless it says false (#115: new canvases write it, a submit or load that leaves it
  out means on, and a canvas saved before #115 turns it on once on open, recorded as
  `rewrites = ["tree-watch"]` in `project.toml`); the view header's switch (`gestures.set_auto_update`, one user submit) turns
  it on for every intaker feeding that view, and the agent turns it on for folders an
  agent writes into. With it off, `links.refresh` (command `links.refresh {view?}`, also
  the project node's and the view header's refresh button) rescans once; no undo step.
  The file tree's own watch is the tree's (#231): no `LinkWatch` runs for its intaker;
  the tree lists its loaded folders again every `watch_interval_s` and applies arrivals
  in place; a refresh of the project or the tree hands it its root again, which lists
  the loaded folders once.
- Python links (`specto_core.python_links`): `entry(items, params, root)` in a file under
  `.specto/links/` returns JSON, a `table {source key: value}` or `samples [[number,
  value], ...]`; it runs once at resolve time, in its own `python -I` process from the
  worker, only after the user approved the file's sha256 in the window (the view's
  Approve button, or the approval card a `link.approve` request opens, #203; the trust
  list lives in the user data dir, `SPECTO_USER_DIR`). No command, tool or process
  approves: `link.approve` only asks. Unapproved or changed code never runs; the link's
  status is `needs_approval` and the target view shows an Approve button. Tests with no
  window call `engine.links.approve` through the root conftest's `approve_link` fixture
  (test-only; not a flag). A
  source value only looks the table up. Per pick (`resolves_to: value`, #168):
  `entry(value, items, params, root)` runs for each new source value (files `{root,
  paths}`, selection `{keys, last}`, time a number of seconds, other ports their JSON),
  out of process, debounced (`python_debounce_s`, 150 ms: the first pick after a quiet
  spell runs at once, a burst runs only its last, from `links.poll`); a result for a
  value the source no longer holds is dropped. `items` is the source view's collection,
  none for a file tree (its data is the whole project; the script has `root`). Every
  output value (table and samples values too) converts to the `output` port type: files
  `{root?, paths}` or a list; selection a key, a list or `{keys, last?}` (into a file
  tree, backslashes and absolute paths under the project become its relative POSIX
  keys); content `{root?, paths}` or a list (the files are read, a folder stands for its
  readable files, a map's for its GeoJSON files) or `{root?, entries}`, GeoJSON only into
  a map; time a number or `{t, timeline?}` (no timeline: the target's own,
  `timebase.OWN_TIMELINE`); camera its model; collection an intaker-grade one (#176):
  `{root?, items}` or a list of items, an item a path or `{key?, parts: {role: path} |
  [path, ...], keys?, meta?}`, each part checked on disk and recorded with `meta.rel`
  and `meta.stat` as an intaker's, keys defaulting to the path without extension, and a
  3d or preview target prepared with its scenes (`links.register_preparer`, the project
  the root that bounds what an item may name, SPEC-80); None
  delivers nothing. A wrong shape fails the link with a fix-it (`python_links.SHAPES`).
  Every root and path an output names (a content output's `entries[].path` too) passes
  `specto_core.pathguard.output_refusal` against the project root before any filesystem
  call (SPEC-75: the code is approved, its output is agent-written text): a network or
  NT object path, a device or stream name fails the link with the same fix-it, before it
  is stat'ed. A single leading slash means a path below the root: files and content
  paths drop it; a collection part drops it where it is no absolute path (Windows; on
  POSIX `/x` is absolute). A network path is accepted only when the project root is
  itself a plain network path (`\\host\share\...`) and the path, normalised (`..`
  resolved), is that root or lies under it; under such a root a relative path is
  normalised against it too, so `..` never leaves it.
  The code runs as `python -I -X utf8` (no script folder, working folder or PYTHONPATH on
  `sys.path`; files and the console are UTF-8); its prints go to stderr. What it gets
  wrong is the job's `ScriptFailed` result: the status (canvas.state) and `link.failed`
  carry the message and `trace`, the script's own frames. A run longer than
  `python_timeout_s` (120 s) fails and its process tree is killed. A failure is kept
  under its resolve key: new data on either end does not run it again, a new pick, new
  params or new code do; `links.refresh` runs the code again. Each run starts a fresh
  interpreter (about 75 ms, plus its imports: numpy about 110 ms) and sees only the
  app's environment's packages. The file tree's `selection` input (keys: relative paths of
  files or folders) selects, reveals and scrolls to them, remembers them in
  `params.selected` and sends their files on `selected_files`; it is not republished
  as a selection.
- Health is runtime state, never in the document: while a link resolves its target
  view is `loading` ("resolving <link>"); a failed resolve is `error` with the reason.

## 7. Time (`specto_core.timebase`)

A player (`video`) emits `TimeValue` on its timeline (the sidecar
`<name>.timeline.json`'s clock, default `stamp`; `video` = seconds of pts without one)
while playing and when a step, scrub or pause lands; a `time` it receives seeks the
nearest frame and is re-emitted once as the landed frame. `time_map` links convert
between timelines with an offset. The **global clock** (`engine.links.global_clock`)
keeps the root view's latest time (`now`) and a key <-> time `TimeTable` per view,
built when a collection lands (item stamps from `meta`, the keys regex or a 13-19 digit
run in the file name; the intaker's `params.timeline` names the clock). A gallery shows
a received time as its nearest frame (`nearest`), its **current item**: marked apart
from the selection ring and revealed, never selected (SPEC-6; `snap` says `current`),
and emits the clicked frame's time (`time_of`), which makes that frame its current item
too, so a 32 fps video and 16 Hz cameras stay in sync. A time on
no named timeline (`OWN_TIMELINE`, ""; a Python link's bare seconds) is taken on the
receiver's own: the video's, the usd stage's, the gallery's clock. The video's
A-B loop, hardware decoding, speed and selected item are agent params (`loop`, `hwdec`,
`speed`, `selected`, `timeline`), not buttons. Sound (#216): `muted` (default true) and
`volume` (0 .. 1) are params too, with a speaker button and a volume slider in the
footer that remember them as navigation; at most one video view per window has sound
(unmuting one mutes the others), and mpv is muted whenever the view is not in normal
playback (paused, scrubbing, a seek or step in flight), so linked time behaves as
before. Thumbnails and posters never open an audio device.

## 8. Jobs, events, readers, intake

- Jobs (`specto_core.jobs`): `JobManager.submit(kind, fn, *args, on_done=, **kw) ->
  JobInfo`, `cancel`, `status`, `list`; `InlineJobManager` (tests), `ThreadJobManager`
  (core, link resolves), `PoolJobManager` (app: `cores - 2` processes, no Qt in workers,
  results in shared memory). `fn(*args, progress=...)` returns a picklable value or a
  `BufferSpec`; `progress` is a `jobs.Progress`: `progress(p, message)` reports and
  `progress.cancelled()` says the job was cancelled (the pool sends `("cancel", id)` to
  the running worker), so a long job stops; a job that never asks runs on and its result
  is dropped. `specto_readers.map_refs(refs, one, progress)` is the loop of a batch job:
  one result, or `{"error", "uri"}`, per ref. Events `job.progress|done|failed` on
  `engine.app_events`.
- Events (`EventStream`, ring per canvas): `doc.patched {txn, patch, kind, origin,
  touched}`, `canvas.submitted`, `canvas.squashed`, `canvas.saved`, `canvas.save_failed
  {path, error, what, fix}` (section 3), `history.restored`, `view.health`,
  `link.arrivals`, `job.*`. `events.since(seq)` prepends `gap` when the ring rolled.
- Readers (`specto_core.kinds`, `specto-readers`): `Locator.can_handle(path) -> priority`,
  `locate(item) -> DataRef`; `Decoder.decode(ref, DecodeRequest) -> TypedBuffer`
  (`uint8 HWC` thumbnails, bytes for text/json) in a worker; manifests
  (`LocatorManifest`, `DecoderManifest`, `ViewManifest {type, label, doc, guide, accepts,
  host, cost, defaults, state_schema, example, uses}`; a view's `doc` says what it shows
  and what picks it, and each param's `description` is one clause, since both ride in the
  agent's standing reference, `specto_app.prompt.reference`, under 19.5k characters for
  core plus the app's views, tested; `guide` holds the controls and the long form of the
  params, which only `describe view:<type>` shows, SPEC-56) in a `PluginManifest` so `describe` and
  aliases (`@images`, `@json`, `@geo`, `@markdown`, `@pointclouds`, `@usd`) work without
  reader code. `usd_info` (purpose `meta`) returns a USD file's root-layer header as JSON
  without pxr (default prim, up axis, metres per unit, time codes, Kit's camera,
  sublayers; `defaulted` and `unavailable` say what fell back and what needs the stage).
  `uses` names machine capabilities (`specto_core.machine`: `ovrtx`, detected by
  importing ovrtx and ovstage in a child Python), and `describe view:<type>` reports them.
  `SPECTO_THREAD_CHECK` (read by the app and the tests: `warn` by default, `raise`
  under the suite, `off`) says what a change of the canvas off its owner thread and off
  its `serial_lock` does (`LiveCanvas.check_thread`, SPEC-18).
  Test-only: `SPECTO_DISABLE_CAPABILITIES` (comma-separated) makes capabilities absent
  without detecting them, `SPECTO_FAKE_CAPABILITIES` present (the usd view then runs the
  stub USD source in the faked renderer's place); a name in both is disabled. A usd view
  that falls back to the stub because this machine has neither renderer
  (`usd_view.source_command`'s `fallback_reason`, SPEC-26) keeps health `ok`, but its
  health hint (the status line; the host's `health_hint`, carried by every health
  written for it) and `snap`'s `fallback_reason` name what is missing, and one notice
  says so. A disabled or faked capability is a fallback someone asked for: it says
  nothing. Data kinds: `image2d image_seq video_ref pointcloud mesh splat usd_stage_ref
  geojson json text markdown table message_stream pose_track joint_track`. `joint_track`
  (`specto_readers.joint_track`): a CSV or Parquet table, one time column plus one column
  per joint, located as a track only when its header (or schema) has a numeric time
  column and another numeric column (a locator may refuse an item by content in
  `locate`; `ReaderRegistry.locate` then tries the next one); decoded to one time-sorted
  float64 table (seconds first, `NaN` for an empty cell; `meta.columns`, `timeline`,
  `skipped`; at most `MAX_CELLS`); `JointTrack.sample(TimeValue)` gives each joint's
  nearest sample, never interpolated. No view claims a table by extension: tracks reach
  a view through a port. xacro (`specto_readers.xacro_expand`) is a code-running path
  (section 10).
- Intake (`specto-intake`): the intaker's params are the spec (section 2); items are
  `Item {key, parts: {role -> path}, index, meta}`; keys are the named groups of
  `params.keys` joined in `group_by` order; several sources namespace keys by source
  index. `meta`: `keys` (the named groups), `source`, `root`, `rel {role -> path
  relative to root}`, `size` and `mtime_ns` (the item's total and newest), `stat {role
  -> [size, mtime_ns]}` (per part, recorded at intake, so file listings such as the
  file tree never stat again). `intake.preview` explains a spec without a canvas.
- Thumbnail cache (`specto_app.thumbs`, SPEC-17): under `<project>/.specto/cache/thumbs/`,
  one `<xx>/<hash>.png` per file, size and look, `hash` the sha1 of `THUMB_VERSION`, the
  path, the size and the look (`cache_path`), `xx` its first two characters. Text chunks:
  `specto-stat` (`size:mtime_ns`, compared on read), `specto-source` (the path) and, for
  images, `specto-source-w`/`-h`. Cards are keyed by their look; images (written
  only from 2 MP up, `IMAGE_CACHE_MIN_PIXELS`) by `image_look(request)`: the purpose, the
  `exif_transpose` option and `IMAGE_DECODER_VERSION`. Writers go through
  `<hash>.<pid>.tmp`, and an archived video's copy for its poster is one too.
  `prune_cache(cache_dir, project_root)` deletes the PNGs of files gone or changed and
  writers' leftovers over `PRUNE_TMP_AGE_S`, touching only names of that shape, never
  through a link or a junction (the cache folder itself included), keeping PNGs with no
  source; `maybe_prune` runs it as a job when a gallery opens, at most once a day
  (`.pruned` stamp's time).

## 9. Quotas and locks (`specto_core.quotas`, `locks`)

`Quotas(web_views=8, viewports3d=3, resident_points=20_000_000, video_decoders=4,
rtx_views=1)`. A view counts by its manifest's `ViewCost`, with its params and this machine
applied (`view_cost(view)`, `view_cost_of`: a usd view costs `rtx_views` only where its
renderer resolves to rtx, so not with `params.renderer: storm`, nor with auto on macOS or
without ovrtx; SPEC-36) when its layout rect touches the window frame
(what renders; off-screen views are paused and free). `quota_problems(doc, quotas,
view_cost)` is applied at submit, whoever submits; the problem names the offenders and
the fix (move views out of the frame or close some). The gestures that make a view live
(`gestures.open_file`, which also expands a file's collapsed dedicated view, and
`set_collapsed` expanding; ADR 0025) first call `make_room(doc, quotas, view_cost, keep)`:
unlocked live views counting toward an exceeded quota collapse in the same submit, the
one counting toward the most exceeded quotas first, then the oldest, floating ones last;
`keep` and locked views never collapse. It returns each collapsed view with every quota
it was collapsed for (`gestures.RoomMade`), which `OpenedFile.collapsed`, `set_collapsed`'s
second value and `canvas.open_file`'s `collapsed` carry; `gestures.room_notice` words it
with `quotas.ceiling_words`. `locked: true` on a view lives in
the document; the user toggles it from the header (`gestures.set_locked`); a submit by
any other origin that moves, resizes, deletes, unlocks or relinks it is refused with
"ask the user" as the fix. Users may move locked views (the lock binds the agent).

## 10. Endpoint, SDK, CLI, MCP

One connection = one client. Newline-delimited JSON-RPC 2.0 over `QLocalServer` named
`specto-<project id>` (a named pipe on Windows, a unix socket on Linux and macOS); the app
writes `.specto/endpoint.json` (`EndpointInfo {kind,
address, pid, project_id, server, api_level, api_compatible}`); `SPECTO_ENDPOINT` and
`SPECTO_PROJECT` override discovery. A `pipe` endpoint's `address` names a local pipe only:
a bare name (`[A-Za-z0-9_.-]`, not only dots) or `\\.\pipe\<name>`. Anything else (a
remote `\\host\pipe\x`, a device or NT path) is refused by its text before any open
(`specto_core.rpc.local_pipe_path`, in `EndpointInfo`'s validator and in `PipeTransport`;
SPEC-74): `endpoint.json` is agent-writable, and opening a remote pipe connects out over SMB.
A `unix` address is a local socket path, never one starting with a backslash or `//`. A
`tcp` address is `host:port`; read from `endpoint.json` or an instance file its host is
loopback only (`127.0.0.0/8`, `::1`, `localhost`), since a remote host there would hand
every call to a server an agent controls. A remote host is the user's choice only through
`--endpoint` / `SPECTO_ENDPOINT` (SPEC-88). One rule, `specto_core.pathguard.endpoint_refusal`,
judges them in `EndpointInfo`'s validator, `read_endpoint_file`, `Instance`'s validator
(a refused entry is not listed), `open_transport` and the GUI drivers
(`tools/endpoint_connect.py`, which loads `pathguard` by its file).

| Method | Params | Result |
|---|---|---|
| `hello` | `{client, origin, api_level, connection?}` | `{server, api_level, api_compatible, project, connection}` |
| `call` | `{name, args}` | the command's `Result` dict |
| `list_commands`, `describe`, `api_info`, `ping` | | as the commands |
| `watch` / `unwatch` | `{since_seq}` | the server pushes `{"method": "event", "params": {seq, ts, kind, payload}}` from `live.events` |

Errors: JSON-RPC `-32000` with `data = SpectoError.to_dict()`; standard codes for
protocol errors; a bad line never drops the connection. `specto_core.rpc.RpcHandler` is
the transport-agnostic handler; `specto_app.endpoint.LocalEndpoint` serves it;
`specto_sdk.testing` serves it in-process (`InProcessEndpoint`) or over TCP for tests
(`TcpEndpoint(endpoint)`, given the in-process endpoint it serves). Every endpoint holds
the canvas's `live.serial_lock` around each request (`SerialLock`: re-entrant, it knows
its holder; `LiveCanvas.submit` takes it too, so the SDK's threads and the GUI thread are
serialised and `check_thread` accepts a thread holding it).

- SDK (`specto_sdk.Client`): `open(project=, endpoint=, origin=)`, `call`, `describe`,
  `list_commands`, `api_info`, `watch`/`events`/`poll_events`, `wait_job`, and
  `client.api.<command_with_underscores>(**args)` generated from `api-info.json`
  (`canvas_submit(document=..., label=...)`, `canvas_state()`, `snap(target=...)`).
  Regenerate `_api.py`, `api-info.json` and `packages/specto-web/types/*.d.ts` with
  `uv run python tools/gen_api.py`; `--check` and `test_generated_files_match_registry`
  fail on drift.
- CLI (`specto <group> <verb>` from `name="group.verb"`): `specto canvas state`,
  `specto canvas submit @doc.json --label "..."`, `specto canvas undo`, `specto snap kobi
  --image`; JSON-valued args take JSON or `@file.json` (quoting rules in `specto
  --help`); `--json` prints the result dict. Built-ins: `init`, `status`, `watch`,
  `api-info`, `instances` (the running apps), `message <instance> <text> [--from]
  [--reply-to]` (replaces the generated `message`; `from` defaults to `NIMITZ_ID`),
  `open [PATH] [--new] [--welcome] [--list] [--log FILE] [--wait S] [app args]`
  (#215: starts `specto-app` on the project PATH is in, detached and without a
  console window (`specto_core.detached`, which the app's project switch and the
  launchers use too, SPEC-28), from the app home's venv, and prints the instance name
  once it registers; a folder in no project becomes one first, named after the folder,
  #217; a project that is open already gets `window.raise` instead; `--list` prints the
  recents, `<user dir>/recents.json`, `specto_core.recents`) and `setup-cli [CHECKOUT]
  [--show]` (keeps the app home in `<user dir>/cli.json`). The app home is
  `SPECTO_APP_HOME`, else that setting, else the checkout the CLI is installed from
  (`uv tool install --editable <checkout>/packages/specto-cli` puts a global `specto`
  on PATH; `specto_cli.open`).
- Instances (#187, `specto_core.instances`): each running app has an instance name, kept
  per project in `<user dir>/instance-names.json`, and a registry entry
  `<user dir>/instances/<name>.json` `{name, project, project_name, pid, endpoint,
  started}` written at start and removed on exit; readers sweep entries of dead pids.
- MCP (`specto_mcp.build_server`): the six agent tools, names and docs from
  `AGENT_TOOLS`, plus `instances` and `message {instance, text, sender?, reply_to?}`
  (#187); read-only tools carry `read_only_hint`, `undo` `destructive_hint`. One
  SDK connection per MCP session with `origin="agent:<session>"`; a rejected submit is a
  tool error listing every problem with its fix-it. No transactions: a submit is whole
  and atomic, so sessions hold no open state.
- Code-running paths (ADR 0006), the complete list; each runs only content whose sha256
  the user approved in the window, in its own `python -I -X utf8` process started from a
  pool worker, never in the app or a worker (`specto_core.children`: `kill_tree` ends a
  child's whole tree, `run_child` caps its output and can start it with `minimal_env`):
  - Python links (section 6, `_python_link_runner.py`);
  - xacro expansion (`specto_readers.xacro_expand`, `_xacro_runner.py`). An approved
    xacro is code, with the same power as an approved Python link: its `${...}` and
    `$(eval ...)` are Python eval, and nothing sandboxes them. The boundary is the
    approval and the child (`python -I`, minimal environment, a fixed Specto temp folder
    as its working folder). What the module adds is that the approval means exactly what
    the user saw, because nothing unapproved can reach the eval text:
    - the root, approved as `approval_hash(root bytes, xacro_args)` (`$(arg)` is
      substituted before the eval, and the canvas, which an agent can write, holds the
      args); the Approve card shows the args;
    - each `$(find)` package folder, approved as `package_hash` under that root
      (package_paths is agent-writable), never implicitly: an unapproved folder raises
      "needs Approve" with the package's name and folder, so the user sees each one; a
      folder whose path holds a quote, `$`, a backtick or a brace is refused;
    - `$(env)`, `$(optenv)`, `$(cwd)` and `$(dirname)` are refused with a fix (pass the
      value as an arg), and `$(eval ...)` sees only a short list of plain builtins
      (`list dict map str float int round len abs min max bool`; never `type` or `vars`);
    - every file xacro reads (includes and `load_yaml` files) and every include glob:
      the path text is checked first (`specto_core.pathguard`'s allowlist on the text
      as written and again on the absolute path: NT object, device and drive-less paths
      and reserved names are refused, a network path only inside the allowed folders,
      and anything outside the root's folder, the project and the approved packages is
      refused before anything opens), then it is opened non-blocking, must be a regular
      file, the handle's final path is checked, and it is read once (at most 32 MB),
      hashed, and must be approved too, except during the Approve of that exact root and
      args (`approved_root_sha`), whose run accepts the files it reads and lists them in
      `accepted` (path and hash) for the host to record and show.

    A timeout kills the child's process tree; output past its byte caps fails the run.
    Untrusted, it says "xacro needs Approve"; plain URDF always opens.

## 11. View hosts (specto-app; `specto_app.hosts`)

```python
from specto_app.hosts import register_factory, HostContext, QuickViewHost, SimpleFactory, HostBridge


class GalleryHost(QuickViewHost):  # one per live view
    def __init__(self, ctx: HostContext, view_id: str, manifest: ViewManifest):
        super().__init__(ctx, view_id, manifest)
        self.item = ctx.create_item("GalleryView.qml", properties={...})  # a QQuickItem

    def receive(self, port: str, value) -> None: ...  # an input port's value (None: unfed)
    def on_patch(self, patch, meta) -> None: ...  # ops under /views/<id> (label, layout, params)
    def snapshot(self) -> dict: ...  # structured state for snap

    # optional: pause(), resume(), on_release(), on_restore(), snapshot_image(),
    # health_hint(), dispose(), on_arrivals(payload): a link.arrivals for one of the
    # view's inputs (SPEC-59: the gallery keeps its `arrived` entries pending, at most
    # 1000, and marks an item new when a delivery holds one; an entry waits through two
    # deliveries, a sort or filter change among them, then is forgotten)
    release_when_paused = True  # optional: paused release_delay_s (3 s), the item leaves the scene


register_factory(SimpleFactory("gallery", GalleryHost, plugin=GALLERY_PLUGIN))
```

- Registration: `register_factory` from Python, the `specto.view_hosts` entry-point group,
  or `specto-app --host-module pkg.mod`. Types without a factory get a `MissingHost` card
  with health `error`, never a crash.
- The registry (`HostRegistry`) follows `engine.live`: it creates a host on first use for
  a view in the document, attaches it to the link runtime (already-resolved inputs are
  replayed), routes `on_patch` with only that view's ops and `on_arrivals` with the
  `link.arrivals` payloads for its inputs, disposes it when the view leaves the
  document. A submit that keeps a view's id but changes its type rebuilds its host: the
  old one is disposed, `links.retarget` resolves the view's links again for the new
  type, the new host takes the slot and, if the view was paused, is paused with the
  same reason (SPEC-48). Exceptions in a host land in the view's health.
- Item lifecycle: the shell calls `hosts.attach(view_id, slot)` from the view's QML slot
  (`objectName "slot:<id>"`) and `detach` when it goes; the item is re-parented and
  resized to the slot, held in a hidden holder item in between. Culling (section 12) calls
  `hosts.pause/resume`; the overworld reads `hosts.snapshot_image`.
- Paused views give back GPU memory (ADR 0030). A host with `release_when_paused`
  (gallery, image, video) that stays paused for its `release_delay_s` (3 s) has its
  item taken out of the scene until its resume (`hosts.is_released`, `released` in its
  snap). The window deletes the item's scene-graph nodes and the memory they hold: the
  gallery's atlas, the video's frame buffer and mpv render context. The host hears
  `on_release`, where it frees what lives elsewhere (the image view lets go of its
  decoded picture). On resume the item goes back into its slot, its own state
  untouched, `on_restore` runs, then `resume`. A slot made meanwhile gets the item
  only at the resume; with no slot then, the item waits in the hidden holder item. Web
  views keep their pages.
- `HostContext` (one per shell): `engine`, `live`, `links`, `qml_engine`, `notices`,
  `project_root`, `view(view_id)`, `manifest_for(type)`, `emit(view_id, port, value)`,
  `submit(document, label)` (a user gesture; errors become notices), `call`/`try_call`
  (project-scoped commands as the user), `set_health`, `health_of`, `create_item`,
  `selected_views()` (the shell's selection, newest first) and `reveal_view(view_id)`
  (the shell selects the view, pans it into the viewport and focuses it; SPEC-32: a
  file whose dedicated view is open already; headless: nothing) and `inform(message)`
  (an info notice that expires, through `notices.inform`, the route the header's expand
  shares; SPEC-34: the views collapsed to make room; headless: stdout).
  `QuickViewHost` wraps them as `self.emit/set_health` and exposes `self.view` /
  `self.params` (read-only, never cached). Picks go through `ctx.live.remember`.
- `HostBridge(host)` is handed to QML as `host` so an item can `host.emit(port, value)`,
  `host.setHealth(status, message)`, `host.call(name, args)`; plain objects become the
  port value models (`port_value`).
- Stepping (#107, `specto_app.hosts.stepping`): viewers and players (image, markdown,
  json, html, pdf, video) mix in `Stepping` and name `step_keys()` (collection order),
  `step_key()` and `show_step(key)`; `step_item(delta)` moves through the collection and
  stops at the ends (no wrap), and a type with a `selection` output emits the item
  stepped to (a pick on the link runtime: no submit, no undo step); pdf has none and
  only shows it. `step_state()` is `{index, count}`, pushed to the QML item as
  `stepIndex`/`stepCount` (`Specto/Step/StepControls.qml` in the footer) or to the page as
  the `step` notification (`web/step.js` in the bar). Keys (`STEP_KEYS`, named by the
  status bar's hint): Left/Right and PageUp/PageDown; pdf keeps the page keys for pages
  and steps on Left/Right, json's Monaco keeps the arrows and steps on PageUp/PageDown,
  video keeps Left/Right for frames and steps on PageUp/PageDown.
- Preview (#106, `specto_app.views.preview_view`): `PreviewHost` makes the renderer of
  the shown item's kind with that type's own factory on a `RendererContext` (the
  preview's view id, health and params; emits routed through the preview, only a pick
  of an item in the collection goes out) and disposes it on a kind switch. It steps
  through its whole collection; the renderer's `step_owner` is the preview, so the
  renderer's controls and keys step the preview and show its place, and the status bar
  names the renderer's keys (`stepType`). Items of no renderer's kind show a card.
- Scratch pad (#184, `specto_core.scratch`, `specto_app.views.scratch_view`): the
  engine's `ScratchStore` (`engine.scratch`) holds named pages of markdown:
  `.specto/scratch/pages.json` (`pages: [{id, name}]`, `active`, `next_page`, `version`
  2) and per page `<page id>.md` plus pictures in `<page id>.assets/`. Changes:
  `append(markdown, page, at, assets)` (a block at the end, or at a caret offset),
  `write(markdown, page)` (the whole page), `add_page`, `rename_page`, `move_page`,
  `remove_page`; each one undo step (`ScratchStep`: pages before / after, texts before /
  after, assets added), with `undo`, `redo`, `squash` per prompt turn; `set_active` is
  navigation; listeners `on_change`. `markdown_of(entries, page)` turns dropped entries
  into markdown (text; png assets embedded; links relative to the page, `file:///` off
  the project; a Specto item as a quote with its source); `collection(page)` is the page
  (key = page id, part `.md`) then each local file and picture it links (key = path
  relative to the project). Every path that reaches the pad as text passes
  `specto_core.pathguard` (section 6) before anything asks whether the file exists: a
  page's link targets (`links()`, `open_link`), a dropped `file:` URL, a dropped Specto
  payload's `abs` paths (`_from_payload`), a `file` entry given to `markdown_of` and
  `link_to`, and a legacy card's `path` at migration. A network path (`\\host\share`,
  `\\host`, `//host`, percent-encoded, a `file://host/...` URL, `\\?\UNC`, `\\.\UNC`)
  or an NT object path is no link: such a link target or payload path is dropped, a
  `markdown_of` file entry raises `InvalidArgs`, and a legacy card keeps its label as
  plain text. Agents write pad text and `index.json`, and the pad rebuilds its collection
  on every change, so no click is needed (SPEC-67, SPEC-71). The card store before
  markdown (`index.json`) migrates on open. A text or image card's `file` is taken only
  when its text passes the guard, it is one plain name (no separator, not `..`) and it
  names a regular file, not a link, directly in the card folder as the store places it
  (a page folder that is a symlink or junction is never migrated, and no image card
  moves into an assets folder that is one); any other card is
  dropped, and nothing outside the folder is read, moved or deleted. `link_to` judges the
  text it is given before joining it onto the project root, so a project on a network
  share still links its own files. `ScratchHost` is a web host (`scratch/index.html`: the markdown renderer and
  outline, Monaco for the source; page events `save`, `caret`, `open`, `paste`) inside
  `Specto/Scratch/ScratchView.qml` (the tab strip, the drop area over the page); it
  serves its pages under the link id `scratch-<view>` (`ScratchItems`, `read:link`
  granted to its page), follows `params.page` (id or name; a tab click remembers it) and
  emits the shown page's `items` and a clicked link's `selection`. Drops and Ctrl+V go
  through `entries_from_mime` in the drag and drop contract's order (Specto payload,
  `image/png`, `text/uri-list`, `text/plain`); a text selection (kind `text`) is its
  words; a payload item with an existing `abs` file is a link unless the drag is a
  rendering (kind frame, prim or feature, no file, or `meta.rendered`), which keeps the
  png. A canvas holds at most one `scratch` view (`SINGLE_TYPES`), and it counts as
  reachable without links (`SOURCE_TYPES`). The controller's undo/redo take whichever of
  the canvas and scratch stacks changed last (`TxnRecord.ts` against `ScratchStep.ts`).
  The prompt bar's agent has seven scratch tools (`specto_app.prompt.scratch`:
  `scratch_read`, `scratch_append`, `scratch_write`, and four for pages); the MCP tool
  set is unchanged. `scratch_append`'s `image` and `file` paths pass `path_refusal` before
  anything asks whether they exist; a refused one raises `InvalidArgs` (SPEC-73).
- Context menu (#154, `specto_app.menu`): the shell has one (`Main.qml` `contextMenu`, a
  `PopMenu`; rows `menu:<entry id>`). A view contributes two things:
  - **its target**: `context_target(hit) -> MenuTarget | None` on the host says what is
    under the cursor. `hit` comes from the view (`{"kind": "items", "row": 3}` from the
    tree's QML, `{"kind": "items", "index": 2}` from the gallery's, a web page's own
    object) or is None for the keyboard (the focused item: the selection). Build it with
    `self.menu_target(kind, items, data)`: `kind` is `items` / `view` / `background`,
    `items` are `MenuItemRef(key, path, is_dir, kind)` (path absolute, native
    separators), `data` anything the view's entries read (`{"index": 2, "image": True}`);
    type, role and the project root are filled in. The default is the view (or its
    background) with the hit as `data`. `context_anchor(target)` places a keyboard-opened
    menu (default: the view's top-left). The view asks with
    `self.open_context_menu(hit, x, y)` (window coordinates); from QML
    `host.contextMenu(hit, x, y)` (`HostBridge`) or its own bridge slot.
  - **its entries**: `default_registry().add(MenuEntry(...))` at import, scoped by
    `view_types` (and/or `roles`, `targets`, default `("items",)`). An entry is data: `id`
    (dotted, `<view type>.<verb>` or `<area>.<verb>`), `label`, `icon` (a shell icon),
    `shortcut` (shown only: the key that does the same in the view), `group` (`open`,
    `copy`, `view`, then any other by name; separators between groups), `order`,
    `visible` / `enabled` predicates on the target (one that raises counts as false), and
    `handler(call: MenuCall)`: `call.target`, `call.host`, `call.copy(text)` (the
    clipboard); it returns a short result (what it copied, the view it opened).
    An entry built from configuration may name its label per target (`text(target)`)
    and hold a one-level **submenu** (`submenu(target) -> [MenuEntry]`, shown in the
    order given, separators where the group changes; empty hides the row): its row has
    `children` and a child runs as `<parent id>/<child id>` (rows
    `menu:launch.open_with/launcher:blender-4.2`). Main.qml opens the children in
    `contextSubMenu` beside the row on hover, a click, Enter or Right; Left or Esc
    closes it.
  - **launchers** (#186, `specto_app.launchers`): on items that are files in any view,
    `launch.default` ("Open with <default launcher>", else the system default) and
    `launch.open_with` (the launchers matching every item, System default, Show in
    Explorer, Edit launchers...). Config: the user dir's `launchers.toml` and the
    project's `.specto/launchers.toml` (merged by id); the system default is refused for
    programs and scripts; the agent's `launchers` / `launch` tools use the same book.
    A launcher's command (variables expanded), its cwd and every path to open pass
    `specto_core.pathguard` before any filesystem call (SPEC-76): a network or device
    command never runs, and such a path is refused with a fix-it and offers no launcher.
  The browsers' (role `browser`) Copy path and Copy relative path (`menu/paths.py`) copy
  one path per item, one per line, native separators; relative paths need every item
  under the project root (else the entry is disabled). A right-click is no pick: on a
  selected row it is about the selection, elsewhere about that row alone. The Menu key
  and Shift+F10 (`ContextMenus.eventFilter` on the window) open it for the view holding
  the keyboard focus, unless a text field or a web page has it; the menu then takes the
  keys (Up/Down, Enter, Esc).
- Drag out (#185, `specto_app.drag`; the mime contract is section 11): a view says what a drag from a spot carries with
  `drag_payload(hit) -> DragPayload | None` (default: `payload_for(context_target(hit))`,
  so a drag takes what the context menu there acts on: a row, the selection it is in, a
  tile) and starts it with `self.start_drag(hit)` (or `start_drag(hit, payload)`), which
  calls `drag.qt.start_drag`, the one place a `QDrag` is made. The mime data, in the order
  drop targets read it: `application/x-specto-item+json` (`{items: [{view, type, key,
  path, abs, kind, label, meta}], text, project}`; `kind` file / image / text / prim /
  feature / frame), `image/png` (a picture: a crop), `text/uri-list` (the files; left out
  for a text or a picture, so an outside app takes those, not the file), `text/html`,
  `text/plain` (the text, else the paths one per line). Gestures: past the platform's drag
  distance (`QStyleHints.startDragDistance`) with the left button held. File tree: a row
  (`DragHandler`; a click still picks). Gallery: a mouse drag that starts on a tile
  (`GalleryItem.dragRequested`, `tileDrag`); on the gaps, and touch, it still scrolls.
  Image view: Ctrl+drag the image; Shift+drag marks a crop (image pixels, Esc or a click
  outside clears it) and a drag from inside it carries the crop as a PNG (`meta.rendered:
  true`: drop targets keep the picture, not a reference to the file). Web views: a
  text selection's `dragstart` (and `Specto.startDrag({text, html})` for pages that see
  drags themselves, the Monaco json view) reaches the host as `drag {text, html}`; the
  host names the file the text came from (`text_source`). Tests replace the exec step
  with `drag.qt.set_runner` (offscreen has no drag loop).
- Theme: QML context properties `theme` (`theme.colors.surface`, `theme.metrics.pad`,
  `theme.fonts.mono`, `theme.glyph("added")`) and `SpectoTheme`; web pages get
  `theme_for_web()` as CSS variables. No raw colours in QML or pages (ADR 0008); chain
  colours are the tokens `chain_0..5` (`specto_app.canvas.chains`).

## 12. Shell (specto-app)

- One window, one free canvas at 1:1 (`specto_app.canvas.controller.CanvasController`):
  `pan {x, y}`, `viewport {x, y, w, h}` in canvas px, `setPan/panBy/home`, `maximize(id)`
  / Esc restores. Views outside the viewport pause (`hosts.pause`) and resume when
  panned back (one paused past its `release_delay_s` gives back its GPU memory, section
  11, ADR 0030); Home brings the file tree back unless the focused view takes it (Home /
  End go to the focused view first; Space held pans, a Space tap goes to the focused
  view, #113). Header drag = move, border drag =
  resize, window resize = "resize window", header lock and close: each one user submit
  (`specto_core.gestures`), snapped by `specto_app.canvas.snapping` (view-to-view and
  to the window frame, the canvas area inside the window's 12 px padding; no guide is
  drawn for it, #120). A pan settling writes the frame origin (`live.navigate`). The normal view draws faint link
  curves and chain-coloured frames.
- Overworld (`specto_app.canvas.overworld`, QML `Overworld`, `OverCard`, `LinkNode`,
  `LinkCurve`, `LinkSummary`, `LinkView`): Tab toggles it; every view pauses and shows
  its snapshot; cards move and resize freely and resolve, snapped, as one user submit;
  wires are chain-coloured with a link node; a node opens the summary with Delete, Ask
  agent to change (`askAgentRequested(text)` -> `promptBar.prefill`) and Advanced (the
  read-only link view).
- App commands (`specto_app.commands`, importable without Qt): `snap {target, image?,
  layout?, path?}` -> `{target, kind, meta, image, image_size, layout}` (`target` a view
  id, `view:<id>`, `@<label>` or `canvas`; `meta.host` = `host.snapshot()` (a usd view's
  carries `specto_app.views.usd_meta.UsdSnapMeta`: `path, renderer, renderer_asked, mode,
  frame_size, camera, look_through, camera_speed, environment, environment_note, sky,
  hdri, sky_controls, ground, sky_fetch, hdri_fetch, fallback_reason, load_timings`; the
  environment fields, SPEC-31:
  what lights the stage, why it is not the choice, the sky's controls in effect and the
  runtime fetches' `{cached, fetching, queued, done, total, error}`; `fallback_reason`,
  SPEC-26: why the stub USD source plays, "" when it does not; `load_timings`,
  SPEC-27: where the shown stage's load spent its seconds, `renderer_s` (a fresh
  renderer's swap), `open_s`, `populate_s`, `environment_s`, `first_frame_s` (to the
    frame `ok` names) and `total_s`, `{}` while a load runs or after a failed one; a
  gallery's carries `new {count, first}`, the arrivals not seen yet and the index of
  the first, SPEC-59),
  `meta.links` the links in and out
  with their resolve state, `meta.rect` the on-screen rect;
  `layout` one line per view and link) and `shell.locate {name}` (the window rect of a
  shell item by `objectName`: `slot:kobi`, `header:kobi`, `close:kobi`,
  `lock:kobi`, `maximize:kobi`, `resize:kobi:bottom-right`, `treeRow:fege:<path>`,
  `tile:kobi:<key>`, `video:rena:play`, `sort:name:kobi`, `approve:tamu`, `panArea` (the
  empty canvas),
  `panHand`, `marquee`, `moreButton` (the global menu; rows `menu:openFolder`, `menu:quit`, ...),
  the context menu's rows `menu:<entry id>` (`menu:copy.path`, `menu:gallery.open_image`),
  `resolvingCount`,
  `undoButton`, `redoButton`, `agentIcon`, `promptPanel`, `promptInput`, `promptSend`,
  `agentDone` (a finished turn), `replyUndo`, `replyDiff`, `replyCode`, `replyCodeCopy`,
  `slashList`, `panelPin`, `panelNewSession`, `panelResize:corner`, `threadDivider`,
  `overCard:kobi`, `overHeader:kobi`, `linkNode:l2`, `linkDelete`, `linkAskAgent`,
  `linkAdvanced`, `linkView`, ...) -> `{found, visible, rect, screen, text}` (`text` what
  a text item says, such as `statusHint`). The GUI flows (`tools/gui_drive.py`,
  `tools/gui_flows/`) drive these with a real mouse, naming generated views by type or
  label (`slot:@html`, `tile:@gallery#2:<key>`, `slot:"Front frames"`; `tools/gui_refs.py`).
  `window.raise {}` -> `{raised, instance}` shows the window on top and asks for focus
  (unminimised; `raised` false headless): what `specto open` calls when the project is
  open already (#215). The CLI grants the app the foreground right first (Windows
  `AllowSetForegroundWindow`); otherwise the system may only flash the taskbar entry.

## 13. Prompt bar (ADR 0015; `specto_app.prompt`)

- One agent session per window: `ClaudeAgentSession` over the Claude Agent SDK
  (`claude-agent-sdk`), on a qasync loop shared with Qt (the welcome screen owns the
  same loop and hands it to the app it starts). `ClaudeAgentOptions`: `permission_mode=
  "auto"`, `allowed_tools=["mcp__specto__*"]` (the six tools as an in-process SDK MCP
  server), `strict_mcp_config`, `setting_sources=["project"]`, the project's `.claude/`
  and `.specto/` skills and commands as a local plugin, `claudeMdExcludes` for every
  ancestor memory file, auto memory off, an inherited `ANTHROPIC_API_KEY` /
  `ANTHROPIC_AUTH_TOKEN` scrubbed (`SPECTO_AGENT_KEEP_API_KEY=1` keeps them). The
  session id is kept in `.specto/agent-session.json` and resumed on the next launch.
  `--agent scripted:<script.json>` swaps in `ScriptedAgentSession` (real tools, no
  model) for GUI flows and tests.
- Every prompt carries the canvas context (`prompt.context`): the whole document (or its
  hash when unchanged), the viewport, the mouse and the view under it, per-view
  selections and picked files, the project's files folder by folder.
- A turn (`prompt.turn.run_turn`): the agent's submits apply as they land; a rejected
  document is retried invisibly; three rejections end the turn with a short failure
  reply; at the end the turn's steps are squashed into one (`live.squash`). The reply
  strip shows one line, Undo (the whole turn), Diff on request (`canvas.history
  patch=true`), and outlines the touched views. A leading `/` passes only for the
  project's own commands and skills.
- The agent's own files (#161, #219; `prompt.skills`, `prompt.commands`, `prompt.data`):
  `save_skill` / `delete_skill` write `.specto/skills/<name>/SKILL.md` (loaded as
  `specto:<name>`); `save_command` / `delete_command` write `.claude/commands/<name>.md`
  (the project command `/<name>`; frontmatter `description` and `argument-hint` only,
  never `allowed-tools` or `model`, no `` !` `` shell line); `data_write` / `data_read` /
  `data_list` / `data_delete` keep named data under `.specto/<folder>/<file>` (json, md,
  txt, csv; never `skills`, `commands`, `links`, `scratch` or a path out of `.specto/`).
  A skill or command changed by a turn, or by hand, reloads by reconnecting the session
  with resume (`skills.skills_key`). Each change is a thread row (`skill`, `command`,
  `data`) with Open and Undo (the turn's changes of that kind back in one step, refused
  when a file changed since). None of them approves link code (ADR 0006, #203): a layout
  a self-made skill restores keeps each python link's approval state.
- The agent panel (#162; `prompt.thread`, `prompt.states`, `prompt.panel`): the agent
  icon (`promptBar.icon`: `state` idle | thinking | working | needs_you | error,
  `asks`, `errorText`, `shake` every 4 s while it needs you; the art is the swappable
  `Shell/AgentIcon.qml`) and the panel (`promptBar.panel`: open on a 150 ms hover, close
  400 ms after the last hold, Esc, pin; width 380..min(720, 60 % of the window), height
  240..window - 120, size and pin in the user's settings per project). The thread
  (`promptBar.thread`, a list model) is `.specto/agent-thread.jsonl`, one JSON entry a
  line (`user`, `peer`, `approval`, `agent`, `turn`, `skill`, `command`, `data`, `code`,
  `fleet`, `launch`, `scratch`, `divider`), 20 turns a page. A turn row's Undo
  (`undoTurnAt(entryId)`) works while its step is the newest; `newSession()` closes the
  agent session, drops `.specto/agent-session.json` and adds a divider.
- Approval cards (#203; `engine.host.approvals` is the prompt bar): `ask_approval(link,
  path, hash, target, requester, verified) -> entry id` adds an `approval` row
  ("<requester> asks you to approve", the file and link; `approvalApprove`,
  `approvalOpen`, `approvalDismiss` while it waits, `approvalRow:<id>`,
  `approvalFrom:<id>`, `approvalStatus:<id>`) and badges the icon. `approveLink(id)`
  approves the file as it is now (refused and marked `changed` when its hash moved since
  the request); `dismissApproval(id)` leaves the link waiting; a link approved in the
  view's header retires its card as `approved`.

## 14. Web views (`specto_app.web`; on ports per ADR 0014)

Every web view (`json`, `markdown`, `map`, `3d`, `html`) runs under an explicit grant list
built by the app from the view's type and the links feeding it, never from the page (a
`preview` showing a web kind runs that kind's page under that kind's grants, #106); the
view's `network: false` param drops its `net:` grants (params narrow, never widen):

| grant | meaning | source |
|---|---|---|
| `read:link:<id>` | fetch the bytes behind the value that link delivered, over `specto://items/<link>/<key>/<role>` | every link into one of the view's input ports |
| `emit:<port>` | emit on that output port through `Specto.emit` | the type's output ports (`VIEW_PORTS`) |
| `params:<key>` | write that key of the view's own params through `Specto.setParams` (one user submit) | `json`: `wrap`; `markdown`: `panels`; `map`: `layers`, `basemap`, `panels`, `limit` (Load more, #181); `3d`: `point_size`, `up` (`panels` = `{<panel>: {collapsed, width}}`, the side panels' state, #130) |
| `cmd:<name>` | call that command through the bridge | `describe`, `vocabulary` always |
| `net:tiles` | raster basemap tiles from `TILE_HOSTS`: the hosts of the basemap providers in `specto_app/web/basemaps.py` (OSM, Esri World Imagery; `params.basemap` = `none` \| `osm` \| `satellite`, #157) | `map` only |
| `net:web` | any http(s) request from the page's frames, http(s) navigations of its frames (never the main frame) | `html`, unless `network: false` (ADR 0019) |

Enforcement is Python-side (`WebView.emit/set_params/call`, the `specto://` scheme
handler by initiator origin `specto://<view id>.page`, a request interceptor that blocks
everything off `specto://`/`qrc:`/`blob:`/`data:` unless `net:web`, or `net:tiles` and a
tile host, charging a request to the view of its top-level page). Grants are re-derived
when a value lands on or leaves an input port and when the view's params change. A refused call rejects with
`{code: "capability", message, fix, data}`.

Bridge (`PageBridge`, `web/bridge.js`), every payload a JSON string: Python -> page
`configured` (label, params, `inputs` = links per input port, capabilities, theme),
`port {port, value}` (latest value per input port; `null` when its link went away),
`params`, `notify(kind, payload)`, `callResult(reqId, json)`; page -> Python `ready`,
`emit(port, value)`, `setParams(patch)`, `call(name, args, reqId)`, `send(kind,
payload)` (a page event for the host: a wikilink followed, a feature hovered, `step
{delta}` from the previous / next controls, answered by the `step {index, count}` notify),
`setHealth`, `pageState` (what the page shows with CSS px rects, for `snap` and the GUI
flows), `log`, `jsError`. Context menu (#154): `bridge.js` turns a right-click, the Menu
key or Shift+F10 the page did not handle (`preventDefault`) into `send("contextmenu", {x,
y, target})` (CSS px; `target` from the page's `Specto.contextTarget(event)`, else
`{kind: "background"}`); the host maps the point into the window and opens the shell's
menu (section 11). Selected text and editable fields keep the browser's own menu. Drag out (#185): a dragged text selection becomes `send("drag", {text, html})`
(the browser's own drag cancelled) and the host starts the drag (section 11);
`Specto.startDrag` does the same for a page's own drag; `config.dragDistance` is the
platform's drag distance. Items at `specto://items/<link>/<key>/<role>` resolve from the
link's delivered value (`web/items.py`).

Offline rule: all web assets are vendored by `packages/specto-web/build.py` (Monaco,
markdown-it, github-markdown-css, Mermaid, KaTeX, MapLibre, Babylon.js 9.31 with its
loaders). Network exceptions: the map's basemap tiles under `net:tiles`, the user's
own page in the html view under `net:web` (ADR 0019), and the usd view's environment
assets: NVIDIA's dynamic skies from NVIDIA's public content bucket and the 3d view's
Poly Haven HDRIs, fetched on first use of the Sky or HDRI choice into the user's data
folder (`<user dir>/skies`, `<user dir>/hdri`), each file pinned by sha256 and size,
never again once cached (ADR 0024; `specto_app.usd.skies`).
TypeScript declarations (`packages/specto-web/types/specto-api.d.ts`, `specto.d.ts`) are
generated by `gen_types.py` and checked by `tools/gen_api.py --check`.

## 15. Theme tokens

`engine.api_info(theme_tokens=...)` embeds the app's token table so web pages and the
CLI share names, never raw colours (ADR 0008). The app owns the values
(`specto_app.theme`); alignment with the Figma variables of page "03 v2 agent-first
shell" is still to be verified by the user (spec #35 story 76).
