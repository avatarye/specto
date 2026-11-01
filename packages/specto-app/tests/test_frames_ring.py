"""The frame ring (#136): a frame source writes RGBA8 frames into three shared-memory
slots and a reader takes the newest complete one, never a slot that is being written."""

from __future__ import annotations

import uuid

import pytest

from specto_app.frames.ring import FrameRing, RingReader, RingTooSmall


def ring_name() -> str:
    return f"specto-test-ring-{uuid.uuid4().hex[:12]}"


def solid(width: int, height: int, rgba: bytes) -> bytes:
    return rgba * (width * height)


@pytest.fixture
def ring():
    r = FrameRing.create(ring_name(), max_width=64, max_height=32)
    yield r
    r.close()


def test_a_written_frame_reads_back_with_its_header(ring):
    ring.write(solid(8, 4, b"\x10\x20\x30\xff"), 8, 4, timestamp=12.5, render_ms=3.25)
    reader = RingReader.open(ring.name)
    try:
        frame = reader.latest()
        assert frame is not None
        assert (frame.seq, frame.width, frame.height, frame.stride) == (1, 8, 4, 32)
        assert (frame.timestamp, frame.render_ms) == (12.5, 3.25)
        assert frame.pixels == solid(8, 4, b"\x10\x20\x30\xff")
    finally:
        reader.close()


def test_a_frame_being_written_is_never_read(ring):
    ring.write(solid(8, 4, b"\x01\x01\x01\xff"), 8, 4)
    reader = RingReader.open(ring.name)
    try:
        with ring.frame(8, 4) as dst:
            dst[: 8 * 4 * 4] = solid(8, 4, b"\x02\x02\x02\xff")
            during = reader.latest()
        after = reader.latest()
    finally:
        reader.close()
    assert during is not None and during.seq == 1
    assert during.pixels == solid(8, 4, b"\x01\x01\x01\xff")
    assert after is not None and after.seq == 2


def test_a_slot_the_reader_holds_is_not_rewritten(ring):
    ring.write(solid(8, 4, b"\x01\x01\x01\xff"), 8, 4)
    reader = RingReader.open(ring.name)
    try:
        with reader.hold() as held:
            for n in range(2, 7):
                ring.write(solid(8, 4, bytes([n, n, n, 255])), 8, 4)
            assert held.seq == 1
            assert bytes(held.pixels) == solid(8, 4, b"\x01\x01\x01\xff")
        assert reader.latest().seq == 6
    finally:
        reader.close()


def test_a_frame_too_big_for_the_slots_is_refused(ring):
    with pytest.raises(RingTooSmall):
        ring.write(solid(65, 32, b"\0\0\0\xff"), 65, 32)
    assert ring.fits(64, 32) and not ring.fits(65, 32)
