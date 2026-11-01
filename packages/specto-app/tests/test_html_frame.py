"""The html view shows the user's page (#71: "html view does not load html").

Diagnosis: ``WebView.qml`` rejected every navigation whose URL was not on the view's
own ``specto://<view>.page/`` origin, and Chromium reports a frame's navigation through
the same ``navigationRequested`` as the main frame's. The html view frames the user's
page on ``specto://<view>.items/``, so the frame's load was refused before the scheme
handler ever saw it, and nothing said so: the host had sent the URL, the page had set the
frame's ``src``, health stayed ``ok``. The pytest process runs with web views off, so
the check runs the shell in a child process with QtWebEngine (html_frame_child.py):
the scheme handler must serve the document and the assets it references relative to
itself, and a refusal must leave a [WARN] in the log.

The framed page cannot reach the WebChannel bridge (the open question of #68): the
transport object ``qt`` is injected into the main frame only, the frame is a different
origin from the view's page (its parent's window is sealed), and no bridge call or page
event arrives from it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWebEngineQuick")

CHILD = Path(__file__).with_name("html_frame_child.py")


@pytest.fixture(scope="module")
def framed() -> tuple[list[dict], str]:
    res = subprocess.run(
        [sys.executable, str(CHILD), "deck.html", "reload", "probe.html"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    lines = [json.loads(ln) for ln in res.stdout.splitlines() if ln.startswith("{")]
    assert lines and "error" not in lines[0], f"child failed\n{res.stdout}\n{res.stderr}"
    return lines, res.stderr


def test_the_html_view_frames_the_page_with_its_relative_assets(framed):
    rows, err = framed
    deck = rows[0]
    assert deck["action"] == "created" and deck["ready"] and deck["health"]["status"] == "ok"
    # the preview is on the tree's picks (#122): the item is keyed by its path
    assert deck["url"] == f"specto://{deck['view']}.items/l2/deck%2Fdeck/html/deck.html"
    assert deck["frame"] is not None  # the page put the URL in its frame
    served = {e["rel"]: e["status"] for e in deck["served"]}
    assert served == {"deck.html": "ok", "style.css": "ok", "deck.js": "ok", "img/a.png": "ok"}
    assert "navigation refused" not in err


def test_the_reload_button_loads_the_page_again(framed):
    # #99: a fragment-only change of the frame's src did not reload the page
    rows, _err = framed
    reload = rows[1]
    assert reload["action"] == "reload" and reload["view"] == rows[0]["view"]
    assert "deck.html" in {e["rel"] for e in reload["served"]}


def test_a_second_file_shows_in_the_same_view_and_its_frame(framed):
    rows, _err = framed
    probe = rows[2]
    # the preview follows the tree's picks (#122): the second file is a pick
    assert probe["view"] == rows[0]["view"] and probe["action"] == "selected"
    assert {e["rel"] for e in probe["served"]} == {"probe.html", "probe.js"}


def test_the_framed_page_cannot_reach_the_webchannel_bridge(framed):
    rows, err = framed
    probe = rows[2]
    lines = [ln for ln in err.splitlines() if "[PROBE" in ln]
    assert len(lines) == 2, err  # at load and 400 ms later; no "bridge=" line
    for ln in lines:
        assert "qt=false transport=false" in ln and "parentQt=blocked:SecurityError" in ln
        assert "origin=specto://" + probe["view"] + ".items" in ln
        assert ln.startswith("[INFO] page " + probe["view"] + ": framed page specto://")
    assert probe["events"] == [] and probe["bridge_calls"] == []
