# Third-party notices

Specto is licensed under the MIT licence (`LICENSE`). This file lists the third-party
components the repository commits, fetches at build time, or depends on at runtime, with
their licences and how Specto uses them. Licence texts and copyright notices travel with
the components themselves, at the locations named below.

Four groups matter to a reader of the public repository:

- **Committed** files from other projects: only the file tree's icon subset (MIT), whose
  licence sits next to the files. Everything else in the tree is Specto's own work.
- **Fetched at build time**, git-ignored and never committed: the web views' JavaScript
  libraries and the 3D view's HDRI environments (`packages/specto-web/build.py`), the Qt
  SDK the native module compiles against, and libmpv (`tools/fetch_libmpv.py`).
- **Fetched at run time**, on first use, into the user's data folder and never committed:
  the usd view's environment assets, NVIDIA's dynamic skies (under NVIDIA's terms, which
  forbid committing them) and the 3d view's Poly Haven HDRIs on an installed app
  (section 3).
- **Runtime dependencies** installed by `uv sync` or `npm install` from public registries.

Nothing is vendored from a proprietary source. Where a dependency is copyleft or
proprietary, the entry says so and how Specto keeps to the licence's terms.

## 1. Committed third-party files

| Component | Licence | Where | Use |
|---|---|---|---|
| Material Icon Theme 5.39.0 (`material-icon-theme`, Material Extensions) | MIT | `packages/specto-app/src/specto_app/qml/Specto/FileTree/icons/` with its `LICENSE` and `README.md`; `map.json` is the theme's association table narrowed to the subset | The file tree's file and folder icons (177 SVGs). Vendored by `tools/vendor_file_icons.py`, pinned; the licence file is kept verbatim. |

Everything else committed is Specto's own: the shell icons (`qml/Specto/Shell/icons`, a
line set drawn for Specto), the logo and app icon (`specto_app/assets`, designed for
Specto), the web views' glyphs (`packages/specto-web/web/icons.js`), the shaders
(`native/shaders`, `qml/Specto/*/shaders`), the native Qt Quick module sources
(`packages/specto-app/native`), the test fixture `packages/specto-web/tests/fixtures/tiny_v4.spz`
(three splats written by Specto with the `@adobe/spz` library) and the mockup renders under
`docs/review/img` (procedural pages, no external assets).

## 2. Fetched at build time (git-ignored)

### 2.1 Web assets, `packages/specto-web/build.py`

Pinned in `packages/specto-web/package.json`, installed from the public npm registry
(`.npmrc`), copied into `web/vendor/<name>/` with each package's licence file beside it as
`web/vendor/<name>.LICENSE`. Nothing loads from a CDN at runtime.

| Package | Version | Licence | Use |
|---|---|---|---|
| `monaco-editor` | 0.52.2 | MIT (Microsoft; its `ThirdPartyNotices.txt` lists the components it bundles, among them the Codicon font) | The JSON view and the changeset diff. |
| `markdown-it` | 15.0.2 | MIT | The markdown view's renderer. |
| `github-markdown-css` | 5.9.0 | MIT | The markdown view's stylesheet. |
| `mermaid` | 12.1.0 | MIT (bundles its own dependencies, d3 and others, under their permissive licences) | Diagrams in the markdown view. |
| `katex` | 0.19.0 | MIT (Khan Academy); the bundled fonts derive from the AMS and Computer Modern fonts (SIL OFL 1.1) | Maths in the markdown view, with `dist/fonts`. |
| `maplibre-gl` | 5.24.0 | BSD-3-Clause (MapLibre contributors; the notice names the Mapbox code it forked) | The map view. Basemap tiles are fetched at runtime only when the view's `net:tiles` capability is on, from OpenStreetMap (ODbL data, tile usage policy applies) or Esri World Imagery, each shown with the credit its terms require (`specto_app/web/basemaps.py`). |
| `babylonjs`, `babylonjs-loaders` | 9.31.0 | Apache-2.0 (its `NOTICE.md` is copied next to the files) | The 3D view's engine and its glTF and SPLAT loaders. Draco, meshopt and KTX2 transcoders are not vendored. |
| `fflate` | 0.8.3 | MIT | Decompression for the SPLAT loader's SOG files. |
| `@adobe/spz` | 0.2.2 | ISC | The SPZ (Gaussian splat) decoder handed to Babylon's loader. |
| `typescript` (dev only) | 5.9.3 | Apache-2.0 | Type-checks `web/*.js` against `types/`; nothing of it ships. |

### 2.2 HDRI environments, `packages/specto-web/web/3d/environments.json`

Five 1k `.hdr` files from [Poly Haven](https://polyhaven.com), CC0 1.0 (no attribution
required; the authors are credited in the manifest: Sergej Majboroda for `studio_small_09`,
Greg Zaal for `kloofendal_overcast`, `kloofendal_48d_partly_cloudy`, `lebombo` and
`potsdamer_platz`). Downloaded by `build.py`, verified by sha256, prefiltered into
`web/vendor/hdri/*.env` for the 3D view's lighting; nothing is fetched at runtime.

