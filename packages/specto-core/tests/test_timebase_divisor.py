"""timebase.stamp_divisor: one magnitude rule for item stamps and stamp columns."""

from __future__ import annotations

from specto_core.timebase import stamp_divisor, stamp_seconds


def test_stamp_divisor_by_magnitude():
    assert stamp_divisor(1_700_000_000_000_000_000) == 10**9
    assert stamp_divisor(1_700_000_000_000_000) == 10**6
    assert stamp_divisor(1_700_000_000_000) == 10**3
    assert stamp_divisor(1_700_000_000) == 1
    assert stamp_divisor(0.0) == 1
    assert stamp_seconds(1_700_000_000_417_499_554) == 1_700_000_000.417499554
