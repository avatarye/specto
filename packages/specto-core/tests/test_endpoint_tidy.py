"""SPEC-98: the endpoint rule's loose ends after SPEC-88.

A port is ASCII digits (a superscript digit passed ``isdigit`` and then made ``int``
raise; a fullwidth one was accepted), and ``pathguard`` keeps importing only the standard
library, which tools/endpoint_connect.py relies on when it loads the file in a venv that
has no specto packages.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from specto_core import pathguard
from specto_core.pathguard import endpoint_refusal

NON_ASCII_PORTS = [
    "127.0.0.1:\u00b9",  # superscript one: isdigit() is True, int() raises
    "127.0.0.1:\uff18\uff10",  # fullwidth 80: isdecimal() is True, int() takes it
    "127.0.0.1:\u0668\u0660",  # Arabic-Indic 80
    "127.0.0.1:5\u00b2",
]


@pytest.mark.parametrize("address", NON_ASCII_PORTS)
@pytest.mark.parametrize("remote_ok", [False, True])
def test_a_non_ascii_port_is_refused_not_raised(address, remote_ok):
    assert "port" in endpoint_refusal("tcp", address, remote_ok=remote_ok)


def test_an_ascii_port_is_taken():
    assert endpoint_refusal("tcp", "127.0.0.1:80") is None
    assert endpoint_refusal("tcp", "[::1]:65535") is None


def test_pathguard_imports_with_the_standard_library_alone():
    """``python -I -S``: no site packages, no user site, no cwd on the path. The module
    loads by its file (as tools/endpoint_connect.py loads it) and judges an endpoint."""
    path = Path(pathguard.__file__)
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('pg', {str(path)!r})\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['pg'] = m\n"
        "spec.loader.exec_module(m)\n"
        "assert m.endpoint_refusal('tcp', '203.0.113.7:1')\n"
        "assert m.endpoint_refusal('tcp', '127.0.0.1:1') is None\n"
        "bad = sorted(n for n in sys.modules if n.split('.')[0] in ('specto_core', 'pydantic'))\n"
        "assert not bad, bad\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
