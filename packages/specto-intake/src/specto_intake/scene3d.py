"""The 3D view's share of an intaker resolve: ``attach_scenes`` prepares every item's
3D part (``specto_readers.scene3d.prepare_scene``) into the item cache dir and records
the result under ``item.meta["scene"]``. Items without a 3D part are left alone; a
file that cannot be read gets ``{"kind": "error", "error": ...}`` so the view can say
so instead of the whole collection failing.

A DAE's converted mesh lives next to it (``robot.dae.glb``, SPEC-42), so a folder's @3d
screener lists it as well: while its DAE is in the same collection, the converted mesh's
item gets no scene, and the view shows the model once (from the DAE, regenerated when
the DAE changes). A converted mesh whose DAE is not in the collection is an ordinary GLB.

``root`` (the collection's root, the project folder for an intaker) bounds the files an
OBJ may name next to it (``../textures/skin.png`` inside the project is fine).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from specto_core.kinds import Item
from specto_readers.dae import converted_path
from specto_readers.scene3d import DAE_EXTS, prepare_scene, scene_role

from .cache import default_cache_root


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def attach_scenes(
    items: list[Item], *, progress: Any = None, root: str | Path | None = None
) -> list[Item]:
    cache = default_cache_root()
    with_part = [(it, role) for it in items if (role := scene_role(it.parts)) is not None]
    converted = {
        _norm(converted_path(it.parts[role])) for it, role in with_part if role.lower() in DAE_EXTS
    }
    with_part = [(it, role) for it, role in with_part if _norm(it.parts[role]) not in converted]
    for i, (it, role) in enumerate(with_part):
        if progress:
            progress(0.8 + 0.2 * i / max(1, len(with_part)), f"preparing {it.key}")
        try:
            it.meta["scene"] = prepare_scene(it.parts[role], cache, root=root)
        except Exception as e:  # one unreadable file must not take the collection down
            it.meta["scene"] = {"kind": "error", "role": role, "files": {}, "error": str(e)}
    return items


__all__ = ["attach_scenes"]
