"""Shared pieces for the video tests: a headless stand-in for the native MpvItem
(``FakeMpv``) that records what the host writes to it and answers what it reads."""

from __future__ import annotations


class FakeMpv:
    """Stands in for the MpvItem of a headless host: records what the host writes to
    it, in order, and answers the properties the host reads."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, object]] = []
        self.props: dict[str, object] = {
            "paused": True, "eof": False, "hasAudio": True,
            "renderFrames": 0, "mpvVersion": "fake", "backend": "none", "error": "",
        }  # fmt: skip

    def setProperty(self, name: str, value: object) -> None:
        self.writes.append((name, value))
        if name == "muted":
            self.props["mute"] = value

    def property(self, name: str) -> object:
        return self.props.get(name)

    def mpvProperty(self, name: str) -> object:
        return self.props.get(name, "")

    def setMpvProperty(self, name: str, value: object) -> None:
        self.writes.append((name, value))

    def pause(self) -> None:
        self.writes.append(("pause", True))

    def play(self) -> None:
        self.writes.append(("pause", False))

    def togglePause(self) -> None:
        self.writes.append(("pause", not self.props["paused"]))

    def seek(self, t: float, exact: bool, relative: bool) -> None:
        self.writes.append(("seek", t))

    def stop(self) -> None:
        self.writes.append(("stop", None))
