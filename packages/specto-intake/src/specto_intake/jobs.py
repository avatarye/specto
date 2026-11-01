"""The scan as a job (HLD 4.2): picklable entry point for any ``JobManager``.

The worker scans, writes the item list into the cache and returns only the
document-sized summary; the GUI side then calls ``col.update``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cache import ItemCache
from .scan import scan


def scan_job(
    spec: dict[str, Any],
    base: str,
    aliases: dict[str, list[str]],
    ext_kinds: dict[str, list[str]],
    cache_root: str,
    project_id: str | None,
    col_id: str,
    progress: Callable[[float, str], None],
) -> dict[str, Any]:
    result = scan(spec, base=Path(base), aliases=aliases, ext_kinds=ext_kinds, progress=progress)
    cache = ItemCache(project_id, Path(cache_root))
    cache.write(col_id, result.content_hash, result.items)
    summary = result.summary()
    summary["cache_path"] = str(cache.path(col_id, result.content_hash))
    return summary
