"""Reader registry: locator and decoder implementations by name, kind and path.

The core ``PluginRegistry`` holds the *manifests* (vocabulary, no code); this registry
holds the *objects*. The worker pool looks decoders up here by name, so a decoder must
be registered in the worker process too: ``default_readers()`` builds the built-ins on
first use in any process.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from specto_core.errors import NotFound
from specto_core.kinds import (
    BUILTIN_DECODERS,
    BUILTIN_LOCATORS,
    DataRef,
    Decoder,
    DecodeRequest,
    Item,
    Locator,
    PluginManifest,
    TypedBuffer,
)

from .decoders import builtin_decoders
from .locators import builtin_locators


class ReaderRegistry:
    def __init__(self, locators: Iterable[Locator] = (), decoders: Iterable[Decoder] = ()) -> None:
        self._locators: dict[str, Locator] = {}
        self._decoders: dict[str, Decoder] = {}
        self._decoders_by_kind: dict[str, list[str]] = {}
        for lo in locators:
            self.register_locator(lo)
        for d in decoders:
            self.register_decoder(d)

    # -- registration ----------------------------------------------------------------
    def register_locator(self, locator: Locator) -> None:
        self._locators[locator.name] = locator

    def register_decoder(self, decoder: Decoder) -> None:
        self._decoders[decoder.name] = decoder
        for k in getattr(decoder, "kinds", ()):
            lst = self._decoders_by_kind.setdefault(k, [])
            if decoder.name not in lst:
                lst.append(decoder.name)

    # -- lookup ----------------------------------------------------------------------
    def locator(self, name: str) -> Locator:
        lo = self._locators.get(name)
        if lo is None:
            raise NotFound(f"no locator {name!r}", fix=f"known: {sorted(self._locators)}")
        return lo

    def decoder(self, name: str) -> Decoder:
        d = self._decoders.get(name)
        if d is None:
            raise NotFound(f"no decoder {name!r}", fix=f"known: {sorted(self._decoders)}")
        return d

    def locators_for(self, path: str) -> list[Locator]:
        """Every locator that handles ``path``, highest priority first."""
        hits = [(lo.can_handle(path), lo) for lo in self._locators.values()]
        return [lo for p, lo in sorted(hits, key=lambda t: -t[0]) if p > 0]

    def locator_for(self, path: str) -> Locator | None:
        hits = self.locators_for(path)
        return hits[0] if hits else None

    def decoder_for(self, kind: str, purpose: str = "full") -> Decoder | None:
        for name in self._decoders_by_kind.get(kind, []):
            d = self._decoders[name]
            if purpose in getattr(d, "purposes", ("full",)):
                return d
        return None

    def locate(self, item: Item, *, accepts: Iterable[str] | None = None) -> DataRef | None:
        """A ``DataRef`` for the item's best part, optionally limited to accepted kinds.
        None when no part resolves: the view lists the item as skipped."""
        wanted = set(accepts) if accepts is not None else None
        best: tuple[int, Locator] | None = None
        for path in item.parts.values():
            for lo in self.locators_for(path):
                kinds = list(getattr(lo, "kinds", []))
                if wanted is not None and not (wanted & set(kinds)):
                    continue
                prio = lo.can_handle(path)
                if best is None or prio > best[0]:
                    best = (prio, lo)
                break
        return best[1].locate(item) if best else None

    def decode(self, ref: DataRef, request: DecodeRequest | None = None) -> TypedBuffer:
        """Decode in this process (tests, headless). The app goes through the pool."""
        request = request or DecodeRequest()
        d = self.decoder_for(ref.kind, request.purpose)
        if d is None:
            raise NotFound(
                f"no decoder for kind {ref.kind!r} purpose {request.purpose!r}",
                fix=f"decoders: {sorted(self._decoders)}",
            )
        return d.decode(ref, request)

    def names(self) -> dict[str, list[str]]:
        return {"locators": sorted(self._locators), "decoders": sorted(self._decoders)}


_default: ReaderRegistry | None = None


def default_readers() -> ReaderRegistry:
    """Process-wide registry with the built-ins (created lazily in workers too)."""
    global _default
    if _default is None:
        _default = ReaderRegistry(builtin_locators(), builtin_decoders())
    return _default


def plugin_manifest() -> PluginManifest:
    """The same vocabulary the core registers as ``builtin``; for hosts that build their
    own ``PluginRegistry`` and want the reader manifests under this package's name."""
    return PluginManifest(
        name="specto-readers",
        version="0.1.0",
        locators=list(BUILTIN_LOCATORS),
        decoders=list(BUILTIN_DECODERS),
    )


def describe() -> dict[str, Any]:
    reg = default_readers()
    return {
        "locators": [m.model_dump() for m in BUILTIN_LOCATORS],
        "decoders": [m.model_dump() for m in BUILTIN_DECODERS],
        "registered": reg.names(),
    }
