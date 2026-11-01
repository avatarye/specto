"""#216: the video item's core has sound. Its options select the platform's audio output
(``ao`` auto, the empty default, never ``null``) and the first audio track (``aid``
auto, never ``no``), with ``mute`` on and ``volume`` 100 to start; the item's ``muted``
and ``volume`` properties write through to mpv and read back from it. Frame-accurate
stepping (``hr-seek``) and ``video-sync=audio`` stay as they were. The poster path
(``thumbs.video_poster``) keeps ``ao=null`` / ``audio=no``: it never opens a device.

The item runs offscreen in a child process (``mpv_audio_child.py``) with no file, so no
audio device is opened: the check is on the properties, not on sound."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from specto_app import thumbs
from specto_app.views import gallery_native

CHILD = Path(__file__).with_name("mpv_audio_child.py")


@pytest.mark.skipif(not gallery_native.is_built(), reason=gallery_native.BUILD_HINT)
def test_the_video_item_opens_sound_muted_and_its_properties_write_through():
    if thumbs.libmpv() is None:
        pytest.skip("no libmpv (build the native plugin)")
    r = subprocess.run([sys.executable, str(CHILD)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT "))
    out = json.loads(line[len("RESULT ") :])
    before, after = out["before"], out["after"]
    assert before["ready"] and before["error"] == ""
    assert before["ao"] in ("", None, []) and before["aid"] == "auto"  # the device, a track
    assert before["muted"] is True and before["mute"] is True and before["volume"] == 100.0
    assert before["hasAudio"] is False  # no file open
    assert before["video_sync"] == "audio" and before["hr_seek"] in ("yes", True)
    assert after["muted"] is False and after["mute"] is False
    assert after["volume"] == 40.0 and after["mpv_volume"] == 40.0
    assert after["error"] == ""


def test_the_poster_never_opens_an_audio_device():
    src = inspect.getsource(thumbs.video_poster)
    assert '("ao", "null")' in src and '("audio", "no")' in src
