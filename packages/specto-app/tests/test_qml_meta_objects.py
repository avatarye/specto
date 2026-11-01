"""Every QObject of the app keeps its class's meta-object (#178). PySide gives an object
its own meta-object when a signal is connected to one of its methods that is not a
``@Slot``; QML caches what it knows of a meta-object by its address, so once that object
dies a later object's meta-object at the same address reads as the dead one's: the agent
panel then had no ``open`` and no ``summon`` in QML, now and then, in long test runs."""

from __future__ import annotations

import ast
import gc
import importlib
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QObject

import specto_app

SRC = Path(specto_app.__file__).parent


def _own_meta_object(obj: QObject) -> bool:
    try:
        mo = obj.metaObject()
    except RuntimeError:  # its C++ side is gone already
        return False
    parent = mo.superClass()
    return parent is not None and parent.className() == mo.className()


def _slots(cls: ast.ClassDef) -> dict[str, bool]:
    """Method name -> has a ``@Slot`` decorator."""
    out: dict[str, bool] = {}
    for node in ast.walk(cls):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out.setdefault(node.name, any(_is_slot(d) for d in node.decorator_list))
    return out


def _is_slot(d: ast.expr) -> bool:
    f = d.func if isinstance(d, ast.Call) else d
    return getattr(f, "id", getattr(f, "attr", "")) == "Slot"


def _unslotted_connects() -> list[tuple[str, str, str, int]]:
    """``<signal>.connect(self.<method>)`` where the class's method has no ``@Slot``."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            slots = _slots(cls)
            for node in ast.walk(cls):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "connect"
                    and node.args
                    and isinstance(node.args[0], ast.Attribute)
                    and isinstance(node.args[0].value, ast.Name)
                    and node.args[0].value.id == "self"
                ):
                    continue
                if slots.get(node.args[0].attr) is False:
                    module = ".".join(path.relative_to(SRC.parent).with_suffix("").parts)
                    found.append((module, cls.name, node.args[0].attr, node.lineno))
    return found


def test_every_signal_a_qobject_connects_to_its_own_method_lands_on_a_slot():
    bad = []
    for module, cls_name, method, line in _unslotted_connects():
        cls = getattr(importlib.import_module(module), cls_name, None)
        if isinstance(cls, type) and issubclass(cls, QObject):
            bad.append(f"{module}:{line} {cls_name}.{method}")
    assert bad == [], "decorate these with @Slot(...): " + ", ".join(bad)


def test_no_live_object_of_the_running_app_has_a_meta_object_of_its_own(app, spin):
    """The shell, the canvas, the overworld, the agent panel and its icon, and the hosts
    of a file tree and a gallery (cross-object connections, which the source check above
    does not see, included)."""
    from app_doc_helpers import pin_link

    doc = app.live.document()
    doc["views"]["kobi"] = {
        "type": "gallery",
        "label": "Front",
        "layout": {"position": [300, 0], "size": [640, 400]},
    }
    doc["links"]["l2"] = pin_link("kobi")
    app.live.submit(doc, origin="agent:test", label="a gallery")
    app.prompt_bar.panel.summon()
    spin(200)
    seen = [
        o
        for o in gc.get_objects()
        if isinstance(o, QObject) and type(o).__module__.startswith("specto_app")
    ]
    names = {type(o).__name__ for o in seen}
    assert {"SpectoApp", "CanvasController", "AgentPanel", "AgentIcon", "GalleryHost"} <= names
    assert sorted({type(o).__qualname__ for o in seen if _own_meta_object(o)}) == []
