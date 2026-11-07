"""xacro expansion for the URDF view (SPEC-43.3, design section 3), on the PyPI ``xacro``
package (BSD-3-Clause, pinned below 2.2). One of the enumerated code-running paths
(ADR 0006, contracts section 10).

xacro's ``${...}`` is a Python ``eval`` with the builtins emptied and dunder names
refused, but ``type`` and ``vars`` exposed: not a sandbox (``python.vars(python.type)``
reaches ``__subclasses__``). So whatever can reach the eval text is either approved or
refused:

- **The root, approved with its args.** ``$(arg x)`` is substituted inside ``${...}``
  before the eval, and ``params.xacro_args`` live in the canvas, which an agent can
  write; so the root is approved as :func:`approval_hash` ``(root bytes, args)``, and a
  change to either needs Approve again. The Approve card shows the args.
- **Each package folder, approved per root.** ``$(find pkg)`` puts the folder's path into
  the eval text, and ``params.package_paths`` is agent-writable too: each resolved
  ``(name, folder)`` is approved as :func:`package_hash` under the root's approval (found
  lazily, as xacro meets each ``$(find)``), so a moved package needs Approve again. A
  folder whose path holds a quote, ``$``, a backtick or a brace is refused outright, as
  is any file whose folder does (``$(dirname)`` substitutes it).
- **No environment.** ``$(env X)``, ``$(optenv X)`` and ``env()`` / ``optenv()`` inside
  ``$(eval ...)`` are refused with a fix (pass the value as an arg), and the child starts
  with a minimal environment (``children.minimal_env``).
- **Every file xacro reads** (the root, each ``xacro:include`` and each file
  ``xacro.load_yaml`` reads, whose unit tags such as ``!degrees`` run the eval too) goes
  through one gated ``open`` (and an include pattern with ``*``, ``?`` or ``[`` through
  a gated ``glob``, which checks the pattern the same way before listing). Before
  opening, its path text is checked: device paths
  (``\\\\.\\``, ``\\\\?\\`` other than a local drive, reserved names such as ``NUL``,
  ``/dev``) and network (UNC) paths outside the allowed folders are refused, and so is
  anything outside the allowed folders (the root's own folder, ``roots`` (the project)
  and the approved package folders). Then it is opened, must be a regular file, its
  final path is taken from the open handle (refused when that cannot be told) and
  checked again, and only then read, at most ``MAX_FILE_BYTES``. The bytes are hashed,
  checked with ``trusted(sha256)``, and those same bytes go to xacro. An unapproved
  root raises :class:`XacroNeedsApproval` before anything is parsed ("xacro needs
  Approve"); so does an unapproved include, YAML file or package, unless
  ``approved_root_sha`` equals the root's approval hash: the user's Approve click on
  that exact root and args accepts what that run meets, and :attr:`XacroResult.accepted`
  lists each such approval for the host to record in the trust store and show.
- **Out of process.** :func:`xacro_task` (the pool's entry point) runs the expansion in
  a fresh ``python -I -X utf8`` interpreter (``_xacro_runner.py``) through
  ``children.run_child``: never in a pool worker, a timeout kills its whole process
  tree, and output past ``OUT_CAP`` / ``ERR_CAP`` bytes fails the run.
  :func:`expand_xacro` is what the child calls; it also restores what it patched.
- ``find_package`` is Specto's package resolver (design section 3): a package name -> its
  folder, or None (fails with the fix: set ``params.package_paths``). It crosses into
  the child by pickle, so it must be importable there: a resolver's bound method, or a
  dict's ``get``.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pickle
import stat
import sys
import threading
import types
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from specto_core.children import minimal_env, run_child
from specto_core.errors import SpectoError

#: Specto's package resolver as xacro needs it: a package name -> its folder, or None
FindPackage = Callable[[str], "str | os.PathLike[str] | None"]
ProgressFn = Callable[[float, str], None]
Role = Literal["root", "include", "yaml", "package"]

RUNNER = Path(__file__).with_name("_xacro_runner.py")
RUN_TIMEOUT_S = 120.0
#: what the child may print before the run fails: the URDF on stdout, warnings on stderr
OUT_CAP = 64 * 1024 * 1024
ERR_CAP = 1024 * 1024
#: the largest file xacro may read (a robot description is kilobytes)
MAX_FILE_BYTES = 32 * 1024 * 1024
YAML_EXTS = (".yaml", ".yml")
#: characters that would let a path break out of the eval text it is substituted into
UNSAFE_PATH_CHARS = frozenset("'\"$`{}")
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
)
_LOCK = threading.Lock()


class XacroError(SpectoError):
    """A xacro file that does not expand (its own error, as one message)."""

    code = "xacro"


class XacroNeedsApproval(XacroError):
    """Something xacro would use is not approved: the view says "xacro needs Approve".
    ``sha256`` is what Approve records: the approval hash for the root (bytes and
    ``args``), the content hash for an include or YAML file, the package hash for a
    package folder."""

    code = "xacro_needs_approval"

    def __init__(
        self,
        path: str,
        sha256: str,
        *,
        role: Role = "root",
        args: dict[str, str] | None = None,
        package: str = "",
    ) -> None:
        self.path = path
        self.sha256 = sha256
        self.role = role
        self.xacro_args = dict(args or {})
        self.package = package
        name = Path(path).name
        if role == "include":
            what = f"its include {name} is not approved"
        elif role == "yaml":
            what = f"its YAML file {name} is not approved"
        elif role == "package":
            what = f"its package {package} at {path} is not approved"
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
            "package": self.package,
        }


@dataclass
class XacroResult:
    urdf: str
    #: every file xacro read, absolute path -> content sha256
    files: dict[str, str] = field(default_factory=dict)
    #: what this run accepted under ``approved_root_sha`` (not approved before), label ->
    #: sha256: the root's path, each newly trusted file's path, ``package <name>:
    #: <folder>`` for each package; what the host records and shows
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


def package_hash(root_approval: str, name: str, folder: str) -> str:
    """What approving one ``$(find name)`` under a root records: the folder it resolved
    to, bound to that root's approval."""
    return _sha256(f"xacro_package\0{root_approval}\0{name}\0{folder}".encode())


