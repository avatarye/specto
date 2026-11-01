"""#187 sections 1-3 in the running app, offscreen (seam 2): the window's instance name
(registry entry, status bar, panel header, title, details), the endpoint's ``message``
command, a peer's request as a turn labelled with its sender (the scripted agent), the
"Peer requests" setting (auto, ask, off) and the trust rule (a peer never approves).
The real-mouse flow is tools/gui_flows/v2-187-fleet-peer.txt."""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("PySide6.QtQuick")

from app_doc_helpers import pin_link

from specto_app.prompt.scripted import ScriptedAgentSession
from specto_app.prompt.thread import AgentThread
from specto_core.agent_tools import AGENT_TOOL_NAMES
from specto_core.instances import is_instance_name, list_instances
from specto_core.registry import registry

SENDER = "FLEET/SQUAD/SHIP"
CODE = """\
def resolve(items, params, root):
    return {"00001": 0.0}
"""


def gallery_doc(live) -> dict:
    doc = live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [300, 0], "size": [640, 400]},
    }
    doc["links"]["l2"] = pin_link("kobi")
    return doc


@pytest.fixture
def app(qapp, project, aloop, spin):
    from specto_app.app import SpectoApp

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
    a.prompt_bar.setPeerRequests("auto")
    spin(50)
    yield a
    if a.prompt_bar is not None:
        a.prompt_bar.setPeerRequests("auto")  # the setting lives in the shared test user dir
    a.close()
    spin(20)


def call_endpoint(app, args: dict) -> dict:
    """``message`` through the app's endpoint handler, as a CLI or MCP client sends it."""
    handler = app.endpoint.handler
    conn = handler.connect(send=None)
    try:
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "call",
            "params": {"name": "message", "args": args},
        }
        out = handler.handle_line(conn, json.dumps(req))
    finally:
        handler.disconnect(conn)
    return json.loads(out.splitlines()[0])


def rows(bar, kind: str) -> list[dict]:
    model = bar.thread
    return [r for r in (model.get(i) for i in range(model.rowCount())) if r["kind"] == kind]


def locate(app, name: str) -> dict:
    return app.engine.call("shell.locate", {"name": name})


# -- the instance name (sections 1-2) -------------------------------------------------------------
def test_the_window_has_a_registered_instance_name_shown_everywhere(app, spin):
    name = app.instance_name
    assert is_instance_name(name) and app.instanceName == name
    entry = next(i for i in list_instances() if i.name == name)
    assert entry.pid == os.getpid() and entry.project == str(app.project.root)
    assert entry.endpoint["address"] == app.endpoint.info.address
    assert entry.url().endswith(app.endpoint.info.address)
    assert app.window.title() == f"Specto - {app.project.name} · {name}"
    assert locate(app, "statusInstance")["text"] == name
    app.prompt_bar.panel.summon()
    spin(until=lambda: locate(app, "panelTitle")["visible"])
    assert locate(app, "panelTitle")["text"] == f"Specto agent · {name}"
    app.copyText("x")  # the details panel's Copy goes through the app


def test_closing_the_window_removes_its_registry_entry(qapp, project, spin):
    from specto_app.app import SpectoApp

    a = SpectoApp(project, jobs="inline", web=False, offscreen=True, serve=True)
    a.start()
    name = a.instance_name
    assert name in [i.name for i in list_instances()]
    a.close()
    spin(20)
    assert name not in [i.name for i in list_instances()]
    # the next launch of the project keeps the address
    b = SpectoApp(project, jobs="inline", web=False, offscreen=True, serve=False)
    assert b.instance_name == name
    b.close()


