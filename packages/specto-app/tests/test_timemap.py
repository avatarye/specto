"""TimeMap: stamps from items, index and named timelines, nearest lookup, video sidecars."""

from __future__ import annotations

import json
from pathlib import Path

from specto_app.timeline.timemap import INDEX, TimeMap, item_stamp, stamp_seconds
from specto_core.kinds import Item


def items(n: int, stamp0: int = 1_700_000_000_417_499_554, dt_ns: int = 61_000_000) -> list[Item]:
    return [
        Item(key=f"{i:05d}", parts={".jpg": f"/x/{i:05d}_{stamp0 + i * dt_ns}.jpg"}, index=i)
        for i in range(n)
    ]


def test_stamp_seconds_by_magnitude():
    assert stamp_seconds(1_700_000_000_417_499_554) == 1_700_000_000.417499554
    assert stamp_seconds(1_700_000_000_417_499) == 1_700_000_000.417499
    assert stamp_seconds(1_700_000_000_417) == 1_700_000_000.417
    assert stamp_seconds(1_700_000_000) == 1_700_000_000.0
    assert stamp_seconds("1700000000417499554") == 1_700_000_000.417499554
    assert (
        stamp_seconds(12.5) == 12.5 and stamp_seconds("x") is None and stamp_seconds(None) is None
    )


def test_item_stamp_sources():
    assert (
        item_stamp(Item(key="a", parts={".jpg": "/x/00042_1700000000417499554.jpg"}))
        == 1_700_000_000.417499554
    )
    assert (
        item_stamp(
            Item(key="a", parts={".jpg": "/x/a.jpg"}, meta={"keys": {"stamp": "1700000000417"}})
        )
        == 1_700_000_000.417
    )
    assert (
        item_stamp(
            Item(key="a", parts={".jpg": "/x/a.jpg"}, meta={"stamp_ns": 2_000_000_000_000_000_000})
        )
        == 2e9
    )
    assert item_stamp(Item(key="a", parts={".jpg": "/x/frame_00042.jpg"})) is None


def test_index_and_named_timelines():
    m = TimeMap.from_items(items(5))
    assert m.has_clock and m.timeline == INDEX
    assert m.supports(INDEX) and m.supports("stamp")  # undeclared: answers any clock
    assert m.time_of(2, INDEX) == 2.0 and m.index_at(2.4, INDEX) == 2 and m.index_at(99, INDEX) == 4
    t2 = m.time_of(2, "stamp")
    assert t2 == 1_700_000_000.417499554 + 2 * 0.061
    assert m.index_at(t2 + 0.02, "stamp") == 2 and m.index_at(t2 + 0.04, "stamp") == 3
    assert m.index_for({"t": t2, "timeline": "stamp"}) == 2 and m.index_for(None) == -1
    assert m.publish_timeline() == "stamp" and m.publish_timeline("ros") == "ros"
    assert m.range("stamp") == (m.time_of(0, "stamp"), m.time_of(4, "stamp")) and m.range(
        INDEX
    ) == (0.0, 4.0)
    assert len(m.ticks("stamp")) == 5


def test_declared_timeline_and_offset():
    m = TimeMap.from_items(items(3), timeline="ros", offset=1.5)
    assert m.publish_timeline() == "ros" and m.publish_timeline("other") == "ros"
    assert m.supports("ros") and not m.supports("other")
    assert m.time_of(0, "ros") == 1_700_000_000.417499554 + 1.5
    assert m.index_at(1_700_000_000.417499554 + 1.5 + 0.061, "ros") == 1
    assert m.index_for({"t": 0.0, "timeline": "other"}) == -1


def test_items_without_stamps_only_answer_index():
    m = TimeMap.from_items(
        [Item(key="a", parts={".jpg": "/x/a.jpg"}), Item(key="b", parts={".jpg": "/x/b.jpg"})]
    )
    assert not m.has_clock and not m.supports("stamp") and m.publish_timeline("stamp") == INDEX
    assert m.time_of(1, INDEX) == 1.0 and m.time_of(1, "stamp") is None and m.range("stamp") is None


def test_video_sidecar_and_fallback(tmp_path: Path):
    side = tmp_path / "clip.timeline.json"
    frames = [
        {
            "frame": i,
            "key": f"{i:05d}",
            "stamp_ns": 1_700_000_000_000_000_000 + i * 50_000_000,
            "pts": i / 20.0,
        }
        for i in range(4)
    ]
    side.write_text(json.dumps({"timeline": "stamp", "frames": frames}))
    m = TimeMap.from_video(tmp_path / "clip.mp4", fps=20.0, frame_count=4)
    assert m.keys == ["00000", "00001", "00002", "00003"] and m.pts == [0.0, 0.05, 0.1, 0.15]
    assert m.timeline == "stamp" and m.publish_timeline() == "stamp"
    assert m.index_at(1_700_000_000.0 + 0.12, "stamp") == 2
    # the collection's declared timeline wins over the sidecar's name
    m2 = TimeMap.from_video(tmp_path / "clip.mp4", timeline="ros", offset=0.0)
    assert m2.timeline == "ros"
    # no sidecar: pts is the clock
    m3 = TimeMap.from_video(tmp_path / "other.mp4", fps=10.0, frame_count=3)
    assert m3.stamps == [0.0, 0.1, 0.2] and m3.pts == m3.stamps and m3.index_at(0.16, "clip") == 2
