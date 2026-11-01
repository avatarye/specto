"""The shell's watch on the launcher files (#186): a save in the editor reloads the book
at once, and a file with problems shows them as notices naming the line, with the
fix-it; the next reload takes them back (a fixed file leaves no stale warning, #192).
The folders are watched as well as the files, so an editor's save by
replace (and a file created by Edit launchers...) is seen too.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Slot

from .book import LauncherBook

#: an editor writes in steps: reload once it settled
SETTLE_MS = 150


class LauncherWatch(QObject):
    def __init__(self, book: LauncherBook, notices: Any = None, parent: QObject | None = None):
        super().__init__(parent)
        self.book = book
        self.notices = notices
        #: the ids of the notices the last report posted: the next one dismisses them
        self._posted: list[int] = []
        self._fs = QFileSystemWatcher(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SETTLE_MS)
        self._timer.timeout.connect(self.check)
        self._fs.fileChanged.connect(self._changed)
        self._fs.directoryChanged.connect(self._changed)
        self._watch()
        book.refresh()
        book.listeners.append(self._reloaded)
        self._report(startup=True)

    def _watch(self) -> None:
        want = []
        for f in self.book.files:
            if f.parent.is_dir():
                want.append(str(f.parent))
            if f.is_file():
                want.append(str(f))
        have = set(self._fs.files()) | set(self._fs.directories())
        missing = [w for w in want if w not in have]
        if missing:
            self._fs.addPaths(missing)

    @Slot(str)
    def _changed(self, _path: str) -> None:
        self._timer.start()

    @Slot(result=bool)
    def check(self) -> bool:
        """Reload when a file changed; True when it did. The watch follows a file that
        was replaced or created."""
        self._watch()
        return self.book.refresh()

    def _reloaded(self, book: LauncherBook) -> None:
        self._report(startup=False)

    def _report(self, *, startup: bool) -> None:
        if self.notices is None:
            return
        for notice_id in self._posted:
            self.notices.dismiss(notice_id)
        self._posted = [
            self.notices.warn(f"launchers: {p.text()}", fix=p.fix or None)
            for p in self.book.problems
        ]
        if not startup and not self.book.problems:
            n = len(self.book.launchers)
            self.notices.info(f"launchers reloaded: {n} launcher{'' if n == 1 else 's'}")

    def close(self) -> None:
        if self._reloaded in self.book.listeners:
            self.book.listeners.remove(self._reloaded)
        self._timer.stop()
        paths = self._fs.files() + self._fs.directories()
        if paths:
            self._fs.removePaths(paths)


__all__ = ["LauncherWatch"]
