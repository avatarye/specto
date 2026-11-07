"""xacro expansion for the URDF view (SPEC-43.3, design section 3), on the PyPI ``xacro``
package (BSD-3-Clause). One of the enumerated code-running paths (ADR 0006).

- **Trusted content only.** xacro's ``${...}`` is a Python ``eval`` with the builtins
  emptied and dunder names refused, but ``type`` and ``vars`` exposed: not a sandbox
  (``python.vars(python.type)`` reaches ``__subclasses__``). So every file xacro reads
  (the root, each ``xacro:include`` and each file ``xacro.load_yaml`` reads, whose unit
  tags such as ``!degrees`` run xacro's eval too) goes through one gated ``open``: it is
  hashed and checked with ``trusted(sha256)`` before xacro sees it, and the bytes hashed
  are the bytes xacro parses. An unapproved root raises :class:`XacroNeedsApproval`
  before anything is parsed ("xacro needs Approve"); so does an unapproved include or
  YAML file, unless ``approved_root_sha`` equals the root's hash: the user's Approve
  click on that exact root approves it with what it reads, and the caller records
  :attr:`XacroResult.files` in the trust store. A changed include needs Approve again.
- **Out of process.** :func:`xacro_task` (the pool's entry point) runs the expansion in
  a fresh ``python -I -X utf8`` interpreter (``_xacro_runner.py``), never in a pool
  worker: an approved file's code cannot patch the worker's decoders for later jobs,
  and xacro's module globals and its ``yaml.SafeLoader`` constructors die with the
  child. :func:`expand_xacro` is what that child calls; it also restores what it patched.
- **``$(find pkg)``** goes to ``find_package``, Specto's package resolver (design section
  3), never to ``ament_index_python``: a package name -> its folder, or None (fails with
  the fix: set ``params.package_paths``). It crosses into the child by pickle, so it
  must be importable there: a resolver's bound method, or a dict's ``get``.
- **``roots``**: when given, xacro reads files only under these folders (the project)
  or a package folder ``find_package`` returned; an absolute or ``../`` path elsewhere
  fails. Without ``roots`` the trust gate is the only protection.
- **``args``** are ``params.xacro_args``: the values ``$(arg name)`` reads.
- What xacro prints to stderr (warnings) comes back in :attr:`XacroResult.warnings`.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pickle
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from specto_core.errors import SpectoError

#: Specto's package resolver as xacro needs it: a package name -> its folder, or None
FindPackage = Callable[[str], "str | os.PathLike[str] | None"]
ProgressFn = Callable[[float, str], None]

RUNNER = Path(__file__).with_name("_xacro_runner.py")
RUN_TIMEOUT_S = 120.0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_LOCK = threading.Lock()


class XacroError(SpectoError):
    """A xacro file that does not expand (its own error, as one message)."""

    code = "xacro"


class XacroNeedsApproval(XacroError):
    """A file xacro would read is not approved: the view says "xacro needs Approve"."""

    code = "xacro_needs_approval"

    def __init__(self, path: str, sha256: str, *, include: bool = False) -> None:
        self.path = path
        self.sha256 = sha256
        self.include = include
        name = Path(path).name
        what = f"its include {name} is not approved" if include else name
        super().__init__(
            f"xacro needs Approve: {what}",
            fix="xacro runs code; approve the file to expand it (plain URDF always opens)",
        )


@dataclass
class XacroResult:
    urdf: str
    #: every file xacro read, absolute path -> sha256 (what an Approve records)
    files: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _under(path: Path, folders: Iterable[Path]) -> bool:
    return any(path == f or path.is_relative_to(f) for f in folders)


def expand_xacro(
    path: str | Path,
    *,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    args: Mapping[str, Any] | None = None,
    approved_root_sha: str | None = None,
    roots: Iterable[str | Path] | None = None,
    progress: ProgressFn | None = None,
) -> XacroResult:
    """Expand the xacro file ``path`` into URDF text, in this process (module
    docstring; the app goes through :func:`xacro_task`)."""
    root = Path(path).resolve()
    try:
        data = root.read_bytes()
    except OSError as e:
        raise XacroError(f"cannot read {root.name}: {e.strerror or e}") from e
    sha = _sha256(data)
    if not trusted(sha):
        raise XacroNeedsApproval(str(root), sha)
    accept_reads = approved_root_sha == sha
    if progress:
        progress(0.0, "expanding xacro")

    import xacro
    import yaml
    from xacro import substitution_args

    allowed = None if roots is None else [Path(r).resolve() for r in roots]
    files: dict[str, str] = {str(root): sha}

    def gated_open(file: Any, mode: str = "r", *a: Any, **kw: Any) -> io.BytesIO:
        if any(c in mode for c in "wax+"):
            raise OSError(f"xacro may not write {file}")
        full = Path(os.fspath(file)).resolve()
        if full == root:
            return io.BytesIO(data)
        if allowed is not None and not _under(full, allowed):
            raise XacroError(
                f"{full.name} is outside the project and the package paths",
                fix="put it in the project, or its package in params.package_paths",
            )
        raw = full.read_bytes()  # an OSError keeps xacro's own report (optional includes)
        digest = _sha256(raw)
        if not (trusted(digest) or accept_reads):
            raise XacroNeedsApproval(str(full), digest, include=True)
        files[str(full)] = digest
        return io.BytesIO(raw)

    def find(pkg: str) -> str:
        hit = find_package(pkg)
        if hit is None:
            raise XacroError(
                f"package `{pkg}` not found", fix="set params.package_paths to its folder"
            )
        folder = Path(os.fspath(hit)).resolve()
        if allowed is not None:
            allowed.append(folder)
        return str(folder)

    mappings = {str(k): _arg_text(v) for k, v in (args or {}).items()}
    stderr = io.StringIO()
    with _LOCK:
        saved_find = substitution_args._eval_find
        saved_dict_find = substitution_args._eval_dict["find"]
        saved_yaml = dict(yaml.SafeLoader.yaml_constructors)
        xacro.open = gated_open  # shadows the builtin for xacro's parse and load_yaml
        substitution_args._eval_find = find
        substitution_args._eval_dict["find"] = find
        try:
            with contextlib.redirect_stderr(stderr):
                doc = xacro.process_file(str(root), mappings=mappings)
            urdf = doc.toprettyxml(indent="  ")
        except Exception as e:  # xacro wraps what an expression or an include raised
            inner = _cause(e)
            if isinstance(inner, XacroError):
                raise inner from None
            raise XacroError(f"{root.name}: {str(e) or type(e).__name__}") from e
        finally:
            del xacro.open
            substitution_args._eval_find = saved_find
            substitution_args._eval_dict["find"] = saved_dict_find
            yaml.SafeLoader.yaml_constructors = saved_yaml
            xacro.all_includes.clear()
            xacro.substitution_args_context.clear()
    if progress:
        progress(1.0, "xacro expanded")
    warnings = [ln.strip() for ln in stderr.getvalue().splitlines() if ln.strip()]
    return XacroResult(urdf=urdf, files=files, warnings=warnings)


def _cause(e: BaseException) -> BaseException:
    """The innermost exception xacro wrapped (``XacroException.exc``, then chaining)."""
    seen: set[int] = set()
    while id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, XacroError):
            return e
        nxt = getattr(e, "exc", None) or e.__cause__ or e.__context__
        if not isinstance(nxt, BaseException):
            break
        e = nxt
    return e


def _arg_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# -- out of process -----------------------------------------------------------------------


def xacro_task(
    path: str,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    args: dict[str, Any],
    approved_root_sha: str | None,
    roots: list[str] | None,
    progress: ProgressFn,
) -> dict[str, Any]:
    """The pool's entry point: expands in a fresh ``python -I`` child and returns plain
    data. ``{urdf, files, warnings}``, or ``{needs_approval: {path, sha256, include}}``:
    a file awaiting Approve is a state the view shows, not a failed job. Any other error
    fails the job with its message."""
    progress(0.0, "expanding xacro")
    request = pickle.dumps(
        {
            "path": path,
            "find_package": find_package,
            "trusted": trusted,
            "args": args,
            "approved_root_sha": approved_root_sha,
            "roots": roots,
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-I", "-X", "utf8", str(RUNNER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )
    name = Path(path).name
    try:
        stdout, stderr = proc.communicate(request, timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired, OSError, ValueError):
            proc.communicate(timeout=2.0)
        raise XacroError(f"{name}: xacro did not finish in {RUN_TIMEOUT_S:g} s") from None
    lines = stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        reply = json.loads(lines[-1]) if lines else None
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        err = stderr.decode("utf-8", "replace").strip().splitlines()
        last = err[-1] if err else f"exit code {proc.returncode}"
        raise XacroError(f"{name}: xacro failed ({last})")
    if "error" in reply:
        raise XacroError(str(reply["error"]))
    progress(1.0, "xacro expanded")
    return reply


def submit_xacro(
    jobs: Any,
    path: str | Path,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    *,
    args: Mapping[str, Any] | None = None,
    approved_root_sha: str | None = None,
    roots: Iterable[str | Path] | None = None,
    canvas: str | None = None,
    on_done: Callable[[Any], None] | None = None,
) -> Any:
    """Queue one expansion on any ``JobManager`` (the pool in the app). ``find_package``
    and ``trusted`` cross to the worker and on to the child by pickle: a resolver's bound
    method and a bound ``TrustStore.approved`` both do. The job's message reads
    "expanding xacro" while it runs: the view's ``loading`` note."""
    return jobs.submit(
        "xacro",
        xacro_task,
        str(path),
        find_package,
        trusted,
        dict(args or {}),
        approved_root_sha,
        None if roots is None else [str(r) for r in roots],
        canvas=canvas,
        on_done=on_done,
    )


__all__ = [
    "RUNNER",
    "FindPackage",
    "XacroError",
    "XacroNeedsApproval",
    "XacroResult",
    "expand_xacro",
    "submit_xacro",
    "xacro_task",
]
