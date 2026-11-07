# Changelog

## Versioning

The mainline (`main`) carries the version: the root `pyproject.toml`, every workspace
package's `pyproject.toml` and `__version__`, the Claude Code plugin's `plugin.json` and
`uv.lock` state the same number, and a release is the commit that sets it, tagged
`vX.Y.Z` on `main`. `specto --version` prints it.

Semantic versioning, in its 0.x reading: a minor bump (0.2 -> 0.3) for new features or a
breaking change (the canvas document, the command API, a view's params or ports, a file
under `.specto/`), a patch bump (0.2.0 -> 0.2.1) for fixes alone. The per-plugin
`PluginManifest.version` values and the `api_level` in `api-info.json` are independent of
the release and move on their own schedule.

## Unreleased

- The web build leaves no truncated HDRI behind (SPEC-45): each `.hdr` streams into a
  `.part` beside it and is renamed into place once its sha256 matches, and a source
  checkout's `specto_web.hdri_source` serves a cached `.hdr` only at the manifest's
  size. A build killed mid-download used to leave a cut-off file that the usd view's
  dome light then read.
- In the 3d view a glTF/GLB keeps its own Y up and is no longer turned by the view's
  `up` param (SPEC-50, ADR 0028 amended), the same as a converted DAE. A Z-up STL, a DAE
  and a glTF now line up in one scene under `up: z`. A glTF exported Z up against the glTF
  specification has to be fixed at its source; `up` turns only STL, OBJ, PLY, point
  clouds and splats.
- The generated TypeScript declarations (`packages/specto-web/types/specto-api.d.ts`)
  name every interface with a valid identifier (SPEC-49): the 3d view's state is
  `_3dState`, and a `$defs` name that reaches an interface is mangled the same way, so
  `tsc` accepts the file (it stopped at `interface 3dState`).
- The offscreen `search` flow no longer fails now and then (SPEC-41). The test driver
  answers a `locate` after the window's next frame, so it reads the item tree that frame
  draws: a list applies its model's row changes at its polish, and until then a removed
  row's delegate was still found, visible (3 runs in 8 on `main`). A normal `shell.locate`
  forces nothing; the fix is in the driver alone.
- The real-mouse GUI tools cannot collide on a shared desktop (SPEC-7).
  `tools/gui_session.py` and `gui_drive.py` drive the desktop only while holding the
  desktop lock, and take it themselves when not run under `desktop_lock.py run`
  (`gui_session.py --lock-wait` sets how long they wait); a desktop another session
  holds is refused. The lock is now an OS lock the system frees when its holder process
  ends, so it never goes stale; `desktop_lock.py` keeps `run` and `status` (`acquire`
  and `release` are gone), and `--keep` leaves the app open but releases the lock when
  the session exits. `--fresh` (also in `gui_qtdrive.py` and the suite's reseeding)
  wipes, under the lock, only a folder a GUI session created; delete a `--work` folder
  from before this once. A session given no `--project` runs on a folder of its own
  under the temp dir, and later sessions sweep old ones a killed session left.
- Paused galleries, images and videos give back their GPU memory (SPEC-3, ADR 0030). A
  paused view is one off-screen, collapsed or behind the overworld. After 3 s paused,
  the view leaves the scene: the gallery's atlas and the video's frame buffer and mpv
  render context are freed, and an image lets go of its decoded picture. A view panned
  out and straight back keeps everything. Each comes back as it was: scroll, zoom,
  selection, the frame shown, playing or not. Coming back costs
  the decode: a gallery re-decodes its visible thumbnails (about 0.6 s), an image its
  picture (0.4 s for a 24-megapixel photo), and a video reopens its file (about 1.4 s).
  An off-screen video kept about 265 MB even if never shown and now keeps under 2 MB; a
  gallery with a filled atlas kept 67 MB and now keeps 1 MB. Web views keep their pages,
  because QtWebEngine offers no release that pays off. `tools/bench_gpu_release.py`
  measures it all on the GPU tier. Also fixed: gallery thumbnails could stay half faded
  in when the app was busy.
- 3d view meshes, follow-ups (SPEC-58): when Babylon's MTL loader cannot take the hook
  that strips texture map options, the view's health says so ("OBJ texture map options
  are not stripped"). Two resolves converting one DAE at once no longer fail with "the
  conversion wrote no readable GLB": the second waits for the first. A crashed
  conversion's temporaries are swept from the cache after an hour.
- After a project slash command (`/layout review`), the agent's next turn gets the
  canvas document and the project's commands again (SPEC-30). A slash command goes to
  the agent without the canvas context, yet the bar counted the document as seen, so the
  next turn could tell the agent the document was unchanged when it had never seen it.
  The same holds after a turn that never reached the agent: its session failed to start,
  or it ended with no reply of the agent's (not logged in, or an API error such as
  "overloaded", SPEC-52).
