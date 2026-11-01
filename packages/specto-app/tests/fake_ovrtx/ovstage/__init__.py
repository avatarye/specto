"""A fake ``ovstage`` 0.3 for seam-2 tests of ``specto-usd-source`` (#137): the calls the
source makes, recorded as JSON lines in ``$FAKE_OVRTX_LOG``. Only what the source uses.

The stage keeps the values written to it (``attrs[(prim, attr)]``), so the fake renderer
renders at the product's current ``resolution``. Tokens are interned to ids as in the real
one; the log names them. A token write logs its values as names, a string write as text.
Inline references (``add_usd_reference_from_string``) are logged with their text.
"""

from __future__ import annotations

import json
import os
import re
from enum import Enum

__version__ = "0.3.0.fake"


def log(call: str, **fields) -> None:
    path = os.environ.get("FAKE_OVRTX_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"call": call, **fields}) + "\n")


class Scope(Enum):
    ALL = 0


class AttributeSemantic(Enum):
    NONE = 0
    MATRIX = 1
    TOKEN_ID = 2
    STRING = 13


#: the interned tokens, both ways (ids from 1: 0 is the empty token)
_TOKENS: dict[str, int] = {}
_NAMES: dict[int, str] = {}


def _intern(name: str) -> int:
    if name not in _TOKENS:
        _TOKENS[name] = len(_TOKENS) + 1
        _NAMES[_TOKENS[name]] = name
    return _TOKENS[name]


def _name(token) -> object:
    return _NAMES.get(token, token) if isinstance(token, int) else token


class _Done:
    def wait(self, *args, **kwargs):
        return self


def numpy_to_dldatatype(dtype, lanes: int = 1):
    return (str(dtype), lanes)


def make_dltensor(array, dtype=None, shape=None, ndim=None):
    return array


class Stage:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attrs: dict[tuple[str, str], object] = {}
        self.floor = 0
        log("Stage", name=name)

    def advance_write_floor(self, ordinal: int, scope=Scope.ALL) -> _Done:
        self.floor = ordinal
        log("advance_write_floor", ordinal=ordinal)
        return _Done()

    def query_from_path_list(self, paths: list[str]) -> list[str]:
        return list(paths)

    def write_attribute(
        self, query, token, *, ordinal, tensors, is_array=False, semantic=None
    ) -> _Done:
        token = _name(token)
        kind = getattr(semantic, "name", None)
        if kind == "STRING":
            value = [bytes(row).decode("utf-8") for row in tensors]
        elif kind == "TOKEN_ID":
            value = [_name(int(v)) for v in tensors.tolist()]
        else:
            value = tensors.tolist()
        for prim in query:
            self.attrs[(prim, token)] = value
        log(
            "write_attribute",
            prims=list(query),
            attr=token,
            ordinal=ordinal,
            value=value,
            semantic=getattr(semantic, "name", None),
        )
        return _Done()

    def destroy(self) -> None:
        log("Stage.destroy")


class PathDictionary:
    def __init__(self, stage: Stage) -> None:
        self.stage = stage

    def create_path_list_from_strings(self, paths: list[str]) -> list[str]:
        return list(paths)

    def intern_token(self, name: str) -> int:
        return _intern(name)


class _Population:
    @staticmethod
    def open_usd_from_string(stage: Stage, text: str, ordinal: int = 0) -> None:
        m = re.search(r"int2 resolution = \((\d+), (\d+)\)", text)
        if m:
            stage.attrs[("/Render/__specto", "resolution")] = [[int(m[1]), int(m[2])]]
        log("open_usd_from_string", text=text, ordinal=ordinal)

    @staticmethod
    def add_usd_reference_from_string(stage: Stage, text: str, target_path: str) -> int:
        stage.refs = getattr(stage, "refs", 0) + 1
        log("add_usd_reference_from_string", text=text, target=target_path, handle=stage.refs)
        return stage.refs

    @staticmethod
    def remove_usd(stage: Stage, handle: int) -> None:
        log("remove_usd", handle=handle)

    @staticmethod
    def apply_usd_changes(stage: Stage, ordinal: int) -> None:
        log("apply_usd_changes", ordinal=ordinal)

    @staticmethod
    def update_from_usd_time_async(stage: Stage, ordinal: int, time: float) -> _Done:
        log("update_from_usd_time", ordinal=ordinal, time=time)
        return _Done()


population = _Population()
