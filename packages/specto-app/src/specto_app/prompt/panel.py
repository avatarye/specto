"""The agent panel's Qt objects (#162), owned by the prompt bar and read by QML as
``promptBar.icon``, ``promptBar.panel`` and ``promptBar.thread``:

- ``AgentIcon``: ``IconStates`` with change signals and the needs-you shake (``shake``
  on entering the state, then every ``SHAKE_MS``; the QML icon runs the motion).
- ``AgentPanel``: ``PanelRules`` with their hover timers, and the panel's size, kept
  inside the window (``clamp_size``), remembered with the pin per project in the user's
  app settings (never in the project folder or the canvas document).
- ``ThreadModel``: the ``AgentThread``'s shown entries as a list model; a reply's
  ``blocks`` come from ``split_markdown``; ``live(entry)`` (the prompt bar) adds what
  only the running app knows: whether a turn can still be undone, or was.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRegularExpression,
    QSettings,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from .states import DEFAULT_W, IconStates, PanelRules, clamp_size, panel_height
from .thread import AgentThread, Entry, divider_text, split_markdown

SHAKE_MS = 4000
PANEL_SETTINGS = "prompt/panel"  # + /<project key>/width|height|pinned


class AgentIcon(QObject):
    changed = Signal()
    shake = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._s = IconStates()
        self._shake = QTimer(self)
        self._shake.setInterval(SHAKE_MS)
        self._shake.timeout.connect(self.shake)

    @Property(str, notify=changed)
    def state(self) -> str:
        return self._s.state

    @Property(int, notify=changed)
    def asks(self) -> int:
        return self._s.asks

    @Property(str, notify=changed)
    def errorText(self) -> str:
        return self._s.error

    @property
    def shaking(self) -> bool:
        return self._shake.isActive()

    def _after(self, fn: Callable[..., None], *args: Any, **kw: Any) -> None:
        before = (self._s.state, self._s.asks, self._s.error)
        fn(*args, **kw)
        if (self._s.state, self._s.asks, self._s.error) == before:
            return
        if self._s.state == "needs_you" and not self._shake.isActive():
            self._shake.start()
            self.shake.emit()
        elif self._s.state != "needs_you":
            self._shake.stop()
        self.changed.emit()

    def turn_started(self) -> None:
        self._after(self._s.turn_started)

    def streamed(self) -> None:
        if self._s.state != "thinking":
            self._after(self._s.streamed)

    def tool_started(self, name: str) -> None:
        self._after(self._s.tool_started, name)

    def tool_finished(self, name: str) -> None:
        self._after(self._s.tool_finished, name)

    def turn_finished(self, status: str, *, error: str = "", panel_open: bool = False) -> None:
        self._after(self._s.turn_finished, status, error=error, panel_open=panel_open)

    def peer_asked(self) -> None:
        """A peer's request waits for the user's Run or Dismiss (#187)."""
        self._after(self._s.peer_asked)

    @Slot()
    def panel_opened(self) -> None:
        self._after(self._s.panel_opened)

    def unavailable(self, why: str) -> None:
        self._after(self._s.unavailable, why)

    def stop(self) -> None:
        self._shake.stop()


def project_key(root: Path) -> str:
    """The settings key of one project: its folder, hashed (no path in the ini)."""
    path = Path(root).resolve().as_posix().lower()
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]


