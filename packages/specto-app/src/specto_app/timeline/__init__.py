"""Time in the app (HLD 3.8): what the video view and the hosts linked on ``time`` share.

- :mod:`.timemap`: item key <-> time for a collection (the thin layer the gallery, json
  and video hosts call);
- :mod:`.controller`: ``TimelineController``, the scrub / step / play model behind the
  video footer's progress bar (``Specto/Video/VideoProgress.qml``);
- :mod:`.readout`: the footer's frame readout, hover timecode and speed stops (#128).

The standalone ``timeline`` view type and the shell-wide time bar were retired with M2
(ADR 0017); the bar lives in the video view's footer.
"""

from __future__ import annotations

from .controller import TimelineController
from .timemap import HOVER_TIME, INDEX, TIME, TimeMap, collection_timeline

__all__ = [
    "HOVER_TIME",
    "INDEX",
    "TIME",
    "TimeMap",
    "TimelineController",
    "collection_timeline",
]
