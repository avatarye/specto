"""Specto readers: built-in locators and decoders plus the worker process pool (HLD 3.7, 4.1).

- ``default_readers()``: the ``ReaderRegistry`` with the image, json, geojson, text and
  markdown locators and the image, json and text decoders.
- ``PoolJobManager``: a ``spawn`` process pool implementing ``specto_core.jobs.JobManager``;
  decoded arrays come back as ``SharedResult`` (shared memory, zero-copy attach).
- ``submit_decode`` / ``submit_decode_batch``: queue decodes on any job manager;
  ``map_refs``: the loop of a batch job (one result or ``{"error": ...}`` per ref), for
  batch jobs of other packages (the gallery's thumbnails).
- ``joint_track``: joint tracks from tables (the ``joint_track`` decoder, ``JointTrack``);
  ``xacro_expand``: xacro for approved content only. Both serve the URDF view.

No Qt anywhere; the app imports this on the GUI thread, the workers never import Qt.
"""

from .decoders import DecodeError, ImageDecoder, JsonDecoder, TextDecoder, builtin_decoders
from .locators import ExtensionLocator, builtin_locators
from .pool import PoolJobManager, SharedResult, default_worker_count, release_result
from .registry import ReaderRegistry, default_readers, describe, plugin_manifest
from .tasks import decode_batch, decode_task, map_refs, submit_decode, submit_decode_batch

__version__ = "0.2.0"

__all__ = [
    "DecodeError",
    "ExtensionLocator",
    "ImageDecoder",
    "JsonDecoder",
    "PoolJobManager",
    "ReaderRegistry",
    "SharedResult",
    "TextDecoder",
    "builtin_decoders",
    "builtin_locators",
    "decode_batch",
    "decode_task",
    "default_readers",
    "default_worker_count",
    "describe",
    "map_refs",
    "plugin_manifest",
    "release_result",
    "submit_decode",
    "submit_decode_batch",
]
