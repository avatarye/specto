"""The dev-only ovrtx profiling tools (SPEC-65, docs/setup/developer.md "Profiling a usd
load"): ``tools/ovrtx_probe`` (a ``sitecustomize`` that hands ovrtx carb settings and
extra ``RendererConfig`` fields in the usd source process only) and
``tools/ovrtx_trace_top.py`` (a summary of a carb CPU profiler Chrome trace)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / "tools" / "ovrtx_probe"
TRACE_TOP = ROOT / "tools" / "ovrtx_trace_top.py"
FAKE = Path(__file__).resolve().parent / "fake_ovrtx"


def _env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(PROBE), str(FAKE), env.get("PYTHONPATH", "")])
    env.update(extra)
    return env


def test_the_probe_does_nothing_outside_the_usd_source():
    """Any other Python process (the bench, gpu_guard, uv) never imports ovrtx through it."""
    env = _env(SPECTO_PROBE_CONFIG='{"enable_profiling": true}')
    code = "import sys; print('ovrtx' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_the_probe_adds_renderer_config_fields_in_the_usd_source(tmp_path: Path):
    """``SPECTO_PROBE_CONFIG`` (a JSON object) reaches ``RendererConfig`` in the source; the
    source's own fields stay."""
    pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")
    from specto_app.frames import control

    log = tmp_path / "ovrtx.log"
    env = _env(FAKE_OVRTX_LOG=str(log), SPECTO_PROBE_CONFIG='{"log_level": "verbose"}')
    argv = [sys.executable, "-m", "specto_app.usd.source", "--renderer", "rtx"]
    src = control.FrameSourceProcess.spawn([*argv, "--width", "64", "--height", "36"], env=env)
    try:
        src.wait_event("ready", timeout=20)
    finally:
        src.close()
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    (renderer,) = [r for r in rows if r["call"] == "Renderer"]
    assert renderer["config"].get("log_level") == "verbose"
    assert renderer["config"].get("enable_crash_reporter") is False


def _zone(name: str, tid: int, ts_s: float, dur_s: float) -> dict:
    return {"name": name, "ph": "X", "pid": 1, "tid": tid, "ts": ts_s * 1e6, "dur": dur_s * 1e6}


def test_the_trace_summary_breaks_the_first_frame_down_by_quality_loop(tmp_path: Path):
    """One event per line, as carb writes it, cut short at the end: the step, its streaming
    loops in order and what the other threads did in each."""
    events = [
        {
            "name": "thread_name",
            "ph": "M",
            "pid": 1,
            "tid": 7,
            "args": {"name": "OVRTX:OperationStream (7)"},
        },
        _zone("ovrtx_step_with_stage_execute (op_id=2)", 7, 1.0, 3.0),
        _zone("RenderQualityLoop[1] (streaming) (accumulation)", 7, 1.0, 1.0),
        _zone("RenderQualityLoop[2] (streaming) (accumulation)", 7, 2.0, 1.5),
        _zone("RenderQualityLoop[3] (accumulation)", 7, 3.5, 0.1),
        _zone("update Mesh", 8, 1.2, 0.5),
        _zone("loadAsset", 9, 2.1, 1.2),
        _zone("Running fiber", 9, 2.1, 0.1),
        {
            "name": "UJITSO: RequestsTotal",
            "ph": "C",
            "pid": 1,
            "tid": 9,
            "ts": 2.5e6,
            "args": {"value": 3},
        },
    ]
    trace = tmp_path / "profile.json"
    text = "[\n" + ",\n".join(json.dumps(e) for e in events) + ',\n{"name":"cut'
    trace.write_text(text, encoding="utf-8")
    out = subprocess.run(
        [sys.executable, "-I", str(TRACE_TOP), str(trace)], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    text = out.stdout
    assert "first frame step: 3.000 s" in text
    step = text[text.index("first frame step") :]  # the top list above names them too
    loop1, loop2 = step.index("streaming loop 1"), step.index("streaming loop 2")
    assert loop1 < step.index("update Mesh") < loop2 < step.index("loadAsset")
    assert "other loops: 1, 0.100 s" in step
    assert "Running fiber" not in text
    assert "UJITSO: RequestsTotal" in text
