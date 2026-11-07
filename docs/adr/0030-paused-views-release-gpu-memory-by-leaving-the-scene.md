---
status: accepted
date: 2026-11-06
---

# Paused views give back GPU memory by leaving the scene

Task SPEC-3. Amends the pause contract of contracts sections 11 and 16.

## Context

A view outside every on-screen window frame is paused, and so is every view while the overworld is open. Until SPEC-3 pausing only stopped per-frame work. The default `pause()` set a flag, and the shell hid the view's slot. A hidden item keeps its scene-graph nodes, so every paused view kept its GPU memory: a gallery its texture atlas, an image its decoded picture, a video mpv's render context. A canvas of many views held the GPU memory of all of them.

Measuring this needs care on the OpenGL backend. The NVIDIA driver keeps the memory an app frees in that process's own pool and reuses it for the app's next allocation. So the Windows per-process GPU counters (Task Manager's dedicated memory) do not drop when a texture is deleted. They show the most the process ever held at once. A standalone probe confirmed it: deleting 160 MB of textures left the counter unchanged, the device-wide figure (`GL_NVX_gpu_memory_info`) dropped by 160 MB, and the next 160 MB allocation reused the freed memory. What paused views cost the user is therefore what they *keep*. `tools/bench_gpu_release.py` measures that: views of one type visit the screen in turn, and the app's memory grows by the footprint of every paused view that kept its memory.

## Decision

1. **A paused view's item may leave the scene.** A host sets `release_when_paused`. Its item is then taken out of the scene (no parent item, no window) from the pause until the resume. The window deletes the item's scene-graph nodes on its next frame, and with them the GPU memory they own. The item itself stays, and so does its state: scroll, zoom, selection, the playing position. On resume the registry puts it back into its slot, and the item rebuilds its nodes. The registry owns this, because it already owns the slot parenting. A slot made while the item is out (a rebuild) gets the item only at the resume (`hosts.is_released`).
2. **The gallery and the video leave the scene.** The gallery's atlas lives in its scene-graph node. When the node is deleted, the atlas goes with it. The native item already treats a new node as an empty atlas (every tile non-resident), so no native change was needed. On resume the gallery host waits for the item's first frame back, which reports nothing resident, and then pumps decodes for what the view shows. The video's node owns its frame buffer and frees mpv's render context. When the item comes back, it reopens its file for the new context, as it already did after re-entering a window. The host keeps the frame shown and seeks back to it once the file is open. It emits no position change in between, so the views that follow its time do not jump. The registry takes the item out before the host's own `pause` runs, so the host knows its item is out.
3. **The image view lets go of its picture.** Qt Quick keeps an image's texture as long as the image holds its picture, whether the item is in the scene or not. So the image host does not leave the scene. Its QML Image drops its source while paused. On resume the same file decodes again, and the zoom and centre come back through the item's existing restore path.
4. **Web views keep their pages.** No release that QtWebEngine 6.12 offers pays off here (measurements below):
   - Taking the item out of the scene makes things worse: a page created out of the scene allocated about 470 MB once shown.
   - `Frozen` frees nothing.
   - `Discarded` frees a text page's memory, but each reload leaks about 45 MB, and the 3D view's WebGL memory is not freed at all.
   - A discarded page also reloads and loses its scroll and editor state.
5. **Other views keep the default.** They stay in their slot, and the hidden slot keeps them from drawing: the pdf view (2 MB kept), the file tree, the preview, the usd view. The usd view already frees its renderer's memory by stopping its source after `idle_kill_s`.

## Consequences

Kept per paused view, before and after (MB; `bench_gpu_release.py`, three views of each type, NVIDIA RTX, OpenGL backend). Resume is the time from the view's return until its slot shows the same pixels as before it left.

| type | kept before | kept after | resume after |
|---|---|---|---|
| gallery (480 frames, atlas filled) | 67 | 1 | 0.6 s (re-decodes the visible thumbnails) |
| image (6000 x 4000 photo) | 132 | 1 | 0.4 s (decodes the photo again) |
| video (640 x 480) | 265, even parked before ever shown | under 2 | 1.4 s (reopens the file, seeks back) |
| pdf | 3 | 2 | 0.05 s |
| html, markdown, json | 16-20 | 16-20 | 0.05 s |
| 3d | about 95 | about 95 | 0.1 s |

- A canvas of many galleries, images and videos now costs about what is on screen, not what is on the canvas. A view parked from its creation never allocates, so an agent's large composition starts cheaply.
- Resume is no longer free for these types. A gallery shows its placeholders until the visible thumbnails decode again. An image shows its loading hint until it decodes. A video shows its frame again after about 1.4 s.
- Each overworld round trip leaks GPU memory in QtWebEngine: about 4 MB per text page shown, and 50-100 MB per 1000 x 760 3D view. The cause is not the pause. The page's surface is re-made at every hide and show, and also at every resize, and the old buffers are never freed (QtWebEngine's `QWE_SharedImageBuffer`s on the ANGLE D3D11 path). Telling Chromium to render through desktop OpenGL (`--use-angle=gl`) stops the leak, but the views then render blank. This is a separate task. The galleries, images and videos leak nothing over 20 round trips.
- The process counters still show the high-water mark. A user who watches Task Manager sees the memory stop growing, not drop.

## Considered options

- Drop the gallery's atlas through a native `releaseAtlas` call: rejected. Taking the item out of the scene frees the same memory with no native change, and the item already handles a fresh node.
- Detach every paused view: rejected. Web pages got worse, and an image's texture survives detaching.
- Discard web pages and restore their scroll state through the bridge: rejected for now. Each reload leaks more than the discard frees, and the 3D view frees nothing.
- Unload the video's file while paused: rejected. Freeing the render context alone drops a parked video from about 265 MB to about 2 MB, and the decoder stays warm for a fast resume.
