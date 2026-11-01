"""The usd view's clock (#141; design ``usd.md`` rev 2, Time): a stage's time codes as the
``usd`` timeline, in seconds = timecode / timeCodesPerSecond. Qt-free; the host
(``views.usd_view``) holds one per loaded stage, from the source's ``loaded`` event.

A stage plays only when something in it is time-sampled (``animated``); most worlds are
static (a Kit world's header says ``startTimeCode = -1, endTimeCode = 0``) and show no
transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from specto_core.links import TimeValue

USD_TIMELINE = "usd"
#: USD's fallback when a root layer authors neither timeCodesPerSecond nor framesPerSecond
DEFAULT_TCPS = 24.0


def _number(value: float) -> str:
    """A timecode for the readout: whole as ``12``, else to the hundredth."""
    return f"{value:.2f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


@dataclass(frozen=True)
class StageTime:
    start: float
    end: float
    tcps: float
    animated: bool

    @classmethod
    def from_loaded(cls, params: dict[str, Any]) -> StageTime:
        """From a ``loaded`` event (``start_time_code``, ``end_time_code``,
        ``time_codes_per_second``, ``animated``); a source that reports none is static."""
        start = float(params.get("start_time_code") or 0.0)
        end = float(params.get("end_time_code") or 0.0)
        tcps = float(params.get("time_codes_per_second") or 0.0)
        if tcps <= 0:
            tcps = DEFAULT_TCPS
        if end < start:
            start, end = end, start
        return cls(start, end, tcps, bool(params.get("animated")) and end > start)

    def clamp(self, timecode: float) -> float:
        return max(self.start, min(self.end, float(timecode)))

    def seconds(self, timecode: float) -> float:
        return float(timecode) / self.tcps

    def timecode(self, seconds: float) -> float:
        return float(seconds) * self.tcps

    def value(self, timecode: float) -> TimeValue:
        """The ``time`` port's value at ``timecode``."""
        return TimeValue(t=self.seconds(timecode), timeline=USD_TIMELINE)

    def readout(self, timecode: float) -> str:
        """The footer's readout: ``"12 / 0-48 @ 24 tc/s"``."""
        return (
            f"{_number(timecode)} / {_number(self.start)}-{_number(self.end)}"
            f" @ {_number(self.tcps)} tc/s"
        )


__all__ = ["DEFAULT_TCPS", "USD_TIMELINE", "StageTime"]
