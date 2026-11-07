---
status: accepted
date: 2026-11-06
---

# The 3d view opens STL and OBJ itself and a DAE through a GLB written beside it

Task SPEC-42. Robot data comes as STL, OBJ and COLLADA (`.dae`) meshes, often Z-up, in metres or millimetres.

## Context

The 3d view is Babylon.js 9.31. Its vendored `babylonjs-loaders` build registers STL and OBJ loaders next to glTF and SPLAT; it has no COLLADA loader. STL carries no units, no up axis and no material; OBJ carries no units or up axis, and its MTL file and textures are separate files named relative to it. COLLADA declares both, in `<asset>`: `<unit meter>` and `<up_axis>` (X_UP, Y_UP or Z_UP).

The view's scene is right-handed (glTF's frame), and its `up` param (`y` or `z`) turns the whole scene's root, so Z-up data stands up.

## Decision

1. **STL and OBJ go to Babylon's loaders unchanged.** Resolve only measures their bounds and vertex count for the first camera. The page fetches an OBJ by its own file name, so its MTL and textures resolve next to it on the items host, as a glTF's do. The STL loader is told to keep the file's coordinates (`DO_NOT_ALTER_FILE_COORDINATES`): by default it swaps y and z, which in a right-handed scene mirrors the mesh. A mesh that comes with no material (an STL, an OBJ without its MTL) gets a mid grey instead of Babylon's white, which vanished on the light background.
2. **STL and OBJ numbers are metres, and the view's `up` param says which axis is up**, as for PLY, point clouds and splats. The default stays `y`. A Z-up robot mesh wants `up: "z"`; a millimetre mesh shows a thousand times too big, and F frames it.
3. **A DAE is converted to a GLB at resolve**, on the resolve job's worker, by trimesh reading through pycollada (both permissive, pure Python on numpy; `THIRD_PARTY_NOTICES.md`). The conversion applies `<unit meter>` (to metres) and `<up_axis>` (to glTF's Y up, by a rotation: Z_UP maps (x, y, z) to (x, z, -y), X_UP to (-y, x, z)). It keeps each instance's accumulated node transform, triangles and polylists, normals, UVs, vertex colours and the common profile's materials, textures embedded. The node hierarchy is flattened under the root; cameras, lights, animation and skins are dropped.
4. **The converted mesh is written next to the DAE as `<name>.dae.glb`**, and written again when the DAE is newer than it. When the DAE's folder cannot take it (read-only, or a stale read-only file in the way), it goes to Specto's cache folder, keyed by the DAE's path, size and mtime.
5. **A converted DAE declares its up axis.** Its scene entry carries `up: "y"`, and the page hangs it under a root the `up` param does not turn. A robot folder with Z-up STLs and Z_UP DAEs then lines up under `up: "z"`: the STLs are turned by the param, the DAEs by their own declaration, and both rotations are the same.
6. **One model shows once.** A folder intaker's `@3d` screener also lists `robot.dae.glb`; while `robot.dae` is in the same collection, the converted mesh's item gets no scene. A converted mesh whose DAE is elsewhere is an ordinary GLB.
7. **The file tree does not mark a converted mesh.** It lists files as they are on disk; `robot.dae.glb` is a real file the user can open, copy, share or delete (it comes back on the next open of the DAE). Marking it would add a tree concept and a rule for every view that lists files, for one format.

## Consequences

- `specto-readers` depends on `trimesh` and `pycollada` (and through them `python-dateutil` and `tzdata`). The import is paid only when a DAE is converted.
- Opening a DAE writes into the user's data folder. That is the owner's decision: the GLB is reusable outside Specto and survives a cache wipe. A watched intaker sees the new file and resolves once more; the converted mesh is fresh by then, so nothing is written again.
- glTF files still follow the `up` param (unchanged): a mixed scene of a Z-up STL and a glTF under `up: "z"` turns the glTF too.
- Thumbnails of the three formats are the mesh kind card, as for every 3D file.

## Considered options

- **pyassimp**: BSD-3-Clause bindings, but it needs the native assimp library, which its wheel does not ship. Rejected: a native dependency per platform for one format.
- **Blender or collada2gltf as a converter**: Blender is GPL and huge; collada2gltf is an unmaintained native binary. Rejected.
- **A COLLADA loader in the page**: Babylon has none; writing one duplicates trimesh's work in JavaScript, with no GLB for other tools to reuse.
- **Only the cache, never next to the file**: the owner chose the file beside the DAE, with the cache as the fallback.
- **Baking the view's up axis into the converted GLB**: rejected. The file would change with a view param, and two views with different params would fight over it.
