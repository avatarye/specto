# Landscape survey (2026-10-12)

Three research passes: existing multi-modal viewers, agent-driven app patterns, and
ROS bag integration. Claims were checked against official docs where possible;
items marked (unverified) were not. Sources at the end.

## Verdict

Specto's niche is real. No existing tool has:

1. One session document spanning USD, splats, video, maps, code and bags. Every tool
   has its own domain-specific layout format.
2. Generic typed link channels across *different kinds* of view (a USD camera tied to
   a MapLibre view through a frame graph). Existing links are hard-coded: Foxglove
   shares time, glue shares attributes, RViz shares TF.
3. One command schema behind GUI, CLI, SDK and MCP. Every agent interface surveyed was
   added beside the GUI, not underneath it.
4. USD (Hydra) plus web maps plus code editors in one native desktop app.
5. Most viewers' MCP servers act on "the active tab"; none makes the session an
   addressable, undoable resource.

Agent control itself is now normal: Rerun, Foxglove, FiftyOne, napari, ParaView,
kepler.gl and CARTO/deck.gl all ship an agent interface.

## Viewers and what to take from each

| Tool | License | Take | Avoid |
|---|---|---|---|
| Rerun | MIT/Apache | Layout ("blueprint") is data on its own timeline, re-read every frame; three layout layers (auto, default from code/`.rbl`, user edits); views are queries over entity paths; multiple timelines per record, `static` data; MCP with get/set blueprint, time cursor, screenshot | `set_blueprint` replaces the whole layout: offer patch edits |
| Foxglove | proprietary since 2.0 (2024) | Panels declare which state fields re-render them; config in layout via `saveState`; `seekPlayback` vs `setPreviewTime` (hover time); tools tagged read-only/destructive | Closed, account-bound, MCP paid-only; tools act on the focused window |
| Lichtblick | MPL-2.0 (Foxglove 1.x fork) | Proof the Foxglove panel model survives open; usable as a fallback ROS panel | No embedding API; a second app with its own time and TF model |
| napari | BSD-3 | npe2 manifest: contributions point at command IDs via `module:attr`, readable without importing plugin code; async slicing, multiscale | |
| glue | BSD-3 | Links between attributes of different datasets; a selection is a predicate (subset state), drawn in every view that can map it and silently skipped where it cannot | |
| ParaView / VisIt | BSD-3 | Demand-driven pipeline; state file separate from trace; client/server split | Python trace after the fact is incomplete; prefer every action as a command |
| F3D / libf3d | BSD-3 | Config blocks chosen by file glob/regex, options declarative or forced with `!`: recipes by file pattern. Gaussian splats since 3.2 | |
| FiftyOne | Apache-2.0 | Operators with `resolve_input` / `execute` / `resolve_output`: one input schema drives both a generated form and an agent call. Dataset = samples + fields; view = stored query | ~64 MCP tools is too many |
| kepler.gl / deck.gl | MIT | `{datasets, config}` saved apart with versions, so a config is a template. CARTO's deck.gl JSON v2 proposal: JSON Schema, conversion reports instead of silent drops, state read-back, patch semantics | Silent failure (e.g. an empty fill colour) |
| PlotJuggler | MPL-2.0 | Plugin roles split: loaders, streamers, parsers, toolboxes; layouts and derived series in Lua/Python. v4 beta adds images, video, point clouds, TF (unverified) | |
| RViz2 | BSD-3-Clause-Clear | One fixed frame resolved through the TF tree: the reference frame-graph model | Tight ROS coupling |
| QuickLook (Windows) | GPL-3.0 (ideas only) | `IViewer`: `Priority`, `CanHandle(path)`, `Prepare`, `View`, `Cleanup`: prioritized reader dispatch | |
| macOS Quick Look | Apple | A cheap static preview path apart from the full interactive view | |
| anywidget / trame | MIT / Apache-2.0 | A state dict synced between Python and JS (`get`/`set`/`save_changes`): the model for the web pane bridge | |
| ply-visualizer | MIT | One core shipped as VS Code/JetBrains extension, web, CLI, Tauri app and MCP server | |
| MCP Apps (SEP-1865) | spec | Tools can return sandboxed `ui://` HTML: a route to show Specto views inside agent chat clients | |

## Agent-driven app patterns

