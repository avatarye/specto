"""Process-level web runtime: the live page table the scheme handler consults, the item
resolver that turns ``specto://items/...`` into files, and the handler install.

One per process (web views share one ``QWebEngineProfile``). ``install(...)`` runs once
after ``QGuiApplication`` exists; web views register themselves on construction, so a
request from ``specto://v3.page`` is checked against the capabilities of the live page
``v3`` and nothing else. Headless (tests): ``web views`` works without any Qt.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from .capabilities import Capabilities
from .items import CompositeItems, ItemResolver, LinkItems

if TYPE_CHECKING:
    from .webview import WebView


class WebRuntime:
    def __init__(self) -> None:
        self.pages: dict[str, WebView] = {}
        self.items = CompositeItems()
        self.handler: Any = None  # the installed QWebEngineUrlSchemeHandler (keep a ref)
        self.interceptor: Any = None  # the installed request interceptor (net:tiles)
        self.log: list[dict[str, Any]] = []
        self.debug = False
        self._links: dict[int, LinkItems] = {}

    # -- web views --------------------------------------------------------------------------
    def register(self, page: WebView) -> None:
        old = self.pages.get(page.page_id)
        if old is not None and old is not page:
            print(
                f"[WARN] web view id {page.page_id!r} reused while the old page is live; "
                "the newer one wins",
                file=sys.stderr,
            )
        self.pages[page.page_id] = page

    def unregister(self, page: WebView) -> None:
        if self.pages.get(page.page_id) is page:
            del self.pages[page.page_id]

    def caps_of(self, page_id: str) -> Capabilities | None:
        p = self.pages.get(page_id)
        return p.caps if p is not None else None

    def flush_all(self) -> int:
        return sum(p.flush() for p in list(self.pages.values()))

    # -- items -------------------------------------------------------------------------------
    def add_items(self, resolver: ItemResolver) -> None:
        """Add a resolver (the link runtime, a table); first match wins."""
        self.items.add(resolver)

    def ensure_links(self, engine: Any) -> LinkItems:
        """Resolve items through an engine's link runtime (once per engine)."""
        key = id(engine)
        r = self._links.get(key)
        if r is None:
            r = self._links[key] = LinkItems(engine.links)
            self.items.add(r)
        return r

    # -- Qt ---------------------------------------------------------------------------------
    def install(self, *, debug: bool = False) -> Any:
        """Install the ``specto://`` handler on the default profile (once). The shell's
        ``qt_setup.install_scheme_handler()`` does the same through ``make_handler``,
        whose defaults are this runtime's page table and item resolvers."""
        if self.handler is not None:
            return self.handler
        from PySide6.QtWebEngineCore import QWebEngineProfile

        from .assets import web_root
        from .scheme import SCHEME, make_handler, make_interceptor

        self.debug = debug
        handler = make_handler(web_root(), debug=debug)
        profile = QWebEngineProfile.defaultProfile()
        profile.installUrlSchemeHandler(SCHEME, handler)
        profile.setUrlRequestInterceptor(make_interceptor())
        return handler


_runtime: WebRuntime | None = None


def runtime() -> WebRuntime:
    global _runtime
    if _runtime is None:
        _runtime = WebRuntime()
    return _runtime


def reset_runtime() -> WebRuntime:
    """Tests: a fresh table (the installed handler, if any, keeps serving)."""
    global _runtime
    _runtime = WebRuntime()
    return _runtime


__all__ = ["WebRuntime", "reset_runtime", "runtime"]
