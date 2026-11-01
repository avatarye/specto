"""Time prompt turns of the real prompt bar against the live agent, offscreen (#73).

    uv run python tools/bench/prompt_bench.py --project C:/tmp/specto-bench-73 --model sonnet \\
        --canvas tools/bench/prompt-bench-canvas.json --select runs/run-001/cam-b \\
        --prompt "move the cam-a gallery next to the file tree" \\
        --prompt "show the selected folder as a gallery" [--out rows.jsonl] [--keep-session]

Runs ``SpectoApp`` offscreen (no web, inline jobs, no endpoint) with the Claude agent on
the user's own login, so every turn costs real money: keep it to a few prompts on a
scratch project. Before each prompt the canvas is reset to ``--canvas`` (one user
submit) and the file tree's selection is set to the files under ``--select``, so every
prompt starts from the same state. One row per turn: model, status, total, first token,
model (API) time, each tool call with its ms, the turn's cost, its output tokens (per
model call too) and the size of each submit_canvas input (#98). ``--keep-session``
resumes the project's last session instead of starting a new one per run.
"""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--model", default="sonnet", help="opus | sonnet | haiku")
    ap.add_argument("--canvas", type=Path, default=HERE / "prompt-bench-canvas.json")
    ap.add_argument("--select", default="", help="folder whose files the file tree has picked")
    ap.add_argument("--prompt", action="append", default=[], help="one turn (repeatable)")
    ap.add_argument("--out", type=Path, default=None, help="append one JSON row per turn")
    ap.add_argument("--keep-session", action="store_true", help="resume the last session")
    ap.add_argument("--agent", default="claude", help="claude, or scripted:<json> for a dry run")
    ap.add_argument("--timeout", type=float, default=180.0)
    ns = ap.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # the bench's model choice and recents stay out of the user's own settings
    os.environ.setdefault("SPECTO_USER_DIR", str(ns.project / ".specto" / "bench-user"))
    sys.path.insert(0, str(ROOT / "packages" / "specto-app" / "src"))
    import qasync
    from PySide6.QtGui import QGuiApplication

    from specto_app.launcher import open_project
    from specto_app.qt_setup import prepare
    from specto_core.links import FilesValue

    if not ns.keep_session:
        session_file = ns.project / ".specto" / "agent-session.json"
        if session_file.exists():
            session_file.unlink()
    prepare(web=False, offscreen=True)
    qapp = QGuiApplication.instance() or QGuiApplication(["prompt-bench"])
    loop = qasync.QEventLoop(qapp)
    asyncio.set_event_loop(loop)

    from specto_app.app import SpectoApp

    project = open_project(ns.project, create=True)
    app = SpectoApp(
        project, jobs="inline", web=False, offscreen=True, serve=False, remember=False,
        agent=ns.agent, loop=loop,
    )
    app.start()
    bar = app.prompt_bar
    assert bar is not None
    inner_call = bar.tools.call

    submit_chars: list[int] = []  # the turn's submit_canvas inputs, compact JSON (#98)

    def traced(name, args):  # progress while a turn runs
        t0 = time.perf_counter()
        res = inner_call(name, args)
        flag = "ERR " if res.is_error else ""
        what = f" {args.get('target')}" if name in ("describe", "snap") else ""
        if name == "submit_canvas":
            submit_chars.append(len(json.dumps(args, separators=(",", ":"))))
            what = f" {submit_chars[-1]} chars"
        print(f"[TOOL] {flag}{name}{what} {(time.perf_counter() - t0) * 1000:.0f} ms", flush=True)
        return res

    bar.tools.call = traced  # type: ignore[method-assign]
    canvas = json.loads(ns.canvas.read_text(encoding="utf-8"))
    canvas["project"]["label"] = project.name
    rows: list[dict] = []

    async def reset() -> None:
        # never processEvents() from inside a coroutine on the qasync loop: it re-enters
        # the SDK's reader task and wedges the session; awaiting lets qasync pump Qt
        app.live.submit(canvas, origin="user", label="bench reset")
        await asyncio.sleep(0.3)  # the app's 8 ms timer resolves the tree's listing
        if ns.select:
            # the submit may have renamed the views (#79): find the file tree by type
            tree = next(v for v, d in app.live.data["views"].items() if d["type"] == "file_tree")
            files = app.engine.links.outputs.get((tree, "all_files"))
            paths = [p for p in (files.paths if files else []) if p.startswith(ns.select)]
            app.engine.links.emit(tree, "selected_files", FilesValue(root=str(project.root), paths=paths))
        await asyncio.sleep(0.3)

    async def one(prompt: str) -> dict:
        done: asyncio.Future = loop.create_future()
        bar.turnFinished.connect(lambda: done.done() or done.set_result(None))
        await reset()
        submit_chars.clear()
        ctx = bar.context()
        picked = {v: s.get("selected_files", {}).get("count") for v, s in ctx.selection.items()}
        print(f"[CTX] files={[f['count'] for f in ctx.files.values()]} picked={picked} "
              f"document={'full' if ctx.document is not None else 'hash'}")
        assert bar.setModel(ns.model), f"unknown model {ns.model}"
        ok = bar.submit(prompt)
        assert ok, "the bar refused the prompt"
        await asyncio.wait_for(done, ns.timeout)
        r = bar.reply
        assert r is not None
        info = getattr(bar.session, "info", {}) or {}
        doc = app.live.document()
        return {
            "model": ns.model,
            "resolved_model": info.get("model", ""),
            "prompt": prompt,
            "status": r.status,
            "summary": r.summary,
            "total_s": round(r.total_ms / 1000, 1),
            "first_token_s": round(r.first_token_ms / 1000, 1) if r.first_token_ms >= 0 else None,
            "model_s": round(r.api_ms / 1000, 1),
            "tools": [(n, round(ms)) for n, ms in r.tool_calls],
            "output_tokens": r.output_tokens,
            "call_output_tokens": r.call_output_tokens,
            "submit_chars": list(submit_chars),
            "attempts_failed": r.attempts_failed,
            "cost_usd": r.cost_usd,
            "views": {
                vid: [v["type"], v["layout"]["position"], v["layout"]["size"]]
                for vid, v in doc["views"].items()
            },
            "links": {lid: [ln["kind"], ln.get("scope"), f"{ln['source']['view']}.{ln['source']['port']}", ln["target"]["view"]] for lid, ln in doc["links"].items()},
        }

    async def run() -> None:
        try:
            for prompt in ns.prompt:
                row = await one(prompt)
                rows.append(row)
                print("[ROW] " + json.dumps(row, ensure_ascii=False), flush=True)
                if ns.out:
                    with ns.out.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        finally:
            await bar.aclose()

    faulthandler.dump_traceback_later(90, repeat=True)  # a stuck turn shows its stacks
    try:
        with loop:
            loop.run_until_complete(run())
    finally:
        faulthandler.cancel_dump_traceback_later()
        app.close()
    print(f"[OK] {len(rows)} turn(s) on {ns.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
