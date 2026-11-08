"""The ``specto://`` URL scheme for web views (HLD 3.11; spike S2 rules).

Hosts:

- ``<view id>.page``: a page's own origin. Any path resolves under the web root
  (``packages/specto-web/web``): the pages, ``bridge.js``, ``vendor/vs``.
- ``app``: the same files for pages that are not a view (demos, docs).
- ``<view id>.items``: the same bytes as ``items`` (below), under the grants of that
  view's page, as an origin of their own: a document served from here (the html view's
  page in its frame) resolves its relative URLs next to itself and stays a different
  origin from the view's page (no bridge). The host names the page whose grants
  apply; a request another page initiates is refused.
- ``items``: the bytes behind a link's value, ``/<link id>/<key>/<role>`` (role is the
  part name, ``.json``/``.jpg``; a key with slashes is percent-encoded into one
  segment). Served only when the request's **initiator** host is a page whose
  capabilities hold ``read:link:<link id>``. Chromium stamps the initiator; the page
  cannot forge it. Top-level navigations to ``items`` are refused. A trailing relative
  path, ``/<link>/<key>/<role>/<rel/path.png>``, serves a file next to that part (a
  note's relative image): ``rel`` may not climb (no ``..``, no absolute path) and the
  result must stay under the part's directory. A trailing path that starts with
  ``@root/`` is relative to the link's root (the project folder, ``ItemResolver.root``)
  instead, must stay under it and name what an OBJ or glTF names (``ROOT_SUFFIXES``: an
  MTL, a buffer, an image): a page that addresses an OBJ as
  ``.../obj/@root/robot/meshes/panel.obj`` gets its ``../textures/skin.png`` (the URL
  resolves the ``..`` before the request) as long as it stays inside the project
  (SPEC-42).

Network: every request a page makes off the ``specto://`` scheme goes through the
request interceptor (``make_interceptor``), under the grants of the view it is made in
(``net_decision``): any http(s) request when the view holds ``net:web`` (the html view
unless its ``network`` param is false), a basemap tile server
(``capabilities.TILE_HOSTS``) when it holds ``net:tiles``; everything else is blocked,
and a main-frame load off the scheme always is. ``qrc:`` (the QWebChannel script) is
always allowed.

Range semantics (measured on PySide6 6.12.0, spike S2): a request carrying a ``Range``
header must receive the full body (Chromium slices and answers 206 itself; a partial
body crashes the process). Large-file slicing is app-controlled with ``?range=start-end``
in the URL, answered as 200 with just the slice and an ``X-Specto-Range`` header.

The pure helpers (``parse_range``, ``resolve_asset``, ``parse_item_path``) have no Qt and
are unit-tested; ``make_handler`` builds the ``QWebEngineUrlSchemeHandler``.
"""

from __future__ import annotations

import mimetypes
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote

from specto_core.containers import is_virtual, open_source, real_path
from specto_core.pathguard import Refusal, output_refusal, path_refusal

from .capabilities import Capabilities, tile_host_allowed

if TYPE_CHECKING:
    from .items import ItemResolver

SCHEME = b"specto"
PAGE_SUFFIX = ".page"
ITEMS_SUFFIX = ".items"
HOST_APP = "app"
HOST_ITEMS = "items"

_RANGE_RE = re.compile(rb"^bytes=(\d*)-(\d*)$")
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_MAX_KEY = 1024


def _plain_text(text: str) -> bool:
    """No control characters and no backslash: what a key or a path segment may hold
    beyond that is any name a file can have (dot folders, spaces, non-ASCII)."""
    return not any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in text)


def _key_ok(key: str) -> bool:
    """An item key is a lookup into a link's items, never opened as a path; refuse
    only what could not be a relative item key (traversal, empty segments)."""
    if not key or len(key) > _MAX_KEY or not _plain_text(key):
        return False
    return all(seg not in ("", ".", "..") for seg in key.split("/"))


_ROLE_RE = re.compile(r"^\.?[A-Za-z0-9_-]{1,32}$")


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive
    total: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def content_range(self) -> bytes:
        return b"bytes %d-%d/%d" % (self.start, self.end, self.total)


