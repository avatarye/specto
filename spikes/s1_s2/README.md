# Spike S1 (shell-deciding half) + S2

Qt Quick on the OpenGL RHI backend, from PySide6, hosting a QtWebEngine view
beside a C++ `TextureItem` that renders raw GL on the render thread and hands the
texture to the scene graph with `QSGOpenGLTexture::fromNative`. Report and
verdict: `docs/spikes/s1-s2.md`.

## Layout

| Path | What |
|---|---|
| `native/` | C++ QML module `Specto.Spike` (`TextureItem`), CMake |
| `build.py` | builds `native/` against the Qt SDK matching the PySide6 wheel, installs to `qml/` |
| `app.py` | PySide6 host window, metrics, JSON result |
| `main.qml` | the scene: WebEngineView card + TextureItem card |
| `scheme.py` | `specto://` URL scheme handler (range semantics documented inline) |
| `web/` | WebGL2 cube field + Monaco (vendored into `web/vendor/`, git-ignored) |
| `run_matrix.py` | runs the measurement matrix, writes `results/*.json` and `results/summary.md` |
| `tests/` | pytest: range parsing, module metadata, offscreen plugin load from PySide6 |

## Reproduce

```
uv sync
uv tool run --from git+https://github.com/miurahr/aqtinstall aqt install-qt windows desktop 6.12.0 win64_msvc2022_64 -O ~/specto-build/Qt --archives qtbase qtdeclarative qtshadertools
cd spikes/s1_s2/web && npm pack monaco-editor@0.52.2 && mkdir vendor && tar -xzf monaco-editor-0.52.2.tgz -C vendor --strip-components=1 package/min/vs && cd ../../..
.venv/Scripts/python spikes/s1_s2/build.py
.venv/Scripts/python -m pytest spikes/s1_s2/tests
.venv/Scripts/python spikes/s1_s2/app.py --seconds 10 --mode 0 --web both --screen 1
.venv/Scripts/python spikes/s1_s2/run_matrix.py
```

`app.py --help` lists the modes (0 native mesh, 1 render-thread upload, 2 Python
push), `--backend d3d11` for the WebEngine baseline, `--screen N` to pick a
monitor, `--stay` to keep the window open.