- **Neovim** is the best model for "define once, expose many ways": one API metadata
  dict (`functions`, `ui_events`, `types`, `error_types`, `version`) from
  `nvim --api-info`; per-function `since` / `deprecated_since`; `api_level`,
  `api_compatible`, `api_prerelease`. Static clients codegen, dynamic clients discover.
- **Omniverse Kit** commands: `do()` plus optional `undo()`; no `undo()` means off the
  undo stack; simple serializable args; errors roll back the command and its children;
  groups make one undo step.
- **Unreal Remote Control**: `generateTransaction` decides whether a remote edit enters
  undo history; `remote/batch` runs requests in order; `remote/info` lists routes.
- **Blender** (from memory): RNA introspection, operators with `poll()` and UNDO flags,
  Info editor logs replayable Python.
- **VS Code**: one handler per command ID; contribution points declare where it shows;
  workspace trust with Restricted Mode. Hidden commands can still be called, so
  handlers must check trust themselves.
- **Agent tool design** (Anthropic guidance, Figma MCP, Playwright MCP): few coarse
  tools; readable names, not UUIDs; concise/detailed response modes; cheap outline
  first (`get_metadata`), then fetch the parts needed; act on semantic refs, not
  pixels; screenshots confirm, they are not the interface; error messages say how to
  fix the call.
- **Codegen in Python**: pydantic models per command, registered by a decorator with
  `name`, `since`, `undoable`, `read_only`, `destructive`; export one `api-info.json`;
  CLI built dynamically with click (Typer fits static signatures, not a runtime
  registry); MCP tools registered in a loop with FastMCP (MCP 2025-06-18 has
  `outputSchema` / `structuredContent`; tool annotations are advisory only); TS types
  via `json-schema-to-typescript`; a generated typed Python SDK for IDE completion.
- **Linked views**: Vega-Lite selections are named parameters combined across views by
  `resolve`; glue selections are predicates over fields; crossfilter's brushed view
  does not filter itself. Echo prevention: origin tags, idempotent setters, one source
  of truth in the session document, revision counters, `QSignalBlocker`.
- **Trust**: Blender auto-run off by default and drivers count as code; VS Code
  Restricted Mode; Jupyter signs content with a per-user secret and treats the user's
  own outputs as trusted. Lesson: every path that can run code must be listed.