def parse_range(header: bytes | str | None, total: int) -> ByteRange | None:
    """Parse one ``bytes=a-b`` range against ``total``; ``None`` = serve everything."""
    if not header or total <= 0:
        return None
    if isinstance(header, str):
        header = header.encode()
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    s, e = m.group(1), m.group(2)
    if s == b"" and e == b"":
        return None
    if s == b"":
        n = min(int(e), total)
        return None if n == 0 else ByteRange(total - n, total - 1, total)
    start = int(s)
    if start >= total:
        return None
    end = total - 1 if e == b"" else min(int(e), total - 1)
    return None if end < start else ByteRange(start, end, total)


def slice_file(path: Path, rng: ByteRange) -> bytes:
    with path.open("rb") as fh:
        fh.seek(rng.start)
        return fh.read(rng.length)


def source_size(path: Path | str) -> int:
    """Size of an item part: the file, or the member of a container (``archive#/member``,
    M2 containers) read by stream."""
    text = str(path)
    if not is_virtual(text):
        return Path(text).stat().st_size
    with open_source(text) as fh:
        fh.seek(0, 2)
        return int(fh.tell())


def read_source(path: Path | str, rng: ByteRange | None = None) -> bytes:
    """Bytes of an item part (whole or a range): a plain file or a container member."""
    text = str(path)
    if not is_virtual(text):
        p = Path(text)
        return slice_file(p, rng) if rng is not None else p.read_bytes()
    with open_source(text) as fh:
        if rng is None:
            return fh.read()
        fh.seek(rng.start)
        return fh.read(rng.length)


def _rooted(rel: str) -> bool:
    """``rel`` names a root itself (a leading separator, or a drive colon second), so
    joined onto a base it would replace the base. Text only."""
    return rel[:1] in ("\\", "/") or rel[1:2] == ":"


def resolve_asset(root: Path, url_path: str) -> Path | None:
    """Map a URL path to a file under ``root``; ``None`` if it escapes or is missing.
    A directory path serves its ``index.html``. The text after the leading slashes may
    not be rooted and passes ``specto_core.pathguard`` before any filesystem call: a
    note's ``![x](/\\\\host\\share\\x.png)`` reaches the page host decoded, as a UNC path
    that would replace ``root`` and make Windows connect out (SPEC-61)."""
    rel = url_path.lstrip("/")
    if _rooted(rel) or path_refusal(rel):
        return None
    root = root.resolve()
    candidate = (root / rel).resolve() if rel else root
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate if candidate.is_file() else None


def page_id_of_host(host: str) -> str | None:
    """``tamu.page`` -> ``tamu``; anything else -> ``None``."""
    if host.endswith(PAGE_SUFFIX):
        vid = host[: -len(PAGE_SUFFIX)]
        if _ID_RE.match(vid):
            return vid
    return None


def items_page_of_host(host: str) -> str | None:
    """``v3.items`` -> ``tamu``; anything else -> ``None``."""
    if host.endswith(ITEMS_SUFFIX):
        vid = host[: -len(ITEMS_SUFFIX)]
        if _ID_RE.match(vid):
            return vid
    return None


def page_url(view_id: str, page: str) -> str:
    """``page_url("tamu", "json/index.html") -> "specto://tamu.page/json/index.html"``."""
    return f"{SCHEME.decode()}://{view_id}{PAGE_SUFFIX}/{page.lstrip('/')}"


def navigation_allowed(page_id: str, url: str, main_frame: bool, *, network: bool = False) -> bool:
    """Where a web view may navigate (``WebView.qml`` asks the bridge, which asks here):
    the main frame only to the page's own origin; a frame inside the page (the html
    view's sandboxed iframe) also to the view's own items origin, where the user's page
    and its relative assets live (``item_doc_url``), and to ``about:blank``; with
    ``network`` (the view holds ``net:web``) a frame also to http(s): a map embed in the
    user's page. Everything else, the shared ``items`` host, another view's origin, the
    network without the grant, is refused."""
    scheme = SCHEME.decode()
    if url.startswith(f"{scheme}://{page_id}{PAGE_SUFFIX}/"):
        return True
    if main_frame:
        return False
    if network and url.startswith(("http://", "https://")):
        return True
    return url == "about:blank" or url.startswith(f"{scheme}://{page_id}{ITEMS_SUFFIX}/")


