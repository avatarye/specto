"""UTF-8 stdout and stderr for the GUI tools (#147): a flow's log prints what a cp1252
console cannot encode (a status line's arrow, U+2193), and redirected to a file on
Windows that raised UnicodeEncodeError and killed the flow."""

from __future__ import annotations

import sys


def utf8_stdio() -> None:
    """Reconfigure stdout and stderr to UTF-8, replacing what still cannot be written."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # None under pythonw
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
