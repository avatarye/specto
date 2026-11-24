"""Web views on macOS composite in software while Qt Quick draws through OpenGL.

On macOS the shell's OpenGL RHI runs on Apple's OpenGL-over-Metal driver. With
Chromium's GPU compositing every web-view frame Qt Quick draws leaves one more
render pipeline state in that driver, never freed (about 60 per second, 70-145 MB a
minute of physical footprint while a 3D or map view animates). With
``--disable-gpu-compositing`` the frames reach Qt Quick as images and the driver keeps
its ~20 pipelines; WebGL still renders on the GPU.
"""

from __future__ import annotations

import os
import subprocess
import sys

from specto_app.qt_setup import NO_GPU_COMPOSITING, web_engine_flags


def test_macos_opengl_composites_in_software():
    out = web_engine_flags("", platform="darwin", backend="opengl")
    assert out.split() == [NO_GPU_COMPOSITING]


def test_flags_already_set_are_kept_and_not_doubled():
    out = web_engine_flags("--foo --bar=1", platform="darwin", backend="opengl")
    assert out.split() == ["--foo", "--bar=1", NO_GPU_COMPOSITING]
    again = web_engine_flags(out, platform="darwin", backend="opengl")
    assert again == out


def test_other_platforms_and_backends_keep_gpu_compositing():
    assert web_engine_flags("", platform="win32", backend="opengl") == ""
    assert web_engine_flags("", platform="linux", backend="opengl") == ""
    assert web_engine_flags("--foo", platform="darwin", backend="metal") == "--foo"


def test_opt_out_keeps_gpu_compositing():
    out = web_engine_flags("--foo", platform="darwin", backend="opengl", gpu_compositing=True)
    assert out == "--foo"


def test_the_flag_reaches_the_environment_chromium_reads():
    """In a fresh process: ``prepare()``'s step writes the flag into the environment Chromium
    reads at ``QtWebEngineQuick.initialize()`` (macOS only; elsewhere nothing)."""
    code = (
        "import os\n"
        "os.environ.pop('QTWEBENGINE_CHROMIUM_FLAGS', None)\n"
        "os.environ.pop('SPECTO_WEB_GPU_COMPOSITING', None)\n"
        "from specto_app import qt_setup\n"
        "qt_setup.apply_web_engine_flags()\n"
        "print(os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', ''))\n"
    )
    env = {**os.environ, "QSG_RHI_BACKEND": "opengl"}
    run = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60
    )
    assert run.returncode == 0, run.stderr
    flags = run.stdout.strip().split()
    if sys.platform == "darwin":
        assert NO_GPU_COMPOSITING in flags
    else:
        assert NO_GPU_COMPOSITING not in flags
