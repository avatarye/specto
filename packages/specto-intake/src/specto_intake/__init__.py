"""Specto intake: sources, screener, keys, grouping, sort (HLD 3.6). No Qt.

Importing this package registers the ``intake.*`` commands in the core registry.

- ``IntakeSpec`` / ``SourceSpec`` / ``Rule``: the spec as data.
- ``Screener``: ordered include/exclude rules compiled to regex.
- ``scan`` / ``explain``: pure functions over a spec; ``ScanResult`` carries items,
  content hash and the kinds histogram.
- ``ItemCache``: item lists in the platform cache dir keyed by project id.
- ``resolve``: the ``intaker`` link resolver and its watcher (``params.watch``).
"""

from . import commands as _commands  # noqa: F401  (registers intake.* commands)
from . import resolve as _resolve  # noqa: F401  (registers the intaker link resolver)
from .cache import ItemCache, default_cache_root
from .keys import group_items, natural_key
from .preview import KEY_PRESETS, guess_keys, keys_preview, preview
from .scan import ScanResult, content_hash, explain, scan, scan_env
from .screener import Screener, glob_to_regex
from .sources import FoundFile, walk_source
from .spec import IntakeSpec, Rule, SourceSpec

__version__ = "0.2.0"

__all__ = [
    "KEY_PRESETS",
    "FoundFile",
    "IntakeSpec",
    "ItemCache",
    "Rule",
    "ScanResult",
    "Screener",
    "SourceSpec",
    "content_hash",
    "default_cache_root",
    "explain",
    "glob_to_regex",
    "group_items",
    "guess_keys",
    "keys_preview",
    "natural_key",
    "preview",
    "scan",
    "scan_env",
    "walk_source",
]
