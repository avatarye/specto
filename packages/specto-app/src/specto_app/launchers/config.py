"""Launcher configuration (#186): the TOML files, their validation, placeholder expansion
and the match rules. No Qt, no processes here.

A **launcher** opens files with an external program the user configured (an isolated
Blender build the system does not know). Two files hold them, both optional: the
machine's ``launchers.toml`` in the user data dir (``SPECTO_USER_DIR`` overrides it) and
the project's ``.specto/launchers.toml``, whose entries merge over the machine's key by
key, by ``id`` (a project says ``id = "blender-4.2"``, ``default = true`` and nothing
else). Every ``[[launcher]]`` table::

    id = "blender-4.2"                # letters, digits, . _ -
    label = "Blender 4.2 LTS"         # default: the id
    command = "D:/Tools/blender-4.2.3/blender.exe"   # a path, or a program on PATH
    args = ["{path}"]                 # default ["{path}"], or ["{paths}"] with each = false
    match = ["*.blend", "*.blend1"]   # file-name globs, case-insensitive; "*/" a folder;
                                      # default ["*"] (any file)
    each = true                       # one process per file; false: one with all of them
    cwd = "{dir}"                     # optional
    env = { BLENDER_USER_CONFIG = "D:/Tools/blender-4.2.3/config" }   # optional
    default = true                    # the preferred launcher for its matches

Placeholders, in ``args``, ``cwd`` and ``env`` values: ``{path}`` (absolute, native
separators), ``{dir}`` (its folder), ``{name}``, ``{stem}`` (the name without its last
extension), ``{project}`` (the project folder), ``{paths}`` (every file: a whole
argument of its own, one argument per file); ``{{`` and ``}}`` are literal braces. With
``each = false`` the single-file placeholders name the first file. Expansion builds an
argv list, never a command line a shell reads, so a space, ``&`` or ``%`` in a path is
just a character of one argument.

A problem names its file and line and carries a fix-it; a launcher with a problem is
left out and the others still load.
"""

from __future__ import annotations

import fnmatch
import os
import re
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MACHINE_FILE = "launchers.toml"
PROJECT_FILE = ".specto/launchers.toml"
KEYS = ("id", "label", "command", "args", "match", "each", "cwd", "env", "default")
PLACEHOLDERS = ("path", "paths", "dir", "name", "stem", "project")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
#: what the system default would run rather than open: refused for the menu and the agent
REFUSED_EXTS = frozenset(
    {
        ".appref-ms", ".application", ".bat", ".cmd", ".com", ".cpl", ".exe", ".gadget",
        ".hta", ".inf", ".jar", ".js", ".jse", ".lnk", ".msc", ".msi", ".msp", ".pif",
        ".ps1", ".ps1xml", ".psc1", ".psd1", ".psm1", ".py", ".pyc", ".pyw", ".pyz",
        ".reg", ".scf", ".scr", ".sct", ".sh", ".url", ".vb", ".vbe", ".vbs", ".ws",
        ".wsc", ".wsf", ".wsh",
    }
)  # fmt: skip
#: commands Windows runs through cmd.exe, which reads the arguments again
BATCH_EXTS = frozenset({".bat", ".cmd"})
#: characters cmd.exe acts on: refused in a batch launcher's arguments
BATCH_UNSAFE = frozenset('&|<>^%!"()\r\n')
TOML_FIX = (
    "TOML strings in double quotes treat \\ as an escape: write Windows paths with / "
    "(\"D:/Tools/blender.exe\") or in single quotes ('D:\\Tools\\blender.exe')"
)

