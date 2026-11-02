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