class AgentPanel(QObject):
    openChanged = Signal()
    sizeChanged = Signal()
    opened = Signal()

    def __init__(self, root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = f"{PANEL_SETTINGS}/{project_key(root)}"
        s = QSettings()
        self._w = int(s.value(f"{self._key}/width", DEFAULT_W, type=int) or DEFAULT_W)
        self._h = int(s.value(f"{self._key}/height", 0, type=int) or 0)
        pinned = bool(s.value(f"{self._key}/pinned", False, type=bool))
        self._win = (0.0, 0.0)  # unknown until the window says
        self._rules = PanelRules(pinned=pinned)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)

    # -- open / close ------------------------------------------------------------------------
    @Property(bool, notify=openChanged)
    def open(self) -> bool:
        return self._rules.open

    @Property(bool, notify=openChanged)
    def pinned(self) -> bool:
        return self._rules.pinned

    def _run(self, timer: tuple[str, int] | None, was: bool) -> None:
        if timer is None:
            self._timer.stop()
        else:
            self._timer.start(timer[1])
        self._changed(was)

    def _changed(self, was: bool) -> None:
        if self._rules.open != was:
            self.openChanged.emit()
            if self._rules.open:
                self.opened.emit()

    @Slot()
    def _fire(self) -> None:
        was = self._rules.open
        self._rules.fire()
        self._changed(was)

    @Slot(bool)
    def hoverIcon(self, on: bool) -> None:
        self._run(self._rules.set(icon=on), self._rules.open)

    @Slot(bool)
    def hoverPanel(self, on: bool) -> None:
        self._run(self._rules.set(panel=on), self._rules.open)

    @Slot(bool, bool, bool)
    def setHolds(self, text: bool, focus: bool, drag: bool) -> None:
        """What holds the panel open besides the pointer: input text, input focus, a
        selection drag."""
        self._run(self._rules.set(text=text, focus=focus, drag=drag), self._rules.open)

    @Slot()
    def summon(self) -> None:
        was = self._rules.open
        self._timer.stop()
        self._rules.summon()
        self._changed(was)

    @Slot()
    def escape(self) -> None:
        was = self._rules.open
        self._timer.stop()
        self._rules.escape()
        self._changed(was)

    @Slot()
    def togglePin(self) -> None:
        was = self._rules.open
        self._run(self._rules.toggle_pin(), was)
        self.openChanged.emit()  # pinned
        self.commit()

    # -- size ----------------------------------------------------------------------------------
    @Property(int, notify=sizeChanged)
    def width(self) -> int:
        """The width the user chose (``shownWidth`` is what fits the window)."""
        return self._w

    @Property(int, notify=sizeChanged)
    def storedHeight(self) -> int:
        """The height the user chose; 0: hug the thread."""
        return self._h

    def _clamp(self, w: float, h: float) -> tuple[int, int]:
        win_w, win_h = self._win
        return clamp_size(w, h, win_w or 1e9, win_h or 1e9)

    @Property(int, notify=sizeChanged)
    def shownWidth(self) -> int:
        return self._clamp(self._w, self._h)[0]

    @Slot(float, float)
    def setWindowSize(self, w: float, h: float) -> None:
        if (w, h) != self._win:
            self._win = (float(w), float(h))
            self.sizeChanged.emit()

    @Slot(float, result=int)
    def heightFor(self, content: float) -> int:
        """The panel's height for a thread of ``content`` px (hugging it up to the cap
        unless the user sized it), inside the window."""
        return panel_height(self._h, content, self._win[1] or 1e9)

    @Slot(float, float)
    def resizeTo(self, w: float, h: float) -> None:
        """A resize drag (the top edge, the right edge, the top-right corner); ``commit``
        when it ends remembers it. ``h`` 0: the height still hugs the thread (a drag on
        the right edge alone)."""
        w2, h2 = self._clamp(w, h)
        if h <= 0:
            h2 = 0
        if (w2, h2) != (self._w, self._h):
            self._w, self._h = w2, h2
            self.sizeChanged.emit()

    @Slot()
    def commit(self) -> None:
        s = QSettings()
        s.setValue(f"{self._key}/width", self._w)
        s.setValue(f"{self._key}/height", self._h)
        s.setValue(f"{self._key}/pinned", self._rules.pinned)
        s.sync()

    def stop(self) -> None:
        self._timer.stop()


ROLES = (
    "entryId",
    "kind",
    "text",
    "blocks",
    "status",
    "label",
    "counts",
    "diff",
    "number",
    "dividerText",
    "canUndo",
    "undone",
    "path",
    "latest",
    "sender",
    "reply_to",
    "verified",
    "canRun",
)
#: the roles ``live`` answers (the rest come from the entry)
LIVE_ROLES = ("canUndo", "undone", "latest", "canRun")