def item_url(link_id: str, key: str, role: str) -> str:
    """``item_url("l3", "runs/front/00042", ".json")`` ->
    ``specto://items/l3/runs%2Ffront%2F00042/json`` (the key is one encoded segment)."""
    return f"{SCHEME.decode()}://{HOST_ITEMS}/{link_id}/{quote(key, safe='')}/{role.lstrip('.')}"


def item_doc_url(view_id: str, link_id: str, key: str, role: str, name: str) -> str:
    """The item's part as a document on the view's own items origin, ending in its file
    name so its relative URLs resolve next to it:
    ``item_doc_url("pola", "l3", "deck", ".html", "deck.html")`` ->
    ``specto://pola.items/l3/deck/html/deck.html``."""
    return (
        f"{SCHEME.decode()}://{view_id}{ITEMS_SUFFIX}/{link_id}/{quote(key, safe='')}"
        f"/{role.lstrip('.')}/{quote(name, safe='')}"
    )


def parse_item_path(url_path: str) -> tuple[str, str, str] | None:
    """``/l3/00042/json`` -> ``("l3", "00042", ".json")``; ``None`` when malformed."""
    parsed = parse_item_rel_path(url_path)
    if parsed is None or parsed[3] is not None:
        return None
    return parsed[:3]


def _rel_segment_ok(seg: str) -> bool:
    """A segment of a note's relative image path: any file name, but no traversal, no
    drive or stream colon (``C:``), no control characters (``resolve_relative`` also
    keeps the result next to the note)."""
    return 0 < len(seg) <= 255 and seg not in (".", "..") and ":" not in seg and _plain_text(seg)


def parse_item_rel_path(url_path: str) -> tuple[str, str, str, str | None] | None:
    """Like ``parse_item_path`` with an optional trailing relative path:
    ``/l1/note/md/img/a.png`` -> ``("l1", "note", ".md", "img/a.png")``. The path is the
    fully encoded one (``QUrl.path(FullyEncoded)``): each segment is decoded here, so an
    encoded slash stays inside the key. Segments must be plain names (no ``..``, no
    empty, no drive); ``None`` when malformed."""
    parts = [unquote(p) for p in url_path.split("/") if p]
    if len(parts) < 3:
        return None
    col, key, role = parts[:3]
    if not (_ID_RE.match(col) and _key_ok(key) and _ROLE_RE.match(role)):
        return None
    rel: str | None = None
    if len(parts) > 3:
        segs = parts[3:]
        if not all(_rel_segment_ok(s) for s in segs):
            return None
        rel = "/".join(segs)
    return col, key, "." + role.lstrip(".").lower(), rel


#: the first segment of a trailing path that is relative to the link's root
ROOT_ANCHOR = "@root"
#: what an ``@root/`` path may name: what an OBJ or a glTF names next to it (its MTL, a
#: glTF's buffers, texture images), nothing else under the project
ROOT_SUFFIXES = frozenset(
    {".mtl", ".bin", ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".gif", ".webp", ".tif",
     ".tiff", ".dds", ".ktx", ".ktx2", ".basis", ".hdr", ".exr"}
)  # fmt: skip


def resolve_relative(part: Path, rel: str, root: str | Path | None = None) -> Path | None:
    """``rel`` resolved next to ``part`` (a note's image), or, when it starts with
    ``@root/``, under ``root``: the part itself (the page fetches an OBJ by that path) or
    what it may name (``ROOT_SUFFIXES``); ``None`` when it escapes that folder, there is no
    root, it names anything else, or it is not a file. The rel, and the text after
    ``@root/``, pass ``specto_core.pathguard``'s allowlist before any filesystem call: a
    UNC, device or NT object path would replace the base, and Windows would connect out
    to it (SPEC-61)."""
    if _rooted(rel) or path_refusal(rel):
        return None
    head, _, rest = rel.partition("/")
    anchored = head == ROOT_ANCHOR
    if anchored:
        if root is None or not rest or _rooted(rest) or path_refusal(rest):
            return None
        base, rel = Path(root).resolve(), rest
    else:
        base = part.resolve().parent
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    if anchored and candidate.suffix.lower() not in ROOT_SUFFIXES and candidate != part.resolve():
        return None
    return candidate if candidate.is_file() else None


