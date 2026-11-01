"""Seam 2 (#203, ADR 0006): ``link.approve`` from the endpoint never approves. It opens
an approval card in the agent thread naming the requester and returns ``pending``;
only the user's click on the card (or the view's Approve) records the hash and the
link resolves. An unanswered or dismissed card leaves the link waiting; a file changed
since the request is not approved from the stale card; the Specto agent's own tools
cannot approve. The real-mouse flow is tools/gui_flows/v2-203-approve-request.txt."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin_link

from specto_app.prompt.scripted import ScriptedAgentSession
from specto_core import CallContext
from specto_core.agent_tools import AGENT_TOOL_NAMES
from specto_core.python_links import TrustStore, sha256_of

SENDER = "FLEET/SQUAD/SHIP"
LINK_FILE = ".specto/links/pick.py"
CODE = """\
def resolve(items, params, root):
    return {it["key"]: it["key"] for it in items}
"""
CHANGED = CODE.replace("return", "import os\n    return")


def python_doc(live) -> dict:
    doc = live.document()
    for vid, x in (("kobi", 300), ("tamu", 960)):
        doc["views"][vid] = {
            "type": "gallery",
            "label": vid,
            "layout": {"position": [x, 0], "size": [600, 400]},
        }
        doc["links"][f"p_{vid}"] = pin_link(vid)
    doc["links"]["l9"] = {
        "kind": "python",
        "source": {"view": "kobi", "port": "selection"},
        "target": {"view": "tamu", "port": "selection"},
        "python": {"path": LINK_FILE, "resolves_to": "table", "output": "selection"},
        "summary": {"sentence": "The rear gallery selects what the python table says."},
    }
    return doc


@pytest.fixture
def app(qapp, project, aloop, spin, monkeypatch, tmp_path):
    from specto_app.app import SpectoApp

    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))  # a fresh trust list
    (project.root / LINK_FILE).parent.mkdir(parents=True, exist_ok=True)
    (project.root / LINK_FILE).write_bytes(CODE.encode())  # LF bytes: the hash the test knows
    sessions: list[ScriptedAgentSession] = []
    turns: list[list[dict]] = []

    def factory(tools):
        s = ScriptedAgentSession(tools, turns)
        sessions.append(s)
        return s

    a = SpectoApp(
        project, jobs="inline", web=False, offscreen=True, serve=True, agent=factory, loop=aloop
    )
    a.start()
    a.sessions = sessions  # type: ignore[attr-defined]
    a.turns = turns  # type: ignore[attr-defined]
    a.live.submit(python_doc(a.live), origin="agent:s1", label="python link")
    spin(50)
    assert state(a) == "needs_approval"
    yield a
    a.close()
    spin(20)


def call_endpoint(app, name: str, args: dict, *, origin: str = "user") -> dict:
    """A command through the app's endpoint handler, as a CLI or MCP client sends it."""
    handler = app.endpoint.handler
    conn = handler.connect(send=None)
    try:
        hello = {"jsonrpc": "2.0", "id": 0, "method": "hello", "params": {"origin": origin}}
        handler.handle_line(conn, json.dumps(hello))
        req = {"jsonrpc": "2.0", "id": 1, "method": "call", "params": {"name": name, "args": args}}
        out = handler.handle_line(conn, json.dumps(req))
    finally:
        handler.disconnect(conn)
    return json.loads(out.splitlines()[-1])


def state(app, link: str = "l9") -> str:
    return (app.engine.links.status(link) or {}).get("state", "")


def rows(bar, kind: str) -> list[dict]:
    model = bar.thread
    return [r for r in (model.get(i) for i in range(model.rowCount())) if r["kind"] == kind]


def locate(app, name: str) -> dict:
    return app.engine.call("shell.locate", {"name": name})


def trusted(app) -> bool:
    return TrustStore().approved(sha256_of((app.project.root / LINK_FILE).read_bytes()))


# -- the endpoint only asks ------------------------------------------------------------------------
def test_link_approve_from_the_endpoint_opens_a_card_and_returns_pending(app, spin):
    bar = app.prompt_bar
    res = call_endpoint(app, "link.approve", {"link": "l9", "from": SENDER}, origin="agent:cli")
    assert "error" not in res, res
    result = res["result"]
    assert result["status"] == "pending" and result["path"] == LINK_FILE
    assert result["hash"] == sha256_of(CODE.encode()) and result["target"]["view"] == "tamu"
    # nothing approved: the link waits, the trust list is untouched, the header still asks
    assert state(app) == "needs_approval" and not trusted(app)
    assert locate(app, "approve:tamu")["visible"]
    # the card names the requester (a claim, no nimitz labels came with it) and the file
    card = rows(bar, "approval")[-1]
    assert card["entryId"] == result["request"] and card["canRun"] is True
    assert card["sender"] == SENDER and card["verified"] is False and card["status"] == "asked"
    assert card["text"] == f"{LINK_FILE} for l9 -> tamu"
    assert bar.icon.asks == 1  # the icon's badge: something needs the user
    bar.panel.summon()
    spin(until=lambda: locate(app, "approvalApprove")["visible"])
    assert locate(app, f"approvalFrom:{card['entryId']}")["text"] == (
        f'"{SENDER}" (unverified) asks you to approve'
    )
    assert locate(app, "approvalOpen")["found"] and locate(app, "approvalDismiss")["found"]
    # Open shows the file as it is now
    assert CODE.strip() in bar.skillMarkdownAt(card["entryId"])


