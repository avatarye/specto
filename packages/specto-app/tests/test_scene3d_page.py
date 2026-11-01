"""The 3D page's own scene bookkeeping (``web/3d/main.js``) under node, against a
recording Babylon fake: a source switch leaves only the new collection's items in the
scene, with the old buffers and materials disposed, late loads kept out, and the camera
re-centred on the first load only, splats import with the vendored SPZ decoder named, and
frames keep coming while a splat sorts and stop once it has settled (#85), and the
chrome of #129: the VIEW and ITEMS panels (All / None, sizes, camera speed, the world
origin, collapse and width kept in params) on the shared side panel and the footer's
frame-all (``scene3d_page.test.mjs``); that decoder reads a v4 (NGSP) file offline
(``spz_decoder.test.mjs``, #66); the shared Unreal Editor viewport controls
(``viewnav.test.mjs``, #152). Skipped when node is not on the PATH."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB_TESTS = Path(__file__).resolve().parents[2] / "specto-web" / "tests"


SUITES = ["scene3d_page.test.mjs", "spz_decoder.test.mjs", "viewnav.test.mjs"]


@pytest.mark.parametrize("suite", SUITES)
def test_the_3d_page_under_node(suite: str):
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
