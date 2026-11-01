"""``specto-usd-source``'s two clocks (#141), with the fake ovrtx/ovstage of
``test_usd_source``: the stage's time codes in ``loaded`` and in the inline root, and
``set_time`` driving both of ovrtx's clocks: USD time (``update_from_usd_time``, in
seconds) and the simulation's ``delta_time``, which advances with it while the time plays
and resets the render history on a jump or a step back."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pxr", reason="usd-core comes with the rtx extra")

from test_usd_source import Fake, load, spawn, until, writes

#: a box that crosses the ground in two seconds: 60 timecodes at 30 per second
ANIMATED = """#usda 1.0
(
    defaultPrim = "World"
    endTimeCode = 60
    metersPerUnit = 1
    startTimeCode = 0
    timeCodesPerSecond = 30
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Box"
    {
        double3 xformOp:translate.timeSamples = {
            0: (-6, 0, 1),
            60: (6, 0, 1),
        }
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
"""
#: a Kit world's header: a range of -1 .. 0 and nothing time-sampled
STATIC = """#usda 1.0
(
    defaultPrim = "World"
    endTimeCode = 0
    metersPerUnit = 1
    startTimeCode = -1
    upAxis = "Z"
)

def Xform "World"
{
    def Cube "Box"
    {
    }
}
"""
RESET = "omni:rtx:viewTile:renderHistoryReset:requestId"
DEFAULT_DELTA = 1 / 60  # the step's delta_time while the time stands still


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    return Fake(tmp_path / "ovrtx.log")


@pytest.fixture
def source(fake: Fake):
    src = spawn(fake)
    yield src
    src.close()


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_a_loaded_stage_reports_its_time_codes_and_the_inline_root_carries_them(
    source, fake, tmp_path
):
    source.wait_event("ready")
    source.send("load", path=str(write(tmp_path, "drive.usda", ANIMATED)))
    loaded = source.wait_event("loaded", timeout=30)
    assert loaded["start_time_code"] == 0.0 and loaded["end_time_code"] == 60.0
    assert loaded["time_codes_per_second"] == 30.0 and loaded["animated"] is True
    (root,) = until(lambda: fake.calls("open_usd_from_string"))
    # ovstage takes USD time in seconds through the root layer's timeCodesPerSecond
    assert "timeCodesPerSecond = 30.0" in root["text"]
    assert "startTimeCode = 0.0" in root["text"] and "endTimeCode = 60.0" in root["text"]


def test_a_kit_world_without_time_samples_is_static(source, tmp_path):
    source.wait_event("ready")
    source.send("load", path=str(write(tmp_path, "world.usda", STATIC)))
    loaded = source.wait_event("loaded", timeout=30)
    assert loaded["start_time_code"] == -1.0 and loaded["end_time_code"] == 0.0
    assert loaded["time_codes_per_second"] == 24.0 and loaded["animated"] is False


def steps_at(fake: Fake, ordinal: int) -> list[dict]:
    return [s for s in fake.calls("step") if s["ordinal"] == ordinal]


def test_set_time_drives_usd_time_and_the_simulation_clock_together(source, fake, tmp_path):
    load(source, write(tmp_path, "drive.usda", ANIMATED))

    source.send("set_time", t=1.0, timecode=30.0)  # a jump from the default time
    (jump,) = until(lambda: fake.calls("update_from_usd_time"))
    assert jump["time"] == 1.0  # seconds, not timecodes
    assert until(lambda: steps_at(fake, jump["ordinal"]))[0]["delta_time"] == DEFAULT_DELTA
    (cut,) = writes(fake, RESET)  # a jump drops the render history: no ghosting
    assert cut["ordinal"] == jump["ordinal"]

    source.send("set_time", t=31 / 30, timecode=31.0)  # playing: one timecode on
    play = until(lambda: fake.calls("update_from_usd_time")[1:])[0]
    assert play["time"] == pytest.approx(31 / 30) and play["ordinal"] > jump["ordinal"]
    # the simulation clock advances with USD time; the settling steps after it stand still
    until(lambda: len(steps_at(fake, play["ordinal"])) > 1)
    first, *rest = steps_at(fake, play["ordinal"])
    assert first["delta_time"] == pytest.approx(1 / 30)
    assert all(s["delta_time"] == DEFAULT_DELTA for s in rest)
    assert len(writes(fake, RESET)) == 1  # no cut while playing

    source.send("set_time", t=0.5, timecode=15.0)  # a step back: the history resets
    back = until(lambda: fake.calls("update_from_usd_time")[2:])[0]
    assert back["time"] == 0.5
    until(lambda: len(writes(fake, RESET)) == 2)
    assert until(lambda: steps_at(fake, back["ordinal"]))[0]["delta_time"] == DEFAULT_DELTA
    assert source.call("state")["timecode"] == 15.0
    assert fake.calls("reset") == []  # not renderer.reset: it stalls a second a call


def test_set_time_on_a_static_stage_writes_nothing(fake, tmp_path):
    src = spawn(fake, "--settle-steps", "5")
    try:
        load(src, write(tmp_path, "world.usda", STATIC))
        src.send("set_time", t=0.0, timecode=0.0)
        until(lambda: src.call("state")["timecode"] == 0.0)
        assert fake.calls("update_from_usd_time") == []
        assert {s["delta_time"] for s in fake.calls("step")} == {DEFAULT_DELTA}
    finally:
        src.close()
