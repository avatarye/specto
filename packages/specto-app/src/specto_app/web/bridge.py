"""``PageBridge``: the Python end of a web view's ``QWebChannel`` (contracts 6, 8; on
ports per ADR 0014).

Python -> page: ``configured(json)`` once after ``ready()``; ``port(json)`` one
``{port, value}`` per input port per flush (latest value; ``null`` when the link feeding
the port went away); ``params(json)`` when the view's params change through a patch;
``notify(kind, json)`` for view-specific data; ``callResult(reqId, json)``.

Page -> Python: ``ready()``, ``emit(port, valueJson)``, ``setParams(patchJson)``,
``call(name, argsJson, reqId)``, ``send(kind, json)`` (a page event for the host),
``log``, ``jsError``, ``setHealth``, ``pageState``, plus two the QML host uses:
``consoleMessage`` and ``loadState``. Every page request is checked against the page's
``Capabilities`` on the Python side (``WebView.emit`` / ``set_params`` / ``call``); the
page's own checks are a convenience. Every payload crosses as a JSON string.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from specto_core.errors import SpectoError

from .capabilities import CapabilityDenied
from .scheme import items_page_of_host, navigation_allowed

if TYPE_CHECKING:
    from .webview import WebView

#: QtWebEngine console levels: 0 info, 1 warning, 2 error.
_CONSOLE_LEVELS = {0: "info", 1: "warn", 2: "error"}


def _host_of(url: str) -> str:
    """``specto://v2.items/l2/deck/html/deck.js`` -> ``v2.items`` (a source that is not
    a URL, ``about:blank`` or a bare file name, has no host)."""
    _scheme, sep, rest = url.partition("://")
    return rest.split("/", 1)[0] if sep else ""


def _ok(result: Any) -> str:
    return json.dumps({"ok": True, "result": result}, default=str)


def _err(e: Exception) -> str:
    if isinstance(e, SpectoError):
        return json.dumps({"ok": False, "error": e.to_dict()}, default=str)
    return json.dumps({"ok": False, "error": {"code": "internal", "message": repr(e)}})


class PageBridge(QObject):
    configured = Signal(str)
    port = Signal(str)
    params = Signal(str)
    notify = Signal(str, str)
    callResult = Signal(str, str)

    def __init__(self, page: WebView) -> None:
        super().__init__()
        self._page = page
        self.ready_seen = False
        self.calls: list[dict[str, Any]] = []  # audit trail (tests, `snap`)

    # -- page -> python ---------------------------------------------------------------
    @Slot()
    def ready(self) -> None:
        self.ready_seen = True
        self.configured.emit(json.dumps(self._page.config(), default=str))
        self._page._ready()

    @Slot(str, str, result=str)
    def emit(self, port: str, value_json: str) -> str:
        try:
            value = json.loads(value_json) if value_json else None
            res = self._page.emit(port, value)
            self.calls.append({"emit": port, "ok": True})
            return _ok(res)
        except Exception as e:  # reported to the page, never raised into Qt
            self.calls.append({"emit": port, "ok": False, "error": str(e)})
            return _err(e)

    @Slot(str, result=str)
    def setParams(self, patch_json: str) -> str:
        try:
            patch = json.loads(patch_json) if patch_json else {}
            if not isinstance(patch, dict):
                raise CapabilityDenied("setParams takes a JSON object", fix="pass {key: value}")
            res = self._page.set_params(patch)
            self.calls.append({"setParams": sorted(patch), "ok": True})
            return _ok(res)
        except Exception as e:
            self.calls.append({"setParams": None, "ok": False, "error": str(e)})
            return _err(e)

    @Slot(str, str, str)
    def call(self, name: str, args_json: str, req_id: str) -> None:
        try:
            args = json.loads(args_json) if args_json else {}
            if not isinstance(args, dict):
                raise CapabilityDenied("call args must be a JSON object", fix="pass {}")
            result = self._page.call(name, args)
            self.calls.append({"call": name, "ok": True})
            self.callResult.emit(req_id, _ok(result))
        except Exception as e:
            self.calls.append({"call": name, "ok": False, "error": str(e)})
            self.callResult.emit(req_id, _err(e))

    @Slot(str, str)
    def send(self, kind: str, payload_json: str) -> None:
        """A page event for the host (a wikilink followed, a feature hovered)."""
        try:
            payload = json.loads(payload_json) if payload_json else None
        except ValueError:
            payload = {"error": "send: not JSON", "raw": payload_json}
        self._page.page_event(kind, payload)

    @Slot(str, str)
    def log(self, level: str, message: str) -> None:
        tag = {"error": "[ERROR]", "warn": "[WARN]"}.get(level, "[INFO]")
        print(f"{tag} page {self._page.page_id}: {message}", file=sys.stderr, flush=True)

    @Slot(str)
    def jsError(self, message: str) -> None:
        self._page.set_health("error", message)

    @Slot(str, str)
    def setHealth(self, status: str, message: str) -> None:
        self._page.set_health(status, message)

    @Slot(str)
    def pageState(self, state_json: str) -> None:
        """The page's own structured snapshot (what it shows, where its controls are in
        CSS px); kept as the latest value and returned by ``snap`` as ``page``."""
        try:
            value = json.loads(state_json)
        except ValueError:
            value = {"error": "pageState: not JSON"}
        self._page.page_state = value if isinstance(value, dict) else {"value": value}

    # -- QML host -> python -----------------------------------------------------------
    @Slot(int, str, int, str)
    def consoleMessage(self, level: int, message: str, line: int, source: str) -> None:
        lvl = _CONSOLE_LEVELS.get(level, "info")
        if items_page_of_host(_host_of(source)) is not None or source.startswith(
            ("http://", "https://")
        ):
            # a framed document (the user's own page in the html view, or a remote frame
            # inside it such as a map embed): its errors are its own, logged with their
            # origin, never the view's health
            self.log("warn" if lvl == "error" else lvl, f"framed page {source}:{line}: {message}")
            return
        self.log(lvl, f"{message} ({source}:{line})")
        if lvl == "error":
            self._page.set_health("error", f"{message} ({source.rsplit('/', 1)[-1]}:{line})")

    @Slot(str, bool, result=bool)
    def allowNavigation(self, url: str, main_frame: bool) -> bool:
        """``WebView.qml`` asks before every navigation, the main frame's or a frame's
        (``scheme.navigation_allowed``); a refusal is logged so a page that never shows
        up leaves a trace."""
        ok = navigation_allowed(
            self._page.page_id, url, main_frame, network=self._page.caps.can_net("web")
        )
        if not ok:
            where = "" if main_frame else " in a frame"
            self.log("warn", f"navigation refused{where}: {url}")
        return ok

    @Slot(str, str)
    def loadState(self, status: str, message: str) -> None:
        # started | ok | error from WebEngineView.loadingChanged
        if status == "started":
            self._page.set_health("loading", "")
        elif status == "error":
            self._page.set_health("error", message or "page failed to load")
        # "ok" waits for the page's ready(); a loaded page without a bridge is not healthy

    # -- python -> page helpers -------------------------------------------------------
    def send_port(self, port: str, value: Any) -> None:
        self.port.emit(json.dumps({"port": port, "value": value}, default=str))

    def send_params(self, params: dict[str, Any]) -> None:
        self.params.emit(json.dumps(params, default=str))

    def send_notify(self, kind: str, payload: Any) -> None:
        self.notify.emit(kind, json.dumps(payload, default=str))


__all__ = ["PageBridge"]
