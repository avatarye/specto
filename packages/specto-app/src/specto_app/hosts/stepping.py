"""Stepping (#107; spec #103 decision 4): a viewer or player steps through its input
collection with previous / next, so it is usable without a gallery.

One mechanism for every viewer and player (image, markdown, json, html, pdf, video; the
``preview`` viewer adopts it the same way): a host mixes in :class:`Stepping` and says
which keys it steps over (``step_keys``, in collection order), which one it shows
(``step_key``) and how to show one without emitting (``show_step``). ``step_item(delta)``
does the rest:

- it moves ``delta`` items in collection order and **stops at the ends**. No wrap: the
  collections are ordered runs (frames, pages of notes, a deck's files) whose ends mean
  something; a wrap from the last frame to the first looks like a step and silently
  jumps a linked gallery across the run, while a stop is visible (the control greys
  out, the counter reads n/n). Home / End are steps of +-huge, so they land on the ends;
- a view whose type declares a ``selection`` output emits the item it stepped to, like
  a pick in a gallery: a user gesture on the link runtime, never a submit, no undo step.
  A view without one (pdf) only shows it;
- the view's controls hear where it is through ``on_step_state({index, count})``: the
  QML items get ``stepIndex`` / ``stepCount``, the web pages a ``step`` notification;
- a host rendering for another one (the preview's renderer, #106) has a ``step_owner``:
  its controls and keys step the owner (the whole collection, whatever the kind) and
  show the owner's place.

The keys are the same pair everywhere unless the view already gives a pair a meaning of
its own; then the other pair steps (``STEP_KEYS``, named by the status bar's hint):

- image, markdown, html: Left / Right and PageUp / PageDown;
- json: PageUp / PageDown (the Monaco editor keeps the arrows for its caret and
  Shift-selection);
- pdf: Left / Right (PageUp / PageDown, Space and Backspace turn the document's pages,
  as in every pdf reader);
- video: PageUp / PageDown (Left / Right step frames, the player's own axis);
- usd: PageUp / PageDown (the keyboard flies the camera).

Home / End jump to the ends in the native viewers and players (``ENDS_TYPES``); the
canvas's event filter hands them to the focused view before its own Home (#113).

Keys only step while the view has the keyboard focus and is not consuming them itself
(a text field, the html view's file list, the user's page inside the html frame).
"""

from __future__ import annotations

from typing import Any

from specto_core.links import Selection
from specto_core.ports import VIEW_PORTS

SELECTION = "selection"
ARROWS = "Left/Right"
PAGES = "PageUp/PageDown"
#: view type -> the keys that step it (the status bar names them)
STEP_KEYS: dict[str, tuple[str, ...]] = {
    "image": (ARROWS, PAGES),
    "markdown": (ARROWS, PAGES),
    "html": (ARROWS, PAGES),
    "json": (PAGES,),
    "pdf": (ARROWS,),
    "video": (PAGES,),
    "usd": (PAGES,),
}
#: view types whose own Home / End jump to the first / last item (or frame, page, time
#: step); a focused one gets them before the canvas's Home (#113)
ENDS_TYPES = frozenset({"image", "video", "pdf", "usd"})


def step_target(index: int, count: int, delta: int) -> int:
    """The index ``delta`` items from ``index`` in a collection of ``count``, stopped at
    the ends; -1 for an empty one. From nothing shown (-1), forward starts at the first
    item and back at the last."""
    if count <= 0:
        return -1
    if index < 0:
        index = -1 if delta > 0 else count
    return max(0, min(count - 1, index + delta))


def step_hint(view_type: str) -> str:
    """The status bar's words for a view's step keys; empty for a type that does not step."""
    keys = STEP_KEYS.get(view_type)
    if not keys:
        return ""
    ends = ", Home/End first / last" if view_type in ENDS_TYPES else ""
    return f"{' or '.join(keys)} previous / next item{ends}"


def step_tips(view_type: str) -> list[str]:
    """The footer's step buttons' tips (#126): previous and next, each naming the keys
    that do the same in this view type (``STEP_KEYS``); bare words for a type with none."""
    keys = STEP_KEYS.get(view_type, ())
    back = " or ".join(k.split("/")[0] for k in keys)
    ahead = " or ".join(k.split("/")[1] for k in keys)
    return [f"previous ({back})", f"next ({ahead})"] if keys else ["previous", "next"]


class Stepping:
    """Mixed into a ``QuickViewHost`` that steps; see the module docstring."""

    view_id: str
    manifest: Any
    item: Any
    #: the host this one renders for (a preview), which steps in its place
    step_owner: Stepping | None = None

    # -- what the host says --------------------------------------------------------------
    def step_keys(self) -> list[str]:
        """The keys the view can show, in collection order."""
        raise NotImplementedError

    def step_key(self) -> str | None:
        """The key shown now (None: nothing)."""
        raise NotImplementedError

    def show_step(self, key: str) -> bool:
        """Show ``key`` without emitting; False when the view cannot show it."""
        raise NotImplementedError

    # -- the mechanism -------------------------------------------------------------------
    def step_state(self) -> dict[str, int]:
        if self.step_owner is not None:
            return self.step_owner.step_state()
        keys = self.step_keys()
        key = self.step_key()
        return {"index": keys.index(key) if key in keys else -1, "count": len(keys)}

    def step_item(self, delta: int) -> int:
        """Previous / next (``delta`` items, stopped at the ends): show the item and,
        when the view type has a selection output, emit it as the user's pick. Returns
        the index shown (-1: nothing to show)."""
        if self.step_owner is not None:
            return self.step_owner.step_item(delta)
        keys = self.step_keys()
        index = self.step_state()["index"]
        target = step_target(index, len(keys), int(delta))
        if target < 0 or target == index:
            return index
        key = keys[target]
        if not self.show_step(key):
            return index
        if self.emits_selection:
            self.emit(SELECTION, Selection(keys=[key], last=key))  # type: ignore[attr-defined]
        self.sync_step()
        return target

    @property
    def emits_selection(self) -> bool:
        decl = VIEW_PORTS.get(self.manifest.type)
        return decl is not None and SELECTION in decl.outputs

    def sync_step(self) -> None:
        """Tell the controls where the view is now (call after any change of the shown
        item or the collection)."""
        self.on_step_state(self.step_state())

    def on_step_state(self, state: dict[str, int]) -> None:
        """Default: the QML item's ``stepIndex`` / ``stepCount`` and its footer's
        ``stepTips`` (the keys of this view type, #126); web hosts notify."""
        if self.item is not None:
            self.item.setProperty("stepIndex", state["index"])
            self.item.setProperty("stepCount", state["count"])
            self.item.setProperty("stepTips", step_tips(self.manifest.type))


__all__ = ["ARROWS", "PAGES", "STEP_KEYS", "Stepping", "step_hint", "step_target", "step_tips"]
