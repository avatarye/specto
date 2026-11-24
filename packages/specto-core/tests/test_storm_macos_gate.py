"""The usd view is gated off on macOS until Storm runs reliably there: on darwin the Storm
capability is absent, with a reason naming the gate and the switch that lifts it, even
where an OpenUSD imaging build is installed. Other platforms are untouched."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from specto_core import machine


@pytest.fixture
def imaging_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """An OpenUSD prefix that has UsdImagingGL, as far as detection looks."""
    site = tmp_path / "lib" / "python"
    (site / "pxr" / "UsdImagingGL").mkdir(parents=True)
    monkeypatch.setattr(machine, "openusd_site_packages", lambda prefix: str(site))
    monkeypatch.delenv(machine.MACOS_STORM_ENV, raising=False)
    return str(tmp_path)


def test_storm_is_gated_off_on_macos_even_with_an_imaging_build(imaging_build, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    cap = machine.detect_storm(imaging_build)
    assert not cap.present
    assert "macOS" in cap.reason and machine.MACOS_STORM_ENV in cap.reason


def test_the_switch_lifts_the_gate(imaging_build, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv(machine.MACOS_STORM_ENV, "1")
    assert machine.detect_storm(imaging_build).present


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_other_platforms_are_not_gated(imaging_build, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    assert machine.detect_storm(imaging_build).present
