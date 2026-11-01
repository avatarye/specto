"""Where the web-page pages and vendored Monaco live: ``specto_web`` locates them
(``packages/specto-web/web`` in a checkout, ``specto_web/web`` in a wheel,
``SPECTO_WEB_ROOT`` to override). Errors here carry the build command as the fix."""

from __future__ import annotations

from pathlib import Path

import specto_web
from specto_core.errors import SpectoError

ENV_VAR = specto_web.ENV_VAR


def web_root() -> Path:
    """The directory served as ``/`` on every page host."""
    root = specto_web.find_web_root()
    if root is None:
        raise SpectoError(
            "web view assets not found",
            fix=f"set {ENV_VAR} to packages/specto-web/web, or run from the source checkout",
        )
    return root


def monaco_ok(root: Path | None = None) -> bool:
    return specto_web.monaco_ok(root or web_root())


def require_monaco() -> Path:
    """The web root, failing loud (with the build command) when Monaco is not vendored."""
    root = web_root()
    if not monaco_ok(root):
        raise SpectoError(
            f"Monaco is not vendored under {specto_web.monaco_dir(root)}",
            fix=specto_web.BUILD_HINT,
        )
    return root


def require_vendor(*files: str) -> Path:
    """The web root, failing loud when any ``vendor/<file>`` a page needs is missing
    (``build.py`` vendors them all; ``--check`` lists what is there)."""
    root = web_root()
    missing = [f for f in files if not (root / "vendor" / f).is_file()]
    if missing:
        raise SpectoError(
            f"web assets not vendored under {root / 'vendor'}: {', '.join(missing)}",
            fix=specto_web.BUILD_HINT,
        )
    return root


__all__ = ["ENV_VAR", "monaco_ok", "require_monaco", "require_vendor", "web_root"]