def part_refused(part: str, root: str | None) -> Refusal | None:
    """Why an item's part may not be looked for on disk: ``pathguard.output_refusal`` of
    the text as recorded against the collection's ``root`` (a network path only inside a
    collection on that share), a container member judged by its archive."""
    return output_refusal(real_path(part) if is_virtual(part) else part, root)


def locate_item(
    host: str,
    url_path: str,
    initiator_host: str | None,
    *,
    caps_of: CapsLookup | None,
    items: ItemResolver | None,
    entry: dict[str, Any],
) -> Path | None:
    """The pure part of an items request: the file behind ``url_path`` (fully encoded)
    on ``host`` (``items``, or ``<view>.items``) for a request initiated by
    ``initiator_host`` (None: no initiator). On ``items`` the initiator must be a page
    holding ``read:link:<link>``; on ``<view>.items`` that view's page holds the grants
    and the initiator may only be that page, its items origin, or nothing (a sandboxed
    frame). ``entry`` gets the status and what was resolved. The part's own path passes
    ``specto_core.pathguard`` before anything is stat'ed (``refused``; SPEC-75): the
    backstop for every host that serves through items, whatever produced the item (a
    Python link's collection, a resolve step's derived files). A network path is served
    only when the collection's root is on that share and the part lies under it."""
    parsed = parse_item_rel_path(url_path)
    if parsed is None:
        entry["status"] = "bad_item_path"
        return None
    col, key, role, rel = parsed
    initiator = page_id_of_host(initiator_host) if initiator_host else None
    page = items_page_of_host(host)
    if page is None:
        page = initiator
    elif initiator_host and (initiator or items_page_of_host(initiator_host)) != page:
        page = None  # another page's request against this view's origin
    entry.update({"col": col, "key": key, "role": role, "page": page})
    caps = caps_of(page) if (caps_of is not None and page) else None
    if caps is None or not caps.can_read(col):
        entry["status"] = "denied"
        return None
    parts = items.parts(col, key) if items is not None else None
    if not parts or role not in parts:
        entry["status"] = "not_found"
        return None
    p = Path(parts[role])
    if part_refused(str(p), items.root(col) if items is not None else None):
        entry["status"] = "refused"
        return None
    if is_virtual(str(p)):  # a container member: the archive must exist
        return p if Path(real_path(str(p))).is_file() else None
    if rel is not None:
        entry["rel"] = rel
        p2 = resolve_relative(p, rel, items.root(col) if items is not None else None)
        if p2 is None:
            entry["status"] = "not_found"
        return p2
    return p if p.is_file() else None


def content_type(path: Path) -> bytes:
    mt, _ = mimetypes.guess_type(path.name)
    if path.suffix in (".js", ".mjs"):
        mt = "text/javascript"
    elif path.suffix == ".json":
        mt = "application/json"
    elif path.suffix == ".wasm":
        mt = "application/wasm"  # not left to the Windows registry (#66)
    return (mt or "application/octet-stream").encode()


def parse_query(query: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in query.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def record(
    log: list[dict[str, Any]] | None,
    entry: dict[str, Any],
    *,
    debug: bool = False,
    seen: set[tuple[Any, ...]] | None = None,
) -> str | None:
    """Keep ``entry`` on ``log`` and print it: every entry with ``debug``, otherwise only
    a request that was not served (denied, not found, blocked), once per host, path and
    status (``seen``), as a ``[WARN]`` line, so a page that never shows up leaves a trace
    in launch.log. Returns the printed line."""
    if log is not None:
        log.append(entry)
    status = entry.get("status", "ok")
    if status == "ok" and not debug:
        return None
    key = (entry.get("host"), entry.get("path"), status)
    if seen is not None and not debug:
        if key in seen:
            return None
        seen.add(key)
    tag = "[OK]" if status == "ok" else "[WARN]"
    line = f"{tag} scheme {entry}"
    print(line, file=sys.stderr, flush=True)
    return line


# -- Qt part ---------------------------------------------------------------------------

#: ``caps_of(view_id) -> Capabilities | None`` (None: no such page).
CapsLookup = Callable[[str], Capabilities | None]


def register_scheme() -> None:
    """Register ``specto://``. Must run before ``QGuiApplication`` is constructed."""
    from PySide6.QtWebEngineCore import QWebEngineUrlScheme

    if QWebEngineUrlScheme.schemeByName(SCHEME).name() == SCHEME:
        return
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.HostAndPort)
    scheme.setDefaultPort(443)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed  # qrc:///qtwebchannel/qwebchannel.js
        | QWebEngineUrlScheme.Flag.CorsEnabled  # items host is a different origin
        | QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)


