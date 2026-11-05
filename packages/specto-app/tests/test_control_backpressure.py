"""The host's handle on a frame source never waits on the source (SPEC-33): a source that
stops reading its control pipe (busy with a slow command) must not freeze the GUI thread
that sends it camera moves. Sends queue; camera, time and size updates still queued
collapse to the latest."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from concurrent.futures import Future

from specto_app.frames import control

#: a source that never reads its control pipe
DEAF = "import time; time.sleep(60)"

#: a source busy for a second before it reads, then echoes every request as an event
LATE_ECHO = """
import json, sys, time
time.sleep(1.0)
for raw in sys.stdin.buffer:
    msg = json.loads(raw)
    line = {"event": "got", "params": {"method": msg["method"], "params": msg["params"]}}
    sys.stdout.buffer.write(json.dumps(line).encode() + b"\\n")
    sys.stdout.buffer.flush()
    if msg["method"] == "shutdown":
        break
"""


def camera(x: float, cut: bool = False) -> dict:
    return {
        "position": [x, 0.0, 2.0],
        "target": [0.0, 0.0, 0.0],
        "up": [0.0, 0.0, 1.0],
        "fov_y": 0.9,
        "near": 0.1,
        "far": 1000.0,
        "cut": cut,
    }


def spawn(script: str) -> control.FrameSourceProcess:
    return control.FrameSourceProcess.spawn([sys.executable, "-c", script])


def test_a_flood_of_camera_moves_never_waits_on_a_source_that_stopped_reading():
    src = spawn(DEAF)
    slowest = [0.0]

    def flood() -> None:
        for i in range(3000):  # far past any pipe buffer
            t0 = time.perf_counter()
            src.send("set_camera", **camera(float(i)))
            slowest[0] = max(slowest[0], time.perf_counter() - t0)

    sender = threading.Thread(target=flood, daemon=True)
    try:
        sender.start()
        sender.join(5.0)
        assert not sender.is_alive(), "a send blocked on the full pipe"
        assert slowest[0] < 0.05
    finally:
        src.proc.kill()  # breaks the pipe under a blocked write
        src.close()


def got(src: control.FrameSourceProcess, until: str) -> list[dict]:
    """The requests the echo source received, up to and with ``until``."""
    out: list[dict] = []
    while not out or out[-1]["method"] != until:
        out.append(src.wait_event("got", timeout=10.0))
    return out


def test_queued_camera_time_and_size_updates_collapse_to_the_latest_in_order():
    src = spawn(LATE_ECHO)
    try:
        for i in range(1, 301):
            src.send("set_camera", **camera(float(i), cut=i == 7))
            src.send("set_time", t=i / 10)
            src.send("resize", width=100 + i, height=50)
        src.send("select", paths=["/World"])  # not an update: it keeps its place
        for i in range(301, 601):
            src.send("set_camera", **camera(float(i)))
        src.send("shutdown")
        seen = got(src, "shutdown")
    finally:
        src.close()
    methods = [m["method"] for m in seen]
    assert len(seen) < 100  # 1202 sent
    cut = methods.index("select")
    before, after = seen[:cut], seen[cut + 1 : -1]
    cams = [m["params"] for m in before if m["method"] == "set_camera"]
    # the latest of each before the selection, and the cut of an earlier move kept
    assert cams[-1]["position"][0] == 300.0 and any(c["cut"] for c in cams)
    assert [m for m in before if m["method"] == "set_time"][-1]["params"]["t"] == 30.0
    assert [m for m in before if m["method"] == "resize"][-1]["params"]["width"] == 400
    assert [m["params"]["position"][0] for m in after] == [600.0]
    assert all(m["method"] == "set_camera" for m in after)


#: a source that reads a little, then dies with the host's requests still unanswered
DIES = "import os, sys, time; sys.stdin.buffer.read(64); time.sleep(0.2); os._exit(7)"


def test_a_source_that_dies_mid_write_fails_every_waiting_call_and_says_it_exited():
    # the writer (a broken pipe) and the reader (stdout's end) both fail the waiting calls;
    # threads switching every microsecond make them meet often
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(20):
            die_mid_write()
    finally:
        sys.setswitchinterval(interval)


def die_mid_write() -> None:
    src = spawn(DIES)
    try:
        answers: list[str] = []
        lock = threading.Lock()

        def ask() -> None:
            try:
                src.call("state", timeout=5.0)
                outcome = "answered"
            except control.SourceError as e:
                outcome = str(e)
            with lock:
                answers.append(outcome)

        askers = [threading.Thread(target=ask, daemon=True) for _ in range(300)]
        for t in askers:
            t.start()
        with contextlib.suppress(control.SourceError):
            for i in range(3000):  # the writer is mid-write when the pipe breaks
                src.send("select", paths=[f"/World/P{i}" * 8])
        for t in askers:
            t.join(6.0)
        assert len(answers) == 300, "a call was left waiting"
        assert all(a != "answered" for a in answers)
        assert src.wait_event("exited", timeout=5.0)["code"] == 7
    finally:
        src.proc.kill()
        src.close()


class Pipes:
    """A control server in this process, over pipes; the test is its host."""

    def __init__(self, handler) -> None:
        r_in, self._w_in = os.pipe()
        self._r_out, w_out = os.pipe()
        self.to_server = os.fdopen(self._w_in, "wb", buffering=0)
        self.from_server = os.fdopen(self._r_out, "rb")
        self.server = control.ControlServer(
            handler,
            on_eof=lambda: None,
            stdin=os.fdopen(r_in, "rb", buffering=0),
            stdout=os.fdopen(w_out, "wb", buffering=0),
        )
        self.server.start()

    def ask(self, rid: int, method: str) -> dict:
        self.to_server.write(json.dumps({"id": rid, "method": method}).encode() + b"\n")
        box: list = []
        reader = threading.Thread(
            target=lambda: box.append(json.loads(self.from_server.readline())), daemon=True
        )
        reader.start()
        reader.join(2.0)
        assert box, f"no answer to {method}"
        return box[0]

    def close(self) -> None:
        self.to_server.close()


def test_an_answer_that_cannot_be_sent_is_an_error_and_the_server_reads_on():
    def handler(method: str, params: dict):
        if method == "later":
            done: Future = Future()
            threading.Timer(0.05, lambda: done.set_result({"value": object()})).start()
            return done
        if method == "now":
            return {"value": object()}
        return {"pong": True}

    pipes = Pipes(handler)
    try:
        later = pipes.ask(1, "later")
        assert later["id"] == 1 and "error" in later
        now = pipes.ask(2, "now")
        assert now["id"] == 2 and "error" in now
        assert pipes.ask(3, "ping") == {"id": 3, "result": {"pong": True}}
    finally:
        pipes.close()