# -- reading files -----------------------------------------------------------------------


def _under(path: Path, folders: Iterable[Path]) -> bool:
    return any(path == f or path.is_relative_to(f) for f in folders)


def _unsafe(text: str) -> bool:
    return any(c in UNSAFE_PATH_CHARS for c in text)


def _device_reason(path: Path) -> tuple[Path, str | None]:
    """``(path, why it is refused)`` from its text alone, before anything opens it; a
    ``\\\\?\\C:\\`` path comes back as plain ``C:\\``."""
    text = str(path)
    if os.name == "nt":
        if text.startswith("\\\\?\\"):
            rest = text[4:]
            if len(rest) >= 3 and rest[1] == ":" and rest[2] == "\\":
                path, text = Path(rest), rest
            else:
                return path, "a device path"
        elif text.startswith("\\\\.\\"):
            return path, "a device path"
        if path.name.split(".")[0].upper() in _DEVICE_NAMES:
            return path, "a device name"
    elif text.startswith(("/dev/", "/proc/", "/sys/")):
        return path, "a device path"
    return path, None


def _is_unc(path: Path) -> bool:
    return os.name == "nt" and str(path).startswith("\\\\")


def _final_path(fd: int) -> Path | None:
    """The path the open handle really points at (links and junctions followed), or None
    when this platform cannot tell."""
    if os.name == "nt":
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(fd)
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)
        if not 0 < n < len(buf):
            return None
        text = buf.value
        if text.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + text[8:])
        return Path(text[4:] if text.startswith("\\\\?\\") else text)
    proc = Path(f"/proc/self/fd/{fd}")
    if proc.exists():
        return Path(os.readlink(proc))
    try:  # pragma: no cover - macOS
        import fcntl

        raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))
        return Path(raw.split(b"\0", 1)[0].decode())
    except (ImportError, AttributeError, OSError):  # pragma: no cover
        return None


