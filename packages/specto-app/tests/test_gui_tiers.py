"""The GUI flows' tiers (#237, ``tools/gui_flows/v2_suite.py``): every flow file has one
tier, every flow outside the offscreen tier says why, an entry's flows share a tier, and
the parallel limit stays within the machine's means."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[3] / "tools"


def _load():
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location("v2_suite", TOOLS / "gui_flows" / "v2_suite.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


suite = _load()


def test_every_flow_file_has_a_tier_and_every_tier_is_known():
    flows = suite.all_flows()
    assert flows, "no flow files found"
    for flow in flows:
        assert suite.tier_of(flow) in suite.TIER_NAMES, flow
    unknown = set(suite.TIERS) - set(flows)
    assert not unknown, f"tiers for flows that do not exist: {sorted(unknown)}"


def test_every_flow_outside_the_offscreen_tier_has_a_reason():
    for flow, (tier, reason) in suite.TIERS.items():
        assert tier != suite.OFFSCREEN, f"{flow}: the table lists only flows off the default tier"
        assert reason.strip(), f"{flow}: a flow in the {tier} tier needs a reason"


def test_the_suite_runs_every_offscreen_flow_or_names_it_by_hand():
    run = {f for e in suite.all_entries().values() for s in e["sessions"] for f in s["flows"]}
    for flow in suite.all_flows():
        if suite.tier_of(flow) == suite.OFFSCREEN:
            assert flow in run or flow in suite.BY_HAND, f"{flow}: in no suite entry"
    for flow in suite.BY_HAND:
        assert flow in suite.all_flows(), flow


def test_an_entrys_flows_share_one_tier():
    for name, entry in suite.all_entries().items():
        tier = suite.entry_tier(entry)  # raises when they do not
        assert tier in suite.TIER_NAMES, name


def test_the_gpu_and_mouse_tiers_stay_small():
    """The desktop tiers are kept minimal (#237 decision 3): the mouse tier is the one
    native drag-and-drop flow; the GPU tier is the renderers (RTX, Storm, mpv, WebGL) and
    the pixel asserts that only mean something there."""
    mouse = [f for f, (t, _) in suite.TIERS.items() if t == suite.MOUSE]
    gpu = [f for f, (t, _) in suite.TIERS.items() if t == suite.GPU]
    assert mouse == ["v2-185-drag-out.txt"]
    assert len(gpu) <= 32  # SPEC-31 the usd view's environment light (RTX); SPEC-43.2 the urdf page
    assert len(suite.all_flows()) - len(gpu) - len(mouse) >= 65


def test_the_parallel_limit_is_four_at_most_and_leaves_eight_cores(monkeypatch):
    monkeypatch.setattr(suite.os, "cpu_count", lambda: 32)
    assert suite.max_jobs() == 4
    monkeypatch.setattr(suite.os, "cpu_count", lambda: 10)
    assert suite.max_jobs() == 2
    monkeypatch.setattr(suite.os, "cpu_count", lambda: 4)
    assert suite.max_jobs() == 1


def test_session_commands_pick_the_runner_of_the_tier(tmp_path):
    sess = {"flows": ["v2-40-free-canvas.txt"], "extra": "--x"}
    off = suite.session_cmd(suite.OFFSCREEN, tmp_path, tmp_path, tmp_path, "e", 0, sess)
    gpu = suite.session_cmd(suite.GPU, tmp_path, tmp_path, tmp_path, "e", 0, sess)
    mouse = suite.session_cmd(suite.MOUSE, tmp_path, tmp_path, tmp_path, "e", 0, sess)
    assert str(suite.QTDRIVE) in off and "offscreen" in off and "--report" in off
    assert str(suite.QTDRIVE) in gpu and "window" in gpu
    assert str(suite.SESSION) in mouse and "--report" not in mouse
    assert off[-1].endswith("v2-40-free-canvas.txt")
