"""xacro expansion for the URDF view (SPEC-43.3, design section 3), on the PyPI ``xacro``
package (BSD-3-Clause, pinned below 2.2). One of the enumerated code-running paths
(ADR 0006, contracts section 10).

- **Trusted content only.** xacro's ``${...}`` is a Python ``eval`` with the builtins
  emptied and dunder names refused, but ``type`` and ``vars`` exposed: not a sandbox
  (``python.vars(python.type)`` reaches ``__subclasses__``).
- **What an approval covers: the root's bytes together with its args.** ``$(arg x)`` is
  substituted inside ``${...}`` before the eval, and ``params.xacro_args`` live in the
  canvas, which an agent can write; so the root is approved as
  :func:`approval_hash` ``(root bytes, args)``, and a change to either needs Approve
  again. The Approve card shows the args (:attr:`XacroNeedsApproval.xacro_args`).
- **Every file xacro reads goes through one gated ``open``** (the root, each
  ``xacro:include`` and each file ``xacro.load_yaml`` reads, whose unit tags such as
  ``!degrees`` run xacro's eval too): opened first, its final path taken from the open
  handle and checked against the allowed folders (so a junction swapped in between does
  not escape), then read once, hashed, checked with ``trusted(sha256)``, and those same
  bytes handed to xacro. An unapproved root raises :class:`XacroNeedsApproval` before
  anything is parsed ("xacro needs Approve"); so does an unapproved include or YAML
  file, unless ``approved_root_sha`` equals the root's approval hash: the user's Approve
  click on that exact root with those args accepts what it reads during that run, and
  :attr:`XacroResult.accepted` lists each such file with its hash, for the host to
  record in the trust store and to show the user. A changed include needs Approve again.
- **Confined by default.** Files are read only under the root's own folder, ``roots``
  (the project) and the package folders ``find_package`` returned; an absolute or
  ``../`` path elsewhere fails. ``confine=False`` lifts it (tests only).
- **Out of process.** :func:`xacro_task` (the pool's entry point) runs the expansion in
  a fresh ``python -I -X utf8`` interpreter (``_xacro_runner.py``), never in a pool
  worker: an approved file's code cannot patch the worker's decoders for later jobs, and
  xacro's module globals and its ``yaml.SafeLoader`` constructors die with the child. A
  timeout kills the child's whole process tree; output past ``OUT_CAP`` / ``ERR_CAP``
  bytes fails the run. :func:`expand_xacro` is what the child calls; it also restores
  what it patched.
- **``$(find pkg)``** goes to ``find_package``, Specto's package resolver (design section
  3), never to ``ament_index_python``: a package name -> its folder, or None (fails with
  the fix: set ``params.package_paths``). It crosses into the child by pickle, so it
  must be importable there: a resolver's bound method, or a dict's ``get``.
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
from typing import Any, Literal

from specto_core.errors import SpectoError
from specto_core.python_links import NO_WINDOW, kill_tree

#: Specto's package resolver as xacro needs it: a package name -> its folder, or None
FindPackage = Callable[[str], "str | os.PathLike[str] | None"]
ProgressFn = Callable[[float, str], None]
Role = Literal["root", "include", "yaml"]

RUNNER = Path(__file__).with_name("_xacro_runner.py")
RUN_TIMEOUT_S = 120.0
#: what the child may print before the run fails: the URDF on stdout, warnings on stderr
OUT_CAP = 64 * 1024 * 1024
ERR_CAP = 1024 * 1024
YAML_EXTS = (".yaml", ".yml")
_LOCK = threading.Lock()


class XacroError(SpectoError):
    """A xacro file that does not expand (its own error, as one message)."""

    code = "xacro"


class XacroNeedsApproval(XacroError):
    """A file xacro would read is not approved: the view says "xacro needs Approve".
    ``sha256`` is what Approve records: the approval hash for the root (bytes and
    ``args``), the content hash for an include or YAML file."""

    code = "xacro_needs_approval"

    def __init__(
        self, path: str, sha256: str, *, role: Role = "root", args: dict[str, str] | None = None
    ) -> None:
        self.path = path
        self.sha256 = sha256
        self.role = role
        self.xacro_args = dict(args or {})
        name = Path(path).name
        if role == "include":
            what = f"its include {name} is not approved"
        elif role == "yaml":
            what = f"its YAML file {name} is not approved"
        else:
            what = f"{name} is not approved" + (" with these xacro_args" if self.xacro_args else "")
        super().__init__(
            f"xacro needs Approve: {what}",
            fix="xacro runs code; approve the file to expand it (plain URDF always opens)",
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "role": self.role,
            "args": self.xacro_args,
        }


@dataclass
class XacroResult:
    urdf: str
    #: every file xacro read, absolute path -> content sha256
    files: dict[str, str] = field(default_factory=dict)
    #: the files this run accepted under ``approved_root_sha`` (not approved before), path
    #: -> sha256, plus the root's approval hash under its path: what the host records
    accepted: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _arg_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def effective_args(args: Mapping[str, Any] | None) -> dict[str, str]:
    """``params.xacro_args`` as xacro receives them: every name and value a string."""
    return {str(k): _arg_text(v) for k, v in (args or {}).items()}


def approval_hash(root_bytes: bytes, args: Mapping[str, Any] | None) -> str:
    """What approving a xacro root records: sha256 over its bytes and the canonical JSON of
    its effective args, so neither can change without a new Approve."""
    canon = json.dumps(effective_args(args), sort_keys=True, separators=(",", ":"))
    return _sha256(root_bytes + b"\0xacro_args\0" + canon.encode("utf-8"))


def _under(path: Path, folders: Iterable[Path]) -> bool:
    return any(path == f or path.is_relative_to(f) for f in folders)


def _final_path(fd: int, fallback: Path) -> Path:
    """The path the open handle really points at (links and junctions followed)."""
    if os.name == "nt":
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(fd)
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)
        if 0 < n < len(buf):
            text = buf.value
            if text.startswith("\\\\?\\UNC\\"):
                text = "\\\\" + text[8:]
            elif text.startswith("\\\\?\\"):
                text = text[4:]
            return Path(text)
    proc = Path(f"/proc/self/fd/{fd}")
    if proc.exists():
        return Path(os.readlink(proc))
    return fallback.resolve()  # pragma: no cover - macOS: no handle query here


def _read_final(path: Path) -> tuple[Path, bytes]:
    """Open ``path``, then read: ``(final path from the handle, bytes)``."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        final = _final_path(fd, path)
        with os.fdopen(fd, "rb", closefd=False) as f:
            return final, f.read()
    finally:
        os.close(fd)