_TOKEN = re.compile(r"\{\{|\}\}|\{([^{}]*)\}|[{}]")
_HEADER = re.compile(r"^\s*\[\[\s*launcher\s*\]\]")
_LINE_OF = re.compile(r"at line (\d+)")


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a launcher file: where, what, and how to fix it."""

    file: str
    line: int
    message: str
    fix: str = ""

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file

    def text(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass(frozen=True)
class Invocation:
    """One process to start: the argv, its working folder ("" keeps Specto's) and the
    variables it adds to the environment."""

    argv: tuple[str, ...]
    cwd: str = ""
    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Launcher:
    id: str
    label: str
    command: str
    args: tuple[str, ...]
    match: tuple[str, ...] = ("*",)
    each: bool = True
    cwd: str = ""
    env: tuple[tuple[str, str], ...] = ()
    default: bool = False
    #: 2: the project's file made it the default, 1: the machine's, 0: not a default
    default_rank: int = 0
    #: ``machine``, ``project``, or ``machine+project`` (merged)
    source: str = "machine"
    #: where it was defined (the last file that set any of its keys) and the line
    file: str = ""
    line: int = 0

    def matches(self, path: str, is_dir: bool = False) -> bool:
        name = os.path.basename(os.path.normpath(path)) if path else ""
        if not name:
            return False
        for pat in self.match:
            if pat.endswith("/") or pat.endswith("\\"):
                if is_dir and fnmatch.fnmatch(name.lower(), pat[:-1].lower()):
                    return True
            elif not is_dir and fnmatch.fnmatch(name.lower(), pat.lower()):
                return True
        return False

    def matches_all(self, items: Sequence[tuple[str, bool]]) -> bool:
        return bool(items) and all(self.matches(p, d) for p, d in items)

    def invocations(self, paths: Sequence[str], project: str = "") -> list[Invocation]:
        """The processes this launcher starts for ``paths``: one per file with ``each``,
        else one with all of them."""
        groups = [[p] for p in paths] if self.each else [list(paths)]
        return [self._invocation(g, project) for g in groups if g]

    def _invocation(self, paths: list[str], project: str) -> Invocation:
        values = placeholder_values(paths, project)
        argv = [self.command]
        for arg in self.args:
            if arg.strip() == "{paths}":
                argv.extend(values["paths"])
            else:
                argv.append(expand(arg, values))
        cwd = expand(self.cwd, values) if self.cwd else ""
        env = tuple((k, expand(v, values)) for k, v in self.env)
        return Invocation(tuple(argv), cwd, env)

    def describe(self) -> dict[str, Any]:
        """What the agent's ``launchers`` tool lists."""
        out: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "match": list(self.match),
            "each": self.each,
            "default": self.default,
            "source": self.source,
        }
        return out


def placeholder_values(paths: Sequence[str], project: str = "") -> dict[str, Any]:
    first = os.path.normpath(paths[0]) if paths else ""
    name = os.path.basename(first)
    return {
        "path": first,
        "paths": [os.path.normpath(p) for p in paths],
        "dir": os.path.dirname(first),
        "name": name,
        "stem": os.path.splitext(name)[0],
        "project": os.path.normpath(project) if project else "",
    }


def placeholder_problem(text: str, *, whole_paths: bool = False) -> str | None:
    """Why ``text`` does not expand (an unknown name, a stray brace, ``{paths}`` inside
    a longer string), or None. ``whole_paths``: ``{paths}`` may stand as the whole text."""
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if tok in ("{{", "}}"):
            continue
        if tok in ("{", "}"):
            return f"a stray {tok!r} in {text!r} (write {tok * 2} for a literal brace)"
        name = m.group(1).strip()
        if name not in PLACEHOLDERS:
            return f"unknown placeholder {{{name}}} in {text!r}"
        if name == "paths" and not (whole_paths and text.strip() == "{paths}"):
            return f"{{paths}} must be a whole argument of its own, not part of {text!r}"
    return None


def expand(text: str, values: dict[str, Any]) -> str:
    """``text`` with its placeholders filled in (``placeholder_problem`` said it parses)."""

    def sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        if tok == "{{":
            return "{"
        if tok == "}}":
            return "}"
        if m.group(1) is None:
            return tok
        value = values[m.group(1).strip()]
        return " ".join(value) if isinstance(value, list) else str(value)

    return _TOKEN.sub(sub, text)


def refused_reason(path: str) -> str | None:
    """Why the system default may not open ``path`` (it would run it), or None."""
    ext = os.path.splitext(path)[1].lower()
    if ext in REFUSED_EXTS:
        return f"{os.path.basename(path)} is a program or script ({ext}): opening it would run it"
    return None


def batch_problem(command: str, argv: Iterable[str]) -> str | None:
    """A ``.bat`` / ``.cmd`` command runs through cmd.exe, which acts on ``& | % ...`` in
    its arguments whatever the quoting: such an argument is refused."""
    if os.path.splitext(command)[1].lower() not in BATCH_EXTS:
        return None
    for arg in argv:
        bad = sorted(set(arg) & BATCH_UNSAFE)
        if bad:
            return (
                f"the argument {arg!r} has {''.join(bad)!r}, which cmd.exe would act on "
                f"(the command {os.path.basename(command)} runs through it)"
            )
    return None


# -- files ------------------------------------------------------------------------------------
@dataclass
class _Raw:
    """One ``[[launcher]]`` table as read, with where it is."""

    data: dict[str, Any]
    file: str
    line: int
    lines: dict[str, int]
    level: str


