"""Launchers (#186): open selected files with external programs the user configured
(isolated Blender builds the system does not know), with the system default, or in
Explorer; from the context menu and for the prompt-bar agent.

``config`` (the TOML files, validation, placeholders, match rules), ``run`` (detached
processes), ``book`` (one project's launchers and the launches), ``menu`` (the context
menu's entries), ``agent`` (the ``launchers`` / ``launch`` tools) are Qt-free; ``watch``
is the shell's file watcher.
"""

from __future__ import annotations

from .book import SYSTEM, Launched, LauncherBook, book_for
from .config import Launcher, Problem

__all__ = ["SYSTEM", "Launched", "Launcher", "LauncherBook", "Problem", "book_for"]
