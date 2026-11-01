"""Regenerate packages/specto-sdk/src/specto_sdk/{api-info.json,_api.py} from the
command registry, then packages/specto-web/types/specto-api.d.ts from api-info.json.
Usage: uv run python tools/gen_api.py [--check]"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

from specto_sdk.generate import main

GEN_TYPES = Path(__file__).resolve().parent.parent / "packages" / "specto-web" / "gen_types.py"

if __name__ == "__main__":
    check = "--check" in sys.argv[1:]
    rc = main(check=check)
    if rc == 0:
        ns = runpy.run_path(str(GEN_TYPES), run_name="specto_web_gen_types")
        rc = ns["main"](["--check"] if check else [])
    sys.exit(rc)
