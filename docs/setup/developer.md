# Developer setup

How to get a Specto checkout building, running and tested. Windows 11 is the verified
platform: every step below was run in a fresh worktree on 2026-10-27 (#214). The Ubuntu
24.04 steps come from #206's README and `packages/specto-app/native/build.py`, verified
on Ubuntu 24.04 on a second machine. macOS is untested.

The user guide, for running Specto once it is built, is [user.md](user.md).

## What you end up with

- `.venv/`: the uv workspace (Python 3.12, every package editable, PySide6 6.12.0, the
  Claude Agent SDK with its bundled `claude` binary).
- `packages/specto-web/web/vendor/`: the web views' third-party assets (git-ignored).
- `packages/specto-app/src/specto_app/qml/Specto/Native/`: the native Qt Quick module
  `Specto.Native` (the gallery, the frame item, rounded clips, the libmpv video item;
  git-ignored). The shell's QML imports it, so build it before the first launch.
- `testdata/`: the small test dataset the GUI flows seed from (git-ignored).

## Prerequisites

Both platforms:

- git, [uv](https://docs.astral.sh/uv/) (it fetches Python 3.12 itself), Node.js 20 or
  newer with npm (the web build), ffmpeg and ffprobe on PATH (`tools/make_testdata.py`).
- The Qt 6.12.0 SDK, installed with aqtinstall in the step below. The version must equal
  the PySide6 wheel's Qt (`build.py` refuses to build otherwise); the SDK's `qtbase` and
  `qtdeclarative` archives are enough, since the `qsb` shader compiler comes from the
  PySide6 wheel.

Windows 11:

- Visual Studio 2022 Build Tools with the "Desktop development with C++" workload,
  including the "C++ CMake tools for Windows" component: `build.py` runs cmake and Ninja
  inside `vcvars64.bat`, so they only need to be on that environment's PATH. A separate
  CMake 3.21 or newer also works.
- 7-Zip (`7z` on PATH) is optional: `tools/fetch_libmpv.py` falls back to `py7zr` in a
  throwaway venv.

Ubuntu 24.04:

```text
sudo apt install build-essential cmake libgl-dev libmpv-dev ffmpeg
```

g++ 11 or newer and CMake 3.21 or newer; Ninja is optional (Makefiles otherwise). The
distribution's `nodejs` package is older than 20: use the NodeSource repository or nvm.

## 1. Clone and sync

```text
git clone git@github.com:avatarye/specto.git
cd specto
uv sync --extra rtx --extra storm
```

`uv sync` creates `.venv` with every workspace member editable. The extras are the usd
view's renderers (`rtx`: NVIDIA ovrtx and ovstage, a 2.3 GB download from pypi.nvidia.com
at install; `storm`: Hydra Storm from the OpenUSD imaging build of
`tools/parked/build_openusd.py`). Without either the usd view shows the stub source's test
pattern. A plain `uv run` re-syncs the venv without extras, so keep them on every call:
`uv run --extra rtx --extra storm specto-app`, or pass `--no-sync`. The commands below
leave the flags out for brevity; add them to every `uv run` once the extras are installed.

The workspace pins public PyPI as its only index (`pyproject.toml`), and
`packages/specto-web/.npmrc` pins the public npm registry, so a private index in a
user-level `uv.toml` or `~/.npmrc` is never consulted.

## 2. Web vendor

```text
uv run python packages/specto-web/build.py            # npm install + copy + HDRIs
uv run python packages/specto-web/build.py --check    # exit 1 when a pinned version is missing
```

Runs `npm install` in `packages/specto-web`, copies the pinned libraries (Monaco,
markdown-it, Mermaid, KaTeX, MapLibre, Babylon.js and its loaders) into `web/vendor/`,
then downloads the 3D view's five Poly Haven HDRIs (sha256-checked) and prefilters them in
a headless Qt WebEngine. `--no-install` copies only, `--no-hdri` skips the environments,
`--hdri-only` redoes just them. Verified: about a minute, all `[OK]`.

## 3. libmpv

The video view's `MpvItem` compiles against libmpv's headers and loads the library at
runtime.

Windows:

```text
uv run python tools/fetch_libmpv.py                   # pinned shinchiro release -> ~/specto-build/libmpv
uv run python tools/fetch_libmpv.py --dest D:/libmpv  # or set SPECTO_LIBMPV_DIR
```

It leaves `libmpv-2.dll`, `include/mpv/*.h` and a `libmpv.json` manifest under the
destination; `--tag latest` asks GitHub for the newest release, `--force` refetches into a
non-empty folder.

Linux: `sudo apt install libmpv-dev` (above) is the normal route; the plugin then
compiles against `/usr/include/mpv` and the loader finds `libmpv.so.2`. Without root,
`uv run python tools/fetch_libmpv.py` unpacks the distribution's own `libmpv2` and
`libmpv-dev` debs into `~/specto-build/libmpv` with `RUNPATH=$ORIGIN`.

### The usd view's skies and HDRIs

The usd view's **Sky** environment (SPEC-31) is one of NVIDIA's seven dynamic skies from
the public Omniverse sample assets, with the MDL they are made of. Their licence allows
no stand-alone or public-repository redistribution (`THIRD_PARTY_NOTICES.md` section
3), so nothing of them is in the repository and there is no build step: the app
fetches them the first time the Sky choice is used (`specto_app.usd.skies`), on a worker
thread, from NVIDIA's public bucket, each file pinned by sha256 (a mismatch is removed
and refused; offline is said under the control), into `<user dir>/skies` (the app's
data folder, `specto_core.instances.user_dir`; `SPECTO_USER_DIR` moves it, as the GUI
flows do), packed as one stored `.usdz` per sky with the stage first, which the view
references as `<name>.usdz[Dynamic/<name>.usd]` (ovrtx compiles the MDL from inside it);
the plain files stay under `skies/src/`, shared between skies, with the licence notice
beside them. The sky asked for comes first, then the rest once, the textured CloudySky
(67 MB of textures) last. Every download goes to a `.part` cut off past its pinned size,
checked, then moved into place, under a lock file on the folder (two Specto processes
take turns). `SPECTO_SKY_BASE_URL` points the fetch at another server (the tests serve a
fake bucket from a local folder). The **HDRI** environment is one of the `.hdr` files of
the 3d view's environments: in a source checkout the ones step 2 downloaded
(`node_modules/.hdri`, `specto_web.hdri_source`), else fetched the same way from Poly
Haven into `<user dir>/hdri`, pinned by `web/3d/environments.json` (`SPECTO_HDRI_BASE_URL`
for a local server); `SPECTO_USD_HDRI` points the view at another `.hdr` (tests). Without
either the panel says so and the view falls back (the sky to the HDRI, the HDRI to the
stage's own light). Storm never renders a sky: it shows the HDRI for it. The sky's ground
(`params.ground`) is the floor of the preview scene each sky package carries.

### Everything (Windows, optional)

The file tree's type-to-search (#232) asks [Everything](https://www.voidtools.com/) when
its service is running and the project's drive is indexed; otherwise it walks the project
folder (always the case on Linux and macOS). The query goes over the Everything SDK's
DLL through `ctypes` (`specto_intake.everything`), no `es.exe` and no process per
keystroke: a search under a 15k-file folder answers in about 20 ms. Every word matches
anywhere in the path below the root (#36): the index is asked for each word as a `path:`
term, or, for a word that occurs in the root's own path (where `path:` would match every
file), as a regex anchored at the root (about 25 ms more on 15k files); each candidate is
checked against its relative path; when the candidates outrun the first page and the
hits do not fill the limit, they are read in one more page up to `INDEX_BOUND`, past
which the walk answers. Install Everything 1.4 from voidtools.com and leave it running,
then fetch the SDK once:

```text
uv run python tools/fetch_everything_sdk.py     # -> ~/specto-build/everything-sdk, DLL copied beside the search
```

It downloads `Everything-SDK.zip` (sha256-checked, read with `zipfile`), keeps the DLL,
the header and the SDK's MIT permission notice under the destination, and copies
`Everything64.dll` with the notice into `packages/specto-intake/src/specto_intake/vendor/`
(git-ignored). `SPECTO_EVERYTHING_DLL` points the search at another DLL;
`SPECTO_EVERYTHING_SDK_DIR` at another destination (`SPECTO_BUILD_ROOT` moves the root). Without the DLL or the service the
search falls back silently; `tests/test_search.py`'s latency test skips.
`SPECTO_SEARCH_BACKEND=scan` forces the walk even with Everything up (the search flow of
the GUI suite runs under it, so it is the same on every machine).

## 4. The Qt SDK and the native plugin

Windows (the SDK goes to `~/specto-build/Qt`, `%USERPROFILE%\specto-build\Qt`; `SPECTO_BUILD_ROOT`
moves every build-time dependency, `--qt-root` or `SPECTO_QT_ROOT` the SDK alone):

```text
uv tool run --from git+https://github.com/miurahr/aqtinstall aqt \
    install-qt windows desktop 6.12.0 win64_msvc2022_64 \
    -O ~/specto-build/Qt --archives qtbase qtdeclarative
uv run python packages/specto-app/native/build.py
```

Linux (the SDK goes to `~/specto-build/Qt`):

```text
uv tool run --no-config --from git+https://github.com/miurahr/aqtinstall aqt \
    install-qt linux desktop 6.12.0 linux_gcc_64 \
    -O ~/specto-build/Qt --archives qtbase qtdeclarative icu
uv run python packages/specto-app/native/build.py
```

`build.py` compiles the native shaders with the wheel's `qsb`, configures with the SDK as
`CMAKE_PREFIX_PATH`, builds Release and installs the module tree under
`specto_app/qml/Specto/Native/` with the libmpv DLL beside it (on Linux a fetched
`libmpv.so.2` and its bundled deps; a system libmpv stays where apt put it). Flags:
`--qt-root`, `--libmpv` (the fetch dir), `--config Release|RelWithDebInfo|Debug`,
`--clean` (wipe `native/build/` first). On Linux the plugin's RUNPATH is the PySide6
wheel's `Qt/lib`, never the SDK's, and an exported `CMAKE_PREFIX_PATH` (ROS, conda) is
ignored. A shell that sourced another stack's environment also leaks `PYTHONPATH` and
`LD_LIBRARY_PATH` into the venv: unset them first.

Verified: `[OK] PySide6 Qt 6.12.0; SDK <build root>\Qt\6.12.0\msvc2022_64`,
installed files `libmpv-2.dll, qmldir, spectoqt.dll, spectoqt.qmltypes, ...`.

## 5. QML shaders

```text
uv run python tools/build_qml_shaders.py            # recompile after changing a shader
uv run python tools/build_qml_shaders.py --check    # fail when a committed .qsb is stale
```

The QML `ShaderEffect` shaders under `qml/Specto/**/shaders/` are compiled with
`pyside6-qsb` to `.qsb` files next to their source for every Qt Quick backend. The `.qsb`
files are committed, so a fresh checkout only needs `--check`; after changing a shader,
run it without `--check` and commit both. Verified: `--check` passes on a fresh
checkout.

## 6. Test data

```text
uv run python tools/make_testdata.py                # about 29 MB under testdata/, 20 s
uv run python tools/make_testdata.py --out DIR      # somewhere else (SPECTO_TESTDATA)
```

Generates the whole dataset from nothing: no source folder, no environment variable, no
network. Every file is synthetic and the output is deterministic (the same bytes on every
run with the same ffmpeg build), so a fresh clone needs only ffmpeg and ffprobe on PATH.
It writes one run, `runs/run-001/`, with two frame series of a moving scene (`cam-a`:
240 frames at 32 Hz; `cam-b`: 240 frames at 16 Hz starting 50 ms later, so no stamp is
shared and the rates differ), each frame a `NNNNN_<stamp_ns>.jpg` with a
same-name `.json` sidecar; `video/cam-a.mp4` (H.264 at 32 fps with a beeping audio
track) and its `cam-a.timeline.json`; per-frame, signal and event CSVs, a YAML and a
markdown summary under `meta/`; GeoJSON sites, pairs, a coverage polygon and the run's
path around (0, 0) under `geo/`; five markdown notes under `notes/`; a PLY point cloud
under `scans/`, a glTF mesh under `models/` and a zip and a tar.gz under `archives/`. The
result is git-ignored; `testdata/README.md` (written by the script) lists it. Running the
script again replaces those folders.

The tests, seeds and benches that need real files read them from `testdata/` in the
checkout, or from `SPECTO_TESTDATA` when set. Unset and unbuilt, each test skips and each
seed says what it needs. The USD tests and flows that want a large stage take it from
`SPECTO_P4_DEPOT` with `SPECTO_P4_WORLD`, `SPECTO_P4_SKY`, `SPECTO_P4_PRIM` and
`SPECTO_P4_PRIM_PARENT` (`tools/gui_flows/_depot.py`), and the #93 flow its page from
`SPECTO_V93_PAGE`; the repo names no such stage or page.

The GUI flows need it: their seeds (`tools/gui_flows/v2-39-seed.py` and the others) copy
from `testdata/` in the checkout, or from `SPECTO_TESTDATA`. A worktree without it seeds
empty projects that can pass falsely (see "Worktrees").

## 7. Run it

```text
uv run --extra rtx --extra storm specto-app                      # the welcome screen
uv run --extra rtx --extra storm specto-app --project <folder>   # one project (created if new)
uv run --extra rtx --extra storm specto --help                   # the CLI
```

`specto-app --help` lists the flags: `--inline-jobs` (no worker pool, for debugging),
`--workers N`, `--no-web`, `--no-serve`, `--offscreen` (tests), `--host-module MODULE`,
`--agent claude|scripted:<script.json>` (the window's agent) and `--test-driver` (the GUI
flows' driver, section 8; never on without it). `SPECTO_PROJECT` is the same as
`--project`.

## Global CLI

The `specto` command talks to running Specto windows and, with `specto open`, starts them.
Installing it once as a global tool puts it on PATH in every terminal, independent of the
checkout's venv.

### Install

```text
uv tool install --editable <checkout>/packages/specto-cli
specto --help
```

`<checkout>` is your clone of avatarye/specto, synced (`uv sync` there, so `.venv/`
exists: that venv runs the app). The install is editable, so pulling the checkout updates
the command; after a change to the CLI's dependencies, run the same command with
`--reinstall`. It works the same on Linux and Windows. Uninstall with
`uv tool uninstall specto-cli`.

Inside the checkout's activated venv, the venv's own `specto` comes first on PATH. Both
are the same code, so it doesn't matter which one runs.

### Open a project

```text
specto open                 # the project the current folder is in
specto open ~/runs/day3     # that project (a plain folder becomes one, named after the folder)
specto open --list          # recent projects, with the instance name of any that is open
specto open --welcome       # the welcome screen
specto open . --no-web      # anything after PATH goes to specto-app
```

`specto open` starts the app detached (closing the terminal doesn't close it) with no
console window. It appends the app's output to `<user dir>/launch.log` (or `--log FILE`),
and prints the window's instance name once it registers:

```text
[OK] rasel  day3  C:\runs\day3  pid 41020
[INFO] log C:\Users\me\AppData\Local\specto\specto\launch.log
```

If a window already has that project open, `specto open` brings it to the front instead;
`--new` opens a second window anyway. `--wait 0` returns right after the start, without
the name. `--json` prints `{pid, instance, instance_pid, project, created, log,
app_home}`. The instance name is what `specto instances` lists and what
`specto message <name> ...` addresses.

### Configuration: where the app comes from

`specto open` launches `python -m specto_app` from a Specto checkout's `.venv`. It finds
that checkout in this order:

1. `SPECTO_APP_HOME=<checkout>` in the environment.
2. `app_home` in `<user dir>/cli.json`, written by `specto setup-cli <checkout>`.
3. The checkout the `specto` command itself is installed from (an editable install).

With the install command above, rule 3 already applies and nothing needs configuring.
`specto setup-cli --show` prints the candidates and which one is used. The user dir is
`platformdirs.user_data_dir("specto", "specto")`: `%LOCALAPPDATA%\specto\specto` on
Windows, `~/.local/share/specto` on Linux. `SPECTO_USER_DIR` overrides it.

### Troubleshooting

- `cannot find the Specto app to launch`, or `... is unusable: ... has no synced venv`:
  run `uv sync` in the checkout, then `specto setup-cli <checkout>` (or set
  `SPECTO_APP_HOME`). A home that is set but broken is reported, not skipped: fix or
  unset it.
- `the app exited before registering`: the CLI prints the end of the launch log; the full
  log is `<user dir>/launch.log`. A typical cause is the checkout's venv missing the
  native plugin or an extra (`uv sync --extra rtx`,
  `python packages/specto-app/native/build.py`).
- `not registered after 30s`, but the window appears later: a slow first start
  (QtWebEngine). `specto instances` lists it once it's up. Raise `--wait`.
- `is open already as <name>`, but the window didn't come forward: Windows may only flash
  its taskbar entry while another application holds the foreground. Click it, or use
  `--new`.
- `specto: command not found` after install: `~/.local/bin` (uv's tool bin dir,
  `uv tool dir --bin`) isn't on PATH. `uv tool update-shell` adds it.
- `specto instances` is empty while a window is open: the global tool and the app resolve
  the user dir differently only if `SPECTO_USER_DIR` is set in one of them. Unset it, or
  set it in both.

## 8. Tests: the three seams

Tests sit at three seams:

- Seam 1, submit to resolved state: `packages/specto-core/tests`,
  `packages/specto-intake/tests`. No Qt.
- Seam 2, view-host ports: `packages/specto-app/tests` (`test_ports_hosts.py`,
  `test_video_host.py`, `test_web_*.py`, ...), offscreen Qt.
- Seam 3, scripted human flows: `tools/gui_flows/*.txt`, run by
  `tools/gui_flows/v2_suite.py` in three tiers (below): offscreen through the app's Qt
  test driver, a GPU window, or a real mouse.

Seams 1 and 2 are pytest:

```text
uv run pytest                                  # every package
uv run pytest packages/specto-core             # one package: 286 tests, 14 s here
uv run ruff check . && uv run ruff format --check .
uv run python tools/gen_api.py --check         # the generated SDK, api-info.json and the .d.ts
```

`packages/specto-app` alone takes about 10.5 minutes. Tests marked `ovrtx` render on the
GPU and skip without the `rtx` extra.

Seam 3 keeps one flow language (`tools/gui_drive.py`'s docstring lists the lines) and has
two executors. Every flow belongs to one of three tiers (`TIERS` in `v2_suite.py`,
checked by `test_gui_tiers.py`; a flow outside the default tier carries its reason), and
one command runs a tier:

```text
# offscreen (the default, most flows): no window, no focus, safe while you work; entries
# run in parallel (--jobs, at most 4 and cores - 8)
uv run python tools/gui_flows/v2_suite.py --work <scratch folder> [--out out/verify/v2] \
    [--tier offscreen] [--jobs 4] [--only open views ...] [--live] [--net]

# the GPU window: a real window that never takes the focus, one entry at a time, for RTX,
# Storm, mpv video, the WebGL pages and the pixel asserts that need the real scene graph
uv run python tools/gui_flows/v2_suite.py --work <scratch folder> --tier gpu [--heavy]

# the real mouse: pyautogui under the desktop lock, when nobody is using the machine; only
# what Qt events cannot reach (native drag and drop into another application)
uv venv /tmp/pyauto && uv pip install --python /tmp/pyauto/Scripts/python.exe pyautogui pillow
uv run python tools/desktop_lock.py run <name> -- /tmp/pyauto/Scripts/python.exe \
    tools/gui_flows/v2_suite.py --work <scratch folder> --tier mouse
```

`--list` prints a tier's entries. Each entry seeds a project under `--work`, launches
the app (one process per session, its own user settings dir under the project) with a
scripted agent and runs its flows; screenshots land in `--out` (default `out/verify/v2`,
git-ignored) and the report, one line per flow, in `<out>/suite-<tier>.txt`. `--heavy` adds the 20 M-point 3D flow (GPU tier),
`--live` one short run against the real Claude Code agent (your Claude login; billed to it),
`--net` the html view with live embeds.

The offscreen and GPU tiers go through the app's test driver: `tools/gui_qtdrive.py`
starts `specto-app --test-driver`, asks `shell.locate` where items are and sends Qt test
events (`QTest`, synthetic wheel and key events) on the app's own GUI thread
(`specto_app.testdrive`). Nothing in the run reaches the desktop: the clipboard is an
in-memory one, mpv plays into the null audio output, launchers start only the flows' fake
programs, `window.raise` raises nothing, and off the offscreen platform the window opens
with `Qt.WindowDoesNotAcceptFocus` and is lowered to the bottom of the z order as it
shows, behind whatever the user is working in (it keeps rendering covered); keys go to
the window's own focus item, so no OS focus is needed (`test_testdrive.py` checks the
foreground window, the cursor and the system clipboard are unchanged after a run). A
line a tier cannot honour carries `mouse:` or `driver:` (the driver's mpv plays into the
null audio output, so the real output is the mouse tier's to check). A normal launch
never opens the driver. Offscreen
the scene graph is the software one, so `shot` lines render through it: the native
gallery's tiles, mpv and WebGL bodies stay blank there, which is why their pixel asserts
live in the GPU tier. One session by hand:

```text
uv run python tools/gui_qtdrive.py --project <folder> --out <dir> [--extra "<specto-app flags>"] \
    [--welcome [--welcome-recent]] [--platform offscreen|window] flow1.txt [flow2.txt ...]
```

The mouse tier is `tools/gui_session.py` and `gui_drive.py` as before. On Linux,
`tools/nested_display.py run -- ...` keeps it on a nested Xephyr display (needs
`python-xlib` in the pyautogui venv and `xclip`). Do not run it while someone is using the
desktop: it clicks.

## 9. The Claude login

The prompt bar runs Claude Code over the Agent SDK (`specto_app.prompt.session`). The
SDK's bundled CLI (`.venv/Lib/site-packages/claude_agent_sdk/_bundled/claude.exe`, 2.1.x)
reads the same credentials as a normal Claude Code install, `~/.claude/.credentials.json`
(`%USERPROFILE%\.claude` on Windows), so one login serves both:

```text
claude auth login       # any Claude Code install, or the bundled claude.exe above
claude auth status
```

The session scrubs an inherited `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` before the
CLI starts, because the SDK merges Specto's environment into the CLI's and a key there
silently outranks the login (logged as `[WARN] agent session: ignoring inherited ...`).
`SPECTO_AGENT_KEEP_API_KEY=1` keeps them. Which `claude` runs: `SPECTO_AGENT_CLI` names it
outright (a path, or `bundled` for the SDK's own); otherwise `SPECTO_CLAUDE_CLI`, when set
and pointing at an installed, executable build, is preferred over PATH, which is for a
machine whose Claude Code signs in through a cloud provider from a build that a GUI
launch's PATH does not find first; otherwise the first `claude` on PATH, else the bundled
one. The session uses project settings only
(`setting_sources=["project"]`), excludes every ancestor memory file and turns auto memory
off, so your own `~/.claude` configuration stays out of the window's agent.

## 10. The Claude Code plugin

`packages/specto-plugin` ships the `specto` skill, the `/specto` command and the
`specto-mcp` server as a local Claude Code marketplace. The CLI prints the plan and runs
it only with `--yes`:

```text
uv run specto plugin install --claude          # print the plan
uv run specto plugin install --claude --yes    # claude plugin marketplace add <marketplace>; claude plugin install specto@specto-app
uv run specto plugin install --fleet FLEET      # a nimitz fleet: nimitz harness add skill ... --fleet FLEET --yes
uv run specto plugin path                      # the marketplace, plugin and skill folders
```

The plugin's MCP server runs `specto-mcp`, and fleet members use the `specto` CLI, so both
must be on the PATH of whatever runs them (the plan warns when they are not).

## Common failures

- **The app fails in `Specto.Native` after a pull.** The plugin is a build artefact: any
  change under `packages/specto-app/native/` (`*.cpp`, `*.h`, the shaders, CMakeLists)
  needs `uv run python packages/specto-app/native/build.py` again; add `--clean` when
  cmake's cache is in doubt. A PySide6 bump needs the matching Qt SDK first (`build.py`
  refuses a version mismatch and says so).
- **`CLINotFoundError: Claude Code not found at <path>` though the file exists.** Before
  #199 the system prompt rode on the CLI's command line and, past the Windows limit of
  32767 characters, `CreateProcess` failed with WinError 206, which Python reports as
  `FileNotFoundError` and the SDK relabelled. Since #199 the prompt is written to
  `%TEMP%/specto/agent/system-prompt-<hash>.md` and the error names the real WinError:
  pull.
- **The usd view shows a test pattern, or `ovrtx` is missing.** A plain `uv run ...`
  re-synced the venv without the extras. Run with `--extra rtx --extra storm`, or
  `uv sync --extra rtx --extra storm` again.
- **`npm install` or `uv sync` asks a private registry for auth.** The checkout pins the
  public registries (above); if a tool outside the checkout does not, `uv tool run
  --no-config ...` ignores the user-level `uv.toml` (the Linux aqt line already does).
- **The video view reports an error.** `libmpv-2.dll` (or `libmpv.so.2`) was not beside
  the plugin at build time: fetch or install libmpv (step 3) and rebuild (step 4);
  `SPECTO_LIBMPV` (a library path) or `SPECTO_LIBMPV_DIR` point the item elsewhere at
  runtime.
- **`npm not found on PATH`** from the web build: install Node.js 20 or newer and reopen
  the shell.
- **A GUI flow passes on an empty project.** The worktree has no `testdata/`: build it or
  point `SPECTO_TESTDATA` at a built copy (below).

## Worktrees

Each ticket is built on its own branch in its own worktree:

```text
git fetch origin
git worktree add ../specto-wt/<name> -b v2/<name> origin/v2/integration
cd ../specto-wt/<name>
uv sync --extra rtx --extra storm
```

A worktree is a fresh checkout as far as the git-ignored artefacts go: it needs its own
`web/vendor/` (step 2, or copy the folder from a built checkout), its own native plugin
(step 4: copying the folder works until the C++ changes, so rebuild after copying) and
test data. For the test data, either build the worktree's own (step 6) or point it at the
main checkout's: `SPECTO_TESTDATA=<main checkout>/testdata` (the seeds honour
it). NEVER junction or symlink `testdata/` into a worktree: `git worktree remove --force`
recurses through the junction and empties the target. The Qt SDK and libmpv live outside the checkout and are shared. Remove a worktree
only when nothing runs in it (`git worktree remove <path>`).
