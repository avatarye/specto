"""The agent panel's thread, headless (#162): the whole session as entries kept in
``.specto/agent-thread.jsonl``, the latest turns shown first and older ones paged in as
the user scrolls back, a new session behind a divider, replies split into markdown and
code blocks, and formatted paste turned into markdown. No Qt here."""

from __future__ import annotations

import json

from specto_app.prompt.thread import (
    THREAD_FILE,
    AgentThread,
    divider_text,
    html_to_markdown,
    patch_counts,
    split_markdown,
)


def ask(thread: AgentThread, text: str, answer: str = "ok") -> None:
    thread.add("user", text=text)
    thread.add("agent", text=answer, status="answered")


def test_a_turn_is_kept_in_the_project_and_comes_back_on_the_next_launch(tmp_path):
    thread = AgentThread(tmp_path)
    assert thread.shown == [] and thread.has_older is False
    ask(thread, "what is under my mouse?", "The file tree.")
    thread.add("turn", label="front gallery", counts="1 view", diff="+ view kobi: gallery")

    lines = (tmp_path / ".specto" / THREAD_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["kind"] for line in lines] == ["divider", "user", "agent", "turn"]

    again = AgentThread(tmp_path)
    assert [(e.kind, e.text or e.label) for e in again.shown] == [
        ("divider", "Session started"),
        ("user", "what is under my mouse?"),
        ("agent", "The file tree."),
        ("turn", "front gallery"),
    ]
    assert again.shown[3].number == 1 and again.shown[3].diff == "+ view kobi: gallery"


def test_the_first_turn_of_a_thread_opens_a_session_and_turns_are_numbered_per_session(
    tmp_path,
):
    thread = AgentThread(tmp_path)
    ask(thread, "one")
    first = thread.add("turn", label="a")
    second = thread.add("turn", label="b")
    assert thread.shown[0].kind == "divider"
    assert (first.number, second.number) == (1, 2)

    divider = thread.new_session()
    assert divider.kind == "divider" and divider.text == "New session"
    ask(thread, "two")
    assert thread.add("turn", label="c").number == 1  # numbering restarts
    # the old session stays readable above the divider
    assert [e.label for e in thread.shown if e.kind == "turn"] == ["a", "b", "c"]


def test_a_streaming_reply_is_saved_once_when_it_is_final(tmp_path):
    thread = AgentThread(tmp_path)
    thread.add("user", text="hi")
    reply = thread.add("agent", status="working", save=False)
    thread.update(reply, text="Hel")
    thread.update(reply, text="Hello", status="answered", save=True)
    saved = [json.loads(x) for x in (tmp_path / ".specto" / THREAD_FILE).read_text().splitlines()]
    assert [(e["kind"], e.get("text")) for e in saved][-1] == ("agent", "Hello")
    assert len(saved) == 3  # divider, user, the final reply


def test_older_turns_are_paged_in_twenty_at_a_time(tmp_path):
    thread = AgentThread(tmp_path)
    for i in range(45):
        ask(thread, f"q{i}")
    again = AgentThread(tmp_path)
    users = [e.text for e in again.shown if e.kind == "user"]
    assert users == [f"q{i}" for i in range(25, 45)]  # the latest 20 turns
    assert again.has_older is True

    assert again.load_older() > 0
    users = [e.text for e in again.shown if e.kind == "user"]
    assert users[0] == "q5" and len(users) == 40
    again.load_older()
    assert again.shown[0].kind == "divider" and again.has_older is False
    assert again.load_older() == 0


def test_a_broken_line_in_the_thread_file_is_skipped(tmp_path):
    thread = AgentThread(tmp_path)
    ask(thread, "one")
    path = tmp_path / ".specto" / THREAD_FILE
    path.write_text(path.read_text() + "{not json\n", encoding="utf-8")
    ask(AgentThread(tmp_path), "two")
    assert [e.text for e in AgentThread(tmp_path).shown if e.kind == "user"] == ["one", "two"]