- Switching projects from the app no longer risks a console window popping up when the
  new window's agent or workers start (SPEC-28). The project switch now starts the new
  app the way `specto open` does, with a hidden console rather than none, through one
  shared helper (`specto_core.detached`) that the launchers use too. An app running
  under `pythonw.exe` relaunches with the `python.exe` beside it. The new window also
  breaks away from its parent's job when the job allows, so it outlives the old window
  even when that one was started by `uv run`.
- A folder row's modified time is the folder's own (SPEC-40). On Windows the time the
  listing showed came from the folder's entry in its parent, which falls behind after a
  change for as long as nothing opens the folder (a file added 5 s after the folder was
  last opened left the row 5 s behind, and it did not catch up on its own). The listing
  now stats each folder, about 20 us a folder locally and milliseconds over a network
  drive, and a listing no longer wanted (a new project, a new search) stops between
  entries. A junction or link the source follows (`follow_links`) shows its target's
  time.
- A usd view counts toward the one live RTX view per canvas only where its renderer is
  RTX on this machine (SPEC-36). `auto` on macOS, or on a machine without ovrtx, renders
  with Storm and no longer collapses another stage's view to make room. A view collapsed
  under the old rule stays collapsed until you expand it (its header's expand, or a
  double-click on its file). Scripted flows can play a machine with a capability through
  `SPECTO_FAKE_CAPABILITIES` (comma-separated, e.g. `ovrtx`): present for every choice,
  with the stub USD source in place of the renderer.