- Pitfall: an "execute arbitrary code" tool (blender-mcp's `execute_blender_code`) is
  a CVE magnet (CVE-2026-10688 listed against it; unverified).

## ROS bags

- **Formats**: MCAP has been rosbag2's default since Iron; `.db3` (sqlite) still
  supported in Jazzy; ROS1 `.bag` is legacy. MCAP (MIT) embeds schemas; old `.db3`
  may not, so a built-in type store is needed.
- **`rosbags`** (Ternaris, Apache-2.0, pure Python, Windows and Linux): reads ROS1
  `.bag`, ROS2 `.db3` and MCAP through `AnyReader`, deserializes CDR and ROS1, ships
  type stores and `rosbags-convert`; `rosbags-image` converts images to OpenCV.
- **`PointCloud2` to numpy**: a structured dtype from `fields` with
  `itemsize=point_step`, then `np.frombuffer`; handle `is_bigendian` and padding.
- **Foxglove** is closed and its embed SDK is a hosted, paid-tier iframe. **Lichtblick**
  (MPL-2.0, v1.29.1 Sep 2026) self-hosts as a web build with local file, remote-file
  and Foxglove WebSocket sources, but has no embedding API.
- **`foxglove-sdk`** (MIT, Rust core, Windows/Linux wheels) lets Specto act as a
  Foxglove WebSocket server, so Lichtblick follows Specto's clock (one-way).
- **Rerun** reads MCAP and maps key ROS 2 types, but not ROS1 or `.db3`; useful as a
  reference for which types to map, not as a bridge.

Recommendation: a native `specto-reader-rosbag` plugin on `rosbags.AnyReader`, mapping

| Bag data | Specto target |
|---|---|
| `Image`, `CompressedImage` | gallery or video dataset |
| `PointCloud2` | 3D view |
| `NavSatFix` | track on the map |
| `/tf`, `/tf_static` | frame graph (time-varying edges; static once) |
| `Odometry` | pose channel / frame edge |
| header stamps | time channel |
| anything else | generic message inspector (JSON tree) |

Lazy, indexed reads per topic and time range for large bags. Optional later: an
"Open in Lichtblick" fallback panel fed by Specto serving the file over HTTP, or by
`foxglove-sdk` if Specto should drive its time.

## Spikes worth doing first

1. Open a sample MCAP with `rosbags.highlevel.AnyReader`, turn one `PointCloud2` into
   numpy (about 50 lines).
2. Load the Lichtblick web build in `QWebEngineView`; check WebGL2 and file loading.
3. Play an H.264 MP4 in `QWebEngineView` to confirm the codec gap.
4. Load the largest local LiDAR file in Babylon.js to decide whether COPC streaming is
   needed early.

## Uncertain

Rerun's current version (docs say 0.39); Lichtblick `.foxe` compatibility and behaviour
inside QtWebEngine; VisIt's license; PlotJuggler 4 performance claims; deck.gl JSON v2
is a CARTO proposal only; Foxglove's 2026 free-tier terms; Blender details from memory
(docs returned 403); the blender-mcp CVE (secondary sources).

## Sources

Viewers: rerun.io/docs/concepts/blueprint · rerun.io/docs/concepts/timelines ·
rerun.io/docs/concepts/transforms · rerun.io/blog/release-0.27 ·
rerun.io/docs/reference/viewer/mcp · github.com/rerun-io/rerun ·
docs.foxglove.dev/docs/extensions · docs.foxglove.dev/docs/agents/mcp-server ·
discourse.openrobotics.org/t/foxglove-2-0-integrated-ui-new-pricing-and-open-source-changes/36583 ·
github.com/lichtblick-suite/lichtblick ·
napari.org/stable/plugins/building_a_plugin/guides.html ·
docs.glueviz.org/en/latest/developer_guide/organization.html ·
docs.paraview.org/en/latest/UsersGuide/introduction.html ·
kitware.com/paraview-6-1-0-release-notes · github.com/f3d-app/f3d ·
f3d.app/docs/next/user/CONFIGURATION_FILE ·
docs.voxel51.com/plugins/developing_plugins.html · docs.voxel51.com/agents/using_agents.html ·
docs.kepler.gl/docs/api-reference/advanced-usages/saving-loading-w-schema ·
carto.com/blog/making-deckgl-ai-ready · github.com/facontidavide/PlotJuggler ·
github.com/ros2/rviz · github.com/QL-Win/QuickLook ·
developer.apple.com/documentation/quicklookui/qlpreviewprovider ·
docs.marimo.io/api/inputs/anywidget.html · kitware.com/trame-architecture-and-capabilities ·
github.com/kleinicke/ply-visualizer ·
modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp

Agent patterns: raw.githubusercontent.com/neovim/neovim/master/runtime/doc/api.txt ·
docs.omniverse.nvidia.com/kit/docs/omni.kit.commands/latest/Overview.html ·
dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-api-http-reference-for-unreal-engine ·
rerun.io/docs/howto/configure-viewer-through-code ·
docs.paraview.org/en/latest/Tutorials/ClassroomTutorials/advancedStateManagement.html ·
code.visualstudio.com/api/extension-guides/command ·
code.visualstudio.com/api/extension-guides/workspace-trust ·
sidefx.com/docs/houdini/hom/rpc.html ·
developers.figma.com/docs/figma-mcp-server/tools-and-prompts · github.com/microsoft/playwright-mcp ·
anthropic.com/engineering/writing-tools-for-agents · nvd.nist.gov/vuln/detail/CVE-2026-10688 ·
gofastmcp.com/servers/tools · vega.github.io/vega-lite/docs/selection.html ·
docs.glueviz.org/en/stable/developer_guide/selection.html ·
github.com/square/crossfilter/pull/95 ·
jupyter-server.readthedocs.io/en/latest/operators/security.html

ROS: docs.foxglove.dev/docs/embed · lichtblick-suite.github.io/docs/docs/connecting-to-data/live-data ·
lichtblick-suite.github.io/docs/docs/extensions/introduction · github.com/foxglove/ws-protocol ·
pypi.org/project/foxglove-sdk · github.com/foxglove/mcap · mcap.dev/spec ·
pypi.org/project/mcap-ros2-support · foxglove.dev/blog/mcap-as-the-ros2-default-bag-format ·
index.ros.org/p/rosbag2_storage_sqlite3 · ternaris.gitlab.io/rosbags ·
gitlab.com/ternaris/rosbags-image · rerun.io/docs/reference/mcap ·
github.com/cruise-automation/webviz ·
discourse.openrobotics.org/t/plotjuggler-4-beta-is-here-unleashing-multi-modal-data/56047
