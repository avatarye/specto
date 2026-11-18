"""tools/soak_usd_switch.py (SPEC-78, developer.md "Soaking stage switches") against the fake
ovrtx: every plan runs clean on small stages; a failing step, a hung load and a close
that times out are faults (exit 1), and the last two leave no source process behind."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from process_helpers import alive

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


@pytest.mark.parametrize(
    ("plan", "case"),
    [(["--plan", "baseline"], "settled"), (["--plan", "isolate", "--op", "switch"], "switch prop")],
    ids=["baseline", "isolate"],
)
def test_a_failing_step_is_a_fault(tmp_path: Path, plan: list[str], case: str):
    """A step that fails is an error where a stage should have loaded: the soak says so
    and exits 1, isolate's loads around the repeated operation included."""
    rc, report = soak(tmp_path, *plan, "--rounds", "2", FAKE_OVRTX_FAIL_STEPS="1")
    assert rc == 1
    assert any(case in f for f in report["faults"])


def test_a_hung_load_writes_the_hang_file_and_kills_the_source(tmp_path: Path):
    """A load that sees neither ``ok`` nor an error in ``--timeout`` s is a hang: the soak
    writes the hang file, then kills the source's tree."""
    rc, report = soak(
        tmp_path, "--plan", "baseline", "--rounds", "1", "--timeout", "3", "--py-spy", "",
        FAKE_OVRTX_HOLD_STEP_S="120",
    )  # fmt: skip
    assert rc == 1
    assert any("hung" in f for f in report["faults"])
    hangs = list((tmp_path / "work").glob("hang-baseline-*.txt"))
    assert len(hangs) == 1
    pid = int(hangs[0].stem.rsplit("-", 1)[1])
    assert not alive(pid)


def test_a_close_that_times_out_is_killed_and_is_a_fault(tmp_path: Path):
    """A source still running at the close timeout (its first frame outlasts it): the soak
    kills what is left of its tree and counts a fault."""
    rc, report = soak(
        tmp_path, "--plan", "close", "--rounds", "1", "--close-timeout", "2",
        FAKE_OVRTX_HOLD_STEP_S="120",
    )  # fmt: skip
    assert rc == 1
    (row,) = [r for r in report["rows"] if r["case"] == "close"]
    assert row["timed_out"] and row["rc"] != 0
    assert any("close round 1" in f for f in report["faults"])
    assert not alive(row["pid"])