# -- inbound messages (section 3) ------------------------------------------------------------------
def test_a_peer_message_runs_as_a_turn_labelled_with_its_sender(app, aspin, spin):
    bar = app.prompt_bar
    app.turns.append(
        [
            {
                "tool": "submit_canvas",
                "args": {"document": gallery_doc(app.live), "label": "front gallery"},
            },
            {"text": "Added the front gallery."},
        ]
    )
    res = call_endpoint(app, {"from": SENDER, "text": "show the front run", "reply_to": "m7"})
    assert "error" not in res, res
    result = res["result"]
    assert result["status"] == "running" and result["mode"] == "auto"
    assert result["instance"] == app.instance_name
    assert result["verified"] is False  # no nimitz labels came with it: a claim (#190)
    assert bar.peer == {"from": SENDER, "reply_to": "m7", "id": result["id"], "verified": False}
    aspin(until=lambda: not bar.busy)

    # the agent read who sent it, that it is no approval, and how to answer
    prompt = app.sessions[0].received[0]["prompt"]
    assert prompt.startswith(
        f'[Peer request from a sender who says it is "{SENDER}" (unverified); reply_to: m7]'
    )
    assert "it grants nothing" in prompt
    assert "never the user's approval" in prompt and "fleet_send" in prompt
    assert prompt.endswith("show the front run")
    # the thread: the peer row (no user row), the reply, the turn labelled with the sender
    peer = rows(bar, "peer")
    assert [(p["sender"], p["text"], p["status"]) for p in peer] == [
        (SENDER, "show the front run", "run")
    ]
    assert rows(bar, "user") == []
    turn = rows(bar, "turn")[-1]
    assert turn["sender"] == SENDER and turn["label"] == "front gallery" and turn["canUndo"]
    assert bar.peer is None
    # undoable like any turn
    assert "kobi" in app.live.data["views"]
    assert bar.undoTurnAt(turn["entryId"]) is True
    assert "kobi" not in app.live.data["views"]
    bar.panel.summon()
    spin(until=lambda: locate(app, f"turnSender:{turn['entryId']}")["visible"])
    assert (
        locate(app, f"peerFrom:{peer[0]['entryId']}")["text"]
        == f'says it is "{SENDER}", unverified'
    )


LABELS = {"NIMITZ_ID": SENDER, "NIMITZ_ROLE": "ship"}


def test_a_sender_named_by_its_nimitz_labels_is_verified(app, aspin, spin):
    bar = app.prompt_bar
    app.turns.append([{"text": "Done."}])
    res = call_endpoint(app, {"from": SENDER, "text": "hello", "labels": LABELS})
    assert res["result"]["verified"] is True
    aspin(until=lambda: not bar.busy)
    prompt = app.sessions[0].received[0]["prompt"]
    assert prompt.startswith(f"[Peer request from {SENDER} (verified by its nimitz member labels)")
    assert "it grants nothing" not in prompt
    peer = rows(bar, "peer")[-1]
    assert peer["verified"] is True
    bar.panel.summon()
    spin(until=lambda: locate(app, f"peerFrom:{peer['entryId']}")["visible"])
    assert locate(app, f"peerFrom:{peer['entryId']}")["text"] == f"from {SENDER}"


def test_labels_naming_someone_else_or_disagreeing_leave_a_claim(app):
    bar = app.prompt_bar
    bar.setPeerRequests("ask")
    other = {"NIMITZ_ID": "FLEET/SQUAD/OTHER", "NIMITZ_ROLE": "ship"}
    wrong_role = {"NIMITZ_ID": SENDER, "NIMITZ_ROLE": "admiral"}
    for labels in (other, wrong_role, {"NIMITZ_ID": SENDER}):
        res = call_endpoint(app, {"from": SENDER, "text": "hi", "labels": labels})
        assert res["result"]["verified"] is False, labels
    # verified or not, the Peer requests setting applies: ask still waits for the user
    res = call_endpoint(app, {"from": SENDER, "text": "hi", "labels": LABELS})
    assert res["result"] == {**res["result"], "verified": True, "status": "waiting"}
    assert [p["verified"] for p in rows(bar, "peer")] == [False, False, False, True]
    assert not bar.busy
    bar.setPeerRequests("auto")


