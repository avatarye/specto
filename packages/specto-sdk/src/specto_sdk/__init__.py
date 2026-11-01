"""Specto SDK: Python client over JSON-RPC to a running app (remote mode only).

    from specto_sdk import Client
    with Client.open() as c:            # nearest .specto/ and its endpoint.json
        c.call("canvas.state", {})
        c.api.canvas_submit(document=doc, label="added a rear gallery")

Transports: Windows named pipe, Unix socket, TCP, or in-process (``specto_sdk.testing``).
"""

from .client import Client
from .endpoint import find_endpoint, resolve_project
from .errors import EndpointNotFound, RemoteError, TransportError
from .generate import load_bundled as load_api_info

__version__ = "0.2.0"

__all__ = [
    "Client",
    "EndpointNotFound",
    "RemoteError",
    "TransportError",
    "find_endpoint",
    "load_api_info",
    "resolve_project",
]
