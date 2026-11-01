"""View hosts this package provides for the built-in view types (contracts 8).

| type         | host             | kind  | page                 |
|--------------|------------------|-------|----------------------|
| ``file_tree`` | ``FileTreeHost`` | quick | ``FileTree/FileTreeView.qml`` |
| ``gallery``  | ``GalleryHost``  | quick | (native GalleryItem) |
| ``image``    | ``ImageHost``    | quick | ``Image/ImageView.qml`` |
| ``json``     | ``JsonViewHost`` | web   | ``json/index.html``  |
| ``video``    | ``VideoHost``    | quick | ``Video/VideoView.qml`` (native MpvItem) |
| ``markdown`` | ``MarkdownViewHost`` | web | ``markdown/index.html`` |
| ``map``      | ``MapViewHost``  | web   | ``map/index.html``   |
| ``3d``       | ``Scene3DHost``  | web   | ``3d/index.html`` (Babylon.js) |
| ``html``     | ``HtmlViewHost`` | web   | ``html/index.html`` (the user's page in a frame) |
| ``pdf``      | ``PdfHost``      | quick | ``Pdf/PdfView.qml`` (QtQuick.Pdf) |
| ``preview``  | ``PreviewHost``  | quick | ``Preview/PreviewView.qml`` (one of the above by kind) |
| ``scratch``  | ``ScratchHost``  | quick | ``Scratch/ScratchView.qml`` (the scratch pad) |

They reach the shell two ways: the ``specto.view_hosts`` entry points in
``specto-app``'s ``pyproject.toml`` (loaded by ``HostRegistry`` on construction), and
``install()`` for in-process callers (demos, tests).
"""

from __future__ import annotations

from ..hosts.base import SimpleFactory
from ..hosts.registry import register_factory
from .file_tree import FileTreeFactory
from .gallery import GalleryFactory
from .html_view import HtmlViewHost
from .html_view import factory as html_factory
from .image_view import ImageFactory
from .image_view import factory as image_factory
from .json_view import JsonViewHost
from .json_view import factory as json_factory
from .map_view import MapViewHost
from .map_view import factory as map_factory
from .markdown_view import MarkdownViewHost
from .markdown_view import factory as markdown_factory
from .pdf_view import PdfFactory
from .pdf_view import factory as pdf_factory
from .preview_view import PreviewFactory
from .scene3d_view import Scene3DHost
from .scene3d_view import factory as scene3d_factory
from .scratch_view import ScratchFactory
from .usd_view import UsdFactory
from .video_view import VideoFactory
from .video_view import factory as video_factory

Factory = (
    SimpleFactory
    | FileTreeFactory
    | GalleryFactory
    | ImageFactory
    | VideoFactory
    | PdfFactory
    | PreviewFactory
    | UsdFactory
    | ScratchFactory
)


def factories() -> list[Factory]:
    return [
        FileTreeFactory(),
        GalleryFactory(),
        image_factory(),
        json_factory(),
        video_factory(),
        markdown_factory(),
        map_factory(),
        scene3d_factory(),
        html_factory(),
        pdf_factory(),
        PreviewFactory(),
        UsdFactory(),
        ScratchFactory(),
    ]


def install() -> list[Factory]:
    """Register the built-in factories with ``specto_app.hosts``. Idempotent enough: a
    later registration of the same type overrides the earlier one."""
    out = factories()
    for f in out:
        register_factory(f)
    return out


def register_builtin_views() -> None:
    """The gallery tools' name for ``install``."""
    install()


__all__ = [
    "FileTreeFactory",
    "GalleryFactory",
    "HtmlViewHost",
    "ImageFactory",
    "JsonViewHost",
    "MapViewHost",
    "MarkdownViewHost",
    "PdfFactory",
    "PreviewFactory",
    "Scene3DHost",
    "ScratchFactory",
    "UsdFactory",
    "VideoFactory",
    "factories",
    "install",
    "register_builtin_views",
]