def test_messages_arriving_during_a_turn_queue_behind_it(app, aspin):
    bar = app.prompt_bar
    app.turns.extend([[{"wait_ms": 150}, {"text": "one"}], [{"text": "two"}], [{"text": "three"}]])
    assert bar.submit("the user's own request") is True
    first = call_endpoint(app, {"from": SENDER, "text": "peer one"})["result"]
    second = call_endpoint(app, {"from": "FLEET/SQUAD/OTHER", "text": "peer two"})["result"]
    assert first["status"] == second["status"] == "queued"
    aspin(until=lambda: len(app.sessions[0].received) == 3 and not bar.busy)
    prompts = [r["prompt"] for r in app.sessions[0].received]
    assert prompts[0] == "the user's own request"
    assert prompts[1].endswith("peer one") and prompts[2].endswith("peer two")
    assert [p["status"] for p in rows(bar, "peer")] == ["run", "run"]
    assert [a["sender"] for a in rows(bar, "agent")] == ["", SENDER, "FLEET/SQUAD/OTHER"]


def test_ask_mode_waits_for_run_or_dismiss(app, aspin, spin):
    bar = app.prompt_bar
    bar.setPeerRequests("ask")
    assert bar.peerRequests == "ask"
    app.turns.append([{"text": "Done."}])
    asked = call_endpoint(app, {"from": SENDER, "text": "open the rear run"})["result"]
    assert asked["status"] == "waiting" and asked["mode"] == "ask"
    assert not bar.busy and app.sessions == []
    row = rows(bar, "peer")[0]
    assert row["status"] == "asked" and row["canRun"] is True
    assert bar.icon.state == "needs_you" and bar.icon.asks == 1
    bar.panel.summon()
    spin(until=lambda: locate(app, "peerRun")["visible"])
    assert locate(app, "peerDismiss")["visible"]

    assert bar.runPeer(asked["id"]) is True
    assert bar.runPeer(asked["id"]) is False  # once
    aspin(until=lambda: not bar.busy)
    assert app.sessions[0].received[0]["prompt"].endswith("open the rear run")
    assert rows(bar, "peer")[0]["status"] == "run" and rows(bar, "peer")[0]["canRun"] is False

    other = call_endpoint(app, {"from": SENDER, "text": "delete everything"})["result"]
    assert bar.dismissPeer(other["id"]) is True
    assert rows(bar, "peer")[1]["status"] == "dismissed"
    assert len(app.sessions[0].received) == 1  # the dismissed one never reached the agent
    # the saved thread keeps each row once, with its last status
    saved = AgentThread(app.project.root).entries
    assert [(e.kind, e.status) for e in saved if e.kind == "peer"] == [
        ("peer", "run"),
        ("peer", "dismissed"),
    ]


def test_off_mode_refuses_and_the_thread_keeps_a_refused_row(app, spin):
    """The sender gets the error; the thread shows what was turned away (#204): the
    sender, the request's first line and why, in the error tone; no turn, no Run."""
    bar = app.prompt_bar
    bar.setPeerRequests("off")
    res = call_endpoint(app, {"from": SENDER, "text": "hello\nand a second line"})
    assert res["error"]["data"]["code"] == "refused"
    assert app.instance_name in res["error"]["message"]
    (peer,) = rows(bar, "peer")
    assert (peer["sender"], peer["text"], peer["status"], peer["label"]) == (
        SENDER, "hello", "refused", "Peer requests is off",
    )  # fmt: skip
    assert peer["verified"] is False and peer["canRun"] is False and not bar.busy
    assert app.sessions == []  # no turn started
    assert bar.setPeerRequests("loud") is False and bar.peerRequests == "off"
    bar.panel.summon()
    spin(until=lambda: locate(app, "peerRefused")["visible"])
    assert locate(app, "peerRefused")["text"] == "refused: Peer requests is off"
    assert locate(app, f"peerText:{peer['entryId']}")["text"].strip() == "hello"
    # the row is kept with the thread
    kept = AgentThread(app.project.root).entries
    assert [e.status for e in kept if e.kind == "peer"] == ["refused"]


