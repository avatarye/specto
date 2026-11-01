"""The agent icon's state machine and the agent panel's rules (#162). No Qt here: the
panel's ``AgentIcon`` / ``AgentPanel`` (``prompt.panel``) drive these with the prompt
bar's events and QML's hover, and run the timers they ask for.

Icon states: ``idle``; ``thinking`` (a turn waits on the model: before the first token,
between tool calls, while text streams); ``working`` (a tool runs: the canvas may
change); ``needs_you`` (an answer the user has not read: the turn ended with a reply and
no canvas change while the panel was closed, or a peer request waits for Run, #187; it
clears when the panel opens; nothing opens by itself); ``error`` (the agent is
unavailable, or its turn errored; it clears when the next turn starts).

Panel: hover resting on the icon opens it after ``OPEN_MS``; it stays open while the
pointer is over the icon or the panel (the gap between them counts as inside), while
the input has text or focus, while a selection drag runs, or while pinned; when the last
hold lets go it closes after ``CLOSE_MS``. Esc closes it whatever holds it (the draft
and the pin stay), and a reopen then waits for the pointer to come back to the icon.
Ctrl+K (``summon``) opens it at once. A pinned panel opens with the window.
"""

from __future__ import annotations

ICON_STATES = ("idle", "thinking", "working", "needs_you", "error")

OPEN_MS = 150
CLOSE_MS = 400

MIN_W = 380
MAX_W = 720
MAX_W_FRACTION = 0.6
MIN_H = 240
#: the panel's top stays this far below the window's top at the most
H_MARGIN = 120
DEFAULT_W = 480
#: an unresized panel hugs its thread up to this height
HUG_H = 610


class IconStates:
    def __init__(self) -> None:
        self.state = "idle"
        #: unread answers (the needs-you badge)
        self.asks = 0
        #: why the agent is in error (the icon's tip)
        self.error = ""
        self._in_turn = False

    def turn_started(self) -> None:
        self._in_turn = True
        self.error = ""
        self.state = "thinking"

    def streamed(self) -> None:
        if self._in_turn:
            self.state = "thinking"

    def tool_started(self, name: str) -> None:
        if self._in_turn:
            self.state = "working"

    def tool_finished(self, name: str) -> None:
        if self._in_turn:
            self.state = "thinking"

    def turn_finished(self, status: str, *, error: str = "", panel_open: bool = False) -> None:
        """``status``: the reply's (changed, answered, failed); ``error`` set when the
        agent itself failed (an exception, an errored result), not when it gave up."""
        self._in_turn = False
        if error:
            self.state, self.error = "error", error
        elif status in ("answered", "failed") and not panel_open:
            self.asks += 1
            self.state = "needs_you"
        else:
            self.state = "error" if self.error else "idle"

    def peer_asked(self) -> None:
        """A peer request in "ask" mode (#187) waits on the user: a badge, and the
        icon needs them unless a turn runs (it shows when that turn ends)."""
        self.asks += 1
        if not self._in_turn and self.state != "error":
            self.state = "needs_you"

    def panel_opened(self) -> None:
        if self.state == "needs_you":
            self.state = "idle"
        self.asks = 0

    def unavailable(self, why: str) -> None:
        self.state, self.error = "error", why


class PanelRules:
    """Inputs (``set``): ``icon`` / ``panel`` hovered, input ``text`` / ``focus``, a
    selection ``drag``. ``set`` returns the timer to start, ``(action, ms)``, or None
    (stop it); ``fire`` is that timer's end."""

    def __init__(self, *, pinned: bool = False) -> None:
        self.pinned = pinned
        self.open = pinned
        self.icon = self.panel = self.text = self.focus = self.drag = False
        #: hover may open the panel (off after Esc until the pointer leaves the icon)
        self.armed = True
        self.pending: str | None = None

    @property
    def held(self) -> bool:
        return self.pinned or self.icon or self.panel or self.text or self.focus or self.drag

    def set(self, **inputs: bool) -> tuple[str, int] | None:
        for name, value in inputs.items():
            if name not in ("icon", "panel", "text", "focus", "drag"):
                raise KeyError(name)
            setattr(self, name, bool(value))
        if not self.icon:
            self.armed = True
        return self._schedule()

    def _schedule(self) -> tuple[str, int] | None:
        if not self.open:
            self.pending = "open" if self.icon and self.armed else None
        else:
            self.pending = None if self.held else "close"
        if self.pending is None:
            return None
        return (self.pending, OPEN_MS if self.pending == "open" else CLOSE_MS)

    def fire(self) -> bool:
        """The timer ran out: do what is pending if it still holds. True when ``open``
        changed."""
        pending, self.pending = self.pending, None
        if pending == "open" and not self.open and self.icon and self.armed:
            self.open = True
            return True
        if pending == "close" and self.open and not self.held:
            self.open = False
            return True
        return False

    def summon(self) -> None:
        self.open, self.pending, self.armed = True, None, True

    def escape(self) -> None:
        self.open, self.pending = False, None
        self.armed = not self.icon

    def toggle_pin(self) -> tuple[str, int] | None:
        self.pinned = not self.pinned
        return self._schedule()


def clamp_size(w: float, h: float, win_w: float, win_h: float) -> tuple[int, int]:
    """Width ``MIN_W`` .. min(``MAX_W``, 60 % of the window), height ``MIN_H`` ..
    window - ``H_MARGIN``; in a window too small for both, the minimum wins."""
    max_w = max(MIN_W, min(MAX_W, MAX_W_FRACTION * win_w))
    max_h = max(MIN_H, win_h - H_MARGIN)
    return round(min(max(w, MIN_W), max_w)), round(min(max(h, MIN_H), max_h))


def panel_height(stored: float, content: float, win_h: float) -> int:
    """The panel's height: the user's (``stored`` > 0) or the thread's up to ``HUG_H``."""
    want = stored if stored > 0 else min(content, HUG_H)
    return clamp_size(MIN_W, want, 0, win_h)[1]


__all__ = [
    "CLOSE_MS",
    "DEFAULT_W",
    "HUG_H",
    "ICON_STATES",
    "OPEN_MS",
    "IconStates",
    "PanelRules",
    "clamp_size",
    "panel_height",
]