def make_handler(
    root: Path,
    *,
    items: ItemResolver | None = None,
    caps_of: CapsLookup | None = None,
    log: list[dict[str, Any]] | None = None,
    debug: bool = False,
) -> Any:
    """Build the ``QWebEngineUrlSchemeHandler``. Install it with
    ``QWebEngineProfile.defaultProfile().installUrlSchemeHandler(SCHEME, handler)``.
    ``items`` / ``caps_of`` / ``log`` default to the process ``WebRuntime`` (live web views,
    intake-backed item resolvers), which is what the shell's ``qt_setup`` relies on."""
    from PySide6.QtCore import QBuffer, QByteArray, QFile, QIODevice, QUrl
    from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlSchemeHandler

    from .runtime import runtime

    rt = runtime()
    if items is None:
        items = rt.items
    if caps_of is None:
        caps_of = rt.caps_of
    if log is None:
        log = rt.log

    Err = QWebEngineUrlRequestJob.Error
    seen: set[tuple[Any, ...]] = set()

    def _log(entry: dict[str, Any]) -> None:
        record(log, entry, debug=debug, seen=seen)

    class SpectoSchemeHandler(QWebEngineUrlSchemeHandler):
        def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
            url = job.requestUrl()
            host = url.host()
            entry: dict[str, Any] = {"host": host, "path": url.path(), "status": "ok"}
            try:
                if host == HOST_ITEMS or items_page_of_host(host) is not None:
                    path = self._item_path(job, entry)
                elif host == HOST_APP or page_id_of_host(host) is not None:
                    path = resolve_asset(root, url.path())
                    if path is None:
                        entry["status"] = "not_found"
                else:
                    entry["status"] = "bad_host"
                    path = None
            except Exception as e:  # never let a Python error kill the request thread
                entry["status"] = f"error: {e}"
                path = None
            if path is None:
                _log(entry)
                job.fail(Err.RequestDenied if entry["status"] == "denied" else Err.UrlNotFound)
                return
            self._reply(job, path, url, entry)

        def _item_path(self, job: QWebEngineUrlRequestJob, entry: dict[str, Any]) -> Path | None:
            url = job.requestUrl()
            initiator = job.initiator()
            return locate_item(
                url.host(),
                url.path(QUrl.ComponentFormattingOption.FullyEncoded),
                initiator.host() if initiator.isValid() else None,
                caps_of=caps_of,
                items=items,
                entry=entry,
            )

        def _reply(self, job: Any, path: Path, url: Any, entry: dict[str, Any]) -> None:
            virtual = is_virtual(str(path))
            total = source_size(path)
            entry["total"] = total
            rng = None
            if url.hasQuery():
                q = parse_query(url.query())
                if "range" in q:
                    rng = parse_range(b"bytes=" + q["range"].encode(), total)
            # QMultiMap<QByteArray, QByteArray>: wrap values in a list (PySide6 6.12.0
            # iterates a bare QByteArray value into one header per character).
            hdrs = {
                QByteArray(b"Accept-Ranges"): [QByteArray(b"bytes")],
                QByteArray(b"Cache-Control"): [QByteArray(b"no-store")],
            }
            if entry.get("page"):
                # items is a different origin from the page: answer CORS for that page only
                origin = f"{SCHEME.decode()}://{entry['page']}{PAGE_SUFFIX}".encode()
                hdrs[QByteArray(b"Access-Control-Allow-Origin")] = [QByteArray(origin)]
                hdrs[QByteArray(b"Vary")] = [QByteArray(b"Origin")]
            if rng is None and not virtual:
                dev = QFile(str(path))  # streamed by Chromium, no Python copy
            else:
                # a range, or a container member (read by stream out of the archive)
                dev = QBuffer()
                dev.setData(QByteArray(read_source(path, rng)))
                if rng is not None:
                    hdrs[QByteArray(b"X-Specto-Range")] = [QByteArray(rng.content_range())]
                    entry["served"] = rng.length
                if virtual:
                    entry["member"] = True
            dev.open(QIODevice.OpenModeFlag.ReadOnly)
            dev.setParent(job)  # lifetime: the job owns the device (Qt's documented pattern)
            job.setAdditionalResponseHeaders(hdrs)
            _log(entry)
            job.reply(content_type(path), dev)

    handler = SpectoSchemeHandler()
    rt.handler = handler
    return handler