### 2.3 The Qt SDK and the native module

`packages/specto-app/native/build.py` compiles the Qt Quick module `Specto.Native` (the
gallery, the frame item, rounded clips, the libmpv video item) against a Qt 6.12.0 SDK
installed with [aqtinstall](https://github.com/miurahr/aqtinstall) (MIT). Qt is
**LGPL-3.0** (also offered under GPL and commercial terms). The module is a shared
library loaded by the Qt the PySide6 wheel ships, so Qt stays a separately installed,
dynamically linked, replaceable library, which is what the LGPL asks of an application
under another licence. No Qt source is modified or committed.

### 2.4 libmpv, `tools/fetch_libmpv.py`

The video view's `MpvItem` loads **libmpv** at runtime (`dlopen` / `LoadLibrary` of
`libmpv-2.dll` or `libmpv.so.2`, never linked at build time) and compiles against its
public C headers (`client.h`, `render.h`, `render_gl.h`, `stream_cb.h`, LGPL-2.1+ as mpv
publishes them). The library itself comes from outside the repository:

- Windows: the shinchiro `mpv-winbuild-cmake` release archive (pinned by tag), a build of
  mpv with FFmpeg and other codecs whose combined licence is **GPL-3.0** (mpv's
  default build is GPL-2.0+; its LGPL mode requires opting out of GPL components).
- Linux: the distribution's `libmpv2` package (GPL-2.0+ as Debian and Ubuntu build it),
  or without root the same debs unpacked next to the plugin.
- macOS: Homebrew's `mpv`.

Specto does not redistribute libmpv. A developer fetches it; the build copies the library
next to the plugin, in a git-ignored folder. A binary distribution of Specto that bundled a
GPL libmpv would have to take the GPL's terms for that distribution into account; that is
flagged here for whoever packages an installer.

## 3. Fetched at run time (into the user's data folder, never committed)

The usd view's environment light (SPEC-31, ADR 0024) fetches its assets the first time the
user picks them, into the user's Specto data folder (`specto_core.instances.user_dir`:
`%LOCALAPPDATA%\specto\specto` on Windows, `~/.local/share/specto` on Linux), outside any
checkout. Each file is pinned by sha256 and size and checked before it is kept; nothing is
fetched again once cached. These are the only app assets fetched at run time.

### 3.1 NVIDIA's dynamic skies, `<user dir>/skies`

The **Sky** environment is one of the seven dynamic skies of the Omniverse sample assets
on NVIDIA's public content bucket
(`https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Skies/`:
`Dynamic/*.usd` with the MDL and textures under `Sky_Elements/materials/`, two small
helpers and the preview scene with its floor, `Sky_Elements/PreviewScene.usda`). These
are NVIDIA "Content" under the **NVIDIA Omniverse License Agreement**
(https://docs.omniverse.nvidia.com/extensions/latest/common/NVIDIA_Omniverse_License_Agreement_2022-2023.html),
a "non-exclusive, non-transferable, non-sublicensable" licence to "Distribute Content
(unmodified or as modified by you) as incorporated into your products or services for
the purpose of enhancing your work, subject to the distribution requirements below, but
not deploy or distribute the Content on a stand-alone basis", with no copies "in any
publicly accessible software repositories" and no use that makes them "redistributable
at no charge". Files in this MIT repository would break all three, so **nothing of them
is committed or built**: each user's own Specto fetches them the first time the Sky
choice is used (`specto_app.usd.skies`), writes the notice `NVIDIA-SKY-NOTICE.txt` beside
them and packs each sky into its own `.usdz` for that installation's use; Specto only
reads them from there and redistributes nothing.

### 3.2 The HDRIs, `<user dir>/hdri`

The **HDRI** environment is one of the 3d view's Poly Haven HDRIs (section 2.2, CC0 1.0,
no attribution required), fetched by the same code from Poly Haven's URLs in
`packages/specto-web/web/3d/environments.json` when the app has no web build beside it
(an installed app); a source checkout uses the build's download instead. A notice,
`POLYHAVEN-CC0.txt`, sits beside them.

## 4. Runtime dependencies (Python, `uv sync`)

Installed from public PyPI only (the workspace pins the index). Versions are in `uv.lock`.

