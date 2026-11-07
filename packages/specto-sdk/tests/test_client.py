"""The SDK client over the in-process fake and over real TCP sockets."""

from __future__ import annotations

import json
import os
import time

import pytest
from conftest import relabelled

from specto_core import Project
from specto_sdk import Client, EndpointNotFound, RemoteError, TransportError, find_endpoint
from specto_sdk._api import UNSET, Api
from specto_sdk.generate import API_INFO_PATH, export_api_info, load_bundled, render_api
from specto_sdk.testing import InProcessEndpoint, TcpEndpoint, tcp_client
from specto_sdk.transport import InProcessTransport


def test_hello_call_and_errors(endpoint):
    c = endpoint.client(origin="agent:s1")
    assert c.connection and c.hello_result["server"] == "specto-app"
    out = c.call("canvas.state", {})
    assert out["document"]["views"]["fege"]["type"] == "file_tree"
    with pytest.raises(RemoteError) as ei:
        c.call("describe", {"target": "view:nope"})
    assert ei.value.code == "not_found" and ei.value.fix
    with pytest.raises(RemoteError) as ei:
        c.request("bogus")
    assert ei.value.code == "rpc:-32601"
    assert c.ping()["connection"] == c.connection
    assert "canvas.submit" in {x["name"] for x in c.list_commands("canvas.")}
    assert c.describe("canvas.submit")["info"]["args_schema"]["properties"]["document"]
    assert c.api_info()["api_level"] == 1
    c.close()
    assert c.closed
    with pytest.raises(TransportError):
        c.ping()


def test_submit_carries_the_hello_origin_and_undo_reverts_it(endpoint):
    c = endpoint.client(origin="agent:s1")
    res = c.call("canvas.submit", {"document": relabelled(endpoint.engine, "Data"), "label": "l"})
    assert res["touched"] == ["fege"] and res["cursor"] == 1
    assert endpoint.engine.live.record(res["txn"]).origin == "agent:s1"
    assert c.call("canvas.undo", {})["label"] == "l"
    assert endpoint.engine.live.data["views"]["fege"]["label"] != "Data"


def test_jobs_wait(endpoint):
    c = endpoint.client()
    job = endpoint.engine.jobs.submit("scan", lambda *, progress: 42)
    seen = []
    info = c.wait_job(job.id, on_progress=seen.append)
    assert info["state"] == "done" and info["result"] == 42 and seen
    assert c.resolve_job({"job": job.id})["id"] == job.id
    assert c.resolve_job({"view": "fege"}) == {"view": "fege"}


def test_watch_and_events(endpoint):
    c = endpoint.client()
    seq = c.call("events.poll", {"since_seq": 0})["last_seq"]
    c.watch(since_seq=seq)
    assert c.events() == []
    c.call("canvas.submit", {"document": relabelled(endpoint.engine, "Data"), "label": "l"})
    evs = c.events()
    kinds = [e["kind"] for e in evs]
    assert "canvas.submitted" in kinds and "doc.patched" in kinds
    assert evs[0]["seq"] == seq + 1
    polled = c.poll_events(since_seq=seq)["events"]
    assert [e["seq"] for e in polled] == [e["seq"] for e in evs]
    c.unwatch()
    c.call("canvas.undo", {})
    assert c.events() == []


def test_typed_api_methods(endpoint):
    c = endpoint.client(origin="agent:s1")
    api = c.api
    assert isinstance(api, Api)
    res = api.canvas_submit(document=relabelled(endpoint.engine, "typed"), label="typed")
    assert res["txn"] == "t1"
    assert api.canvas_state()["document"]["views"]["fege"]["label"] == "typed"
    assert UNSET is not None and "UNSET" in repr(UNSET)
    with pytest.raises(TypeError):
        api.canvas_submit({})  # keyword-only


def test_generated_files_match_registry():
    info = export_api_info()
    assert json.loads(API_INFO_PATH.read_text(encoding="utf-8")) == info
    assert load_bundled()["commands"][0]["name"] == info["commands"][0]["name"]
    from specto_sdk import _api

    src = open(_api.__file__, encoding="utf-8").read()
    assert src == render_api(info), "run tools/gen_api.py"
    names = {c["name"] for c in info["commands"]}
    for name in names:
        assert hasattr(Api, name.replace(".", "_"))