class ThreadModel(QAbstractListModel):
    olderChanged = Signal()
    olderLoaded = Signal(int)  # entries added at the top

    def __init__(
        self,
        thread: AgentThread,
        live: Callable[[Entry], dict[str, Any]] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.thread = thread
        self.live = live
        self._loading = False
        self._roles = {Qt.ItemDataRole.UserRole + i: name for i, name in enumerate(ROLES)}
        self._ids = {name: role for role, name in self._roles.items()}
        #: the rows the views know of (``sync`` catches up with the thread)
        self._count = len(thread.shown)

    # -- the model -----------------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else self._count

    def roleNames(self) -> dict[int, QByteArray]:
        return {k: QByteArray(v.encode()) for k, v in self._roles.items()}

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> Any:
        name = self._roles.get(role)
        shown = self.thread.shown
        if name is None or not index.isValid() or not 0 <= index.row() < len(shown):
            return None
        e = shown[index.row()]
        if name in LIVE_ROLES:
            return (self.live(e) if self.live is not None else {}).get(name, False)
        return _value(e, name)

    @Slot(int, result="QVariantMap")
    def get(self, row: int) -> dict[str, Any]:
        """Every role of one row (tests, flows)."""
        shown = self.thread.shown
        if not 0 <= row < len(shown):
            return {}
        e = shown[row]
        out = {name: _value(e, name) for name in ROLES if name not in LIVE_ROLES}
        out.update({name: False for name in LIVE_ROLES})
        if self.live is not None:
            out.update(self.live(e))
        return out

    def row_of(self, entry: Entry) -> int:
        return next((i for i, e in enumerate(self.thread.shown) if e is entry), -1)

    # -- changes -------------------------------------------------------------------------------
    def sync(self) -> None:
        """The thread grew at the end (an entry, or a divider and an entry)."""
        n = len(self.thread.shown)
        if n > self._count:
            self.beginInsertRows(QModelIndex(), self._count, n - 1)
            self._count = n
            self.endInsertRows()

    def entry_changed(self, entry: Entry) -> None:
        row = self.row_of(entry)
        if row >= 0:
            ix = self.index(row)
            self.dataChanged.emit(ix, ix)

    def removed(self, entry: Entry) -> None:
        """Drop an entry the thread never saved (a reply that had nothing to say)."""
        row = self.row_of(entry)
        if row < 0:
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        self.thread.entries.remove(entry)
        self._count -= 1
        self.endRemoveRows()

    def refresh_live(self) -> None:
        """Undo can come and go with every canvas change: refresh the live roles only
        (a reply's markdown is not laid out again)."""
        if self._count:
            roles = [self._ids[name] for name in LIVE_ROLES]
            self.dataChanged.emit(self.index(0), self.index(self._count - 1), roles)

    # -- paging --------------------------------------------------------------------------------
    @Property(bool, notify=olderChanged)
    def hasOlder(self) -> bool:
        return self.thread.has_older

    @Property(bool, notify=olderChanged)
    def loading(self) -> bool:
        return self._loading

    @Slot()
    def loadOlder(self) -> None:
        """Scrolled back to the top: show skeleton rows (``loading``), then page the
        previous turns in on a later loop pass."""
        if self._loading or not self.thread.has_older:
            return
        self._loading = True
        self.olderChanged.emit()
        QTimer.singleShot(60, self, self._page_in)

    def _page_in(self) -> None:
        first = self.thread.first
        start = self.thread.page_start(first)
        if start < first:
            self.beginInsertRows(QModelIndex(), 0, first - start - 1)
            self.thread.first = start
            self._count += first - start
            self.endInsertRows()
        self._loading = False
        self.olderChanged.emit()
        self.olderLoaded.emit(first - start)


def _value(e: Entry, name: str) -> Any:
    if name == "entryId":
        return e.id
    if name == "blocks":
        return split_markdown(e.text) if e.kind == "agent" else []
    if name == "dividerText":
        return divider_text(e.text, e.at) if e.kind == "divider" else ""
    return getattr(e, name)


class MarkdownHighlighter(QSyntaxHighlighter):
    """The input's markdown, shown as it reads (#162): the text stays the markdown the
    agent gets (marks and all), styled in place: **bold**, *italic*, `code`, fenced
    blocks, list markers, headings. Colours from the theme."""

    INLINE = (
        (r"\*\*[^*\n]+\*\*|__[^_\n]+__", "bold"),
        (r"(?<![*\w])\*[^*\s][^*\n]*\*(?!\*)|(?<![_\w])_[^_\s][^_\n]*_(?!\w)", "italic"),
        (r"`[^`\n]+`", "code"),
        (r"^\s*(?:[-*+]|\d+[.)])\s", "marker"),
        (r"^#{1,6}\s.*$", "heading"),
    )

    def __init__(self, document: QTextDocument, colors: dict[str, str], mono: str) -> None:
        super().__init__(document)
        self.formats: dict[str, QTextCharFormat] = {}
        bold = QTextCharFormat()
        bold.setFontWeight(QFont.Weight.Bold)
        italic = QTextCharFormat()
        italic.setFontItalic(True)
        code = QTextCharFormat()
        code.setFontFamilies([mono])
        code.setBackground(QColor(colors.get("mono_bg", "#f3efe7")))
        marker = QTextCharFormat()
        marker.setForeground(QColor(colors.get("agent", "#b86a2b")))
        heading = QTextCharFormat()
        heading.setFontWeight(QFont.Weight.Bold)
        self.formats = {
            "bold": bold,
            "italic": italic,
            "code": code,
            "marker": marker,
            "heading": heading,
        }
        self.rules = [(QRegularExpression(rx), name) for rx, name in self.INLINE]
        self.fence = QRegularExpression(r"^\s{0,3}(```|~~~)")

    def highlightBlock(self, text: str) -> None:
        in_code = self.previousBlockState() == 1
        if self.fence.match(text).hasMatch():
            self.setFormat(0, len(text), self.formats["code"])
            self.setCurrentBlockState(0 if in_code else 1)
            return
        if in_code:
            self.setFormat(0, len(text), self.formats["code"])
            self.setCurrentBlockState(1)
            return
        self.setCurrentBlockState(0)
        for rx, name in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), self.formats[name])


__all__ = [
    "SHAKE_MS",
    "AgentIcon",
    "AgentPanel",
    "MarkdownHighlighter",
    "ThreadModel",
    "project_key",
]
