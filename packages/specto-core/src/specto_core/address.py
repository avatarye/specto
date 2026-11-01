"""Address grammar (HLD 3.3).

    address  := [ "proj:" slug "/" ] "canvas:" id "/" object { "." property }
    object   := kind ":" id      kind in col view group frame region link chan space job recipe

``canvas:`` is never implicit in a tool call. ``recipe:`` and ``job:`` are project-scoped
and may omit the canvas component. ``proj:`` defaults to the connection's project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import AddressError
from .names import is_name

KINDS: tuple[str, ...] = (
    "col",
    "view",
    "group",
    "frame",
    "region",
    "link",
    "chan",
    "space",
    "job",
    "recipe",
)
PROJECT_SCOPED_KINDS: frozenset[str] = frozenset({"job", "recipe"})

#: kind -> id prefix (HLD 3.2). Ids are kind-prefixed and monotonic per canvas.
ID_PREFIX: dict[str, str] = {
    "view": "v",
    "col": "c",
    "group": "g",
    "frame": "f",
    "link": "l",
    "region": "r",
    "chan": "ch",
    "space": "s",
    "job": "j",
    "recipe": "rc",
}
PREFIX_KIND: dict[str, str] = {v: k for k, v in ID_PREFIX.items()}

_SLUG = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_ID = r"[A-Za-z0-9_][A-Za-z0-9_-]*"
_PROP = r"[A-Za-z_][A-Za-z0-9_]*"
_RE = re.compile(
    rf"^(?:proj:(?P<proj>{_SLUG})/)?"
    rf"(?:canvas:(?P<canvas>{_ID})(?:/|$))?"
    rf"(?:(?P<kind>[a-z]+):(?P<id>{_ID}))?"
    rf"(?P<props>(?:\.{_PROP})*)$"
)


@dataclass(frozen=True, slots=True)
class Address:
    kind: str | None = None
    id: str | None = None
    canvas: str | None = None
    proj: str | None = None
    props: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_canvas(self) -> bool:
        return self.kind is None and self.canvas is not None

    @property
    def object_ref(self) -> str:
        """``kind:id`` without canvas or project."""
        if self.kind is None or self.id is None:
            raise AddressError("address names no object")
        return f"{self.kind}:{self.id}"

    @property
    def prop_pointer(self) -> str:
        """JSON Pointer suffix for the property path (``.state.zoom`` -> ``/state/zoom``)."""
        return "".join("/" + p for p in self.props)

    def with_canvas(self, canvas: str) -> Address:
        return Address(self.kind, self.id, canvas, self.proj, self.props)

    def __str__(self) -> str:
        return format_address(self)


def parse_address(
    text: str, *, default_canvas: str | None = None, default_proj: str | None = None
) -> Address:
    """Parse an address string. Raises ``AddressError`` with a fix-it hint.

    Bare ids are accepted when they identify the kind on their own: a view name
    (``tamu``, ``files``) or a prefixed id (``l3``); this is a convenience for humans,
    agents should send ``view:tamu``.
    """
    if not isinstance(text, str) or not text.strip():
        raise AddressError("empty address", fix="use canvas:<id>/<kind>:<id>")
    text = text.strip()
    bad = AddressError(
        f"cannot parse address {text!r}", fix="grammar: [proj:slug/]canvas:id/kind:id[.prop]"
    )
    m = _RE.match(text)
    if not m:
        # maybe a bare view name ("tamu", "tamu.state") or a prefixed id ("l3")
        head, *rest = text.split(".")
        bare = re.match(r"^(?P<prefix>[a-z]+)\d+$", head)
        if is_name(head):
            kind = "view"
        elif bare and bare.group("prefix") in PREFIX_KIND:
            kind = PREFIX_KIND[bare.group("prefix")]
        else:
            raise bad
        if default_canvas is None and kind not in PROJECT_SCOPED_KINDS:
            raise AddressError(
                f"address {text!r} names no canvas", fix=f"prefix it: canvas:<id>/{kind}:{head}"
            )
        return Address(kind, head, default_canvas, default_proj, tuple(p for p in rest if p))
    proj = m.group("proj") or default_proj
    canvas = m.group("canvas") or default_canvas
    kind = m.group("kind")
    oid = m.group("id")
    props = tuple(p for p in m.group("props").split(".") if p)
    if kind is None and oid is None:
        if m.group("canvas") is None:
            raise bad
        if props:
            raise AddressError("a canvas address takes no property path")
        return Address(None, None, canvas, proj, ())
    if kind not in KINDS:
        raise AddressError(f"unknown kind {kind!r}", fix=f"kinds: {', '.join(KINDS)}")
    if canvas is None and kind not in PROJECT_SCOPED_KINDS:
        raise AddressError(
            f"address {text!r} names no canvas", fix=f"prefix it: canvas:<id>/{kind}:{oid}"
        )
    return Address(kind, oid, canvas, proj, props)


def format_address(a: Address) -> str:
    parts = []
    if a.proj:
        parts.append(f"proj:{a.proj}/")
    if a.canvas:
        parts.append(f"canvas:{a.canvas}")
        if a.kind:
            parts.append("/")
    if a.kind:
        parts.append(f"{a.kind}:{a.id}")
    return "".join(parts) + "".join("." + p for p in a.props)


def kind_of_id(oid: str) -> str | None:
    """Return the kind an id shows on its own (a view name ``tamu`` ->
    ``view``, a prefixed id ``l3`` -> ``link``), or None."""
    if is_name(oid):
        return "view"
    m = re.match(r"^([a-z]+)\d+$", oid)
    if not m:
        return None
    return PREFIX_KIND.get(m.group(1))
