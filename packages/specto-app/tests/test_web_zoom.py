"""#189, the side panel and footer text keep the theme size: each web page in the app's
own WebView.qml in headless Qt WebEngine (``web_zoom_child.py``, no window on screen).
Ctrl+wheel was Chromium's page zoom, a factor on every CSS px, so the markdown OUTLINE
(and the map's LAYERS, the 3D panels, every footer) grew and shrank with it. Now no
page zooms: the outline renders at the theme's 11 px at any view width and after
Ctrl+wheel, and Ctrl+wheel over the note zooms the note alone."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import specto_web

CHILD = Path(__file__).with_name("web_zoom_child.py")
WEB = specto_web.find_web_root()
SMALL = 11  # theme metric font_px_small: the outline rows and the footer
PAGES = ["markdown", "map", "3d", "json", "html"]

pytestmark = pytest.mark.skipif(
    WEB is None or not (WEB / "vendor" / "markdown-it").is_dir(),
    reason="the web vendor libs are not built: " + specto_web.BUILD_HINT,
)


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("zoom")
    note = tmp / "n.md"
    note.write_text(
        "# Title\n\ntext\n\n## Section one\n\nmore\n\n### Deep\n\nx\n", encoding="utf-8"
    )
    plan = {"pages": PAGES, "note": str(note), "tmp": str(tmp)}
    res = subprocess.run(
        [sys.executable, str(CHILD), json.dumps(plan)],
        capture_output=True,
        text=True,
        timeout=200,
        check=False,
    )
    line = next((x for x in res.stdout.splitlines() if x.startswith("RESULT ")), None)
    said = f"{res.stdout[-3000:]}\n{res.stderr[-3000:]}"
    assert line, f"no result from the child (rc {res.returncode})\n{said}"
    out = json.loads(line[len("RESULT ") :])
    assert "error" not in out, f"{out['error']}\n{said}"
    return out["pages"]


def screen_px(m: dict, key: str) -> float:
    """What the user sees: the CSS px times Chromium's page zoom."""
    return round(m[key] * m["zoomFactor"], 2)


def test_the_outline_keeps_the_theme_size_at_any_view_width(pages):
    md = pages["markdown"]
    assert md["w500"]["width"] == 500 and md["w900"]["width"] == 900
    assert screen_px(md["w500"], "row") == screen_px(md["w900"], "row") == SMALL


def test_ctrl_wheel_over_the_outline_does_not_zoom_it(pages):
    md = pages["markdown"]
    assert md["outline"]["zoomFactor"] == 1
    assert screen_px(md["outline"], "row") == SMALL
    assert screen_px(md["outline"], "footer") == SMALL
    assert md["outline"]["docZoom"] == 1, "the wheel was over the outline, not the note"


def test_ctrl_wheel_over_the_note_zooms_the_note_alone(pages):
    md = pages["markdown"]
    assert md["note"]["zoomFactor"] == 1
    assert md["note"]["docZoom"] > 1
    assert screen_px(md["note"], "row") == SMALL


def test_the_note_zoom_is_told_to_the_host_once_the_notches_settle(pages):
    """#202: each settled level goes out as one `zoom {level}` page event (the host
    remembers it in params.zoom); a second notch later is a second, larger level."""
    md = pages["markdown"]
    first = md["note"]["docZoom"]
    assert md["sent"][0] == ["zoom", {"level": first}]
    assert md["sent"][1][0] == "zoom" and first < md["sent"][1][1]["level"] <= 3


def test_a_stale_echo_of_an_earlier_level_moves_nothing(pages):
    md = pages["markdown"]
    assert md["echo"]["docZoom"] == md["sent"][1][1]["level"]


def test_the_agent_s_zoom_applies_and_a_missing_one_is_1(pages):
    """The agent's params.zoom shows (the outline keeps the theme's size, nothing is
    told back), a params without a zoom shows the note at 1."""
    md = pages["markdown"]
    assert md["agent"]["docZoom"] == 2 and md["agent"]["zoomFactor"] == 1
    assert screen_px(md["agent"], "row") == SMALL
    assert md["cleared"]["docZoom"] == 1
    assert md["told"] == md["sent"]  # the applied levels were not sent back


@pytest.mark.parametrize("page", [p for p in PAGES if p != "markdown"])
def test_ctrl_wheel_over_another_views_chrome_does_not_zoom_the_page(pages, page):
    got = pages[page]
    assert got["footer"]["zoomFactor"] == 1
    assert screen_px(got["footer"], "footer") == SMALL


@pytest.mark.parametrize("page", PAGES)
def test_a_page_zoom_set_from_outside_does_not_hold(pages, page):
    assert pages[page]["forced"] == 1