def read_file(path: Path, level: str) -> tuple[list[_Raw], list[Problem]]:
    """The launcher tables of one file (none when it does not exist), each checked on
    its own, and the file's problems."""
    file = str(path)
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except FileNotFoundError:
        return [], []
    except (OSError, UnicodeDecodeError) as e:
        return [], [Problem(file, 0, f"cannot read the file: {e}", "save it as UTF-8 text")]
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        m = _LINE_OF.search(str(e))
        return [], [Problem(file, int(m.group(1)) if m else 0, f"not valid TOML: {e}", TOML_FIX)]
    problems: list[Problem] = []
    for key in data:
        if key != "launcher":
            problems.append(
                Problem(
                    file,
                    _key_line(text.splitlines(), 0, None, key),
                    f"unknown top-level key {key!r}",
                    "every launcher is a [[launcher]] table",
                )
            )
    tables = data.get("launcher", [])
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        problems.append(
            Problem(file, 1, "'launcher' is not a list of tables", "write each one as [[launcher]]")
        )
        return [], problems
    lines = text.splitlines()
    heads = [i + 1 for i, line in enumerate(lines) if _HEADER.match(line)]
    raws: list[_Raw] = []
    for n, table in enumerate(tables):
        start = heads[n] if n < len(heads) else 0
        end = heads[n + 1] if n + 1 < len(heads) else None
        keys = {k: _key_line(lines, start, end, k) or start for k in table}
        raw = _Raw(dict(table), file, start, keys, level)
        bad = _check_keys(raw)
        if bad:
            problems.extend(bad)
        else:
            raws.append(raw)
    return raws, problems


def _key_line(lines: list[str], start: int, end: int | None, key: str) -> int:
    pat = re.compile(rf"^\s*\"?{re.escape(key)}\"?\s*=")
    for i in range(start, len(lines) if end is None else end - 1):
        if pat.match(lines[i]):
            return i + 1
    return 0


def _check_keys(raw: _Raw) -> list[Problem]:
    """The types and placeholders of one table's keys (completeness waits for the merge)."""
    out: list[Problem] = []
    d = raw.data

    def bad(key: str, message: str, fix: str) -> None:
        out.append(Problem(raw.file, raw.lines.get(key, raw.line), message, fix))

    for key in d:
        if key not in KEYS:
            bad(key, f"unknown key {key!r} in a launcher", f"the keys are: {', '.join(KEYS)}")
    lid = d.get("id")
    if not isinstance(lid, str) or not ID_RE.match(lid):
        bad(
            "id",
            "a launcher needs an id" if lid is None else f"the id {lid!r} is not a plain name",
            'letters, digits, . _ and - (id = "blender-4.2")',
        )
    for key in ("label", "command", "cwd"):
        if key in d and (not isinstance(d[key], str) or (key != "cwd" and not d[key].strip())):
            bad(key, f"{key} must be a non-empty string", f'{key} = "..."')
    for key in ("each", "default"):
        if key in d and not isinstance(d[key], bool):
            bad(key, f"{key} must be true or false", f"{key} = true")
    for key in ("args", "match"):
        if key in d and (
            not isinstance(d[key], list) or not all(isinstance(a, str) for a in d[key])
        ):
            example = '["{path}"]' if key == "args" else '["*.blend"]'
            bad(key, f"{key} must be a list of strings", f"{key} = {example}")
    if isinstance(d.get("match"), list) and not d["match"]:
        bad("match", "match is empty: the launcher would match nothing", 'match = ["*.blend"]')
    env = d.get("env")
    if env is not None and (
        not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values())
    ):
        bad("env", "env must be a table of strings", 'env = { NAME = "value" }')
    if isinstance(d.get("args"), list):
        for a in d["args"]:
            why = isinstance(a, str) and placeholder_problem(a, whole_paths=True)
            if why:
                bad("args", why, f"placeholders: {', '.join('{' + p + '}' for p in PLACEHOLDERS)}")
    for key, texts in (("cwd", [d.get("cwd")]), ("env", list((env or {}).values()))):
        for t in texts:
            why = isinstance(t, str) and placeholder_problem(t)
            if why:
                bad(key, why, "{paths} only as an argument; {{ and }} for literal braces")
    return out


def merge(machine: list[_Raw], project: list[_Raw]) -> tuple[list[Launcher], list[Problem]]:
    """The project's tables over the machine's by id, key by key; every merged launcher
    complete and consistent, or left out with a problem. Machine order first, then the
    project's new ones."""
    merged: dict[str, dict[str, Any]] = {}
    problems: list[Problem] = []
    for level, raws in (("machine", machine), ("project", project)):
        seen: set[str] = set()
        for raw in raws:
            lid = raw.data["id"]
            if lid in seen:
                problems.append(
                    Problem(raw.file, raw.line, f"a second launcher with the id {lid!r}",
                            "give each launcher its own id; the first one is used")
                )  # fmt: skip
                continue
            seen.add(lid)
            entry = merged.setdefault(lid, {"data": {}, "levels": [], "raws": []})
            entry["data"].update(raw.data)
            entry["levels"].append(level)
            entry["raws"].append(raw)
            if "default" in raw.data:
                entry["default_level"] = level
    out: list[Launcher] = []
    for lid, entry in merged.items():
        launcher, problem = _build(lid, entry)
        if problem is not None:
            problems.append(problem)
        else:
            out.append(launcher)
    return out, problems


