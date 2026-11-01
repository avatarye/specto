"""Python link code the prompt-bar agent writes (#168): ``write_link_code`` and
``read_link_code`` over ``<project>/.specto/links/<name>.py``, the files python links
reference (``python.path``).

Writing never approves (ADR 0006): the user's approval of the file's exact content
(the view's Approve button, ``link.approve``) stays the gate, so a link on code the
agent wrote shows ``needs_approval`` until the user looked at it, and a rewritten file
needs approval again. What is checked before a file is written: the name, the size,
that the code parses and that it defines the entry function. Each write returns a
``CodeChange`` with the file before and after, so the agent panel's Undo puts a turn's
link code back in one step, and the links on the file resolve again (they show
``needs_approval``, or run when the user had approved that exact content). No Qt here.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from specto_core.agent_tools import ToolSpec
from specto_core.errors import InvalidArgs, NotFound
from specto_core.python_links import TrustStore, sha256_of

LINKS_DIR = ".specto/links"
#: what ``PythonRef.path`` accepts for the file name
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
MAX_NAME = 64
MAX_CODE = 100_000  # bytes
DEFAULT_ENTRY = "resolve"

LINK_CODE_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "write_link_code",
            "Write the Python code of a python link as .specto/links/<name>.py (replacing "
            "the file if it exists). The code must parse and define the entry function "
            "(entry, default 'resolve'): resolve(value, items, params, root) for a per-pick "
            "link (resolves_to value), resolve(items, params, root) for table and "
            "samples. Writing never approves it: the link shows "
            "needs_approval until the user approves the file's content in the view's "
            "header, so write the file, submit the python link on it, and tell the user "
            "nothing more (the panel shows the file with Open and Undo). describe "
            "link:python gives the entry's arguments and what it returns.",
            params={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The file name without .py: letters, digits, _ and -, "
                        "at most 64 (world_to_gis); the link's python.path is "
                        ".specto/links/<name>.py",
                    },
                    "code": {"type": "string", "description": "The whole Python file."},
                    "entry": {
                        "type": "string",
                        "description": "The entry function the link calls (python.entry); "
                        "default 'resolve'.",
                    },
                },
                "required": ["name", "code"],
            },
        ),
        ToolSpec(
            "read_link_code",
            "Read the Python code of .specto/links/<name>.py: the code, whether the user "
            "approved this exact content (approved) and the links that run it with their "
            "state (resolved, failed with its error, needs_approval).",
            read_only=True,
            params={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The file name without .py."}
                },
                "required": ["name"],
            },
        ),
    )
}


@dataclass(frozen=True)
class CodeChange:
    """One link code file written: its text before (``None``: it was new) and after."""

    name: str
    path: Path
    before: str | None
    after: str
    #: the user had approved this exact content already (an undo, the same code again)
    approved: bool = False

    @property
    def rel(self) -> str:
        return f"{LINKS_DIR}/{self.name}.py"

    @property
    def note(self) -> str:
        state = "approved" if self.approved else "needs your approval"
        return f"wrote link code {self.name}: {state}"


#: the entry's positional arguments by the link's ``resolves_to``
SIGNATURES = {
    "table": "(items, params, root)",
    "samples": "(items, params, root)",
    "value": "(value, items, params, root)",
}


def entry_problem(code: str, entry: str, resolves_to: str | None = None) -> str | None:
    """Why ``code`` cannot serve a python link calling ``entry``, or ``None``: it must
    parse and define ``entry`` at the top level as a plain function taking the
    arguments the link passes (``SIGNATURES`` by ``resolves_to``; either form when no
    link says yet)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        where = f"line {e.lineno}" + (f", column {e.offset}" if e.offset else "")
        return f"the code does not parse ({where}: {e.msg})"
    defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    fn = next((n for n in reversed(defs) if n.name == entry), None)
    if fn is None:
        have = ", ".join(n.name for n in defs) or "none"
        return f"the code defines no top-level function {entry!r} (functions: {have})"
    if isinstance(fn, ast.AsyncFunctionDef):
        return f"{entry} is async; the link calls it as a plain function"
    a = fn.args
    positional = len(a.posonlyargs) + len(a.args)
    required = positional - len(a.defaults)
    modes = [resolves_to] if resolves_to in SIGNATURES else list(SIGNATURES)
    wanted = {SIGNATURES[m].count(",") + 1 for m in modes}
    if a.vararg is None and not any(required <= n <= positional for n in wanted):
        forms = " or ".join(dict.fromkeys(f"{entry}{SIGNATURES[m]}" for m in modes))
        return f"{entry} takes {positional} arguments; the link calls {forms}"
    return None


