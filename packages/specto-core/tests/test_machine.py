"""Machine capabilities (#135): the ``ovrtx`` flag is detected by importing ovrtx and
ovstage in a child Python, so the app's own process never loads them, and no renderer
(no GPU context) is ever created."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from specto_core.machine import detect_ovrtx

#: a library that is not installed, even where the rtx extra is (it shadows the real one)
MISSING = 'raise ModuleNotFoundError("no module", name=__name__)\n'


def _site(tmp_path: Path, ovrtx_init: str, *, ovstage: bool = True) -> dict[str, str]:
    site = tmp_path / "site"
    (site / "ovrtx").mkdir(parents=True)
    (site / "ovrtx" / "__init__.py").write_text(ovrtx_init, encoding="utf-8")
    (site / "ovstage").mkdir()
    ovstage_init = '__version__ = "0.3.0"\n' if ovstage else MISSING
    (site / "ovstage" / "__init__.py").write_text(ovstage_init, encoding="utf-8")
    return os.environ | {"PYTHONPATH": str(site)}


def test_ovrtx_is_present_when_both_libraries_import(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("specto_core.machine.rtx_hardware", lambda **kw: (True, "RTX GPU"))
    env = _site(tmp_path, '__version__ = "0.6.0"\n')
    cap = detect_ovrtx(sys.executable, env=env)
    assert cap.name == "ovrtx" and cap.present
    assert cap.version == "0.6.0"
    assert "ovstage 0.3.0" in cap.reason
    assert "ovrtx" not in sys.modules  # the app's process never imported it


def test_ovrtx_is_absent_with_the_install_hint_when_not_installed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("specto_core.machine.rtx_hardware", lambda **kw: (True, "RTX GPU"))
    cap = detect_ovrtx(sys.executable, env=_site(tmp_path, MISSING))
    assert not cap.present and cap.version is None
    assert "not installed" in cap.reason and "specto-app[rtx]" in cap.reason


def test_ovrtx_is_absent_with_the_error_when_its_import_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("specto_core.machine.rtx_hardware", lambda **kw: (True, "RTX GPU"))
    env = _site(tmp_path, 'raise ImportError("DLL load failed: vulkan-1.dll")\n')
    cap = detect_ovrtx(sys.executable, env=env)
    assert not cap.present and "vulkan-1.dll" in cap.reason
    env = _site(tmp_path / "b", '__version__ = "0.6.0"\n', ovstage=False)
    cap = detect_ovrtx(sys.executable, env=env)
    assert not cap.present and "ovstage" in cap.reason


def test_installed_ovrtx_is_never_imported_without_rtx_hardware(tmp_path, monkeypatch):
    marker = tmp_path / "imported"
    env = _site(tmp_path, f"open({str(marker)!r}, 'w').close()\n__version__ = 'fake'\n")
    monkeypatch.setattr(
        "specto_core.machine.rtx_hardware", lambda **kw: (False, "no NVIDIA RTX-capable GPU")
    )
    cap = detect_ovrtx(sys.executable, env=env)
    assert not cap.present and "GPU" in cap.reason and not marker.exists()


def test_rtx_hardware_requires_an_rtx_architecture_not_an_nvidia_name(monkeypatch):
    from types import SimpleNamespace

    from specto_core import machine

    def probe(stdout):
        monkeypatch.setattr(
            machine.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
        )
        return machine.rtx_hardware()

    assert probe("NVIDIA GeForce RTX 4070 Ti, 8.9\n")[0]
    assert probe("NVIDIA T4, 7.5\n")[0]
    assert not probe("NVIDIA GeForce GTX 1080 Ti, 6.1\n")[0]
    assert not probe("NVIDIA GeForce GTX 1660, 7.5\n")[0]
    assert not probe("NVIDIA A100, 8.0\n")[0]  # compute GPU, no RT cores


def test_a_capability_named_in_specto_disable_capabilities_is_absent(monkeypatch):
    from specto_core.machine import machine_capabilities

    monkeypatch.setenv("SPECTO_DISABLE_CAPABILITIES", "ovrtx")
    cap = machine_capabilities()["ovrtx"]
    assert not cap.present
    assert cap.reason == "disabled by SPECTO_DISABLE_CAPABILITIES"


def test_a_capability_named_in_specto_fake_capabilities_is_present_without_detection(
    monkeypatch,
):
    """Scripted flows run as on a machine with ovrtx (SPEC-36): the capability is
    present without detecting it; disabled wins over faked."""
    from specto_core import machine

    def never(*a, **k):
        raise AssertionError("a faked capability is not detected")

    monkeypatch.setattr(machine, "detect_ovrtx", never)
    monkeypatch.setenv("SPECTO_DISABLE_CAPABILITIES", "storm")
    monkeypatch.setenv("SPECTO_FAKE_CAPABILITIES", "ovrtx, storm")
    caps = machine.machine_capabilities()
    assert caps["ovrtx"].present
    assert caps["ovrtx"].reason == "faked by SPECTO_FAKE_CAPABILITIES"
    assert not caps["storm"].present  # disabled wins
    assert machine.faked() == {"ovrtx"}  # everywhere: the usd view's source too
