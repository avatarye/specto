# User guide: running Specto from a checkout

There is no installer yet (#200 is planned), so Specto runs from a developer checkout
built as in [developer.md](developer.md). This guide starts once that is done and covers
the first launch, the agent, and the few things worth knowing on day one. Vocabulary is in
`GLOSSARY.md`.

## Before you start

- A built checkout: `uv sync --extra rtx --extra storm`, the web vendor, the native plugin.
- A Claude Code login on this machine: `claude auth login` with any Claude Code install
  (the bundled CLI in the venv shares `~/.claude/.credentials.json`). `claude auth status`
  checks it. The agent runs on your claude.ai account and costs what any Claude Code
  session costs.
- Windows 11 or Ubuntu 24.04. macOS is untested.
- Optional: the global `specto` command, to start Specto from any terminal (below).

## First launch

From the checkout:

```text
uv run --extra rtx --extra storm specto-app
```

The welcome screen offers **Open folder...**, a folder field with **Open**, and **Recent
projects** (empty the first time). When you start it from inside a project it also offers
**Open the project of this folder**. A project is any folder of data with a `.specto/`
inside; your files stay where they are and nothing outside `.specto/` is written. **Open
folder...** opens the folder's project, or creates one there when the folder has none.
The window then shows the canvas: the project node at the origin, the file tree right of
it, the status bar at the bottom and the S agent icon at the bottom left.

To skip the welcome screen, pass the folder: `specto-app --project <folder>` (created if
it has no `.specto/` yet), or set `SPECTO_PROJECT`. One window shows one project; opening
another from the menu starts a second process.

What `.specto/` holds:

| File | What |
|---|---|
| `project.toml` | name, id |
| `canvas.json`, `canvas.journal.jsonl` | the canvas document (the state of the canvas) and its journal |
| `endpoint.json` | where the running app answers (the CLI, the SDK and MCP read it; gone when the app exits) |
| `agent-session.json`, `agent-thread.jsonl` | the agent's Claude Code session id and the thread shown in the panel |
| `links/*.py` | Python link code the agent wrote; each runs only once you approved it |
| `scratch/` | the scratch pad's pages (`<page>.md`, `<page>.assets/`, `pages.json`) |
| `skills/`, `commands/` | skills the agent saved for this project and your own commands |
| `launchers.toml` | the project's launchers (see below) |

## Global CLI

The `specto` command can also start Specto from any terminal, without the checkout's
venv. Install it once from your synced checkout:

```text
uv tool install --editable <checkout>/packages/specto-cli
```

Then:

```text
specto open                 # the project the current folder is in
specto open <folder>        # that project (a plain folder becomes one)
specto open --list          # recent projects, with the instance name of any that is open
specto open --welcome       # the welcome screen
```

The app starts detached and the command prints the window's instance name. If a window
already has that project open, it comes to the front instead. Configuration (where the
app comes from) and troubleshooting are in
[developer.md, Global CLI](developer.md#global-cli).

## The agent and a first prompt

The S icon at the bottom left is the project's agent; its eyes show its state (blinking:
idle; looking around: thinking; scanning: working; wide: it needs you; X: unavailable or
errored). Rest the pointer on it, or press Ctrl+K, to open the agent panel: the thread
above, the prompt bar at its foot. Type what you want to see and press Enter, for
instance "show the cam-a frames as a gallery, linked to the file tree". The agent
composes the canvas; the thread shows a row for the turn with its label and **Undo**
(the whole turn) and **Diff** on request. The panel's header has a new-session button
(the thread stays above a divider), a pin, and two chips at the prompt bar: the model
for the next turn (Opus, Sonnet or Haiku; Sonnet by default) and **peers** (below). Esc
closes the panel and keeps your draft; Ctrl+Shift+V pastes plain text.

Views the agent adds have a short name (`kobi`, `tamu`) in their header: use it when you
talk to the agent ("move kobi under the tree").

If the agent icon shows X eyes on the first prompt, the Claude Code session did not
start: check `claude auth status`, then the app's console output, which names the error.
A `[WARN] agent session: ignoring inherited ANTHROPIC_API_KEY` line there is normal: the
window runs on your login, never on a key from the environment (developer.md, "The
Claude login").

## The basics

**Views and links.** Everything on the canvas is a view: the file tree, galleries, image,
video, markdown, json, html, pdf, map, 3D and usd viewers, previews and the scratch pad.
Views connect by links: picking files in the tree feeds the views linked to it, a gallery's
pick drives an image view, a video's time drives galleries linked on time. Double-click a
file in the tree for a preview of it. In a view's header: the auto-update eye, then
refresh, lock, float, collapse, maximise and close; drag the header to move, a border to
resize, and views snap to each other and to the window. Ctrl+Z and Ctrl+Y undo and redo
every change, yours and the agent's, in time order; the status bar shows the undo depth.
Right-click (or Shift+F10) opens the context menu for what is under the pointer.

**The file tree.** It lists a folder when you open it, so a big project shows at once;
everything starts closed except a chain of single folders from the top. Picking a folder
still picks every file under it. To find a file, type in the box at the top of the tree
(or just start typing with the tree focused): the hits replace the tree, best first, with
their folder under the name; click one to pick it, double-click to preview it, the target
button at its right to show it in the tree; Escape returns to the tree. On Windows the
search uses [Everything](https://www.voidtools.com/) when it is running and the project's
drive is indexed, which answers in a few milliseconds whatever the project's size; without
it (and on Linux and macOS) the search walks the project folder, which takes longer on a
big one. The footer says which answered when nothing is found. Everything is optional:
install it from voidtools.com and leave it running; a checkout also needs the SDK's DLL
once (`uv run python tools/fetch_everything_sdk.py`). The search does not look inside
archives (zip, tar), while the tree lists their members.

**The overworld.** Tab toggles the zoomed-out level: every view pauses and shows its
snapshot as a card, the links are drawn as wires with a node each, and you move and
resize freely; positions resolve, snapped, when you come back. Clicking a link node opens
its summary with **Delete**, **Ask agent to change** and **Advanced** (the link's
read-only view).

**The scratch pad.** Double-click the project node to open the project's one scratch
pad: named pages (tabs) of markdown, edited in place, where you drop or paste what you
find (text, pictures, files, items from views). Each page is one file under
`.specto/scratch/`; the agent can read and add to it when you ask.

**Launchers.** The context menu's **Open with** opens selected files in an external
program: the system default, **Show in Explorer**, and any launcher you configured in
`launchers.toml`, either the machine's (**Edit launchers...** opens it in your editor) or
the project's `.specto/launchers.toml` (**Edit project launchers...**, created from a
commented example the first time). Each entry is a `[[launcher]]` table:

```toml
[[launcher]]
id = "blender-4.2"
label = "Blender 4.2 LTS"
command = "D:/Tools/blender-4.2.3/blender.exe"
args = ["{path}"]
match = ["*.blend", "*.blend1"]
default = true
```

Placeholders are `{path}`, `{paths}`, `{dir}`, `{name}`, `{stem}` and `{project}`; the
full rules are in `packages/specto-app/src/specto_app/launchers/config.py`. A save
reloads the file at once, and a bad line shows as a notice naming the line.

**Python links.** When the agent needs code to connect two views it writes
`.specto/links/<name>.py` and the view's header shows **Approve** with the file open in
the thread; nothing runs until you approve, and an edited file needs approving again.

**Instance name and peers.** Each running Specto has a five-letter name (`rasel`), shown
first in the status bar, in the panel header and in the window title, stable for this
project on this machine. Other Claude Code agents of your nimitz fleet message the
window's agent by it (`specto message rasel <text>`); `specto instances` lists the
running ones. A message arrives as a peer request: the **peers** chip in the panel
decides whether it runs as a turn (`auto`, the default), waits for you with **Run** and
**Dismiss** (`ask`) or is refused (`off`). The **recipients** list in **Details** (the
info button at the status bar's right end) names which fleet members the agent may
message back; empty means every fleet nimitz reports.

**The global menu.** The button at the top right of the canvas opens **Open folder...**
(Ctrl+O; it opens the folder's project, or creates one there), **Recent projects** and
**Quit** (Ctrl+Q). **Details** (instance, recipients, project, endpoint, workers) is the
info button at the right end of the status bar. Canvas snapshots come from the agent
(`snap`) or the endpoint; there's no menu row for them.

## Where settings live

Per machine, in the user data dir: `C:\Users\<you>\AppData\Local\specto\specto` on
Windows, `~/.local/share/specto` on Linux; `SPECTO_USER_DIR` overrides it.

| File | What |
|---|---|
| `settings/specto/specto-app.ini` | the model chip, Peer requests, fleet recipients, the panel's size and pin, tree details |
| `recents.json` | the welcome screen's recent projects |
| `trusted-links.json` | the Python link files you approved, by content hash |
| `launchers.toml` | the machine's launchers |
| `instance-names.json`, `instances/` | instance names per project and the registry of running apps |
| `launch.log` | output of apps started from the menu or by `specto open` |
| `cli.json` | the checkout `specto open` launches the app from (`specto setup-cli`) |

Per project, in `.specto/` (above). Delete `.specto/` to start a project over; delete the
user dir to forget recents, approvals and settings.

## Known limits

- No installer or packaged app: a built checkout and a shell are needed (#200).
- Windows 11 and Ubuntu 24.04; macOS untested.
- The agent needs a Claude Code login; `ANTHROPIC_API_KEY` in the environment is ignored
  unless `SPECTO_AGENT_KEEP_API_KEY=1`.
- One project per window and one canvas per project; a Specto v1 project
  (`.specto/canvases/`) is refused, with no migration.
- The usd view needs an extra: `rtx` (NVIDIA GPU, a 2.3 GB download) or `storm`; without
  one it shows a test pattern. The video view needs libmpv built in.
- Nothing loads from the network except the map's basemap tiles (when a view has the
  `net:tiles` capability) and pages the html view is allowed to reach.
- The file tree's search finds files on disk by name, not archive members; with
  Everything, only what its index holds (its exclude settings apply). The project node
  shows no file count until a view pinned to the whole folder has scanned it; the tree
  never scans the whole project.
- The seam-3 GUI tests take over the mouse and keyboard; do not run them while using the
  desktop.
