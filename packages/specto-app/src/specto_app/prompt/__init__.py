"""The prompt bar's agent loop (#44, ADR 0015): one agent session per window drives the
canvas through the six agent tools. ``turn`` is the Qt-free core (one prompt = one undo
step), ``context`` what every message carries, ``session`` the seam the Agent SDK sits
behind, ``scripted`` the fake agent tests and GUI flows drive, ``bar`` the QML-facing
object."""
