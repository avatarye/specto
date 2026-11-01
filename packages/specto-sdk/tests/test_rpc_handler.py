"""The transport-agnostic JSON-RPC handler (specto_core.rpc), driven line by line."""

from __future__ import annotations

import json

from conftest import relabelled

from specto_core import Engine, Project
from specto_core.registry import API_COMPATIBLE, API_LEVEL
from specto_core.rpc import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SPECTO_ERROR,
    EndpointInfo,
    RpcHandler,
    endpoint_name,
    read_endpoint_file,
    remove_endpoint_file,
    write_endpoint_file,
)


def req(method, params=None, rid=1):
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})


def one(handler, conn, method, params=None, rid=1):
    out = handler.handle_line(conn, req(method, params, rid))
    lines = [json.loads(ln) for ln in out.strip().split("\n")]
    return lines[0], lines[1:]


def submit(h, conn, label):
    doc = relabelled(h.engine, label)
    return one(
        h, conn, "call", {"name": "canvas.submit", "args": {"document": doc, "label": label}}
    )


def test_hello_and_call(endpoint):
    h = endpoint.handler
    conn = h.connect()
    resp, _ = one(h, conn, "hello", {"client": "t", "origin": "agent:s9", "api_level": API_LEVEL})
    r = resp["result"]
    assert r["server"] == "specto-app" and r["api_compatible"] is True
    assert r["connection"] == conn.id and r["project"] is None
    assert conn.origin == "agent:s9"

    resp, _ = one(h, conn, "call", {"name": "canvas.state", "args": {}})
    assert resp["result"]["document"]["schema_version"] == 2

    # provenance carries the hello origin
    resp, _ = submit(h, conn, "Data")
    assert endpoint.engine.live.record(resp["result"]["txn"]).origin == "agent:s9"


def test_api_level_incompatible(endpoint):
    h = endpoint.handler
    conn = h.connect()
    resp, _ = one(h, conn, "hello", {"client": "t", "origin": "user", "api_level": API_LEVEL + 5})
    assert resp["result"]["api_compatible"] is False
    resp, _ = one(h, conn, "hello", {"api_level": API_COMPATIBLE - 1})
    assert resp["result"]["api_compatible"] is False


def test_error_mapping(endpoint):
    h = endpoint.handler
    conn = h.connect()
    resp, _ = one(h, conn, "call", {"name": "nope.x", "args": {}})
    err = resp["error"]
    assert err["code"] == SPECTO_ERROR and err["data"]["code"] == "unknown_command"
    resp, _ = one(h, conn, "call", {"name": "canvas.submit", "args": {"bogus": 1}})
    assert resp["error"]["data"]["code"] == "invalid_args"
    assert resp["error"]["data"]["data"]["errors"]
    resp, _ = one(h, conn, "frobnicate")
    assert resp["error"]["code"] == METHOD_NOT_FOUND
    resp, _ = one(h, conn, "ping", {"extra": 1})
    assert resp["error"]["code"] == INVALID_PARAMS
    out = json.loads(h.handle_line(conn, "{not json"))
    assert out["error"]["code"] == PARSE_ERROR and out["id"] is None
    out = json.loads(h.handle_line(conn, json.dumps({"id": 3, "params": {}})))
    assert out["error"]["code"] == INVALID_REQUEST and out["id"] == 3
    assert h.handle_line(conn, "   ") is None


def test_notification_and_batch(endpoint):
    h = endpoint.handler
    conn = h.connect()
    # a notification (no id) gets no reply even on error
    assert h.handle_line(conn, json.dumps({"jsonrpc": "2.0", "method": "nope"})) is None
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "list_commands", "params": {"prefix": "canvas."}},
    ]
    out = json.loads(h.handle_line(conn, json.dumps(batch)))
    assert [r["id"] for r in out] == [1, 2]
    assert {c["name"] for c in out[1]["result"]["commands"]} >= {"canvas.submit", "canvas.undo"}
    out = json.loads(h.handle_line(conn, "[]"))
    assert out["error"]["code"] == INVALID_REQUEST


def test_watch_pushes_events_and_backlog(endpoint):
    h = endpoint.handler
    pushed = []
    conn = h.connect(send=pushed.append)
    engine = endpoint.engine
    submit(h, conn, "one")
    seq_before = engine.live.events.last_seq
    resp, _ = one(h, conn, "watch", {"since_seq": 0})
    assert resp["result"]["seq"] == seq_before
    # backlog replayed first
    assert len(pushed) == seq_before and pushed[0]["method"] == "event"
    assert pushed[0]["params"]["seq"] == 1
    pushed.clear()
    submit(h, conn, "two")
    kinds = [m["params"]["kind"] for m in pushed]
    assert "doc.patched" in kinds and "canvas.submitted" in kinds
    resp, _ = one(h, conn, "unwatch")
    assert resp["result"] == {}
    pushed.clear()
    engine.call("canvas.undo", {})
    assert pushed == []
    resp, _ = one(h, conn, "unwatch")
    assert resp["error"]["data"]["code"] == "not_found"


def test_watch_without_push_rides_on_responses(endpoint):
    """A transport with no push channel gets notifications appended to the next reply."""
    h = endpoint.handler
    conn = h.connect()
    _resp, extra = one(h, conn, "watch", {"since_seq": 10_000})
    assert extra == []  # nothing newer than 10000
    _, extra = submit(h, conn, "one")
    assert extra and all(m["method"] == "event" for m in extra)
    # disconnect drops the subscription
    h.disconnect(conn)
    endpoint.engine.call("canvas.undo", {})
    assert conn.pending == []


def test_gap_event_when_buffer_rolled(endpoint):
    from specto_core.events import EventStream

    engine = endpoint.engine
    engine.live.events = EventStream(capacity=3)
    for i in range(5):
        engine.live.submit(relabelled(engine, f"label {i}"), label=str(i))
    h = endpoint.handler
    conn = h.connect()
    _, extra = one(h, conn, "watch", {"since_seq": 0})
    assert extra[0]["params"]["kind"] == "gap"


def test_endpoint_file_roundtrip(tmp_path):
    proj = Project.init(tmp_path / "p", "demo")
    assert read_endpoint_file(proj) is None
    info = EndpointInfo(kind="pipe", address=endpoint_name(proj.id), pid=123, project_id=proj.id)
    path = write_endpoint_file(proj, info)
    assert path == proj.specto_dir / "endpoint.json"
    back = read_endpoint_file(proj)
    assert back == info and back.url() == f"pipe://specto-{proj.id}"
    assert EndpointInfo.from_url("tcp://127.0.0.1:5000").address == "127.0.0.1:5000"
    remove_endpoint_file(proj)
    assert read_endpoint_file(tmp_path / "p") is None


def test_hello_reports_project(tmp_path):
    proj = Project.init(tmp_path / "p", "demo proj")
    h = RpcHandler(Engine(project=proj))
    conn = h.connect()
    resp, _ = one(h, conn, "hello", {"client": "t"})
    assert resp["result"]["project"] == {"id": proj.id, "name": "demo proj", "slug": "demo_proj"}
