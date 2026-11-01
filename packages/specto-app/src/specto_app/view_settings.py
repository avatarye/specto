"""``ViewSettings``: how the user likes to look at views, kept per user in the app
settings (``QSettings``, like the prompt bar's model chip), never in the canvas document:
the agent composes the canvas, these only change what the user sees of it.

QML reads it as the ``viewSettings`` context object. ``treeDetails`` (#115): the file
tree's rows show a second line (modified time by age, size); each tree's footer toggles
it (#122), for every tree at once.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, QSettings, Signal, Slot

TREE_DETAILS = "view/tree_details"  # the details toggle, in the user's settings ini


class ViewSettings(QObject):
    treeDetailsChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tree_details = bool(QSettings().value(TREE_DETAILS, False, type=bool))

    @Property(bool, notify=treeDetailsChanged)
    def treeDetails(self) -> bool:
        return self._tree_details

    @Slot(bool)
    def setTreeDetails(self, on: bool) -> None:
        if bool(on) == self._tree_details:
            return
        self._tree_details = bool(on)
        settings = QSettings()
        settings.setValue(TREE_DETAILS, self._tree_details)
        settings.sync()
        self.treeDetailsChanged.emit()

    @Slot()
    def toggleTreeDetails(self) -> None:
        self.setTreeDetails(not self._tree_details)
