"""libmpv runs none of its built-in Lua scripts (#172). libmpv embeds LuaJIT, which on
Windows unwinds every Lua error, even one a script catches, as an SEH exception
(0xE24C4A02). faulthandler (on in every pytest run) logs each one from a vectored
exception handler on libmpv's script thread and walks the Python thread states without
the GIL while they come and go: the dumps flooded test logs and, racing thread exits,
segfaulted the run. The video view and the poster need no Lua, so neither loads it.

Each case opens libmpv repeatedly in a child process (``mpv_lua_child.py``) and counts the
exceptions faulthandler logged; before the fix about half the openings logged one."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from specto_app import thumbs
from specto_app.views import gallery_native

CHILD = Path(__file__).with_name("mpv_lua_child.py")
RUNS = 12


def lua_errors(*args: str) -> int:
    r = subprocess.run(
        [sys.executable, str(CHILD), *args], capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("LUA_ERRORS "))
    return int(line.split()[1])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg to make the clip")
def test_a_video_poster_runs_no_lua(tmp_path: Path):
    if thumbs.libmpv() is None:
        pytest.skip("no libmpv (build the native plugin)")
    clip = tmp_path / "red.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=red:s=32x32:d=1:r=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    assert lua_errors("poster", str(clip), str(RUNS)) == 0


@pytest.mark.skipif(not gallery_native.is_built(), reason=gallery_native.BUILD_HINT)
def test_a_video_view_item_runs_no_lua():
    if thumbs.libmpv() is None:
        pytest.skip("no libmpv (build the native plugin)")
    assert lua_errors("item", str(RUNS)) == 0
