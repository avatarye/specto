# Spike S1, Storm half: the OpenUSD imaging build

Date: 2026-10-12. Branch `m2/usd`, script `tools/build_openusd.py`.
Scope per HLD section 10 (`shell` decision, Storm half) and 5.5: get a Storm-capable
OpenUSD (imaging, usdview, Python bindings) built against Specto's runtime
(Python 3.12, PySide6 6.12.0) so the in-process `StormItem` vs frame-source
question can be measured. Storm is not integrated into Specto here.

## Environment and pins

| Item | Value |
|---|---|
| OpenUSD | `v26.08` (latest release tag; `v26.11-alpha` is a prerelease), shallow clone at `<build root>\openusd-src` |
| Build venv | `<build root>\openusd-venv`, uv, Python 3.12.10 (a system CPython), PySide6 6.12.0 (the uv.lock wheel), shiboken6 6.12.0, PyOpenGL 3.1.10, Jinja2 3.1.6 |
| Compiler | MSVC 2022 Build Tools 17.14 (cl 14.44) via `vcvars64.bat`, generator `Visual Studio 17 2022 -A x64` (build_usd.py's default on Windows) |
| CMake | 4.1.2 |
| Install prefix | `<build root>\openusd` |
| Logs | `<build root>\openusd-build.log` (build_usd.py stdout), `<build>\log.txt` (its verbose log), `openusd-build-driver.log` (script stdout) |
| Machine | 32 logical cores, jobs = 28 (cores - 4) |

## What ran

`tools/build_openusd.py` clones the tag, makes the venv with uv, installs the three
Python packages, writes `<build root>\openusd-build\_build.bat` (calls
`vcvars64.bat`, puts the venv first on PATH) and runs:

```
build_usd.py --build-variant release --usd-imaging --usdview --python
  --build-python-info <venv>\Scripts\python.exe C:\Python312\include C:\Python312\libs\python312.lib 3.12
  --no-examples --no-tutorials --no-docs --no-tests --no-vulkan
  --jobs 28 --build <build root>\openusd-build --src <build root>\openusd-deps
  <build root>\openusd
```

MaterialX, OpenSubdiv, TBB and zlib are the defaults build_usd.py pulls for
`--usd-imaging`; OpenVDB, Ptex, OpenImageIO, OpenColorIO, Embree, Alembic, Draco
are off (their defaults).

### Two things that bit on the first attempt

1. `--imaging` and `--usd-imaging` are mutually exclusive in `build_usd.py`
   (argparse group); `--usd-imaging` already implies imaging.
2. Launching the driver from Git Bash breaks `vcvars64.bat`: bash cannot export
   the `ProgramFiles(x86)` variable (parentheses), so `VsDevCmd` cannot find
   `vswhere.exe`. Run the script from PowerShell or cmd.

Also pre-empted: `build_usd.py` derives the Python include dir from
`sysconfig.get_path("include")`, which inside a venv on Windows is `<venv>\Include`
(no `Python.h`). The script passes `--build-python-info` with the base install's
headers and `python312.lib` instead.

## Timeline (wall clock, 2026-10-12, jobs = 28)

| Time | Step |
|---|---|
| 22:50 | start; TBB 2020.3 prebuilt zip, MaterialX 1.39.5, OpenSubdiv 3.6.1 downloaded |
| 22:50-22:51 | MaterialX and OpenSubdiv configured and built |
| 22:51 | USD configure (`Visual Studio 17 2022 -A x64`, Build Tools instance) |
| 23:05 | `cmake --build . --config Release --target install -j 28` exit 0 |
| | **Total 14.7 min** (`build_usd.py` exit 0) |

## Result: PASS

`build_usd.py` printed "Success!"; nothing is partial:

- `<build root>\openusd\lib`: 154 `usd_*.lib` (188 files total); `bin`:
  73 files including `usdview.cmd`, `usdcat`, `usdrecord`, `tbb.dll`, `MaterialX*.dll`.
- Python package at `<build root>\openusd\Lib\site-packages\pxr`
  (26.x installs there, not `lib/python`).
- Install size 309 MB. The `vswhere.exe is not recognized` line in the log is
  printed by `VsDevCmd` telemetry inside `vcvars64.bat` and is harmless: cl and
  the generator were found (`CMAKE_GENERATOR_INSTANCE` is the Build Tools path).

To use it:

```
set PATH=<build root>\openusd\bin;<build root>\openusd\lib;%PATH%
set PYTHONPATH=<build root>\openusd\Lib\site-packages
<build root>\openusd-venv\Scripts\python ...
```

Rerunning `tools/build_openusd.py` (from PowerShell or cmd) is incremental:
build_usd.py skips finished dependencies and the USD tree rebuilds only what
changed. `--clean` wipes build/deps/install for a from-scratch run.

## Smoke test

From the build venv with PATH/PYTHONPATH as above:

```
python -c "from pxr import Usd, UsdImagingGL; print(Usd.GetVersion()); print(UsdImagingGL.Engine.GetRendererPlugins())"
(0, 26, 8)
['HdStormRendererPlugin']
```

Import takes 0.36 s.

**usdview GUI, 10 s** (`tools/usdview_smoke.py`, run under the desktop lock):

```
uv run --directory <checkout> python tools/desktop_lock.py run m2-usd -- ^
  <build root>/openusd-venv/Scripts/python tools/usdview_smoke.py out/verify/m2/usd-usdview.png
```

The script writes `<build root>\openusd-smoke\cube.usda` (a `Cube` of size 2
with `displayColor` (0.2, 0.6, 0.9)), launches `usdview` with the build venv's
Python (PySide6 6.12.0), clicks the viewport, presses F and Alt-drags to tumble,
screenshots the window with `QScreen.grabWindow` at 10 s, then terminates usdview.
Result: window up after 1.0 s; viewport HUD `Hydra: Storm`, `Hgi: OpenGL`;
first frame 1635 ms (shader compile), then `Render: 3.04 ms (328 FPS)` with the
cube selected and framed. (The screenshot is not kept in the repository.) The
only noise in `usdview.log` is PyOpenGL's "No numpy module present" (numpy is
not in the build venv; usdview does not need it for this).

