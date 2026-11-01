"""The 3D view's share of an intaker resolve: ``attach_scenes`` prepares every item's
3D part (``specto_readers.scene3d.prepare_scene``) into the item cache dir and records
the result under ``item.meta["scene"]``. Items without a 3D part are left alone; a
file that cannot be read gets ``{"kind": "error", "error": ...}`` so the view can say
so instead of the whole collection failing.
"""

from __future__ import annotations

from typing import Any

from specto_core.kinds import Item
from specto_readers.scene3d import prepare_scene, scene_role

from .cache import default_cache_root


def attach_scenes(items: list[Item], *, progress: Any = None) -> list[Item]:
    cache = default_cache_root()
    with_part = [(it, role) for it in items if (role := scene_role(it.parts)) is not None]
    for i, (it, role) in enumerate(with_part):
        if progress:
            progress(0.8 + 0.2 * i / max(1, len(with_part)), f"preparing {it.key}")
        try:
            it.meta["scene"] = prepare_scene(it.parts[role], cache)
        except Exception as e:  # one unreadable file must not take the collection down
            it.meta["scene"] = {"kind": "error", "role": role, "files": {}, "error": str(e)}
    return items


__all__ = ["attach_scenes"]