| Package | Licence | Use |
|---|---|---|
| PySide6, PySide6-Essentials, PySide6-Addons, shiboken6 (Qt 6.12.0) | **LGPL-3.0** (or GPL-2.0 / GPL-3.0 / commercial) | The app's Qt Quick shell, QML, SVG, local sockets. Used as the unmodified wheels, dynamically loaded. |
| PySide6-WebEngine, PySide6-Pdf | LGPL-3.0 as above; Qt WebEngine embeds Chromium (BSD-3-Clause and other permissive licences) and Qt PDF embeds PDFium (BSD-3-Clause) | The web views (JSON, markdown, map, 3D, html, scratch) and the pdf view. |
| claude-agent-sdk | MIT, **but it bundles the Claude Code CLI** (`claude.exe` / `claude` under `_bundled/`), a proprietary Anthropic binary under Anthropic's commercial terms and the user's own Claude login | The canvas agent: a Claude Code session behind the prompt bar. Specto spawns the bundled CLI (or `SPECTO_AGENT_CLI`); it never redistributes it. |
| mcp | MIT | The `specto-mcp` server for Claude Code and other MCP clients. |
| pydantic, pydantic-core | MIT | The canvas document and every validated model. |
| numpy | BSD-3-Clause (with 0BSD, MIT, Zlib and CC0 parts) | Image buffers, the usd source's frames. |
| scipy | BSD-3-Clause; its wheels bundle Qhull (Qhull licence), OpenBLAS and LAPACK (BSD-3-Clause variants) and the GCC runtime (GPL-3.0 with the GCC Runtime Library Exception) | Convex hulls (Qhull) for the usd view's collision view. |
| pillow | MIT-CMU | Image decoding and thumbnails. |
| pyarrow | Apache-2.0 (Apache Arrow; its wheels bundle Arrow C++ and its permissive dependencies, listed in the wheel's `LICENSE.txt` and `NOTICE.txt`; OpenSSL as a dynamically linked library under its OpenSSL / SSLeay licence; on Windows the MSVC C++ runtime `msvcp140` under Microsoft's redistributable terms) | Reads tables (CSV, Parquet) for joint tracks, the URDF view's `joints` port. About 90 MB installed. |
| xacro | BSD-3-Clause (Robert Haschke, Bielefeld University) | Expands `.xacro` robot descriptions for the URDF view, for approved content only and in a process of its own (its `${...}` evaluates Python). |
| PyYAML | MIT | Pulled in by xacro (its `load_yaml`, read with `safe_load`). |
| PyOpenGL | BSD-3-Clause | The usd view's Storm source draws offscreen with it. |
| platformdirs | MIT | User and cache directories. |
| watchdog | Apache-2.0 | Change notifications for watched folders (ReadDirectoryChangesW, inotify, FSEvents): `specto_core.fswatch`, ADR 0027. Used as the unmodified wheel. |
| click | BSD-3-Clause | The `specto` CLI. |
| qasync | BSD-2-Clause | asyncio on the Qt event loop. |
| Transitive: anyio, attrs, httpx, httpcore, h11, idna, sniffio, starlette, uvicorn, sse-starlette, python-multipart, jsonschema (+ referencing, rpds-py, jsonschema-specifications), PyJWT, cryptography, cffi, pycparser, typing-extensions, typing-inspection, annotated-types, opentelemetry-api, packaging, colorama, truststore, pywin32 (Windows) | MIT, BSD, Apache-2.0, PSF, MIT-0 | Pulled in by the SDK, mcp, pydantic and click; none copyleft. |
| Dev only: pytest, pytest-cov, coverage, ruff, pluggy, iniconfig, Pygments | MIT, Apache-2.0, BSD-2-Clause | Tests and linting; nothing ships. |

### 4.1 Optional renderers for the usd view (not installed by default)

| Package | Licence | Use |
|---|---|---|
| `ovrtx` 0.6.0, `ovstage` 0.3.0 (`uv sync --extra rtx`) | **NVIDIA proprietary.** The PyPI package is a stub that downloads the binary from `pypi.nvidia.com` at install under NVIDIA's software licence agreement; it is not redistributable by Specto | The RTX renderer of the usd view (`specto_app/usd/`). Specto only imports the installed module through its public API; the repository holds no NVIDIA code or data. Without the extra the view falls back to Storm or a test pattern. |
| `usd-core` 26.11 | Apache-2.0 (Pixar's OpenUSD, with its modified-Apache notice) | Stage bounds for the RTX source. |
| OpenUSD imaging build (`tools/parked/build_openusd.py`, built locally, found through `SPECTO_OPENUSD`) | Apache-2.0 (OpenUSD and its build dependencies: TBB Apache-2.0, OpenSubdiv Apache-2.0, OpenImageIO BSD-3-Clause, among others) | The Hydra Storm renderer. Built by the developer from source; nothing of it is in the repository. |

## 5. Tooling that fetches but ships nothing

- `aqtinstall` (MIT): installs the Qt SDK for the native build.
- `py7zr` (LGPL-2.1+) in a throwaway venv, or 7-Zip (LGPL-2.1+ with unRAR restriction) on
  PATH: extracts the libmpv archive on Windows.
- `patchelf` (GPL-3.0) through `uvx`: sets `RUNPATH` on a root-less Linux libmpv fetch.
  A tool run, not a dependency.
- `npm` and Node.js 20+: the web build.
- `ffmpeg` / `ffprobe` (LGPL-2.1+ or GPL depending on the build) on PATH:
  `tools/make_testdata.py` renders the git-ignored test dataset with them.

## 6. Services the app may contact at runtime

Only when the user turns the capability on for a view: OpenStreetMap and Esri basemap
tiles (`net:tiles`, section 2.1), the html view's live embeds (`net:web`), and the agent's
connection to Anthropic through the user's own Claude Code login. On the user's first pick
of the usd view's Sky or HDRI environment: NVIDIA's public content bucket and Poly Haven,
once per asset (section 3). Nothing else leaves the machine.
