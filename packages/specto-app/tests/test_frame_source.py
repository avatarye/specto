"""A frame source process (#136) driven over the control channel: the stub source serves
its moving test pattern into the ring, follows size changes, wakes a listener after
frames and exits when the host goes away."""

from __future__ import annotations

import sys
import time

import pytest

from specto_app.frames import control
from specto_app.frames.ring import RingReader
from specto_app.frames.server import connect_wake

RED, GREEN, BLUE, YELLOW = (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)


def pixel(frame, x: int, y: int) -> tuple[int, ...]:
    at = y * frame.stride + x * 4
    return tuple(frame.pixels[at : at + 4])


def wait_frame(reader: RingReader, timeout: float = 5.0, after: int = 0, **want):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        f = reader.latest()
        if f is not None and f.seq > after and all(getattr(f, k) == v for k, v in want.items()):
            return f
        time.sleep(0.005)
    raise AssertionError(f"no frame with {want}")


@pytest.fixture
def source():
    src = control.FrameSourceProcess.spawn(
        [sys.executable, "-m", "specto_app.frames.stub_source", "--width", "64", "--height", "48"]
    )
    yield src
    src.close()


def test_the_stub_source_serves_its_pattern_into_the_ring(source):
    ready = source.wait_event("ready")
    reader = RingReader.open(ready["ring"])
    try:
        f = wait_frame(reader, width=64, height=48)
        # quadrants below the top band: red | green over blue | yellow
        assert pixel(f, 16, 16) == RED and pixel(f, 48, 16) == GREEN
        assert pixel(f, 16, 40) == BLUE and pixel(f, 48, 40) == YELLOW
        assert wait_frame(reader, after=f.seq)  # it keeps moving
    finally:
        reader.close()


def test_a_resize_changes_the_frame_size_in_the_same_ring(source):
    ready = source.wait_event("ready")
    reader = RingReader.open(ready["ring"])
    try:
        assert source.call("resize", width=32, height=16) == {"width": 32, "height": 16}
        f = wait_frame(reader, width=32, height=16)
        assert pixel(f, 8, 6) == RED and pixel(f, 24, 12) == YELLOW
    finally:
        reader.close()


def test_a_frame_too_big_for_the_ring_moves_to_a_successor(source):
    ready = source.wait_event("ready")
    reader = RingReader.open(ready["ring"])
    try:
        source.call("resize", width=128, height=96)
        moved = source.wait_event("ring")
        assert moved["name"] != ready["ring"]
        deadline = time.monotonic() + 5
        while not reader.closed and time.monotonic() < deadline:
            time.sleep(0.005)
        assert reader.closed and reader.successor == moved["name"]
        bigger = RingReader.open(moved["name"])
        try:
            f = wait_frame(bigger, width=128, height=96)
            assert pixel(f, 32, 32) == RED
        finally:
            bigger.close()
    finally:
        reader.close()


def test_the_listener_is_woken_after_frames(source):
    ready = source.wait_event("ready")
    with connect_wake(ready["wake"]) as wake:
        assert wake.poll(5.0)
        assert wake.recv_bytes()


def test_the_source_exits_when_the_host_closes_its_stdin(source):
    source.wait_event("ready")
    source.proc.stdin.close()
    assert source.proc.wait(timeout=10) == 0


def test_an_unknown_method_is_an_error_not_a_crash(source):
    source.wait_event("ready")
    with pytest.raises(control.SourceError, match="unknown method"):
        source.call("teleport")
    assert source.call("pause") == {}