def expand_xacro(
    path: str | Path,
    *,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    args: Mapping[str, Any] | None = None,
    approved_root_sha: str | None = None,
    roots: Iterable[str | Path] = (),
    confine: bool = True,
    progress: ProgressFn | None = None,
) -> XacroResult:
    """Expand the xacro file ``path`` into URDF text, in this process (module
    docstring; the app goes through :func:`xacro_task`)."""
    given = Path(path).absolute()
    try:
        root, data = _read_final(given)
    except OSError as e:
        raise XacroError(f"cannot read {given.name}: {e.strerror or e}") from e
    mappings = effective_args(args)
    approval = approval_hash(data, mappings)
    if not trusted(approval):
        raise XacroNeedsApproval(str(root), approval, role="root", args=mappings)
    accept_reads = approved_root_sha == approval
    if progress:
        progress(0.0, "expanding xacro")

    import xacro
    import yaml
    from xacro import substitution_args

    allowed = [root.parent, *(Path(r).resolve() for r in roots)] if confine else None
    files: dict[str, str] = {str(root): _sha256(data)}
    accepted: dict[str, str] = {str(root): approval} if accept_reads else {}

    def gated_open(file: Any, mode: str = "r", *a: Any, **kw: Any) -> io.BytesIO:
        if any(c in mode for c in "wax+"):
            raise OSError(f"xacro may not write {file}")
        asked = Path(os.fspath(file)).absolute()
        final, raw = _read_final(asked)  # an OSError keeps xacro's own report (optional)
        if final == root:
            return io.BytesIO(data)
        if allowed is not None and not _under(final, allowed):
            raise XacroError(
                f"{final.name} is outside the project and the package paths",
                fix="put it in the project, or its package in params.package_paths",
            )
        digest = _sha256(raw)
        role: Role = "yaml" if final.suffix.lower() in YAML_EXTS else "include"
        if not trusted(digest):
            if not accept_reads:
                raise XacroNeedsApproval(str(final), digest, role=role)
            accepted[str(final)] = digest
        files[str(final)] = digest
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
    return XacroResult(urdf=urdf, files=files, accepted=accepted, warnings=warnings)


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


