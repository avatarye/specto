"""View hosts for specto-app (contracts section 8).

    from specto_app.hosts import register_factory, QuickViewHost, HostContext

Register a factory before the app opens its canvas; see ``registry.py`` for the entry
point group and the ``--host-module`` flag.
"""

from .base import (
    QML_DIR,
    SHELL_QML,
    HostBridge,
    HostContext,
    QuickViewHost,
    SimpleFactory,
    ViewHostFactory,
)
from .registry import (
    ENTRY_POINT_GROUP,
    HostRegistry,
    MissingHost,
    clear_pending,
    pending_factories,
    register_factory,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "QML_DIR",
    "SHELL_QML",
    "HostBridge",
    "HostContext",
    "HostRegistry",
    "MissingHost",
    "QuickViewHost",
    "SimpleFactory",
    "ViewHostFactory",
    "clear_pending",
    "pending_factories",
    "register_factory",
]