def test_a_reply_splits_into_markdown_and_fenced_code_blocks():
    text = (
        "Frames `00017` and `00093` have no file in `rear/`.\n\n"
        '```json\n{ "frame": 17 }\n```\n'
        "More in [the notes](notes/a.md)."
    )
    assert split_markdown(text) == [
        {"kind": "md", "text": "Frames `00017` and `00093` have no file in `rear/`.", "lang": ""},
        {"kind": "code", "text": '{ "frame": 17 }', "lang": "json"},
        {"kind": "md", "text": "More in [the notes](notes/a.md).", "lang": ""},
    ]
    # a fence still open while the reply streams is code to the end
    assert split_markdown("see:\n```py\nx = 1") == [
        {"kind": "md", "text": "see:", "lang": ""},
        {"kind": "code", "text": "x = 1", "lang": "py"},
    ]
    assert split_markdown("") == []


def test_pasted_formatting_becomes_markdown():
    html = (
        "<html><body><!--StartFragment--><p>Add a <b>depth</b> view under <i>zila</i>:</p>"
        "<ul><li>colormap: <code>turbo</code></li><li>range: 0.5 to 40 m</li></ul>"
        '<p>See <a href="https://example.com/x">the docs</a>.</p>'
        "<pre>a  =  1\nb = 2</pre><!--EndFragment--></body></html>"
    )
    assert html_to_markdown(html) == (
        "Add a **depth** view under *zila*:\n\n"
        "- colormap: `turbo`\n"
        "- range: 0.5 to 40 m\n\n"
        "See [the docs](https://example.com/x).\n\n"
        "```\na  =  1\nb = 2\n```"
    )
    assert html_to_markdown("<ol><li>one</li><li>two<ul><li>inner</li></ul></li></ol>") == (
        "1. one\n2. two\n   - inner"
    )
    assert html_to_markdown("<h2>Title</h2>plain<br>next") == "## Title\n\nplain\nnext"


def test_a_turn_row_counts_the_views_and_links_its_patch_touched():
    patch = [
        {"op": "add", "path": "/views/kobi", "value": {}},
        {"op": "replace", "path": "/views/tamu/layout/position", "value": [0, 0]},
        {"op": "add", "path": "/links/l3", "value": {}},
        {"op": "replace", "path": "/root_view", "value": "kobi"},
    ]
    assert patch_counts(patch) == "2 views, 1 link"
    assert patch_counts([{"op": "add", "path": "/views/kobi", "value": {}}]) == "1 view"
    assert patch_counts([]) == ""


def test_a_divider_says_when_its_session_started():
    from datetime import datetime

    now = datetime(2026, 10, 24, 15, 0).timestamp()
    at = datetime(2026, 10, 24, 9, 12).timestamp()
    assert divider_text("Session started", at, now) == "Session started today 09:12"
    yesterday = datetime(2026, 10, 23, 18, 5).timestamp()
    assert divider_text("New session", yesterday, now) == "New session yesterday 18:05"
    older = datetime(2026, 10, 2, 8, 0).timestamp()
    assert divider_text("New session", older, now) == "New session Oct 2 08:00"


def test_a_peer_request_opens_a_turn_and_is_saved_once_with_its_last_status(tmp_path):
    """#187: a peer row is a request like a user one (it opens the session and a page);
    saved again when it runs or is dismissed, it comes back once, as it ended."""
    thread = AgentThread(tmp_path, page_turns=1)
    peer = thread.add(
        "peer", text="show the rear run", sender="FLEET/SQUAD/SHIP", reply_to="m1", status="asked"
    )
    assert [e.kind for e in thread.entries] == ["divider", "peer"]
    thread.update(peer, status="run", save=True)
    thread.add("agent", text="Done.", status="answered", sender="FLEET/SQUAD/SHIP")
    ask(thread, "and the front?")
    again = AgentThread(tmp_path, page_turns=1)
    peers = [e for e in again.entries if e.kind == "peer"]
    assert [(p.status, p.sender, p.reply_to) for p in peers] == [("run", "FLEET/SQUAD/SHIP", "m1")]
    assert [e.kind for e in again.entries] == ["divider", "peer", "agent", "user", "agent"]
    assert again.shown[0].kind == "user"  # the latest page starts at the user's request
    assert again.load_older() == 3 and again.shown[1].kind == "peer"
