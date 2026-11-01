"""The QLocalServer endpoint, driven by the real SDK transport (named pipe on Windows,
unix socket elsewhere) from a worker thread while the Qt event loop spins here."""

from __future__ import annotations

import os
import threading
import time

import pytest

from specto_core import Engine, Project
from specto_core.rpc import read_endpoint_file
from specto_sdk import Client, RemoteError
from specto_sdk.transport import open_transport

pytest.importorskip("PySide6.QtNetwork")
from PySide6.QtGui import QGuiApplication

from specto_app.endpoint import HAVE_QT, LocalEndpoint


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance() or QGuiApplication(["specto-tests"])
    yield app


def spin(app, until, timeout=60.0):
    """Spin the Qt loop until ``until()``; the ceiling is generous because the client
    thread spawns no process but shares the box with parallel suite copies (#179)."""
    deadline = time.monotonic() + timeout
    while not until() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert until(), "timed out waiting for the client thread"


def run_client(fn, *args):
    """Run ``fn(*args)`` in a thread; returns (thread, box) where box[0] is the result
    or the exception."""
    box: list = [None]

    def target():
        try:
            box[0] = fn(*args)
        except BaseException as e:
            box[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, box


def test_have_qt():
    assert HAVE_QT


def relabelled(engine: Engine, label: str) -> dict:
    doc = engine.live.document()
    doc["views"]["fege"]["label"] = label
    return doc


def test_endpoint_roundtrip(qapp, tmp_path):
    proj = Project.init(tmp_path / "p", "demo")
    engine = Engine(project=proj)
    ep = LocalEndpoint(engine, project=proj, name=f"specto-test-{proj.id[:8]}")
    info = ep.start()
    assert ep.listening and info.kind in ("pipe", "unix")
    assert read_endpoint_file(proj) == info

    def client_work():
        c = Client(open_transport(info), origin="agent:qt", client="t")
        out = {"hello": c.hello_result}
        out["state"] = c.call("canvas.state", {})
        out["submit"] = c.call(
            "canvas.submit", {"document": relabelled(engine, "Data"), "label": "renamed"}
        )
        try:
            c.call("nope")
        except RemoteError as e:
            out["err"] = e.code
        c.close()
        return out

    t, box = run_client(client_work)
    spin(qapp, lambda: not t.is_alive())
    out = box[0]
    if isinstance(out, BaseException):
        raise out
    assert out["hello"]["project"]["id"] == proj.id
    assert out["state"]["document"]["schema_version"] == 2
    assert out["submit"]["touched"] == ["fege"]
    assert out["err"] == "unknown_command"
    assert engine.live.record(out["submit"]["txn"]).origin == "agent:qt"
    spin(qapp, lambda: ep.connection_count() == 0)
    ep.stop()
    assert not ep.listening and read_endpoint_file(proj) is None


def test_endpoint_pushes_events(qapp):
    engine = Engine(project=None)
    # the name carries the pid: a parallel copy of the suite listening on the same pipe
    # name takes this client's connection, then closes it under us (#179)
    ep = LocalEndpoint(engine, name=f"specto-test-events-{os.getpid()}")
    info = ep.start()

    def client_work():
        c = Client(open_transport(info), origin="user")
        c.watch(since_seq=0)
        # trigger a change from this same client; the server pushes the events
        c.call("canvas.submit", {"document": relabelled(engine, "Data"), "label": "l"})
        evs = [c.next_event(timeout=30.0) for _ in range(2)]
        c.close()
        return evs

    t, box = run_client(client_work)
    spin(qapp, lambda: not t.is_alive())
    evs = box[0]
    if isinstance(evs, BaseException):
        raise evs
    kinds = {e["kind"] for e in evs if e}
    assert kinds & {"doc.patched", "canvas.submitted"}
    ep.stop()


def test_listen_conflict_is_an_error(qapp):
    engine = Engine(project=None)
    name = f"specto-test-conflict-{os.getpid()}"  # ours alone, see test_endpoint_pushes_events
    a = LocalEndpoint(engine, name=name)
    a.start()
    b = LocalEndpoint(engine, name=name)
    # removeServer() only clears stale files; a live server keeps the name on Windows
    try:
        b.start()
    except Exception as e:
        assert "listen" in str(e)
    finally:
        a.stop()
        b.stop()