- The 3d view opens STL, OBJ and COLLADA (`.dae`) meshes (SPEC-42, ADR 0028), from a
  double-click (dedicated) or a Ctrl+double-click (contextual) in the file tree and in
  any `@3d` intaker. STL and OBJ load with Babylon's own loaders, an OBJ with its MTL and
  textures from next to it (`../textures` too, inside the project; one it cannot reach is
  named in the view's health); a mesh without a material draws in grey. Their unit is the
  view's new `mesh_unit` param (m, cm, mm, in; default m), and its `up` param stands Z-up
  robot meshes upright. A DAE is converted to a GLB on first open (a child process, 120 s
  and 256 MB at most), written next to it as `<name>.dae.glb` (the converted mesh;
  Specto's cache when the folder cannot take it) and written again whenever the DAE
  differs from the one it was made from, an older copy included; an open view loads the
  new one. Its `<unit>` and `<up_axis>` are applied, so it stands upright whatever `up`
  says. STL and OBJ files over 512 MB are refused with the reason.
  **New runtime dependencies:** trimesh and pycollada (with python-dateutil and tzdata).
- Gallery captions sit on the thumbnails (SPEC-6): each key overlays its tile's bottom
  edge, light text on a dark scrim that reads on light and dark images, and takes no row
  height, so more tiles fit. A time-linked gallery marks its current item (the frame at
  the playback time) with a `now`-coloured bar and "· now" after the caption, apart from
  the selection ring; following playback no longer moves the selection, and `snap`
  reports the followed frame as `current`. The arrow keys, Enter, Space and the keyboard
  context menu act from the user's pick, not from the followed frame. A thumbnail too
  small or too short for its caption (a panorama) shows none and no scrim. The ring and
  the scrim now composite over the image, so they also show on transparent parts of a
  thumbnail. New theme tokens: `image_scrim`, `text_on_image`, `current_mark`.
- Switching a usd view's view mode can no longer freeze the app (SPEC-33, ADR 0026). On
  a stage with dense colliders, collision built its convex hulls for minutes on the
  renderer's thread, the source stopped reading, and an orbit's camera moves blocked the
  GUI thread on a full pipe; a canvas saved in collision hung on "streaming textures" the
  same way. Hulls now come from Qhull (milliseconds), built once a stage on a thread of
  their own while the picture goes on, with "building the collision overlay" in health
  and the pill on "Collision…" meanwhile; a failed build is said once and tried again on
  the next ask, and one past 90 s answers an error. The host queues what it sends a frame
  source, camera, time and size updates collapsing to the newest, and the source keeps
  reading while a command runs. **New runtime dependency:** scipy (for Qhull).
- The file tree's search shows its hits inside the tree instead of a flat list (SPEC-38,
  ADR 0022 amendment). While the search box has text the tree shows only the hits and the
  folders above them, those folders open; a folder whose own name matches is a hit too,
  shown in full when hits are under it, closed when none are, and opening it lists it.
  Every gesture works on hit rows as on any row (click, Ctrl, Shift, double-click,
  Ctrl+double-click, the context menu, drag), and Down from the search box moves to the
  first hit; details mode shows a file's time and size and, for a folder only on the way
  to hits, how many hits are under it. Clearing the search brings the tree back as it was
  (open folders, scroll), picks kept; picks set from outside meanwhile (the agent, a
  linked selection) apply at once and show in the tree when the search clears. The search
  tree is built from the hits alone, so nothing is listed for it. `FileSearch.search`
  takes a `HitFilter` (`TreeLister.hit_filter()`) whose folder predicate returns folders
  as hits; the flow `v2-232-search.txt` covers the new behaviour.
- Opening or expanding a view past a quota makes room instead of failing (SPEC-34, ADR
  0025). A double-click on a second stage while the first stage's usd view is live was
  refused with "canvas document has 1 problem" (one live RTX view per canvas) and opened
  nothing; now the second stage opens its own usd view and the first collapses to its
  title bar in the same undo step, with a notice naming it and the quota. A double-click
  on the first stage again expands its view the same way, as does the header's expand.
  The same holds for every quota (a fifth video, a fourth 3D viewport, a ninth web view).
  Locked views are never collapsed. `canvas.open_file` reports the views it collapsed in
  `collapsed` (each with every quota it was collapsed for) and an `expanded` action; an
  agent's `canvas.submit` over a quota is still refused with the fix.
- A double-click on a file in the file tree opens a dedicated view; Ctrl+double-click the
  contextual preview (SPEC-32, ADR 0023). The dedicated view is of the type that shows the
  file's kind (image, video, markdown, json, pdf, html, 3d, usd, map), fed that one file
  by a pinned link from the project source, labelled `<name> · <folder>`; the same file
  again reveals it (selected, panned into view) instead of opening another, and a file no
  view shows opens a new `text` view saying "Not supported: <name> (<ext>)". The preview
  on the tree's picks now says "Contextual · follows selection" in its header and on its
  overworld card; a Ctrl+double-click leaves the file selected, and a kind the preview
  cannot render (a usd stage) opens dedicated instead. The file tree's context menu gains
  "Open in its own view" and "Open in contextual view"; `canvas.open_file` gains `mode`
  (`contextual`, the default as before, or `dedicated`) and reports the mode it took. The
  flow language gains `ctrldblclick`.
- The usd view gets an environment light (SPEC-31): the VIEW panel's Stage / Sky / HDRI
  control (`params.environment`, one user submit per change, kept across stages, reported
  by `snap` and `describe view:usd`, settable by the agent) lights the stage with its own
  lights (and #148's fallback dome), with one of NVIDIA's seven dynamic skies (a Sky list,
  `params.sky`: clear, cirrus, light and heavy cumulus, overcast, night, the textured
  cloudy one; an MDL dome with a sun, RTX only) or with one of the 3d view's five HDRIs as
  a dome light (an HDRI list, `params.hdri`). The sky's **time of day**, **cloudiness** and
  **haze** sliders (`params.time_of_day` in hours, `cloudiness` and `haze` 0-1, null for the
  sky's own value) drive it live through the material's own inputs, the sun moving with
  the time of day and the viewer's own sun light with it (the textured sky takes the time
  of day and the cloudiness; it has no haze), and a **Ground** toggle (`params.ground`,
  off by default) brings in the floor of the preview scene the skies ship with. The
  environment is composed over the stage in the viewer's session, never written to the
  file, and adds to the stage's own lights (ADR 0024): under RTX a reference swapped at
  runtime, under Storm a sublayer of the session layer. Storm cannot render MDL and shows
  an HDRI for the sky (the panel shows which, and lets you pick it); a sky or an HDRI not
  fetched yet falls back the same way, each said under the control, in `snap`
  (`environment_note`) and to the agent. The skies and, on an installed app, the HDRIs
  are fetched on first use, never committed (the skies' licence allows no stand-alone or
  public-repository redistribution): off the GUI thread, from NVIDIA's public bucket and
  Poly Haven, each file pinned by sha256 and size, cut off past its size and checked
  before it is kept, under a lock on the folder, into the user's Specto data folder
  (`<user dir>/skies`, `<user dir>/hdri`; the textured sky's 67 MB of textures last), each
  sky packed as one `.usdz` with its MDL inside, with the progress under the control and a
  plain message offline. A source checkout's web build supplies the HDRIs.
- The usd view gets an orbit camera and F frames the whole selected hierarchy (#39). The
  VIEW panel's Free / Orbit control (`params.camera_mode`, one user submit per change,
  reported by `snap` and `describe view:usd`, settable by the agent) switches between
  Unreal Editor's free camera and one that turns around a pivot: drag orbits the
  selection's centre, else the point under the press, else the point ahead; middle-drag
  pans; right-drag and the wheel dolly toward the pivot; Alt changes nothing and a click
  still picks. Switching keeps the camera where it is, and the status bar's line follows
  the mode. F and the panel's new Frame selected button frame the union of each selected
  prim's whole subtree (the source's `bounds` answers a prim's subtree at the stage's
  current time, visibility and purpose honoured), so an Xform or Scope frames all of its
  children; prims without bounds keep the picked-point cube.
- A folder row's details line is never empty (#38): with details on it shows the folder's
  own modified time, coloured by age like a file's, and once the folder is listed how many
  items it shows (`12 items`, `empty`); an archive shows its file's size; a folder whose
  time is not known yet says `not listed yet`. Every row keeps the one row height. The
  time comes from one stat of the folder itself (SPEC-40: the copy in its parent's
  directory entry falls behind on Windows): no walk, and nothing is read under a folder
  that is not open. Auto-update carries a folder's new time.
- The file tree's search matches the path, not only the name (#36): every word must occur
  somewhere in the file's path below the project folder (`notes readme` finds
  `docs/notes/readme.md`; `docs/read` and `docs\read` match across the separator), never
  in the folders above it; files whose name holds every word rank above those reached
  only through their folders. Both backends agree. Everything is asked for a word that
  also occurs in the project's own path as a regex anchored below the project (as a plain
  term it would match every file), and its candidates are read through up to a bound,
  past which the walk answers rather than an unfinished list.
