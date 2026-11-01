"""The web views' pages under node (#130): the shared chrome, the side panel standard and
the footer with its step controls (``chrome.test.mjs``), and what the map, markdown,
json and html pages build from their notifications and send back
(``web_views.test.mjs``) and the scratch pad's page (``scratch_page.test.mjs``, #184),
against a small page DOM built from each page's own index.html. Skipped when node is
not on the PATH."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB_TESTS = Path(__file__).resolve().parents[2] / "specto-web" / "tests"


@pytest.mark.parametrize(
    "suite",
    ["chrome.test.mjs", "web_views.test.mjs", "bridge.test.mjs", "scratch_page.test.mjs"],
)
def test_the_web_pages_under_node(suite: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    res = subprocess.run(
        [node, "--test", "--test-reporter=spec", str(WEB_TESTS / suite)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == 0, f"node --test failed\n{res.stdout}\n{res.stderr}"
