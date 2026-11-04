# Specto

One desktop viewer for the data agents produce: image sets, video, USD and other
3D, JSON, GeoJSON, source code and markdown, with drag-and-drop into any view.

Status: v2 on the mainline, released as 0.2.0 and under active development; built and
tested on Windows 11, with Ubuntu 24.04 and macOS routes that are run less often. The
design background is in [DESIGN.md](DESIGN.md); decisions in `docs/adr/`.

Issue numbers in the code and docs (`#NNN`) are history and do not resolve here. Current
work is tracked as `SPEC-n` tasks; see [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md).
How work is done: [docs/agents/workflow.md](docs/agents/workflow.md).

Licence: [MIT](LICENSE). Third-party components and what they are licensed under:
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Setup guides: [docs/setup/developer.md](docs/setup/developer.md) (build, test and run a
checkout on Windows 11 or Ubuntu 24.04) and [docs/setup/user.md](docs/setup/user.md)
(the first launch, the agent and the basics). There is no installer yet (SPEC-24).
Releases and the versioning rule: [CHANGELOG.md](CHANGELOG.md); `specto --version`
prints the installed one.

## Development

uv workspace monorepo, Python 3.12. Packages live under `packages/` and follow
the HLD package table (`docs/review/hld.md`, section 2):

| Package | Role |
|---|---|
| `specto-core` | Canvas document, addresses, command registry, transactions and undo, channels, events. No Qt. |
| `specto-intake` | Sources, screener, keys, grouping, sort. |
| `specto-readers` | Built-in locators and decoders. |
| `specto-sdk` | Python client over JSON-RPC to a running app. |
| `specto-cli`, `specto-mcp` | Generated surfaces over the SDK. |
| `specto-app` | PySide6 shell, view hosts, endpoint, worker pool. |

Setup and checks:

```text
uv sync                       # creates .venv with every workspace member (editable)
uv run pytest                 # all package tests
uv run pytest packages/specto-core
uv run ruff check . && uv run ruff format --check .
uv run python tools/gen_api.py   # regenerate specto_sdk/{api-info.json,_api.py} (--check before merging)
```

The native Qt Quick module (`Specto.Native`: the gallery, the frame item, rounded clips
and the libmpv video item) is built against a Qt SDK of the PySide6 wheel's exact
version; see `packages/specto-app/native/build.py` for the full recipe. On Linux (Ubuntu 24.04):

```text
sudo apt install build-essential cmake libgl-dev libmpv-dev ffmpeg
uv tool run --no-config --from git+https://github.com/miurahr/aqtinstall aqt \
    install-qt linux desktop 6.12.0 linux_gcc_64 -O ~/specto-build/Qt \\
    --archives qtbase qtdeclarative icu
uv run python tools/fetch_libmpv.py              # only without root: libmpv from apt's debs
uv run python packages/specto-app/native/build.py
```

On macOS (Apple Silicon, tested on macOS 26; Xcode Command Line Tools, Homebrew):

```text
brew install cmake mpv
uv python install 3.12                      # uv-managed CPython: a conda or Homebrew
uv sync --python-preference only-managed    # 3.12 on PATH would be picked otherwise
uv tool run --no-config --from aqtinstall aqt \
    install-qt mac desktop 6.12.0 clang_64 -O ~/specto-build/Qt \\
    --archives qtbase qtdeclarative
uv run python packages/specto-app/native/build.py   # libmpv from Homebrew's prefix
uv run python packages/specto-web/build.py          # vendored web assets (npm)
```

Qt Quick runs on OpenGL 4.1 core there (deprecated by Apple, still shipped). The GUI tools
(`tools/gui_drive.py`) need Screen Recording and Accessibility for the terminal.

### The usd view's renderer

The default (`renderer: auto`) depends on the platform. On Windows and Linux it is RTX
where ovrtx is installed (`uv sync --extra rtx`, NVIDIA ovrtx, an RTX GPU) and Hydra Storm
otherwise; on macOS, which ovrtx does not support, it is always Storm. An explicit
`renderer: rtx` or `renderer: storm` on the view wins everywhere, and `describe view:usd`
reports `renderer_auto`, what `auto` runs on this machine. Storm needs the OpenUSD imaging
build (`~/specto-build/openusd`, `SPECTO_BUILD_ROOT` moving the root, or
`SPECTO_OPENUSD`): build it once with `python3 tools/parked/build_openusd.py` (pip's
`usd-core` has no `UsdImagingGL`); its PyOpenGL and numpy are plain specto-app
dependencies, so a plain `uv run specto-app` keeps Storm. With neither renderer the view
shows the stub source's test pattern. A plain `uv run` re-syncs the venv without extras,
so keep `--extra rtx` on every call where RTX is wanted.

On Linux, a shell that has sourced another stack's environment (ROS, for one) leaks
`PYTHONPATH`, `LD_LIBRARY_PATH` and `CMAKE_PREFIX_PATH` into the venv and the native
build; unset them first.

Clients: `specto` (CLI), `specto-mcp` (MCP server) and `specto_sdk.Client` all talk
JSON-RPC to the app's `QLocalServer` endpoint found through `.specto/endpoint.json`
(`specto init <dir>` creates a project; `specto status` shows what answers).

Contracts between packages are in `docs/contracts.md`. Test data is git-ignored
under `testdata/` (see `testdata/README.md`, built by `tools/make_testdata.py`).
