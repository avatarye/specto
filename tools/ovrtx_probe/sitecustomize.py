"""Dev-only ovrtx probe for profiling a usd load (SPEC-65, docs/setup/developer.md
"Profiling a usd load"). Not part of Specto: nothing in packages/ imports it.

Put this folder first on PYTHONPATH; Python then runs this file at startup in every
process. In the usd frame source (``python -m specto_app.usd.source``) alone, before
ovrtx creates its first renderer, it:

- hands ovrtx the carb settings in ``SPECTO_PROBE_CARB_SETTINGS``, a command-line style
  string (``--/plugins/carb.profiler-cpu.plugin/saveProfile=true --/app/x=1``);
- adds the ``ovrtx.RendererConfig`` fields in ``SPECTO_PROBE_CONFIG``, a JSON object
  (``{"log_level": "verbose"}``).

Every other process (the bench, gpu_guard, uv) is left alone.

WARNING: the settings go through ``ovrtx.settings.apply_settings``, an INTERNAL ovrtx 0.6
extension: ``ovrtx_query_extension`` hands back its vtable and slot 0 is taken to be
``ovrtx_result_t (*)(ovx_string_t settings)``. ovrtx documents neither the name nor the
layout ("all extension names and vtable contracts are internal"), so an ovrtx upgrade may
break this, or worse, call something else. Check the extension against the new release
before trusting it: a valid string answers status 0, an unterminated quote status 1.
"""

from __future__ import annotations

import json
import os
import sys


def _apply_carb_settings(text: str) -> int:
    import ctypes

    from ovrtx._src import bindings  # internal module, ovrtx 0.6

    lib = bindings._ovrtx_loader._load_library()
    lib.ovrtx_query_extension.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.ovrtx_query_extension.restype = bindings.ovrtx_result_t

    class OvxString(ctypes.Structure):
        _fields_ = [("ptr", ctypes.c_char_p), ("length", ctypes.c_size_t)]

    vtable = ctypes.c_void_p()
    result = lib.ovrtx_query_extension(b"ovrtx.settings.apply_settings", ctypes.byref(vtable))
    if int(result.status.value) != 0 or not vtable.value:
        raise RuntimeError("this ovrtx has no ovrtx.settings.apply_settings extension")
    slot0 = ctypes.c_void_p.from_address(vtable.value).value
    apply = ctypes.CFUNCTYPE(bindings.ovrtx_result_t, OvxString)(slot0)
    raw = text.encode()
    return int(apply(OvxString(raw, len(raw))).status.value)


def _patch_renderer_config(extra: dict) -> None:
    import ovrtx

    original = ovrtx.RendererConfig.__init__

    def __init__(self, *args, **kwargs):
        kwargs.update(extra)
        original(self, *args, **kwargs)

    ovrtx.RendererConfig.__init__ = __init__


def _main() -> None:
    if "specto_app.usd.source" not in getattr(sys, "orig_argv", sys.argv):
        return
    settings = os.environ.get("SPECTO_PROBE_CARB_SETTINGS", "").strip()
    extra = json.loads(os.environ.get("SPECTO_PROBE_CONFIG", "").strip() or "{}")
    if settings:
        status = _apply_carb_settings(settings)
        print(f"[probe] carb settings status {status}: {settings}", file=sys.stderr, flush=True)
    if extra:
        _patch_renderer_config(dict(extra))
        print(f"[probe] RendererConfig fields: {extra}", file=sys.stderr, flush=True)


_main()