def test_a_requester_named_by_its_nimitz_labels_is_verified(app):
    labels = {"NIMITZ_ID": SENDER, "NIMITZ_ROLE": "ship"}
    res = call_endpoint(app, "link.approve", {"link": "l9", "from": SENDER, "labels": labels})
    card = rows(app.prompt_bar, "approval")[-1]
    assert res["result"]["status"] == "pending" and card["verified"] is True
    # verified or not, nothing is approved
    assert state(app) == "needs_approval" and not trusted(app)


def test_without_a_sender_the_card_names_the_callers_origin_and_connection(app):
    res = call_endpoint(app, "link.approve", {"link": "l9"}, origin="agent:s7")
    card = rows(app.prompt_bar, "approval")[-1]
    assert res["result"]["status"] == "pending"
    assert card["sender"].startswith("agent:s7 over ") and card["verified"] is False


# -- only the user's click approves ----------------------------------------------------------------
def test_the_users_click_on_the_card_approves_and_the_link_resolves(app, spin):
    bar = app.prompt_bar
    rid = call_endpoint(app, "link.approve", {"link": "l9", "from": SENDER})["result"]["request"]
    assert bar.approveLink(rid) is True
    spin(100)
    # the table is built (the code ran); the link now waits for a pick, not for approval
    assert state(app) == "waiting" and trusted(app)
    card = rows(bar, "approval")[-1]
    assert card["status"] == "approved" and card["canRun"] is False
    assert not locate(app, "approve:tamu")["visible"]
    # a second request for content the user approved already asks nothing
    res = call_endpoint(app, "link.approve", {"link": "l9", "from": SENDER})["result"]
    assert res["status"] == "approved" and res["request"] is None
    assert len(rows(bar, "approval")) == 1
    # the card's Approve is spent
    assert bar.approveLink(rid) is False


def test_an_unanswered_card_never_resolves_and_dismiss_leaves_the_link_waiting(app, spin):
    bar = app.prompt_bar
    rid = call_endpoint(app, "link.approve", {"link": "l9", "from": SENDER})["result"]["request"]
    spin(600)
    assert state(app) == "needs_approval" and not trusted(app)
    # asking again adds another card; the link still waits
    rid2 = call_endpoint(app, "link.approve", {"link": "l9", "from": "cli"})["result"]["request"]
    spin(100)
    assert rid2 != rid and state(app) == "needs_approval"
    assert bar.dismissApproval(rid) is True and bar.dismissApproval(rid) is False
    cards = {c["entryId"]: c for c in rows(bar, "approval")}
    assert cards[rid]["status"] == "dismissed" and cards[rid]["canRun"] is False
    assert cards[rid2]["canRun"] is True
    assert state(app) == "needs_approval" and not trusted(app)
    assert locate(app, "approve:tamu")["visible"]


def test_a_file_changed_since_the_request_is_not_approved_from_the_stale_card(app, spin):
    bar = app.prompt_bar
    rid = call_endpoint(app, "link.approve", {"link": "l9", "from": SENDER})["result"]["request"]
    (app.project.root / LINK_FILE).write_bytes(CHANGED.encode())
    assert bar.approveLink(rid) is False
    spin(100)
    assert state(app) == "needs_approval"
    assert not TrustStore().approved(sha256_of(CODE.encode()))
    assert not TrustStore().approved(sha256_of(CHANGED.encode()))
    card = rows(bar, "approval")[-1]
    assert card["status"] == "changed" and card["canRun"] is False
    assert any("changed since" in n["message"] for n in app.notices._items)


def test_approving_in_the_views_header_retires_the_card(app, spin):
    bar = app.prompt_bar
    rid = call_endpoint(app, "link.approve", {"link": "l9", "from": SENDER})["result"]["request"]
    assert app.controller.approveView("tamu") is True
    spin(100)
    assert state(app) == "waiting" and trusted(app)
    card = next(c for c in rows(bar, "approval") if c["entryId"] == rid)
    assert card["status"] == "approved" and card["canRun"] is False


# -- the Specto agent cannot approve --------------------------------------------------------------
def test_the_agents_tools_cannot_approve(app, aspin, spin):
    bar = app.prompt_bar
    assert not [n for n in AGENT_TOOL_NAMES if "approv" in n]
    app.turns.append(
        [
            {"tool": "link.approve", "args": {"link": "l9"}},
            {"text": "Approved it."},
        ]
    )
    bar.submit("approve the python link")
    aspin(until=lambda: not bar.busy)
    refused = app.sessions[0].calls[0]
    assert refused.is_error and "unknown tool" in refused.text
    assert state(app) == "needs_approval" and not trusted(app)
    assert rows(bar, "approval") == []  # the agent cannot even raise the card
    # the command itself, called with the prompt bar's own context, only asks
    ctx = CallContext(connection="prompt-bar", origin="agent:prompt")
    res = app.engine.call("link.approve", {"link": "l9"}, ctx)
    assert res["status"] == "pending" and state(app) == "needs_approval" and not trusted(app)
    assert rows(bar, "approval")[-1]["sender"] == "agent:prompt over prompt-bar"
