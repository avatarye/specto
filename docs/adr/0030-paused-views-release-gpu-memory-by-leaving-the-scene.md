---
status: accepted
date: 2026-11-06
---

# Paused views give back GPU memory by leaving the scene

Task SPEC-3. Amends the pause contract of contracts section 11 (view hosts) and the culling of section 12 (shell).

## Context

A view outside every on-screen window frame is paused, and so is every view while the overworld is open. Until SPEC-3 pausing only stopped per-frame work. The default `pause()` set a flag, and the shell hid the view's slot. A hidden item keeps its scene-graph nodes, so every paused view kept its GPU memory: a gallery its texture atlas, an image its decoded picture, a video mpv's render context. A canvas of many views held the GPU memory of all of them.

Measuring this needs care on the OpenGL backend. The NVIDIA driver keeps the memory an app frees in that process's own pool and reuses it for the app's next allocation. So the Windows per-process GPU counters (Task Manager's dedicated memory) do not drop when a texture is deleted. They show the most the process ever held at once. A standalone probe confirmed it: deleting 160 MB of textures left the counter unchanged, the device-wide figure (`GL_NVX_gpu_memory_info`) dropped by 160 MB, and the next 160 MB allocation reused the freed memory. What paused views cost the user is therefore what they *keep*. `tools/bench_gpu_release.py` measures that: views of one type visit the screen in turn, and the app's memory grows by the footprint of every paused view that kept its memory.

## Decision

1. **A paused view's item may leave the scene.** A host sets `release_when_paused`. Once the view has stayed paused for `release_delay_s` (3 s by default), the registry takes its item out of the scene: no parent item, no window. The window deletes the item's scene-graph nodes on its next frame, and with them the GPU memory they own. The item itself stays, and so does its state: scroll, zoom, selection, the playing position. On resume the registry puts it back into its slot, and the item rebuilds its nodes. The host hears both moves through `on_release` and `on_restore`; `on_restore` runs before `resume`. The registry owns all of this, because it already owns the slot parenting.
   - A view panned out and straight back within the delay is never released, so it pays no resume cost.
   - Only an item that sits in its slot is released. A view whose slot is being rebuilt is never released.
   - A slot made while the item is out gets the item only at the resume. A released view with no slot at the resume waits in the hidden holder item until a slot takes it.
   - `hosts.is_released` and the host's `snap` (`released`) say which views are out.
2. **The gallery and the video leave the scene.**
   - Gallery: its atlas lives in its scene-graph node, so it goes with the node. The native item already treats a new node as an empty atlas (every tile non-resident), so dropping it needs no native change. On `on_restore` the host waits for the item's first frame back, which reports nothing resident, and then pumps decodes for what the view shows.
   - Video: its node owns its frame buffer and frees mpv's render context. The item then reopens its file for the next context, as it already did after re-entering a window. On `on_release` the host records the frame shown. While the video is out, and while its file reopens, it emits no position change, so the views that follow its time do not jump. Once the file is open, it seeks back to that frame, or to the frame a time received meanwhile asked for. A video that was playing plays on only after that landing.
3. **The image view lets go of its picture, too.** Qt Quick keeps an image's texture as long as the image holds its picture, in the scene or not, so taking the item out alone frees nothing. On `on_release` the QML Image also drops its source. On `on_restore` the same file decodes again, and the zoom and centre come back through the item's existing restore path.
4. **Web views keep their pages.** No release that QtWebEngine 6.12 offers pays off here (measurements below):
   - Taking the item out of the scene makes things worse: a page created out of the scene allocated about 470 MB once shown.
   - `Frozen` frees nothing.
   - `Discarded` frees a text page's memory, but each reload leaks about 45 MB, and the 3D view's WebGL memory is not freed at all.
   - A discarded page also reloads and loses its scroll and editor state.
5. **Other views keep the default.** They stay in their slot, and the hidden slot keeps them from drawing: the pdf view (2 MB kept), the map (a web view), the file tree, the preview, the usd view. The usd view already frees its renderer's GPU memory, in its own process, by stopping its source after `idle_kill_s` (60 s).
6. **Two edge fixes in the native gallery.** First, a tile's fade-in alpha is baked into its vertices at sync. The item asked for the frames that finish the fade only within 220 ms of the last upload, measured when the GUI thread got round to it. A busy GUI thread left tiles half faded, and a resume re-uploads every visible tile at once, so it hit this most. The item now judges by the time of the frame it reports. Second, the item ends a press or drag when it loses the mouse grab (`mouseUngrabEvent`), since an item that leaves the scene mid-press gets no release event.

## Consequences

Kept per paused view, before and after (MB; `bench_gpu_release.py`, three views of each type, NVIDIA RTX, OpenGL backend). Resume is the time from the view's return until its slot shows the same pixels as before it left.

| type | kept before | kept after | resume after |
|---|---|---|---|
| gallery (480 frames, atlas filled) | 67 | 1-5 | 0.6 s (re-decodes the visible thumbnails) |
| image (6000 x 4000 photo) | 132 | about 0 | 0.4 s (decodes the photo again) |
| video (640 x 480) | 265, even off-screen from creation, never shown | 1-5 | 1.0-1.4 s (reopens the file, seeks back) |
| pdf | 3 | 2 | 0.05 s |
| html, markdown, json | 16-20 | 16-20 | 0.05 s |
| map | 124 | 124 | 0.06 s |
| 3d | about 95 | about 95 | 0.1 s |
| usd (RTX, small stage) | 2570 until `idle_kill_s` (60 s), then about 10 | unchanged | 0.06 s within 60 s; 4.2 s after (the renderer relaunches) |

Within the 3 s delay nothing is released, and resume costs nothing for any type.

- A canvas of many galleries, images and videos now costs about what is on screen, not what is on the canvas. A view off-screen from its creation never allocates, so an agent's large composition starts cheaply.
- Resume is no longer free for these types once the delay has passed. A gallery shows its placeholders until the visible thumbnails decode again. An image shows its loading hint until it decodes. A video shows its frame again after about 1.4 s. Drawing the view's overworld snapshot over its slot until the first real frame would hide this. It is a follow-up: the snapshot store holds only the last overworld grab, not a grab taken at the release.
- Each overworld round trip leaks GPU memory in QtWebEngine: about 4 MB per text page shown, and 50-100 MB per 1000 x 760 3D view. The cause is not the pause. The page's surface is re-made at every hide and show, and also at every resize, and the old buffers are never freed (QtWebEngine's `QWE_SharedImageBuffer`s on the ANGLE D3D11 path). Telling Chromium to render through desktop OpenGL (`--use-angle=gl`) stops the leak, but the views then render blank. That leak is task SPEC-47, so the "no leak over 20 overworld round trips" criterion is met here only for the views this ADR releases. Galleries, images and videos leak nothing over 20 round trips.
- The process counters still show the high-water mark. A user who watches Task Manager sees the memory stop growing, not drop.

## Considered options

- Drop the gallery's atlas through a native `releaseAtlas` call: rejected. Taking the item out of the scene frees the same memory with no native change, and the item already handles a fresh node.
- Detach every paused view: rejected. Web pages got worse, and an image's texture survives detaching.
- Discard web pages and restore their scroll state through the bridge: rejected for now. Each reload leaks more than the discard frees, and the 3D view frees nothing.
- Unload the video's file while paused: not needed. Freeing the render context alone drops an off-screen video from about 265 MB to about 2 MB, and the item already reopens its file for the new context; unloading on top of that would add nothing.
- Release at the pause, with no delay: rejected. Panning a video out and back would pay the 1.4 s reopen every time.
