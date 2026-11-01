"""Per-view capability lists (HLD 3.11, on ports per ADR 0014): ``read:link:l3``,
``emit:selection``, ``params:layers``, ``cmd:describe``, ``net:tiles``, ``net:web``.

Pure (no Qt). The Python side of the bridge, the URL-scheme handler and the network
request interceptor all consult a ``Capabilities`` object; the page never sees more than
it was granted. Lists are built by the app from the view's type and the links feeding
it (``default_capabilities``), never from the page. The document's ``params`` may only
narrow them (an agent must not widen them): ``network: false`` drops the type's ``net:``
grants.

Kinds:

- ``read:link:<id>``: fetch the bytes behind the value a link delivered to this view
  (collection items, read contents) over ``specto://items/<link>/<key>/<role>``.
- ``emit:<port>``: emit on that output port of the view (``emit``); receiving is free.
- ``params:<key>``: write that key of the view's own ``params`` as a user submit.
- ``cmd:<name or glob>``: call that command through the bridge.
- ``net:tiles``: load basemap tiles from the hosts in ``TILE_HOSTS``.
- ``net:web``: any http(s) request made in the page's frames, and http(s) navigations of
  its frames (never of the page itself): the html view's user page with its remote
  images and embedded maps (#93).

Every other network request from a page is blocked.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from specto_core.errors import SpectoError
from specto_core.ports import VIEW_PORTS

from . import basemaps

KINDS: tuple[str, ...] = ("read", "emit", "cmd", "net", "params")


class CapabilityDenied(SpectoError):
    code = "capability"


def _parse(cap: str) -> tuple[str, str]:
    if ":" not in cap:
        raise ValueError(f"capability {cap!r}: expected kind:target, e.g. emit:selection")
    kind, target = cap.split(":", 1)
    if kind not in KINDS:
        raise ValueError(f"capability {cap!r}: kind must be one of {KINDS}")
    if not target:
        raise ValueError(f"capability {cap!r}: empty target")
    return kind, target


@dataclass(frozen=True)
class Capabilities:
    """An immutable set of ``kind:target`` grants. Targets may use ``*`` globs
    (``cmd:canvas.*``, ``read:link:*``); matching is exact-case ``fnmatch``."""

    grants: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def of(cls, caps: Iterable[str]) -> Capabilities:
        seen = []
        for c in caps:
            _parse(c)
            seen.append(c)
        return cls(frozenset(seen))

    def allows(self, kind: str, target: str) -> bool:
        for g in self.grants:
            gk, gt = _parse(g)
            if gk == kind and fnmatchcase(target, gt):
                return True
        return False

    def require(self, kind: str, target: str, *, page: str = "") -> None:
        if not self.allows(kind, target):
            raise CapabilityDenied(
                f"page {page or '?'} lacks capability {kind}:{target}",
                fix=f"grant {kind}:{target} to the page in the app (capabilities are app-side)",
                kind=kind,
                target=target,
                granted=sorted(self.grants),
            )

    def can_read(self, link_id: str) -> bool:
        return self.allows("read", f"link:{link_id}")

    def can_emit(self, port: str) -> bool:
        return self.allows("emit", port)

    def can_set_param(self, key: str) -> bool:
        return self.allows("params", key)

    def can_call(self, command: str) -> bool:
        return self.allows("cmd", command)

    def can_net(self, what: str) -> bool:
        return self.allows("net", what)

    def __iter__(self):
        return iter(sorted(self.grants))

    def __contains__(self, cap: object) -> bool:
        return cap in self.grants

    def __len__(self) -> int:
        return len(self.grants)


#: Commands any page may call; all read-only and cheap.
ALWAYS_ALLOWED_COMMANDS: tuple[str, ...] = ("describe", "vocabulary")

#: Hosts a page holding ``net:tiles`` may fetch from (raster basemap tiles): the hosts of
#: the basemap providers' templates (``basemaps.BASEMAPS``, #157). Everything else off the
#: ``specto://`` scheme is blocked by the request interceptor.
TILE_HOSTS: tuple[str, ...] = basemaps.tile_hosts()

#: Default grants per built-in web view type, beyond its output ports and its links in.
#: Other web view types (the 3D view) add their own entry.
_TYPE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "json": ("params:wrap",),
    # `panels` {<name>: {collapsed, width}}: the side panels' state (sidepanel.js, #130)
    "markdown": ("params:panels",),
    "map": (
        "params:layers",
        "params:basemap",
        "params:panels",
        # `limit`: the footer's Load more raises it by a batch (#181)
        "params:limit",
        "net:tiles",
    ),
    # `view_mode`: lit / unlit / wireframe / collision, the footer's mode pill (#153);
    # `environment*`: the footer's HDRI pill and the VIEW panel's environment group (#175)
    "3d": (
        "params:point_size",
        "params:up",
        "params:camera_speed",
        "params:panels",
        "params:view_mode",
        "params:environment",
        "params:environment_intensity",
        "params:environment_background",
        "params:environment_rotation",
    ),
    "html": ("net:web",),  # a user's page: its files and the network, no params
}

#: The view param that turns a type's network grants off (``network: false``).
NETWORK_PARAM = "network"


def tile_host_allowed(host: str) -> bool:
    return host.lower() in TILE_HOSTS


def default_capabilities(
    view_type: str, inputs: Mapping[str, str], params: Mapping[str, object] | None = None
) -> Capabilities:
    """Grants for a view of ``view_type`` whose input ports are fed by the links in
    ``inputs`` (``{port: link id}``): read every link in, emit every output port the
    type declares, the type's own params and network needs (none when the view's
    ``params`` say ``network: false``), and the always-allowed commands."""
    caps: list[str] = [f"cmd:{c}" for c in ALWAYS_ALLOWED_COMMANDS]
    caps.extend(f"read:link:{lid}" for lid in inputs.values() if lid)
    decl = VIEW_PORTS.get(view_type)
    if decl is not None:
        caps.extend(f"emit:{port}" for port in decl.outputs)
    caps.extend(_TYPE_DEFAULTS.get(view_type, ()))
    if params is not None and params.get(NETWORK_PARAM) is False:
        caps = [c for c in caps if not c.startswith("net:")]
    return Capabilities.of(dict.fromkeys(caps))


__all__ = [
    "ALWAYS_ALLOWED_COMMANDS",
    "KINDS",
    "NETWORK_PARAM",
    "TILE_HOSTS",
    "Capabilities",
    "CapabilityDenied",
    "default_capabilities",
    "tile_host_allowed",
]