def test_tcp_roundtrip_with_push(endpoint):
    with TcpEndpoint(endpoint) as srv:
        a = tcp_client(srv, origin="agent:a")
        b = tcp_client(srv, origin="agent:b")
        assert a.connection != b.connection
        a.watch(since_seq=endpoint.engine.live.events.last_seq)
        b.call("canvas.submit", {"document": relabelled(endpoint.engine, "from b"), "label": "b"})
        ev = a.next_event(timeout=2.0)
        assert ev is not None
        kinds = [ev["kind"]] + [e["kind"] for e in a.events(timeout=0.5)]
        assert "canvas.submitted" in kinds
        # an unknown command is a clean error, not a dead socket
        with pytest.raises(RemoteError):
            b.call("nope")
        assert b.ping()
        b.close()
        for _ in range(50):
            if len(endpoint.handler.connections) == 1:
                break
            time.sleep(0.02)
        assert len(endpoint.handler.connections) == 1
        a.close()


def test_client_open_via_endpoint_file(tmp_path, monkeypatch):
    proj = Project.init(tmp_path / "proj", "demo")
    with pytest.raises(EndpointNotFound) as ei:
        find_endpoint(proj)
    assert "endpoint.json" in ei.value.fix
    ep = InProcessEndpoint(project=proj)
    with TcpEndpoint(ep) as srv:
        srv.advertise(proj)
        info, p = find_endpoint(proj)
        assert info.kind == "tcp" and p.id == proj.id
        # nearest-project resolution from a subdirectory
        sub = tmp_path / "proj" / "a" / "b"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        with Client.open() as c:
            assert c.project.id == proj.id
            assert c.hello_result["project"]["id"] == proj.id
            assert c.call("canvas.state", {})["project"]["name"] == "demo"
        # explicit url beats the file
        monkeypatch.setenv("SPECTO_ENDPOINT", f"tcp://{srv.address}")
        monkeypatch.chdir(tmp_path)
        info, p = find_endpoint()
        assert info.address == srv.address and p is None
    monkeypatch.delenv("SPECTO_ENDPOINT")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(EndpointNotFound) as ei:
        Client.open()
    assert ".specto" in ei.value.message


def test_inprocess_transport_closes_connection(endpoint):
    tr = InProcessTransport(endpoint.handler)
    assert tr.conn.id in endpoint.handler.connections
    tr.close()
    assert tr.conn.id not in endpoint.handler.connections
    with pytest.raises(TransportError):
        tr.send("{}")


def test_pipe_transport_import_guard():
    from specto_sdk.transport import PipeTransport

    if os.name != "nt":
        pytest.skip("named pipes are Windows only")
    with pytest.raises(TransportError) as ei:
        PipeTransport("specto-no-such-server-xyz")
    assert "app running" in ei.value.fix


def test_an_endpoint_s_requests_hold_the_canvas_lock_and_a_bare_thread_does_not(monkeypatch):
    """Every endpoint takes the canvas's serial_lock around a request, so its threads may
    submit; a bare submit from another thread still raises after the endpoint was used."""
    import threading

    monkeypatch.setenv("SPECTO_THREAD_CHECK", "raise")
    ep = InProcessEndpoint()
    live = ep.engine.live
    results: list[object] = []

    def via_endpoint() -> None:
        c = ep.client(origin="agent:t")
        results.append(c.call("canvas.submit", {"edits": {"project": {"label": "Rig"}}}))

    def bare() -> None:
        try:
            live.submit(edits={"project": {"label": "Bare"}}, origin="agent:t")
        except RuntimeError as e:
            results.append(e)

    for target in (via_endpoint, bare):
        worker = threading.Thread(target=target)
        worker.start()
        worker.join(10)
    assert results[0]["txn"] and isinstance(results[1], RuntimeError)
    assert live.data["project"]["label"] == "Rig"