def _build(lid: str, entry: dict[str, Any]) -> tuple[Launcher, Problem | None]:
    d = entry["data"]
    raws: list[_Raw] = entry["raws"]
    last = raws[-1]

    def where(key: str) -> tuple[str, int]:
        for raw in reversed(raws):
            if key in raw.data:
                return raw.file, raw.lines.get(key, raw.line)
        return last.file, last.line

    each = d.get("each", True)
    args = tuple(d.get("args", ["{path}"] if each else ["{paths}"]))
    default = bool(d.get("default", False))
    launcher = Launcher(
        id=lid,
        label=str(d.get("label") or lid),
        command=str(d.get("command") or ""),
        args=args,
        match=tuple(d.get("match", ["*"])),
        each=each,
        cwd=str(d.get("cwd") or ""),
        env=tuple((str(k), str(v)) for k, v in (d.get("env") or {}).items()),
        default=default,
        default_rank=(2 if entry.get("default_level") == "project" else 1) if default else 0,
        source="+".join(entry["levels"]),
        file=last.file,
        line=last.line,
    )
    if not launcher.command:
        machine_hint = (
            " (the project's file only overrides a launcher of that id in the machine's)"
            if entry["levels"] == ["project"]
            else ""
        )
        return launcher, Problem(
            last.file,
            last.line,
            f"the launcher {lid!r} has no command{machine_hint}",
            'command = "D:/Tools/blender-4.2.3/blender.exe"',
        )
    if not each and not any(a.strip() == "{paths}" for a in args):
        file, line = where("args")
        return launcher, Problem(
            file,
            line,
            f"the launcher {lid!r} has each = false but no {{paths}} argument: it would "
            "open only the first file",
            'args = ["{paths}"], or each = true',
        )
    return launcher, None


def load(machine_path: Path, project_path: Path | None) -> tuple[list[Launcher], list[Problem]]:
    """Both files, merged: the launchers and every problem found."""
    m_raws, m_problems = read_file(machine_path, "machine")
    p_raws, p_problems = (
        read_file(project_path, "project") if project_path is not None else ([], [])
    )
    launchers, problems = merge(m_raws, p_raws)
    return launchers, m_problems + p_problems + problems


TEMPLATE = """\
# Specto launchers (#186): open files with programs the system does not know.
# Saved changes load at once. A project can add or override launchers (by id) in
# <project>/.specto/launchers.toml.
#
# [[launcher]]
# id = "blender-4.2"
# label = "Blender 4.2 LTS"
# command = "D:/Tools/blender-4.2.3/blender.exe"   # / or single quotes for Windows paths
# args = ["{path}"]          # {path} {paths} {dir} {name} {stem} {project}
# match = ["*.blend", "*.blend1"]
# each = true                # one process per file (false: one process, args = ["{paths}"])
# cwd = "{dir}"
# env = { BLENDER_USER_CONFIG = "D:/Tools/blender-4.2.3/config" }
# default = true             # "Open with Blender 4.2 LTS" at the top of the menu
"""

PROJECT_TEMPLATE = """\
# Specto launchers of this project (#186): add launchers for its files, or override the
# machine's by id (a project's `default = true` wins over the machine's). Saved changes
# load at once. The machine's file: Edit launchers... in the Open with menu.
#
# [[launcher]]
# id = "blender-4.2"          # the machine's id: this entry overrides its fields
# default = true              # "Open with Blender 4.2 LTS" at the top of the menu
#
# [[launcher]]
# id = "project-tool"
# label = "Project tool"
# command = "{project}/tools/run.exe"   # {project} is this folder
# args = ["{path}"]           # {path} {paths} {dir} {name} {stem} {project}
# match = ["*.dat"]
"""


__all__ = [
    "BATCH_EXTS",
    "BATCH_UNSAFE",
    "KEYS",
    "MACHINE_FILE",
    "PLACEHOLDERS",
    "PROJECT_FILE",
    "PROJECT_TEMPLATE",
    "REFUSED_EXTS",
    "TEMPLATE",
    "Invocation",
    "Launcher",
    "Problem",
    "batch_problem",
    "expand",
    "load",
    "merge",
    "placeholder_problem",
    "placeholder_values",
    "read_file",
    "refused_reason",
]
