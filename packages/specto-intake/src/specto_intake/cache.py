"""Item-list cache: the platform cache dir, keyed by project id and content hash.

``<user cache>/specto/items/<project id>/<col>.<content_hash>.json``. The document only
keeps ``item_count`` and ``content_hash``; the gallery and ``intake.ls`` read the list
from here. ``SPECTO_CACHE_DIR`` overrides the root (tests, portable installs).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import platformdirs

from specto_core.kinds import Item

from .scan import items_to_json

APP_NAME = "specto"
NO_PROJECT = "no-project"


def default_cache_root() -> Path:
    env = os.environ.get("SPECTO_CACHE_DIR")
    if env:
        return Path(env)
    return Path(platformdirs.user_cache_dir(APP_NAME, APP_NAME))


class ItemCache:
    def __init__(self, project_id: str | None, root: Path | None = None) -> None:
        self.project_id = project_id or NO_PROJECT
        self.root = Path(root) if root else default_cache_root()

    @property
    def dir(self) -> Path:
        return self.root / "items" / self.project_id

    def path(self, col_id: str, content_hash: str) -> Path:
        return self.dir / f"{col_id}.{content_hash}.json"

    def has(self, col_id: str, content_hash: str) -> bool:
        return self.path(col_id, content_hash).exists()

    def write(self, col_id: str, content_hash: str, items: list[Item]) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.path(col_id, content_hash)
        payload = {"col": col_id, "content_hash": content_hash, "items": None}
        head, tail = json.dumps(payload).split("null")
        fd, tmp = tempfile.mkstemp(prefix=f"{col_id}.", suffix=".tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(head)
                f.write(items_to_json(items))
                f.write(tail)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return target

    def read(self, col_id: str, content_hash: str) -> list[Item] | None:
        p = self.path(col_id, content_hash)
        if not p.exists():
            return None
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return [Item.model_validate(d) for d in data["items"]]

    def prune(self, col_id: str, keep_hash: str | None) -> int:
        """Drop older item lists of ``col_id``; returns how many files went."""
        n = 0
        if not self.dir.exists():
            return 0
        for p in self.dir.glob(f"{col_id}.*.json"):
            if keep_hash is not None and p.name == f"{col_id}.{keep_hash}.json":
                continue
            p.unlink(missing_ok=True)
            n += 1
        return n