- The mark is the E4 redraw (#32): the same S on the same grid, its tail now hooking back
  under the bowl and tapering to a point, the eyes r 6 where E4 places them; every asset
  follows (the body, its centre line, the 16 px drawing, the eye poses, the app icon,
  `specto.svg`, `specto.ico`).

## 0.2.0 - 2026-11-01

The first numbered release: Specto v2 as it stands on the mainline. Versioning starts
here; 0.1.0 was the number the packages carried while v1 and v2 were built, and nothing
was tagged.

### The canvas

- Agent-first: the agent composes the canvas (views, links, positions) through six tools
  over the canvas document; the user looks, interacts and arranges. The agent is a Claude
  Code session behind the agent icon and the hover thread panel, with its own prompt,
  slash commands, saved skills and scoped `.specto/` data files.
- One canvas per project, a Pydantic canvas document, every change (the user's and the
  agent's) one undo step in time order, journaled so the history survives a relaunch.
- Two layers, the pannable canvas and the float layer; the overworld (Tab) shows every
  view as a card with the links drawn as wires; snapping, culling, window frames.
- The tile floor under the canvas and the overworld, with agent, new-view and selection
  cues.
- The scratch pad: one per project, markdown pages as tabs under `.specto/scratch/`,
  fed by drops, paste and the agent.
- Drag out of views: gallery tiles, the image or a crop of it as a PNG, text selections
  from the web views, file tree rows.
- One context menu for the shell (right-click, Shift+F10, the Menu key); launchers open
  files in external programs from `launchers.toml`, per machine or per project.

### Views

- File tree, gallery, image, video (libmpv, sound), markdown, json, html, pdf, map
  (MapLibre, basemaps), 3D (Babylon.js: point clouds, glTF/GLB, splats, HDRI
  environments, view modes), usd, preview and scratch, each with the shared header,
  footer and side-panel standard.
- The usd view runs a separate source process: NVIDIA RTX through ovrtx (`--extra rtx`)
  or Hydra Storm from an OpenUSD imaging build (`--extra storm`); `auto` is RTX first on
  Windows and Linux and always Storm on macOS. Unreal Editor viewport navigation, view
  modes (lit, unlit, wireframe, collision), picks, outliner, time scrubbing, a renderer
  that dies says why.
- Walk-throughs: the agent plans and plays a camera walk along the canvas's links.

### The file tree

- Lists one folder at a time, on expand, on its own worker thread, and never scans the
  whole project for itself (#231, ADR 0022): a 150k-file folder shows rows in about 10 ms
  instead of 7 s. Everything starts collapsed but a chain of single-child folders from the
  root; a folder pick still means every file under it, resolved on the worker; restored
  picks, the `selection` input and `canvas.open_file` list the folders they need first;
  auto-update follows the loaded folders only; counts and sizes show where known.
  `project.all_files` keeps its meaning for the views pinned to it; the project node shows
  no count until one of them has scanned the folder.
- Type-to-search (#232): a search box at the top of the tree (typing in the focused tree
  starts one); the hits replace the tree, best first; click picks, double-click opens, the
  target button shows the hit in the tree, Escape returns. On Windows, Everything answers
  over its SDK DLL when it is running and indexes the project (optional; fetched once by
  `tools/fetch_everything_sdk.py`); otherwise, and elsewhere, a walk of the folder. The
  footer says which backend found nothing. Search never looks inside archives.
  `SPECTO_SEARCH_BACKEND=scan` forces the walk.
- The GUI flows (`tools/gui_flows`): `expand` opens the closed folders above its target
  first, in both drivers, and waits for a freshly opened folder's rows before it clicks the
  next chevron; flows that clicked under a top folder open it first; two offscreen flows
  cover the search box (`v2-232-search.txt`, under the scan backend) and a big folder
  opening one level at a time (`v2-231-lazy-tree.txt`).

### Links

- Ports on every view; data, selection, time and camera links; follow links on a key
  field; port tips and port capsules.
- Python links: per-pick scripts the agent writes under `.specto/links/`, run out of
  process only after the user approves the file; typed outputs.

### Clients, fleet and launch

- `specto` (CLI), `specto-mcp` (MCP server) and `specto_sdk.Client`, all generated from
  `api-info.json`; `specto --version` prints the release.
- `specto open [PATH]` launches the app from a global CLI; `specto instances` and
  `specto message <name> <text>` reach a running window by its instance name.
- Fleet: instance names, peer requests from other nimitz fleet members with allowed
  recipients, `fleet_status` and `fleet_send` for the agent.
- A Claude Code plugin (`specto` skill, `/specto` command, the MCP server) shipped as
  package data with an install plan.

### Platforms and setup

- Windows 11 and Ubuntu 24.04 build, run and test; macOS (Apple Silicon) builds the
  native plugin and runs Storm on OpenGL 4.1 core.
- Setup guides: `docs/setup/developer.md` (build, test and run a checkout) and
  `docs/setup/user.md` (the first launch, the agent and the basics). There is no
  installer yet (#200).

### The GUI flows run offscreen, in three tiers

- A second executor for the flows (#237): `tools/gui_qtdrive.py` starts
  `specto-app --test-driver` and sends Qt test events on the app's own GUI thread
  (`specto_app.testdrive`), so a run opens no window, takes no focus and moves no mouse.
  The driver exists only behind the flag: a normal launch has none. Under it the clipboard
  is in-memory (`specto_app.clipboard`), mpv plays into the null audio output, launchers
  start only the flows' fake programs, `window.raise` raises nothing, and each run has its
  own settings dir and process, so entries run in parallel (`--jobs`, at most 4).
- Every flow belongs to one of three tiers (`TIERS` in `tools/gui_flows/v2_suite.py`,
  checked by `test_gui_tiers.py`): `offscreen` (the default, most flows), `gpu` (a real
  window that never takes the focus: RTX, Storm, mpv video, WebGL pages and the pixel
  asserts that need the real scene graph) and `mouse` (pyautogui under the desktop lock,
  only native drag and drop). `--tier` runs one, `--list` prints it, and the report is one
  line per flow in `<out>/suite-<tier>.txt`. Six orphan flows joined the suite; the
  wheel events now carry timestamps, so a run of notches no longer folds into one flick.

### Publication

- MIT licence: `LICENSE`, the `license` field in every package, and
  `THIRD_PARTY_NOTICES.md` for the vendored, bundled and fetched components. Issue
  numbers (`#NNN`) in the code and docs refer to the prior tracker, not to this
  repository's issues.
- The build-time dependencies (the Qt SDK, libmpv, the OpenUSD imaging build, the
  Everything SDK) default to `~/specto-build` on every platform; `SPECTO_BUILD_ROOT` moves
  the root, `SPECTO_QT_ROOT`, `SPECTO_LIBMPV_DIR` and `SPECTO_OPENUSD` one entry each.
- The test dataset is generated from nothing by `tools/make_testdata.py` (two synthetic
  frame series `cam-a` and `cam-b` at different rates with sidecars and stamps from
  1700000000, a video, GeoJSON, notes, a point cloud, a mesh and archives); nothing else
  is needed to run the tests. `--out DIR` writes it elsewhere for `SPECTO_TESTDATA`.
