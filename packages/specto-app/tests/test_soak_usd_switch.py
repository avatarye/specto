"""tools/soak_usd_switch.py (SPEC-78, developer.md "Soaking stage switches") against the fake
ovrtx: every plan runs clean on small stages, and a failing step is a fault (exit 1)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "soak_usd_switch.py"
FAKE = Path(__file__).resolve().parent / "fake_ovrtx"
STAGE = '#usda 1.0\n(\n    defaultPrim = "World"\n    upAxis = "Z"\n)\ndef Xform "World" {}\n'


def soak(tmp_path: Path, *args: str, **extra_env: str) -> tuple[int, dict]:
    stages = []
    for name in ("big", "big_alt", "prop", "small"):
        path = tmp_path / f"{name}.usda"
        path.write_text(STAGE, encoding="utf-8", newline="\n")
        stages += [f"--{name.replace('_', '-')}", str(path)]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(FAKE), env.get("PYTHONPATH", "")])
    env["FAKE_OVRTX_LOG"] = str(tmp_path / "ovrtx.log")
    env.update(extra_env)
    work = tmp_path / "work"
    argv = [sys.executable, str(TOOL), "--work", str(work), "--timeout", "60", *args, *stages]
    run = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=600)
    report = json.loads((work / "soak.json").read_text(encoding="utf-8"))
    return run.returncode, report


@pytest.mark.parametrize(
    "plan",
    [
        ["--plan", "interactive", "--rounds", "1"],
        ["--plan", "isolate", "--op", "mid", "--rounds", "2"],
        ["--plan", "isolate", "--op", "failed", "--rounds", "2"],
        ["--plan", "close", "--rounds", "1"],
        ["--plan", "baseline", "--fresh", "--rounds", "2"],
    ],
    ids=lambda p: "-".join(a.lstrip("-") for a in p[1:4:2]),
)
def test_each_plan_runs_clean_against_the_fake_renderer(tmp_path: Path, plan: list[str]):
    rc, report = soak(tmp_path, *plan)
    assert report["faults"] == [] and rc == 0
    assert report["rows"]


def test_a_failing_step_is_a_fault(tmp_path: Path):
    """A step that fails is an error where a stage should have loaded: the soak says so
    and exits 1."""
    rc, report = soak(tmp_path, "--plan", "baseline", "--rounds", "2", FAKE_OVRTX_FAIL_STEPS="1")
    assert rc == 1
    assert any("settled" in f for f in report["faults"])