class LinkCodeStore:
    """The project's ``.specto/links``: validated writes, reads with the approval state,
    and their undo. ``engine`` (optional) is the running engine: its trust store says
    what the user approved, its link runtime re-resolves the links on a written file."""

    def __init__(self, root: Path, *, engine: Any = None, trust: TrustStore | None = None):
        self.root = Path(root)
        self.dir = self.root / LINKS_DIR
        self.engine = engine
        self._trust = trust

    @property
    def trust(self) -> TrustStore:
        if self._trust is not None:
            return self._trust
        if self.engine is not None:
            return self.engine.links.trust
        return TrustStore.default()

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.py"

    def _read(self, name: str) -> str | None:
        try:
            return self.path(name).read_bytes().decode("utf-8")
        except FileNotFoundError:
            return None

    def _name(self, name: Any) -> str:
        name = str(name or "").strip()
        name = name.removeprefix(f"{LINKS_DIR}/").removesuffix(".py")
        if not name:
            raise InvalidArgs(
                "the link code needs a name", fix="world_to_gis: letters, digits, _, -"
            )
        if len(name) > MAX_NAME:
            raise InvalidArgs(f"link code name {name[:20]!r}... is longer than {MAX_NAME}")
        if not NAME_RE.match(name):
            raise InvalidArgs(
                f"link code name {name!r} has characters a link path cannot take",
                fix="letters, digits, _ and - only, no folders: world_to_gis",
            )
        return name

    def _links_on(self, rel: str) -> list[tuple[str, dict[str, Any]]]:
        if self.engine is None:
            return []
        links = self.engine.live.data["links"]
        return [
            (lid, link)
            for lid, link in links.items()
            if link.get("kind") == "python" and (link.get("python") or {}).get("path") == rel
        ]

    def _resolve_links(self, rel: str) -> None:
        for lid, _ in self._links_on(rel):
            self.engine.links.resolve(lid)

    def _link_states(self, rel: str) -> list[dict[str, Any]]:
        out = []
        for lid, link in self._links_on(rel):
            st = self.engine.links.status(lid) or {}
            row = {"link": lid, "entry": link["python"].get("entry", DEFAULT_ENTRY)}
            row["state"] = st.get("state", "waiting")
            if st.get("message"):
                row["message"] = st["message"]
            out.append(row)
        return out

    def write(self, name: Any, code: Any, entry: Any = None) -> CodeChange:
        name = self._name(name)
        text = str(code if code is not None else "").replace("\r\n", "\n")
        if not text.strip():
            raise InvalidArgs("the code is empty", fix="write the whole file, with def resolve")
        if not text.endswith("\n"):
            text += "\n"
        data = text.encode("utf-8")
        if len(data) > MAX_CODE:
            raise InvalidArgs(
                f"the code is {len(data)} bytes, more than {MAX_CODE}",
                fix="keep link code small: read data files at resolve time instead of "
                "embedding them",
            )
        rel = f"{LINKS_DIR}/{name}.py"
        # the entry named, and every entry a link on the file calls, as that link calls it
        calls: list[tuple[str, str | None]] = [(str(entry).strip(), None)] if entry else []
        for _, link in self._links_on(rel):
            py = link["python"]
            calls.append((py.get("entry", DEFAULT_ENTRY), py.get("resolves_to")))
        for fn, mode in dict.fromkeys(calls or [(DEFAULT_ENTRY, None)]):
            why = entry_problem(text, fn, mode)
            if why is not None:
                args = SIGNATURES.get(mode or "value", SIGNATURES["value"])
                raise InvalidArgs(
                    why, fix=f"define def {fn}{args} at the top level (describe link:python)"
                )
        before = self._read(name)
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._resolve_links(rel)
        return CodeChange(name, path, before, text, approved=self.trust.approved(sha256_of(data)))

    def read(self, name: Any) -> dict[str, Any]:
        name = self._name(name)
        text = self._read(name)
        rel = f"{LINKS_DIR}/{name}.py"
        if text is None:
            have = ", ".join(p.stem for p in sorted(self.dir.glob("*.py")))
            raise NotFound(f"no link code {rel}", fix=f"link code files: {have or 'none'}")
        sha = sha256_of(text.encode("utf-8"))
        approved = self.trust.approved(sha)
        return {
            "name": name,
            "path": rel,
            "code": text,
            "sha256": sha,
            "approved": approved,
            "state": "approved" if approved else "needs_approval",
            "links": self._link_states(rel),
        }

    def undo(self, changes: list[CodeChange]) -> bool:
        """Put every file of ``changes`` (one turn's) back as it was before them.
        Refused, changing nothing, when a file no longer holds what the turn left."""
        last: dict[str, CodeChange] = {}
        first: dict[str, CodeChange] = {}
        for c in changes:
            first.setdefault(c.name, c)
            last[c.name] = c
        if any(self._read(n) != c.after for n, c in last.items()):
            return False
        for name, c in first.items():
            if c.before is None:
                self.path(name).unlink(missing_ok=True)
            else:
                self.path(name).write_bytes(c.before.encode("utf-8"))
            self._resolve_links(c.rel)
        return True

    def run(self, tool: str, args: dict[str, Any]) -> tuple[CodeChange | None, dict[str, Any]]:
        """One link code tool call: the change (``None`` for a read) and the result the
        agent reads."""
        if tool == "read_link_code":
            return None, self.read(args.get("name"))
        if tool != "write_link_code":
            raise KeyError(tool)
        change = self.write(args.get("name"), args.get("code"), args.get("entry"))
        data: dict[str, Any] = {
            "wrote": change.rel,
            "replaced": change.before is not None,
            "approved": change.approved,
            "links": [lid for lid, _ in self._links_on(change.rel)],
        }
        data["note"] = (
            "the user approved this exact content already: links on it run"
            if change.approved
            else "not approved: links on it show needs_approval until the user approves "
            "the file in the view's header; reference it from a python link as "
            f'python: {{"path": "{change.rel}", "entry": ...}}'
        )
        return change, data


__all__ = ["LINK_CODE_TOOLS", "SIGNATURES", "CodeChange", "LinkCodeStore", "entry_problem"]
