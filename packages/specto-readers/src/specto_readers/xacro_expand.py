"""xacro expansion for the URDF view (SPEC-43.3, design section 3), on the PyPI ``xacro``
package (BSD-3-Clause).

- **Trusted content only** (ADR 0006). xacro's ``${...}`` is a Python ``eval`` with the
  builtins emptied and dunder names refused, but ``type`` and ``vars`` exposed: not a
  sandbox. So every file xacro reads as XML (the root and each ``xacro:include``) is
  hashed and checked with ``trusted(sha256)`` before it is parsed; the bytes hashed are
  the bytes parsed. An unapproved root raises :class:`XacroNeedsApproval` before
  anything is parsed ("xacro needs Approve"); so does an unapproved include, unless
  ``accept_includes`` (the user's Approve gesture on the root: the click approves the
  file with what it includes, and the caller records :attr:`XacroResult.files`). A
  changed include needs Approve again. YAML that ``xacro.load_yaml`` reads is data
  (``yaml.safe_load``) and is not gated.
- **``$(find pkg)``** (and ``find()`` inside ``$(eval ...)``) goes to ``find_package``,
  Specto's package resolver (design section 3), never to ``ament_index_python``. A
  package it cannot find fails with the fix: set ``params.package_paths``.
- **``args``** are ``params.xacro_args``: the values ``$(arg name)`` reads.
- xacro keeps its state in module globals; a lock serialises expansions in one process
  (the pool runs one job per worker anyway) and the patched functions are restored.
- What xacro prints to stderr (warnings) comes back in :attr:`XacroResult.warnings`.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from specto_core.errors import SpectoError

#: Specto's package resolver as xacro needs it: a package name -> its folder, or a
#: ``(folder, how found)`` pair, or None when it is not found
FindPackage = Callable[[str], "str | Path | tuple[str | Path, str] | None"]
ProgressFn = Callable[[float, str], None]

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
        what = f"its include {Path(path).name} changed" if include else Path(path).name
        super().__init__(
            f"xacro needs Approve: {what}",
            fix="xacro runs code; approve the file to expand it (plain URDF always opens)",
        )


@dataclass
class XacroResult:
    urdf: str
    #: every file read as XML, absolute path -> sha256 (what an Approve records)
    files: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expand_xacro(
    path: str | Path,
    *,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    args: Mapping[str, Any] | None = None,
    accept_includes: bool = False,
    progress: ProgressFn | None = None,
) -> XacroResult:
    """Expand the xacro file ``path`` into URDF text (module docstring)."""
    root = str(Path(path).resolve())
    try:
        data = Path(root).read_bytes()
    except OSError as e:
        raise XacroError(f"cannot read {Path(root).name}: {e.strerror or e}") from e
    sha = _sha256(data)
    if not trusted(sha):
        raise XacroNeedsApproval(root, sha)
    if progress:
        progress(0.0, "expanding xacro")

    import xacro
    from xacro import substitution_args

    files: dict[str, str] = {root: sha}
    original_parse = xacro.parse

    def gated_parse(inp: Any, filename: str | None = None) -> Any:
        if inp is not None or filename is None:
            return original_parse(inp, filename)
        full = str(Path(xacro.root_dir, filename).resolve())
        if full == root:
            raw = data
        else:
            try:
                raw = Path(full).read_bytes()
            except OSError as e:
                # xacro's own report, so an optional include stays optional
                xacro.filestack.pop()
                raise xacro.XacroException(f"{e.strerror}: {full}", exc=e) from e
            digest = _sha256(raw)
            if not (trusted(digest) or accept_includes):
                raise XacroNeedsApproval(full, digest, include=True)
            files[full] = digest
        return original_parse(raw.decode("utf-8"), filename)

    def find(pkg: str) -> str:
        hit = find_package(pkg)
        if isinstance(hit, tuple):
            hit = hit[0]
        if hit is None:
            raise XacroError(
                f"package `{pkg}` not found", fix="set params.package_paths to its folder"
            )
        return os.fspath(hit)

    mappings = {str(k): _arg_text(v) for k, v in (args or {}).items()}
    stderr = io.StringIO()
    with _LOCK:
        saved_find = substitution_args._eval_find
        saved_dict_find = substitution_args._eval_dict["find"]
        xacro.parse = gated_parse
        substitution_args._eval_find = find
        substitution_args._eval_dict["find"] = find
        try:
            with contextlib.redirect_stderr(stderr):
                doc = xacro.process_file(root, mappings=mappings)
            urdf = doc.toprettyxml(indent="  ")
        except XacroError:
            raise
        except Exception as e:  # xacro raises its own and whatever an expression raised
            raise XacroError(f"{Path(root).name}: {_message(e)}") from e
        finally:
            xacro.parse = original_parse
            substitution_args._eval_find = saved_find
            substitution_args._eval_dict["find"] = saved_dict_find
            xacro.all_includes.clear()
            xacro.substitution_args_context.clear()
    if progress:
        progress(1.0, "xacro expanded")
    warnings = [ln.strip() for ln in stderr.getvalue().splitlines() if ln.strip()]
    return XacroResult(urdf=urdf, files=files, warnings=warnings)


def xacro_task(
    path: str,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    args: dict[str, Any],
    accept_includes: bool,
    progress: ProgressFn,
) -> dict[str, Any]:
    """The pool's entry point: plain data back. ``{urdf, files, warnings}``, or
    ``{needs_approval: {path, sha256, include}}``: a file awaiting Approve is a state the
    view shows, not a failed job. Any other error fails the job with its message."""
    try:
        out = expand_xacro(
            path,
            find_package=find_package,
            trusted=trusted,
            args=args,
            accept_includes=accept_includes,
            progress=progress,
        )
    except XacroNeedsApproval as e:
        return {"needs_approval": {"path": e.path, "sha256": e.sha256, "include": e.include}}
    return {"urdf": out.urdf, "files": out.files, "warnings": out.warnings}


def submit_xacro(
    jobs: Any,
    path: str | Path,
    find_package: FindPackage,
    trusted: Callable[[str], bool],
    *,
    args: Mapping[str, Any] | None = None,
    accept_includes: bool = False,
    canvas: str | None = None,
    on_done: Callable[[Any], None] | None = None,
) -> Any:
    """Queue one expansion on any ``JobManager`` (the pool in the app). ``find_package``
    and ``trusted`` cross to the worker by pickle: a resolver instance and a bound
    ``TrustStore.approved`` both do. The job's message reads "expanding xacro" while it
    runs: the view's ``loading`` note."""
    return jobs.submit(
        "xacro",
        xacro_task,
        str(path),
        find_package,
        trusted,
        dict(args or {}),
        accept_includes,
        canvas=canvas,
        on_done=on_done,
    )


def _arg_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _message(e: BaseException) -> str:
    # a SpectoError raised by ``find`` inside xacro comes back wrapped
    inner = getattr(e, "exc", None)
    if isinstance(inner, XacroError):
        return str(inner)
    return str(e) or type(e).__name__


__all__ = [
    "FindPackage",
    "XacroError",
    "XacroNeedsApproval",
    "XacroResult",
    "expand_xacro",
    "submit_xacro",
    "xacro_task",
]