# -- out of process -----------------------------------------------------------------------


@dataclass
class ChildRun:
    stdout: bytes
    stderr: bytes
    returncode: int | None
    timed_out: bool = False
    #: "stdout" or "stderr" when that stream passed its cap (the run was killed)
    over_cap: str | None = None


def run_child(
    cmd: list[str],
    request: bytes,
    *,
    timeout: float,
    out_cap: int = OUT_CAP,
    err_cap: int = ERR_CAP,
) -> ChildRun:
    """Run ``cmd`` with ``request`` on stdin, reading its output up to the caps. A timeout
    or a passed cap kills its whole process tree (``python_links.kill_tree``), so code
    it ran cannot leave grandchildren behind."""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=NO_WINDOW,
        start_new_session=os.name != "nt",
    )
    out: list[bytes] = []
    err: list[bytes] = []
    over: list[str] = []

    def pump(stream: Any, cap: int, sink: list[bytes], name: str) -> None:
        total = 0
        while chunk := stream.read(65536):
            total += len(chunk)
            if total > cap:
                over.append(name)
                kill_tree(proc)
                break
            sink.append(chunk)

    def feed() -> None:
        with contextlib.suppress(OSError):
            proc.stdin.write(request)
            proc.stdin.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, out_cap, out, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, err_cap, err, "stderr"), daemon=True),
        threading.Thread(target=feed, daemon=True),
    ]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5.0)
    for t in threads:
        t.join(timeout=5.0)
    return ChildRun(
        b"".join(out), b"".join(err), proc.returncode, timed_out, over[0] if over else None
    )


def xacro_task(
    path: str,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    args: dict[str, Any],
    approved_root_sha: str | None,
    roots: list[str],
    progress: ProgressFn,
) -> dict[str, Any]:
    """The pool's entry point: expands in a fresh ``python -I`` child and returns plain
    data. ``{urdf, files, accepted, warnings}``, or ``{needs_approval: {path, sha256,
    role, args}}``: a file awaiting Approve is a state the view shows, not a failed job.
    Any other error fails the job with its message."""
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
    name = Path(path).name
    run = run_child(
        [sys.executable, "-I", "-X", "utf8", str(RUNNER)], request, timeout=RUN_TIMEOUT_S
    )
    if run.timed_out:
        raise XacroError(f"{name}: xacro did not finish in {RUN_TIMEOUT_S:g} s")
    if run.over_cap:
        cap = OUT_CAP if run.over_cap == "stdout" else ERR_CAP
        raise XacroError(f"{name}: xacro printed more than {cap} bytes on {run.over_cap}")
    lines = run.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        reply = json.loads(lines[-1]) if lines else None
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        err = run.stderr.decode("utf-8", "replace").strip().splitlines()
        last = err[-1] if err else f"exit code {run.returncode}"
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
    roots: Iterable[str | Path] = (),
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
        [str(r) for r in roots],
        canvas=canvas,
        on_done=on_done,
    )


__all__ = [
    "ERR_CAP",
    "OUT_CAP",
    "RUNNER",
    "ChildRun",
    "FindPackage",
    "XacroError",
    "XacroNeedsApproval",
    "XacroResult",
    "approval_hash",
    "effective_args",
    "expand_xacro",
    "run_child",
    "submit_xacro",
    "xacro_task",
]
