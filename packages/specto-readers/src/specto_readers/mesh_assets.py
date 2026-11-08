"""The file types a mesh may name beside itself, shared by every place that serves them:
the 3d view's ``@root/`` paths (``specto_app.web.scheme.ROOT_SUFFIXES``, ADR 0028) and
the urdf view's textures and the files next to its meshes (``specto_readers.urdf``, ADR
0029). One list, so the two views serve the same images.
"""

from __future__ import annotations

#: texture images: what Babylon's texture loaders read
TEXTURE_EXTS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tga",
        ".gif",
        ".webp",
        ".tif",
        ".tiff",
        ".dds",
        ".ktx",
        ".ktx2",
        ".basis",
        ".hdr",
        ".exr",
    }
)
#: an OBJ's material library
MATERIAL_EXTS: frozenset[str] = frozenset({".mtl"})
#: a glTF's external buffers
BUFFER_EXTS: frozenset[str] = frozenset({".bin"})
#: everything a mesh may name beside itself
MESH_SIDE_EXTS: frozenset[str] = MATERIAL_EXTS | BUFFER_EXTS | TEXTURE_EXTS

__all__ = ["BUFFER_EXTS", "MATERIAL_EXTS", "MESH_SIDE_EXTS", "TEXTURE_EXTS"]