def _check_text(asked: Path, allowed: list[Path] | None, *, is_root: bool = False) -> Path:
    """The checks on a path's text alone, before anything opens or lists it (module
    docstring); the absolute path back."""
    asked, device = _device_reason(Path(os.path.abspath(os.fspath(asked))))
    if device:
        raise XacroError(f"{asked} is {device}", fix="name a file in the project")
    if allowed is not None and not is_root and not _under(asked, allowed):
        raise XacroError(
            f"{asked.name} is outside the project and the package paths",
            fix="put it in the project, or its package in params.package_paths",
        )
    if allowed is None and _is_unc(asked):
        raise XacroError(f"{asked} is a network path", fix="name a file in the project")
    return asked


def _read_checked(
    asked: Path, allowed: list[Path] | None, *, is_root: bool = False
) -> tuple[Path, bytes]:
    """Check ``asked`` by its text, open it, check where the handle leads, then read it
    (module docstring): ``(final path, bytes)``. An OSError from opening passes through
    (xacro's own report keeps an optional include optional)."""
    asked = _check_text(asked, allowed, is_root=is_root)
    fd = os.open(asked, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise XacroError(f"{asked.name} is not a regular file")
        final = _final_path(fd)
        if final is None:
            raise XacroError(f"cannot tell where {asked.name} leads", fix="name the file itself")
        if allowed is not None and not is_root and not _under(final, allowed):
            raise XacroError(
                f"{final.name} is outside the project and the package paths",
                fix="put it in the project, or its package in params.package_paths",
            )
        if _unsafe(str(final.parent)):
            raise XacroError(
                f"the folder of {final.name} has a quote, $, backtick or brace in its path",
                fix="rename the folder",
            )
        with os.fdopen(fd, "rb", closefd=False) as f:
            raw = f.read(MAX_FILE_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_FILE_BYTES:
        raise XacroError(f"{final.name} is larger than {MAX_FILE_BYTES} bytes")
    return final, raw


def _both(path: str | os.PathLike[str]) -> list[Path]:
    """A folder as written (absolute) and as resolved: the text check sees the first, the
    handle check the second."""
    lexical = Path(os.path.abspath(os.fspath(path)))
    return [lexical, lexical.resolve()]


# -- expansion ---------------------------------------------------------------------------


def _refuse_env(*_: Any, **__: Any) -> str:
    raise XacroError(
        "xacro's $(env) and $(optenv) are off in Specto",
        fix="pass the value through params.xacro_args and read it with $(arg)",
    )


def expand_xacro(
    path: str | Path,
    *,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    args: Mapping[str, Any] | None = None,
    approved_root_sha: str | None = None,
    roots: Iterable[str | Path] = (),
    progress: ProgressFn | None = None,
    _confine: bool = True,
) -> XacroResult:
    """Expand the xacro file ``path`` into URDF text, in this process (module
    docstring; the app goes through :func:`xacro_task`). ``_confine=False`` lifts the
    folder confinement: tests only."""
    given = Path(os.path.abspath(os.fspath(path)))
    allowed: list[Path] | None = None
    if _confine:
        allowed = [*_both(given.parent)]
        for r in roots:
            allowed.extend(_both(r))
    try:
        root, data = _read_checked(given, allowed, is_root=True)
    except OSError as e:
        raise XacroError(f"cannot read {given.name}: {e.strerror or e}") from e
    if allowed is not None:
        allowed.append(root.parent)
    mappings = effective_args(args)
    approval = approval_hash(data, mappings)
    if not trusted(approval):
        raise XacroNeedsApproval(str(root), approval, role="root", args=mappings)
    accept = approved_root_sha == approval
    if progress:
        progress(0.0, "expanding xacro")

    import xacro
    import yaml
    from xacro import substitution_args

    files: dict[str, str] = {str(root): _sha256(data)}
    accepted: dict[str, str] = {str(root): approval} if accept else {}

    def gated_open(file: Any, mode: str = "r", *a: Any, **kw: Any) -> io.BytesIO:
        if any(c in mode for c in "wax+"):
            raise OSError(f"xacro may not write {file}")
        final, raw = _read_checked(Path(os.fspath(file)), allowed)
        if final == root:
            return io.BytesIO(data)
        digest = _sha256(raw)
        role: Role = "yaml" if final.suffix.lower() in YAML_EXTS else "include"
        if not trusted(digest):
            if not accept:
                raise XacroNeedsApproval(str(final), digest, role=role)
            accepted[str(final)] = digest
        files[str(final)] = digest
        return io.BytesIO(raw)

    def gated_glob(pattern: str, *a: Any, **kw: Any) -> list[str]:
        # an include filename with * ? [ is globbed: listing is checked like opening
        _check_text(Path(pattern), allowed)
        return real_glob.glob(pattern, *a, **kw)

    def find(pkg: str) -> str:
        hit = find_package(pkg)
        if hit is None:
            raise XacroError(
                f"package `{pkg}` not found", fix="set params.package_paths to its folder"
            )
        folder = Path(os.path.abspath(os.fspath(hit)))
        if _unsafe(str(folder)):
            raise XacroError(
                f"package `{pkg}`'s folder has a quote, $, backtick or brace in its path",
                fix="move or rename the package folder",
            )
        digest = package_hash(approval, pkg, str(folder))
        if not trusted(digest):
            if not accept:
                raise XacroNeedsApproval(str(folder), digest, role="package", package=pkg)
            accepted[f"package {pkg}: {folder}"] = digest
        if allowed is not None:
            allowed.extend(_both(folder))
        return str(folder)

    stderr = io.StringIO()
    patched = {
        "_eval_find": find,
        "_env": _refuse_env,
        "_optenv": _refuse_env,
        "_eval_env": _refuse_env,
        "_eval_optenv": _refuse_env,
    }
    with _LOCK:
        saved = {name: getattr(substitution_args, name) for name in patched}
        saved_dict = {k: substitution_args._eval_dict[k] for k in ("find", "env", "optenv")}
        saved_yaml = dict(yaml.SafeLoader.yaml_constructors)
        xacro.open = gated_open  # shadows the builtin for xacro's parse and load_yaml
        real_glob = xacro.glob
        xacro.glob = types.SimpleNamespace(glob=gated_glob)
        for name, fn in patched.items():
            setattr(substitution_args, name, fn)
        substitution_args._eval_dict.update(find=find, env=_refuse_env, optenv=_refuse_env)
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
            xacro.glob = real_glob
            for name, fn in saved.items():
                setattr(substitution_args, name, fn)
            substitution_args._eval_dict.update(saved_dict)
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


def xacro_task(
    path: str,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    args: dict[str, Any],
    approved_root_sha: str | None,
    roots: list[str],
    progress: ProgressFn,
) -> dict[str, Any]:
    """The pool's entry point: expands in a fresh ``python -I`` child with a minimal
    environment and returns plain data. ``{urdf, files, accepted, warnings}``, or
    ``{needs_approval: {path, sha256, role, args, package}}``: something awaiting Approve
    is a state the view shows, not a failed job. Any other error fails the job."""
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
        [sys.executable, "-I", "-X", "utf8", str(RUNNER)],
        request,
        timeout=RUN_TIMEOUT_S,
        out_cap=OUT_CAP,
        err_cap=ERR_CAP,
        env=minimal_env(),
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
    "MAX_FILE_BYTES",
    "OUT_CAP",
    "RUNNER",
    "FindPackage",
    "XacroError",
    "XacroNeedsApproval",
    "XacroResult",
    "approval_hash",
    "effective_args",
    "expand_xacro",
    "package_hash",
    "submit_xacro",
    "xacro_task",
]
