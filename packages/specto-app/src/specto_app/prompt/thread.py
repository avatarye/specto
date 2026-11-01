"""The agent panel's thread (#162): the whole conversation with the window's agent, as
entries kept in ``.specto/agent-thread.jsonl`` (one JSON object per line, appended), so
a relaunch shows the session it resumes. No Qt here.

Entries: ``user`` (a request as typed, markdown), ``agent`` (a reply, markdown;
``status`` ``working`` while it streams, then ``answered``, ``changed`` or ``failed``),
``turn`` (a prompt turn that changed the canvas: its submit label, the views and links
it touched, the diff; numbered ``t<n>`` per session), ``peer`` (a request from another
agent of the user's fleet, #187: ``sender``, ``reply_to``, ``status`` asked, queued,
run or dismissed; it opens a turn like a ``user`` entry, and that turn's ``agent`` and
``turn`` entries carry its ``sender``), ``approval`` (a ``link.approve`` request from
the endpoint, #203: ``sender`` the requester, ``link``, ``path`` and ``hash`` the file
and content it asks about, ``status`` asked, approved, dismissed or changed; only the
user's click on its Approve approves), ``skill`` (the project skills a
turn saved or deleted, #161), ``command`` (the project slash commands a turn saved or
deleted, #219), ``data`` (the agent's data files under ``.specto/`` a turn wrote or
deleted, #219), ``code`` (the link code files a turn wrote, #168), ``fleet``
(one message the agent sent to a nimitz fleet member, or tried to, #187), ``launch`` (a
program the agent launched, #186: "launched Blender 4.2 on a.blend"; status ``failed``
when Specto refused it, #192: "refused System default on setup.exe: <why>"), ``scratch``
(the scratch pad changes a turn made, #184) and
``divider`` (a session began: the thread's first turn,
or the panel's new-session button). The panel shows the latest ``PAGE_TURNS`` turns and
pages older ones in as the user scrolls back (``load_older``).

Also here: ``split_markdown`` (a reply as markdown and fenced-code blocks: the thread
renders code with a Copy button) and ``html_to_markdown`` (formatted text pasted into
the input becomes markdown, the form the agent reads).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

THREAD_FILE = "agent-thread.jsonl"
#: turns per page: shown at first, and added per scroll back
PAGE_TURNS = 20
KINDS = (
    "user",
    "peer",
    "approval",
    "agent",
    "turn",
    "skill",
    "command",
    "data",
    "code",
    "fleet",
    "launch",
    "scratch",
    "divider",
)
#: the entries that open a turn: a request typed by the user or sent by a peer
REQUESTS = ("user", "peer")


@dataclass
class Entry:
    id: int
    kind: str
    text: str = ""
    at: float = 0.0
    #: ``agent``: working, answered, changed, failed; ``fleet``: sent, failed
    status: str = ""
    #: ``turn``: the submit label; ``skill``: "saved skill specto:<name>"; ``command``:
    #: "saved command /<name>"; ``data``: "wrote .specto/layouts/<name>.json"; ``code``:
    #: "wrote link code <name>: needs your approval"; ``fleet``: "to FLEET/SQUAD" or
    #: "not sent to FLEET/SQUAD: <why>" (``text``: the message); ``launch``: "launched
    #: <label> on <file>"
    label: str = ""
    #: ``turn``: "2 views, 1 link"
    counts: str = ""
    diff: str = ""
    #: ``turn``: t<n> within its session
    number: int = 0
    #: ``skill`` / ``command`` / ``data`` / ``code``: the (last) file the turn changed
    path: str = ""
    #: ``peer`` and its turn's ``agent`` / ``turn``: who sent it (a fleet path, #187)
    sender: str = ""
    #: ``peer``: the id the sender wants quoted back
    reply_to: str = ""
    #: ``peer`` and its turn's rows: the sender was verified from the caller's nimitz
    #: labels (#190); False: only claimed
    verified: bool = False
    #: ``approval`` (#203): the python link asked about and the sha256 of the file
    #: content the requester saw (``path`` is the file)
    link: str = ""
    hash: str = ""

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", 0, 0.0) or k == "kind"}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Entry:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


class AgentThread:
    """Every entry of the project's thread; ``shown`` is the part the panel lists."""

    def __init__(self, root: Path | None, *, page_turns: int = PAGE_TURNS) -> None:
        self.path = Path(root) / ".specto" / THREAD_FILE if root is not None else None
        self.page_turns = page_turns
        self.entries: list[Entry] = self._load()
        self._next_id = max((e.id for e in self.entries), default=0) + 1
        #: index of the first shown entry
        self.first = self.page_start(len(self.entries))

    # -- reading -----------------------------------------------------------------------------
    def _load(self) -> list[Entry]:
        if self.path is None:
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: dict[int, Entry] = {}
        for line in lines:
            try:
                data = json.loads(line)
                entry = Entry.from_json(data)
            except (ValueError, TypeError):
                continue  # a torn or foreign line: skip it, keep the rest
            if entry.kind in KINDS:
                # an entry saved again later (a peer request run or dismissed) keeps its
                # place and takes the newer values
                out[entry.id] = entry
        return list(out.values())

    def page_start(self, end: int) -> int:
        """Where the page of ``page_turns`` turns ending before ``end`` starts: at a
        request (with the divider right above it, when there is one)."""
        starts = [i for i in range(end) if self.entries[i].kind in REQUESTS]
        if len(starts) <= self.page_turns:
            return 0
        i = starts[-self.page_turns]
        if i > 0 and self.entries[i - 1].kind == "divider":
            i -= 1
        return i

    @property
    def shown(self) -> list[Entry]:
        return self.entries[self.first :]

    @property
    def has_older(self) -> bool:
        return self.first > 0

    def load_older(self) -> int:
        """Show the page before the first shown entry; returns how many entries it added."""
        start = self.page_start(self.first)
        added, self.first = self.first - start, start
        return added

    # -- writing -----------------------------------------------------------------------------
    def add(self, kind: str, *, save: bool = True, **values: Any) -> Entry:
        """Append an entry (a request opens a session first when the thread has
        none). ``save=False`` holds it back from the file until ``update(save=True)``:
        a reply while it streams."""
        if kind in REQUESTS and not any(e.kind == "divider" for e in self.entries):
            self.add("divider", text="Session started")
        if kind == "turn":
            values.setdefault("number", self._turns_in_session() + 1)
        entry = Entry(id=self._next_id, kind=kind, at=values.pop("at", time.time()), **values)
        self._next_id += 1
        self.entries.append(entry)
        if save:
            self._append(entry)
        return entry

    def update(self, entry: Entry, *, save: bool = False, **values: Any) -> Entry:
        for k, v in values.items():
            setattr(entry, k, v)
        if save:
            self._append(entry)
        return entry

    def new_session(self) -> Entry:
        return self.add("divider", text="New session")

    def _turns_in_session(self) -> int:
        n = 0
        for e in reversed(self.entries):
            if e.kind == "divider":
                break
            n += e.kind == "turn"
        return n

    def _append(self, entry: Entry) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(entry.to_json(), ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[WARN] agent thread not saved: {e}")


# -- labels -------------------------------------------------------------------------------------
def divider_text(text: str, at: float, now: float | None = None) -> str:
    """``Session started today 09:12`` / ``yesterday 18:05`` / ``Oct 2 08:00``."""
    when, today = datetime.fromtimestamp(at), datetime.fromtimestamp(now or time.time())
    days = (today.date() - when.date()).days
    day = {0: "today", 1: "yesterday"}.get(days) or f"{when:%b} {when.day}"
    return f"{text} {day} {when:%H:%M}"


def patch_counts(patch: list[dict[str, Any]]) -> str:
    """ "2 views, 1 link": the distinct views and links a turn's patch touched."""
    seen: dict[str, set[str]] = {"views": set(), "links": set()}
    for op in patch:
        parts = [p for p in str(op.get("path", "")).split("/") if p]
        if len(parts) >= 2 and parts[0] in seen:
            seen[parts[0]].add(parts[1])
    out = []
    for key, noun in (("views", "view"), ("links", "link")):
        n = len(seen[key])
        if n:
            out.append(f"{n} {noun}{'' if n == 1 else 's'}")
    return ", ".join(out)


# -- markdown ----------------------------------------------------------------------------------
_FENCE = re.compile(r"^\s{0,3}(```|~~~)\s*([\w+#.-]*)\s*$")


def split_markdown(text: str) -> list[dict[str, str]]:
    """A reply as blocks: ``{"kind": "md" | "code", "text", "lang"}``; a fence still
    open (the reply streaming) runs to the end."""
    blocks: list[dict[str, str]] = []
    buf: list[str] = []
    fence: str | None = None
    lang = ""

    def flush(kind: str) -> None:
        body = "\n".join(buf)
        body = body.strip("\n") if kind == "code" else body.strip()
        if body:
            blocks.append({"kind": kind, "text": body, "lang": lang if kind == "code" else ""})
        buf.clear()

    for line in (text or "").splitlines():
        m = _FENCE.match(line)
        if fence is None and m:
            flush("md")
            fence, lang = m.group(1), m.group(2)
        elif fence is not None and m and m.group(1) == fence and not m.group(2):
            flush("code")
            fence, lang = None, ""
        else:
            buf.append(line)
    flush("code" if fence is not None else "md")
    return blocks


class _Markdown(HTMLParser):
    """Clipboard HTML (a browser's, an editor's) to markdown: paragraphs, line breaks,
    headings, bold, italic, inline code, preformatted blocks, links, nested lists and
    quotes. Everything else keeps its text."""

    SKIP = {"style", "script", "head", "title"}
    BLOCK = {"p", "div", "section", "article", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.lists: list[list[Any]] = []  # [kind, counter]
        self.skip = 0
        self.pre = 0
        self.code = 0
        self.href: list[str | None] = []
        self.quote = 0

    # helpers
    def _text(self) -> str:
        return "".join(self.out)

    def _break(self, n: int) -> None:
        """End the current line with at least ``n`` newlines (none at the start)."""
        text = self._text()
        if not text.strip():
            return
        have = len(text) - len(text.rstrip("\n"))
        if have < n:
            self.out.append("\n" * (n - have))

    def _prefix(self) -> str:
        return "> " * self.quote

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skip += 1
            return
        if tag in self.BLOCK:
            self._break(1 if self.lists else 2)
            if tag[0] == "h" and tag[1:].isdigit():
                self.out.append(self._prefix() + "#" * int(tag[1]) + " ")
        elif tag == "br":
            self.out.append("\n" + self._prefix())
        elif tag in ("b", "strong"):
            self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")
        elif tag == "code" and not self.pre:
            self.code += 1
            self.out.append("`")
        elif tag == "pre":
            self._break(2)
            self.pre += 1
            self.out.append("```\n")
        elif tag == "a":
            href = dict(attrs).get("href")
            self.href.append(href)
            if href:
                self.out.append("[")
        elif tag in ("ul", "ol"):
            if not self.lists:
                self._break(2)
            self.lists.append([tag, 0])
        elif tag == "li":
            self._break(1)
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            indent = "   " * (len(self.lists) - 1)
            bullet = f"{kind[1]}. " if kind[0] == "ol" else "- "
            self.out.append(self._prefix() + indent + bullet)
        elif tag == "blockquote":
            self._break(2)
            self.quote += 1
            self.out.append(self._prefix())

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self._break(1 if self.lists else 2)
        elif tag in ("b", "strong"):
            self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")
        elif tag == "code" and not self.pre and self.code:
            self.code -= 1
            self.out.append("`")
        elif tag == "pre" and self.pre:
            self.pre -= 1
            self.out.append("\n```")
            self._break(2)
        elif tag == "a" and self.href:
            href = self.href.pop()
            if href:
                self.out.append(f"]({href})")
        elif tag in ("ul", "ol") and self.lists:
            self.lists.pop()
            self._break(1 if self.lists else 2)
        elif tag == "blockquote" and self.quote:
            self.quote -= 1
            self._break(2)

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre:
            self.out.append(data)
            return
        text = re.sub(r"\s+", " ", data)
        if self._text().endswith(("\n", " ", "- ", "> ")) or not self._text():
            text = text.lstrip()
        self.out.append(text)


def html_to_markdown(html: str) -> str:
    parser = _Markdown()
    parser.feed(html)
    parser.close()
    text = parser._text()
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


__all__ = [
    "PAGE_TURNS",
    "REQUESTS",
    "THREAD_FILE",
    "AgentThread",
    "Entry",
    "divider_text",
    "html_to_markdown",
    "patch_counts",
    "split_markdown",
]
