"""SPEC-49: the TypeScript declarations ``packages/specto-web/gen_types.py`` writes
(``types/specto-api.d.ts``) name every interface with a valid identifier, so ``tsc``
accepts them; a view type that starts with a digit (``3d``) is mangled."""

from __future__ import annotations

import re
import runpy
from pathlib import Path

GEN_TYPES = Path(__file__).resolve().parents[2] / "specto-web" / "gen_types.py"
IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def _gen() -> dict:
    return runpy.run_path(str(GEN_TYPES), run_name="specto_web_gen_types")


def test_every_generated_interface_name_is_an_identifier():
    names = re.findall(r"^\s*interface (\S+) \{", _gen()["generate"](), flags=re.M)
    assert names and [n for n in names if not IDENT.fullmatch(n)] == []


def test_a_name_that_starts_with_a_digit_is_mangled():
    pascal = _gen()["pascal"]
    assert pascal("3d") == "_3d"
    assert pascal("gallery") == "Gallery" and pascal("canvas.open_file") == "CanvasOpenFile"


def test_a_defs_name_reaching_an_interface_through_a_union_is_mangled_too():
    """A ``$ref`` to a ``$defs`` entry that is not itself an object (a union of one) names
    the interface it emits after the entry: that name goes through ``pascal`` as well."""
    em = _gen()["Emitter"]()
    defs = {
        "3d_extra": {
            "anyOf": [{"type": "object", "properties": {"x": {"type": "number"}}}, {"type": "null"}]
        }
    }
    assert em.ts({"$ref": "#/$defs/3d_extra"}, defs) == "_3dExtra | null"
    assert list(em.named) == ["_3dExtra"]