**usdrecord, headless Storm** (no window, 0.98 s):

```
usdrecord --renderer Storm --imageWidth 640 cube.usda cube-usdrecord.png
```

Writes a 640x480 PNG of the cube face-on (the default camera looks down -Z, so
one lit face fills the square) at `<build root>\openusd-smoke\`. This is
the path a Storm frame source would use if `StormItem` in-process fails.

## What the StormItem decision needs next

The build removes the "full OpenUSD build" risk (R19). The in-process `StormItem`
vs frame-source decision (HLD 5.5, section 10) now needs the Storm counterpart of
`spikes/s1_s2`:

1. A C++ `StormItem` in the spike's native module that owns a
   `UsdImagingGLEngine`, renders into its own FBO on the Qt Quick render thread
   under the OpenGL RHI (same `QSGOpenGLTexture::fromNative` path as `TextureItem`),
   and requests frames from the GUI thread (the design rule from S1).
   `pxrConfig.cmake` at `<build root>\openusd` gives CMake the targets.
2. The two things only Storm can answer: whether Hydra's GL context use coexists
   with Qt's shared context on the render thread (`UsdImagingGLEngine` draws into
   the current context; Qt's render thread owns one), and whether a Storm
   `Render()` of a mid-size stage stays inside the 16 ms frame beside WebEngine
   without touching the GUI thread (the Python side only sets stage and camera).
3. Re-run the S1 matrix with the Storm item in place of the torus: 60 Hz at
   1080p, GUI thread > 80 % idle, WebEngine unchanged. Pass keeps `StormItem`
   in-process; a miss on either number demotes Storm to a frame source
   delivering a shared-memory frame (copy cost already measured: 0.46 ms CPU +
   0.59 ms GPU per 1080p RGBA).
4. Process-stability check: a Storm crash takes the app down (HLD 4.4 accepts
   this for in-process items); exercise a bad stage and a renderer-plugin reload
   before committing to it.
