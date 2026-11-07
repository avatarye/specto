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

1. **STL and OBJ go to Babylon's loaders unchanged.** Resolve only measures their bounds and vertex count for the first camera, reading the file in blocks. The STL loader is told to keep the file's coordinates (`DO_NOT_ALTER_FILE_COORDINATES`): by default it swaps y and z, which in a right-handed scene mirrors the mesh. A mesh that comes with no material (an STL, an OBJ without its MTL) gets a mid grey instead of Babylon's white, which vanished on the light background.
2. **An OBJ's MTL and textures resolve relative to the OBJ, anywhere inside the project.** Robot packages name textures in a sibling folder (`../textures/skin.png`). The page fetches the OBJ (and a glTF) under the link's root, `specto://items/<link>/<key>/obj/@root/<path from the project folder>`. The browser resolves `../` against that URL before it asks, and the items host serves an `@root/` path from the project folder only while it stays inside, and only for what an OBJ or glTF names: an MTL, a buffer, an image. The root comes from the resolver that answers for the link. Resolve checks the same references (each `mtllib`, each `map_*` in the MTLs): one missing, or outside the project, is named in the view's health note. A map's options (`-s 1 1 1`, `-clamp on`, ...) are not part of its file name: resolve strips them, and so does the page before Babylon's MTL loader builds the texture's URL (it strips only `map_bump`'s `-bm`).
3. **STL and OBJ units come from the view's `mesh_unit` param** (`m`, the default, `cm`, `mm` or `in`; robot STLs are often millimetres). The host owns the unit table and sends the page the factor. **The view's `up` param says which axis is up**, as for PLY, point clouds and splats; the default stays `y`, and a Z-up robot mesh wants `up: "z"`.
4. **A DAE is converted to a GLB at resolve**, by trimesh reading through pycollada (both permissive, pure Python on numpy; `THIRD_PARTY_NOTICES.md`). pycollada parses with ElementTree: lxml is not in the runtime, which keeps it out of the licences and keeps external entities unresolved. The conversion runs in an isolated child Python (`-I`, started in the cache folder, so no module in the data folder or the current directory is imported), killed with its whole process tree after 120 s (a venv launcher runs the real Python as its own child); only the tail of its output is read back. A DAE over 256 MB is refused. Both reasons reach the view's health. It applies `<unit meter>` (to metres) and `<up_axis>` (to glTF's Y up, by a rotation: Z_UP maps (x, y, z) to (x, z, -y), X_UP to (-y, x, z)). It keeps each instance's accumulated node transform, triangles and polylists, normals, UVs, vertex colours and the common profile's materials, textures embedded. The node hierarchy is flattened under the root; cameras, lights, animation and skins are dropped.
5. **The converted mesh is written next to the DAE as `<name>.dae.glb`.** It records what it was made from in its own `asset.extras.specto`: the DAE's size and mtime and the converter's version. It is used only while those match, so a DAE swapped for an older copy (a checkout, a restore, `cp -p`) is converted again, and so is a GLB Specto did not write. The scene entry's `version` names that state, and the host puts it in the GLB's URL (`?v=`), so a view that has the model open loads the new one when the DAE changes.
6. **When the DAE's folder cannot take the converted mesh, it goes to Specto's cache folder**, keyed by the DAE's path, size and mtime: a read-only folder, a read-only stale file in the way, a path past the system's limit, or another writer holding the file at that moment. Every write goes through a temporary file beside its target (`.<name>.<process>.<thread>.tmp`) and a rename, so two writers never share a temporary and a reader never sees half a GLB; the child writes its GLB straight to a file of that kind its parent owns and removes, so a killed child leaves nothing; what a crashed process left in the cache (temporaries and logs over an hour old) is swept before the next conversion. A cache record that cannot be written (on Windows, a reader holding the old one) is skipped; the scene stands.
7. **A converted DAE declares its up axis.** Its scene entry carries `up: "y"`, and the page hangs it under a root the `up` param does not turn. A robot folder with Z-up STLs and Z_UP DAEs then lines up under `up: "z"`: the STLs are turned by the param, the DAEs by their own declaration, and both rotations are the same.
8. **One model shows once.** A folder intaker's `@3d` screener also lists `robot.dae.glb`; while `robot.dae` is in the same collection, the converted mesh's item gets no scene. A converted mesh whose DAE is elsewhere is an ordinary GLB.
9. **The file tree does not mark a converted mesh.** It lists files as they are on disk; `robot.dae.glb` is a real file the user can open, copy, share or delete (it comes back on the next open of the DAE). Marking it would add a tree concept and a rule for every view that lists files, for one format.
10. **STL and OBJ files over 512 MB are refused** with the reason in health: Babylon parses them whole in the page.

## Consequences

- `specto-readers` depends on `trimesh` and `pycollada` (and through them `python-dateutil` and `tzdata`). The import is paid only in the conversion's child process.
- Opening a DAE writes into the user's data folder. That is the owner's decision: the GLB is reusable outside Specto and survives a cache wipe. A watched intaker sees the new file and resolves once more; the converted mesh matches by then, so nothing is written again. The temporary file of a write can show for a moment in a listing taken during the write; a watcher may see it come and go and resolve once more. The watch code does not filter `.*.tmp` today.
- A converted mesh opened directly (the `.glb` itself) is an ordinary glTF and follows the `up` param.
- glTF files still follow the `up` param (unchanged): a mixed scene of a Z-up STL and a glTF under `up: "z"` turns the glTF too.
- A texture an OBJ names outside the project is not served, by design; the health note says which.
- Thumbnails of the three formats are the mesh kind card, as for every 3D file.

## Considered options

- **pyassimp**: BSD-3-Clause bindings, but it needs the native assimp library, which its wheel does not ship. Rejected: a native dependency per platform for one format.
- **Blender or collada2gltf as a converter**: Blender is GPL and huge; collada2gltf is an unmaintained native binary. Rejected.
- **A COLLADA loader in the page**: Babylon has none; writing one duplicates trimesh's work in JavaScript, with no GLB for other tools to reuse.
- **Only the cache, never next to the file**: the owner chose the file beside the DAE, with the cache as the fallback.
- **Trusting the converted mesh by mtime alone**: rejected. An older DAE put back (a checkout, a restore) is older than the GLB and would keep it.
- **A content hash of the DAE in the record**: not taken. Size and mtime catch the swaps above without reading the whole file on every open.
- **Baking the view's up axis into the converted GLB**: rejected. The file would change with a view param, and two views with different params would fight over it.
- **OBJ siblings only in the OBJ's own folder**: rejected. Robot packages keep textures beside the meshes folder; the project is the boundary instead.
