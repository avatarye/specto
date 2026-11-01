"""Where the user's large USD depot is (read only, never synced or edited), for the seeds
that reference USD assets in it. Nothing here names a real depot: ``SPECTO_P4_DEPOT`` is
the depot root (else ``~/depot``), ``SPECTO_P4_WORLD`` a world file in it, relative to the
root (else ``worlds/example_world.usda``), ``SPECTO_P4_SKY`` a dome-light sky layer in
it (else ``skies/example_sky.usd``), and for the selection flow ``SPECTO_P4_PRIM`` a
pickable prim path in the world with ``SPECTO_P4_PRIM_PARENT`` its parent. A seed whose file is missing says so and exits 1."""

from __future__ import annotations

import os
from pathlib import Path

WORLD_DEFAULT = "worlds/example_world.usda"
SKY_DEFAULT = "skies/example_sky.usd"


def perforce_depot() -> Path:
    return Path(os.environ.get("SPECTO_P4_DEPOT") or (Path.home() / "depot"))


def depot_world() -> Path:
    """The depot's world file (``SPECTO_P4_WORLD``, relative to the depot root)."""
    return perforce_depot() / os.environ.get("SPECTO_P4_WORLD", WORLD_DEFAULT)


def depot_sky() -> Path:
    """The depot's sky layer (``SPECTO_P4_SKY``, relative to the depot root)."""
    return perforce_depot() / os.environ.get("SPECTO_P4_SKY", SKY_DEFAULT)
