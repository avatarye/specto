"""Machine capabilities: what this machine can run, for backends that are gated on it
(DESIGN.md, Platforms: "a view that needs ovrtx says so, and degrades or refuses
cleanly where it is missing"). Not the glossary's capability, which is a grant a web view
holds.

``ovrtx`` (#132): NVIDIA's RTX renderer and its scene store, ``ovrtx`` and ``ovstage``,
both import. They are imported in a child Python, so the app's own process never loads
them (ovrtx ships its own USD runtime); importing creates no renderer and so no GPU
context, and ovrtx loads its native library only when a renderer is made. On Windows and
Linux an ``auto`` usd view renders with RTX where it is present and with Hydra Storm
otherwise (:func:`usd_renderer`); on macOS, which ovrtx does not support, ``auto`` is
always Storm. An explicit ``renderer: rtx`` or ``storm`` wins everywhere.

``storm`` (#143): Hydra Storm, from the OpenUSD imaging build (pip ``usd-core`` has no
``UsdImagingGL``) at :func:`openusd_prefix`, plus PyOpenGL (a specto-app dependency). Found by
looking at the files; nothing is imported.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

DISABLE_ENV = "SPECTO_DISABLE_CAPABILITIES"
INSTALL_HINT = "install the specto-app[rtx] extra (needs pypi.nvidia.com)"
OPENUSD_ENV = "SPECTO_OPENUSD"
STORM_HINT = (
    f"build OpenUSD with imaging (tools/parked/build_openusd.py) or point {OPENUSD_ENV} at "
    "an install; PyOpenGL comes with specto-app"
)
DETECT_TIMEOUT_S = 30.0

_PROBE = """
import json
out = {}
for name in ("ovrtx", "ovstage"):
    try:
        mod = __import__(name)
    except ModuleNotFoundError as e:
        out = {"missing": name} if e.name == name else {"error": f"import {name}: {e!r}"}
        break
    except Exception as e:
        out = {"error": f"import {name}: {type(e).__name__}: {e}"}
        break
    out[name] = getattr(mod, "__version__", None)
print(json.dumps(out))
"""


class MachineCapability(BaseModel):
    """One named thing this machine can or cannot do, and why."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    present: bool
    version: str | None = None
    reason: str = ""


def detect_ovrtx(
    python: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = DETECT_TIMEOUT_S,
) -> MachineCapability:
    """Whether ``python`` (default: this interpreter) imports ovrtx and ovstage, asked
    in a child process with ``env``."""

    def absent(reason: str) -> MachineCapability:
        return MachineCapability(name="ovrtx", present=False, reason=reason)

    if python is None and env is None and importlib.util.find_spec("ovrtx") is None:
        return absent(f"ovrtx is not installed: {INSTALL_HINT}")
    try:
        run = subprocess.run(
            [python or sys.executable, "-P", "-c", _PROBE],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(env) if env is not None else None,
            check=False,
        )
        out = json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else {}
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return absent(f"could not ask {python or sys.executable}: {type(e).__name__}: {e}")
    if "missing" in out:
        return absent(f"{out['missing']} is not installed: {INSTALL_HINT}")
    if "error" in out:
        return absent(out["error"])
    if "ovrtx" not in out:
        return absent(f"the probe failed (exit {run.returncode}): {run.stderr.strip()[-300:]}")
    return MachineCapability(
        name="ovrtx",
        present=True,
        version=out["ovrtx"],
        reason=f"ovrtx {out['ovrtx']} and ovstage {out.get('ovstage')} import",
    )


def openusd_prefix() -> str:
    """The OpenUSD imaging build's install folder: ``SPECTO_OPENUSD``, else where
    ``tools/parked/build_openusd.py`` installs it (``SPECTO_BUILD_ROOT``/openusd; the root
    is ``~/specto-build`` unless the variable says otherwise)."""
    if os.environ.get(OPENUSD_ENV):
        return os.environ[OPENUSD_ENV]
    default = os.path.join(os.path.expanduser("~"), "specto-build")
    return os.path.join(os.environ.get("SPECTO_BUILD_ROOT", default), "openusd")


def openusd_site_packages(prefix: str) -> str:
    """Where the build at ``prefix`` keeps ``pxr``: build_usd.py installs the Python
    modules as a venv lays them out, ``Lib/site-packages`` on Windows and
    ``lib/python3.X/site-packages`` elsewhere."""
    if sys.platform == "win32":
        return os.path.join(prefix, "Lib", "site-packages")
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return os.path.join(prefix, "lib", version, "site-packages")


def detect_storm(prefix: str | None = None) -> MachineCapability:
    """Whether the OpenUSD build at ``prefix`` (default :func:`openusd_prefix`) has
    UsdImagingGL and this Python has PyOpenGL; by the files, without importing either."""
    prefix = prefix or openusd_prefix()
    imaging = os.path.join(openusd_site_packages(prefix), "pxr", "UsdImagingGL")
    if not os.path.isdir(imaging):
        reason = f"no OpenUSD imaging build at {prefix}: {STORM_HINT}"
        return MachineCapability(name="storm", present=False, reason=reason)
    if importlib.util.find_spec("OpenGL") is None:
        reason = f"PyOpenGL is not installed: {STORM_HINT}"
        return MachineCapability(name="storm", present=False, reason=reason)
    return MachineCapability(name="storm", present=True, reason=f"OpenUSD imaging at {prefix}")


def machine_capabilities() -> dict[str, MachineCapability]:
    """Every machine capability of this process's Python, detected now; the ones named in
    ``SPECTO_DISABLE_CAPABILITIES`` (comma-separated) are absent without asking, so tests
    and scripted flows can run as on a machine without them."""
    off = {n.strip() for n in os.environ.get(DISABLE_ENV, "").split(",") if n.strip()}
    detect = {"ovrtx": detect_ovrtx, "storm": detect_storm}
    reason = f"disabled by {DISABLE_ENV}"
    return {
        name: MachineCapability(name=name, present=False, reason=reason) if name in off else d()
        for name, d in detect.items()
    }


def rtx_first(platform: str | None = None) -> bool:
    """Whether an ``auto`` usd view prefers RTX on ``platform`` (``sys.platform``; None:
    this one): Windows and Linux, where ovrtx runs; never macOS, where ``auto`` is Storm."""
    return (sys.platform if platform is None else platform) != "darwin"


def usd_renderer(
    params: Mapping[str, Any],
    caps: Mapping[str, MachineCapability],
    platform: str | None = None,
) -> str:
    """The renderer a usd view runs: ``params.renderer`` (auto, rtx, storm). ``auto`` is
    platform-dependent (:func:`rtx_first`): on Windows and Linux rtx when the ovrtx
    capability is present, else storm; on macOS always storm. Storm without its
    capability means the host falls back to the stub source. An explicit rtx stays rtx
    on a machine without it; the view's health says why it cannot start."""
    asked = params.get("renderer") or "auto"
    if asked != "auto":
        return str(asked)
    ovrtx = caps.get("ovrtx")
    if rtx_first(platform) and ovrtx is not None and ovrtx.present:
        return "rtx"
    return "storm"
