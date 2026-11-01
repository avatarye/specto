"""Web view host (HLD 3.11, 4.1; contracts 6-8; ports per ADR 0014).

- ``scheme``: the ``specto://`` URL scheme. Page pages live on their own host
  (``specto://v3.page/json/index.html``) so Chromium's origin is the page's identity;
  the bytes behind a link's value (``specto://items/l3/00042/json``) are served only
  when the initiating page holds ``read:link:l3``.
- ``capabilities``: the per-page capability list and its matching rules.
- ``bridge``: the ``QWebChannel`` object (input ports in, ``emit`` / ``setParams`` /
  ``call`` within the capability list, JS errors into view health).
- ``page``: ``WebView`` (one page) and ``WebViewHost`` (the ``QuickViewHost`` for
  ``host="web"`` view types).
- ``runtime``: the process-wide page table, item resolvers and handler install.
- ``items``: how a link's value becomes files (``ItemResolver``).
"""

from .capabilities import Capabilities, CapabilityDenied, default_capabilities
from .items import CompositeItems, ItemResolver, ItemTable, LinkItems
from .runtime import WebRuntime, runtime
from .webview import WebView, WebViewHost

__all__ = [
    "Capabilities",
    "CapabilityDenied",
    "CompositeItems",
    "ItemResolver",
    "ItemTable",
    "LinkItems",
    "WebRuntime",
    "WebView",
    "WebViewHost",
    "default_capabilities",
    "runtime",
]