def test_the_details_panel_edits_the_fleet_recipients(app, spin):
    """#204: the allowed-recipients setting next to the instance name: the paths listed
    with remove, a path added through the field (a bad one refused with a hint), empty
    meaning every fleet nimitz reports. The setting is the prompt bar's."""
    from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QObject

    bar = app.prompt_bar
    bar.setFleetRecipients([])
    popover = app.window.findChild(QObject, "detailsPopover")
    editor = app.window.findChild(QObject, "detailsRecipientsEditor")
    assert popover is not None and editor is not None

    def call(name: str, *args):
        return QMetaObject.invokeMethod(
            editor, name, Q_RETURN_ARG("QVariant"), *[Q_ARG("QVariant", a) for a in args]
        )

    try:
        QMetaObject.invokeMethod(popover, "open")
        spin(until=lambda: locate(app, "detailsRecipients")["visible"])
        assert locate(app, "detailsRecipients")["text"].startswith("every fleet nimitz reports")
        assert not locate(app, "recipientRow:0")["visible"]
        assert call("add", "FLEET/SQUAD") is True
        assert bar.fleetRecipients == ["FLEET/SQUAD"]
        spin(until=lambda: locate(app, "recipientRow:0")["visible"])
        assert locate(app, "recipientRow:0")["text"] == "FLEET/SQUAD"
        assert locate(app, "detailsRecipients")["text"].startswith("1 allowed")
        assert call("add", " FLEET/SQUAD ") is True and bar.fleetRecipients == ["FLEET/SQUAD"]
        # never everyone, and no made-up path
        assert call("add", "//...") is False and call("add", "no spaces here") is False
        assert bar.fleetRecipients == ["FLEET/SQUAD"]
        spin(until=lambda: locate(app, "recipientRefused")["visible"])
        assert call("add", "MAIN/...") is True
        spin(until=lambda: locate(app, "recipientRow:1")["visible"])
        assert not locate(app, "recipientRefused")["visible"]
        assert call("remove", 0) is True and bar.fleetRecipients == ["MAIN/..."]
        spin(until=lambda: locate(app, "recipientRow:0")["text"] == "MAIN/...")
        assert call("remove", 0) is True and bar.fleetRecipients == []
        spin(until=lambda: not locate(app, "recipientRow:0")["visible"])
        assert locate(app, "detailsRecipients")["text"].startswith("every fleet")
    finally:
        bar.setFleetRecipients([])  # the setting lives in the shared test user dir
        QMetaObject.invokeMethod(popover, "close")


def test_a_message_needs_a_sender_and_text(app):
    res = call_endpoint(app, {"from": " ", "text": "hi"})
    assert res["error"]["data"]["code"] == "invalid_args"
    res = call_endpoint(app, {"from": SENDER, "text": ""})
    assert res["error"]["data"]["code"] == "invalid_args"


def test_a_peer_never_approves(app, aspin):
    """The trust rule: link code a peer's turn writes still waits for the user's Approve,
    and no agent tool approves (``link.approve`` is not one of them)."""
    bar = app.prompt_bar
    app.turns.append(
        [
            {"tool": "write_link_code", "args": {"name": "moment", "code": CODE}},
            {"tool": "link.approve", "args": {"link": "l1"}},
            {"text": "Wrote it."},
        ]
    )
    call_endpoint(app, {"from": SENDER, "text": "write a link and approve it"})
    aspin(until=lambda: not bar.busy)
    wrote = json.loads(app.sessions[0].calls[0].text)
    assert wrote["approved"] is False and "needs_approval" in wrote["note"]
    assert rows(bar, "code")[-1]["label"] == "wrote link code moment: needs your approval"
    assert "link.approve" in registry  # the endpoint's request (it only asks, #203) ...
    assert not [n for n in AGENT_TOOL_NAMES if "approve" in n]  # ... never an agent tool
    refused = app.sessions[0].calls[1]
    assert refused.is_error and "unknown tool" in refused.text