NET_PASS_SCHEMES: tuple[str, ...] = ("specto", "qrc", "blob", "data")


def net_page(initiator_host: str, first_party_host: str = "") -> str | None:
    """The view a request is made in: the top-level page (``first_party_host``: the main
    frame, which never leaves its view's page), else the initiator when it is a view's
    page or its items origin. A request a remote frame makes itself (inside a map embed)
    has that remote initiator, and the view's page as its first party (measured on
    PySide6 6.12.0)."""
    page = page_id_of_host(first_party_host) if first_party_host else None
    if page is None and initiator_host:
        page = page_id_of_host(initiator_host) or items_page_of_host(initiator_host)
    return page


def net_decision(
    initiator_host: str,
    url_scheme: str,
    url_host: str,
    caps_of: CapsLookup,
    *,
    first_party_host: str = "",
    main_frame: bool = False,
) -> str:
    """Pure part of the interceptor: ``"ok"`` or the reason a request is blocked.
    Requests on ``specto://``, ``qrc:``, ``blob:`` and ``data:`` are never blocked here
    (the scheme handler decides ``specto``); a main-frame load off them always is;
    anything else needs the view it is made in (``net_page``) to hold ``net:web``
    (http(s), any host) or ``net:tiles`` and a tile host."""
    if url_scheme in NET_PASS_SCHEMES:
        return "ok"
    if main_frame:
        return "denied:main_frame"
    page = net_page(initiator_host, first_party_host)
    if page is None:
        return "no_page"
    caps = caps_of(page)
    web = caps is not None and caps.can_net("web")
    tiles = caps is not None and caps.can_net("tiles")
    if web and url_scheme in ("http", "https"):
        return "ok"
    if not (web or tiles):
        return "denied:net"
    if not (tiles and tile_host_allowed(url_host)):
        return "denied:host"
    return "ok"


def make_interceptor(
    *, caps_of: CapsLookup | None = None, log: list[dict[str, Any]] | None = None
) -> Any:
    """Build the ``QWebEngineUrlRequestInterceptor`` that enforces the ``net:`` grants.
    Install with ``profile.setUrlRequestInterceptor(interceptor)`` (keep a reference)."""
    from PySide6.QtWebEngineCore import (
        QWebEngineUrlRequestInfo,
        QWebEngineUrlRequestInterceptor,
    )

    from .runtime import runtime

    rt = runtime()
    if caps_of is None:
        caps_of = rt.caps_of
    if log is None:
        log = rt.log

    seen: set[tuple[Any, ...]] = set()
    main_frame = QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame

    class SpectoRequestInterceptor(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info: Any) -> None:
            url = info.requestUrl()
            scheme = url.scheme()
            if scheme in NET_PASS_SCHEMES:
                return
            initiator = info.initiator()
            ihost = initiator.host() if initiator.isValid() else ""
            first = info.firstPartyUrl().host()
            why = net_decision(
                ihost,
                scheme,
                url.host(),
                caps_of,
                first_party_host=first,
                main_frame=info.resourceType() == main_frame,
            )
            if why != "ok":
                record(
                    log,
                    {
                        "host": url.host(),
                        "path": url.path(),
                        "status": f"net_{why}",
                        "page": net_page(ihost, first),
                    },
                    debug=rt.debug,
                    seen=seen,
                )
                info.block(True)

    interceptor = SpectoRequestInterceptor()
    rt.interceptor = interceptor
    return interceptor
