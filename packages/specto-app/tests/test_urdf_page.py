"""The urdf view's page (``web/urdf/``, SPEC-43.2) under node: its kinematics and ROBOT
panel bookkeeping against a hand-written section-11 model (``urdf_kin.test.mjs``), and
the page itself against the recording Babylon fake (``urdf_page.test.mjs``): the robot's
scene graph, primitives, shared meshes and placeholders, the five view modes, overlays,
environment and grid, the VIEW and ROBOT panels, sliders, selection and the footer.
Skipped when node is not on the PATH."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB_TESTS = Path(__file__).resolve().parents[2] / "specto-web" / "tests"

SUITES = ["urdf_kin.test.mjs", "urdf_page.test.mjs"]


@pytest.mark.parametrize("suite", SUITES)
def test_the_urdf_page_under_node(suite: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    res = subprocess.run(
        [node, "--test", "--test-reporter=spec", str(WEB_TESTS / suite)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert res.returncode == 0, f"node --test failed\n{res.stdout}\n{res.stderr}"
