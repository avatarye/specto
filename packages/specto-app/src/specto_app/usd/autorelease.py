"""An Objective-C autorelease pool for the usd frame source's main thread (macOS).

The source renders on its main thread but never runs a Cocoa event loop, which is what
drains autorelease pools in an app. Storm on macOS draws through Metal (HgiMetal), whose
objects are autoreleased; with no pool they were never released, and every stage load
kept its engine's Metal memory (physical footprint, which RSS does not show). The source
drains one pool per turn of its loop. Elsewhere this is a no-op.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import sys
from collections.abc import Iterator


@functools.cache
def _objc() -> ctypes.CDLL:
    lib = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    lib.objc_autoreleasePoolPush.restype = ctypes.c_void_p
    lib.objc_autoreleasePoolPush.argtypes = []
    lib.objc_autoreleasePoolPop.restype = None
    lib.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
    return lib


@contextlib.contextmanager
def autorelease_pool() -> Iterator[None]:
    """Release what the body autoreleases when it ends (macOS); nothing elsewhere."""
    if sys.platform != "darwin":
        yield
        return
    lib = _objc()
    token = lib.objc_autoreleasePoolPush()
    try:
        yield
    finally:
        lib.objc_autoreleasePoolPop(token)
