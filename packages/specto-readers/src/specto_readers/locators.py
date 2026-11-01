"""Built-in locators (HLD 3.7): cheap, by extension, GUI-thread safe.

A locator answers ``can_handle(path) -> priority`` and turns an ``Item`` into a
``DataRef`` for the part it handles. The manifests the core already registers under the
``builtin`` plugin (``specto_core.kinds.BUILTIN_LOCATORS``) are the source of truth for
extensions, aliases and kinds; this module supplies the behaviour.
"""

from __future__ import annotations

from pathlib import PurePath

from specto_core.containers import stat_virtual
from specto_core.errors import NotFound
from specto_core.kinds import BUILTIN_LOCATORS, DataKind, DataRef, Item, LocatorManifest


class ExtensionLocator:
    """Handles files by extension; ``locate`` picks the first matching part of the item."""

    def __init__(self, manifest: LocatorManifest) -> None:
        self.manifest = manifest
        self.name = manifest.name
        self.extensions = tuple(e.lower() for e in manifest.extensions)
        self.kinds: list[DataKind] = list(manifest.kinds)
        self.priority = manifest.priority

    @property
    def kind(self) -> DataKind:
        return self.kinds[0]

    def can_handle(self, path: str) -> int:
        """Priority for ``path`` (a virtual ``archive#/member`` path counts by its member
        suffix). A manifest without kinds (the container one) handles nothing here:
        intake expands archives, the members carry the kinds."""
        if not self.kinds:
            return 0
        return self.priority if PurePath(path).suffix.lower() in self.extensions else 0

    def part_of(self, item: Item) -> tuple[str, str] | None:
        for role, path in item.parts.items():
            if self.can_handle(path):
                return role, path
        return None

    def locate(self, item: Item) -> DataRef:
        hit = self.part_of(item)
        if hit is None:
            raise NotFound(
                f"item {item.key!r} has no part the {self.name} locator handles",
                fix=f"parts: {list(item.parts)}; handled: {list(self.extensions)}",
            )
        role, path = hit
        meta = {"key": item.key, "role": role, "index": item.index}
        try:
            st = stat_virtual(path)  # the archive's stat for a member
            meta["size"] = st.st_size
            meta["mtime_ns"] = st.st_mtime_ns
        except OSError as e:
            meta["missing"] = str(e)
        return DataRef(kind=self.kind, uri=path, meta=meta)

    def __repr__(self) -> str:
        return f"ExtensionLocator({self.name!r}, {self.kind}, {self.extensions})"


def builtin_locators() -> list[ExtensionLocator]:
    return [ExtensionLocator(m) for m in BUILTIN_LOCATORS]
