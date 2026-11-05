"""The v2 seam-3 suite: every scripted human flow of the v2 shell, each on its own seeded
project, with screenshots in out/verify/v2/ (spec #35 Testing Decisions, ticket #49).
Every flow belongs to one of three tiers (#237, ``TIERS``), and one command runs a tier:

    # the offscreen tier (the default): no window, no focus, safe while the desktop is in
    # use; entries run in parallel (--jobs, at most 4 and cores - 8)
    uv run python tools/gui_flows/v2_suite.py --work <scratch folder> [--out out/verify/v2]
        [--tier offscreen] [--jobs 4] [--only open views ...] [--live] [--net]

    # the GPU window tier: a real window that never takes the focus (the same driver on
    # the windows platform; RTX / Storm, mpv video, WebGL pages); one at a time
    uv run python tools/gui_flows/v2_suite.py --work <scratch> --tier gpu [--heavy]

    # the real-mouse tier: pyautogui, under the desktop lock, when nobody uses the machine
    uv run python tools/desktop_lock.py run v2-mouse -- <pyauto python> \\
        tools/gui_flows/v2_suite.py --work <scratch> --tier mouse

The offscreen and GPU tiers drive the app through its test driver (``gui_qtdrive.py``:
``specto-app --test-driver``, Qt test events on the GUI thread, a fake clipboard, muted
audio, fake launchers); the mouse tier through ``gui_session.py`` / ``gui_drive.py``
(a real mouse and keyboard). Each entry seeds a project folder under ``--work``, launches
one app process per session (killed afterwards) and drives one or more flow files.
``--heavy`` adds the 20 M-point 3D flow (GPU tier); ``--live`` the one short run against
the real Claude Code agent (your Claude login; billed to it); ``--net`` the html view on
a page with live remote map frames (#93). The report (one line per flow) lands in
``<out>/suite-<tier>.txt``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SESSION = HERE.parent / "gui_session.py"
QTDRIVE = HERE.parent / "gui_qtdrive.py"
sys.path.insert(0, str(HERE.parent))
from console_utf8 import utf8_stdio  # noqa: E402

TIER_NAMES = ("offscreen", "gpu", "mouse")
SCRIPTED = f"--agent scripted:{(HERE / 'v2-49-agent-script.json').as_posix()}"
SCRIPTED_44 = f"--agent scripted:{(HERE / 'v2-44-agent-script.json').as_posix()}"
SCRIPTED_SELECT = f"--agent scripted:{(HERE / 'v2-fix-select-agent-script.json').as_posix()}"
SCRIPTED_161 = f"--agent scripted:{(HERE / 'v2-161-agent-script.json').as_posix()}"
SCRIPTED_162 = f"--agent scripted:{(HERE / 'v2-162-agent-script.json').as_posix()}"
SCRIPTED_168 = f"--agent scripted:{(HERE / 'v2-168-agent-script.json').as_posix()}"
SCRIPTED_169 = f"--agent scripted:{(HERE / 'v2-169-agent-script.json').as_posix()}"
SCRIPTED_174 = f"--agent scripted:{(HERE / 'v2-174-agent-script.json').as_posix()}"
SCRIPTED_184 = f"--agent scripted:{(HERE / 'v2-184-agent-script.json').as_posix()}"
SCRIPTED_187 = f"--agent scripted:{(HERE / 'v2-187-agent-script.json').as_posix()}"
SCRIPTED_187_SEND = f"--agent scripted:{(HERE / 'v2-187-send-agent-script.json').as_posix()}"
#: the #187 flow's nimitz: a fake that records its argv, never a real fleet member
FAKE_NIMITZ = str(
    HERE / "v2-187-fake-nimitz" / ("nimitz.cmd" if sys.platform == "win32" else "nimitz")
)
SCRIPTED_186 = f"--agent scripted:{(HERE / 'v2-186-agent-script.json').as_posix()}"
SCRIPTED_219 = f"--agent scripted:{(HERE / 'v2-219-agent-script.json').as_posix()}"
SCRIPTED_191 = f"--agent scripted:{(HERE / 'v2-191-agent-script.json').as_posix()}"


def seed(script: str, *args: str) -> list[str]:
    return ["uv", "run", "python", str(HERE / script), *args]


def make_v1_project(root: Path) -> None:
    """A Specto v1 (M2) project: ``.specto/project.toml`` and a canvases/ folder, no
    ``canvas.json`` (spec #35 story 6)."""
    spec = root / ".specto"
    (spec / "canvases").mkdir(parents=True, exist_ok=True)
    (spec / "project.toml").write_text(
        'id = "m2proj"\nname = "m2-project"\nslug = "m2-project"\n', encoding="utf-8"
    )
    (spec / "canvases" / "c1.json").write_text(
        json.dumps({"schema_version": 1, "canvas": {"id": "c1"}, "objects": {}}),
        encoding="utf-8",
    )


# name -> (seed command or callable, sessions). A session: flows plus gui_session flags
# (and "env": extra environment, e.g. QT_SCALE_FACTOR for a 150 % run).
SUITE: dict[str, dict] = {
    "open": {
        # stories 1-5, 7-17, 29, 31, 35, 37, 51: welcome, file tree, the prompt bar
        # composes galleries linked on frame, diff on request, Ctrl+Z, relaunch from the
        # recent list restores layout, selection and undo history
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [
            {
                "flows": ["v2-49-open.txt"],
                "extra": SCRIPTED,
                "welcome": True,
                "prefix": "49-01",
            },
            {
                "flows": ["v2-49-reopen.txt"],
                "extra": SCRIPTED,
                "welcome": True,
                "recent": True,
                "prefix": "49-08",
            },
        ],
    },
    "thumb-size": {
        # #173: Ctrl+wheel grows the gallery thumbnails, a click keeps the size, and a
        # relaunch from the recent list shows it again
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [
            {"flows": ["v2-173-thumb-size.txt"]},
            {"flows": ["v2-173-reopen.txt"], "welcome": True, "recent": True, "prefix": "173-04"},
        ],
    },
    "project-node": {
        # #83: the project source drawn at the origin, an old canvas rewired on load, hover
        # says all files and the count, nothing moves it, orphans and Delete of the only
        # link to it are refused, Home brings it back, the overworld shows it
        "seed": seed("v2-83-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-83-project-node.txt"]}],
    },
    "v1-refused": {
        # story 6: a v1 project typed on the welcome card is refused with a message
        "seed": make_v1_project,
        "sessions": [{"flows": [], "welcome": True, "only": True, "prefix": "49-12"}],
    },
    "prompt": {
        # stories 8-17, 19: the agent panel on hover (#162), streaming, invisible retries,
        # failure after three, a slash command refused, a whole turn undone from its row
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-44-prompt-bar.txt"], "extra": SCRIPTED_44}],
    },
    "skills": {
        # #161: the agent saves a project skill; the strip's Open and Undo; the next turn
        # uses it
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-161-skills.txt"], "extra": SCRIPTED_161}],
    },
    "layouts": {
        # #219: the agent authors a layouts skill, a /layout command and the layout file
        # itself (skill, command and data rows with Open and Undo); /layout review brings
        # a dragged-away layout back as one undoable turn
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-219-layouts.txt"], "extra": SCRIPTED_219}],
    },
    "agent-prompt": {
        # #162: the agent icon and its hover thread panel: markdown replies with Copy, the
        # turn row's Undo and diff, needs-you with the panel closed, slash list, pin,
        # resize, Esc keeps the draft, a new session below a divider; #170: the S icon
        # art and its equal left and bottom gaps at two window sizes
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-162-agent-prompt.txt"], "extra": SCRIPTED_162}],
    },
    "fleet-peer": {
        # #187 sections 1-3: the instance name in the status bar and the panel header; a
        # fleet agent's message runs as a turn labelled with the sender; Peer requests: ask
        # waits for Run; the details popover's instance with Copy
        "seed": seed("v2-39-seed.py", "{project}"),
        # (the fake nimitz too: no turn here sends, but nothing may reach a real member)
        "sessions": [
            {
                "flows": ["v2-187-fleet-peer.txt"],
                "extra": SCRIPTED_187,
                "env": {"SPECTO_NIMITZ": FAKE_NIMITZ},
            }
        ],
    },
    "agent-code": {
        # #168 parts 4-5: the agent writes per-pick link code; the code row's Open and
        # Undo; the link waits for the user's Approve, then tree B follows the pick
        "seed": seed("v2-168-agent-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-168-agent-code.txt"], "extra": SCRIPTED_168}],
    },
    "scratch": {
        # #184: the scratch pad (markdown pages) from the project node, Ctrl+V, the agent's
        # three blocks and their one-row Undo, a link in the pad's preview, the source
        # editor's autosave and Ctrl+Z, the tab strip (add, rename, move, close, undo)
        "seed": seed("v2-184-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-184-scratch.txt"], "extra": SCRIPTED_184}],
    },
    "fleet-send": {
        # #187 section 4: the agent's fleet_send through a fake nimitz; one thread row
        # per send, a refused one (not an allowed recipient) in the error tone
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [
            {
                "flows": ["v2-187-fleet-send.txt"],
                "extra": SCRIPTED_187_SEND,
                "env": {"SPECTO_NIMITZ": FAKE_NIMITZ},
            }
        ],
    },
    "user-removed": {
        # #169: the user closes a view mid-turn; the agent's edit adding a view goes
        # through under a new name; removing the closed view again is a no-op
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-169-user-removed.txt"], "extra": SCRIPTED_169}],
    },
    "tile-floor": {
        # #191, #201: the canvas tile floor at rest, the thinking bands at the S icon,
        # new-view bands and the done band, the selection footprint (hidden while
        # dragged), a far view's edge pulses and chip (click pans there), the wiring
        # layer's dim, a pan over a still floor (#197), the overworld's floor
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-191-tile-floor.txt"], "extra": SCRIPTED_191}],
    },
    "walkthrough": {
        # #174: "walk me through tamu" in the agent prompt: preparing lights the path,
        # the establishing shot in the overworld, hops at 1:1 with rings and the pulse,
        # pause, the hop track, the summary with its chips, Replay, Esc back
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-174-walkthrough.txt"], "extra": SCRIPTED_174}],
    },
    "dataflow": {
        # stories 3, 34-36, 39, 50, 56: the project intaker, picks in the tree feed a
        # gallery, close and undo
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-39-first-dataflow.txt"]}],
    },
    "image-picks": {
        # #116: the image view on the tree's picks leaves "loading" as they grow
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-116-image-picks-grow.txt"]}],
    },
    "chrome": {
        # #120: no top bar or window-frame guide, undo/redo and the global menu floating
        # top-right, the bottom bar's own surface, the ink selection highlight
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-120-shell-chrome.txt"]}],
    },
    "canvas": {
        # stories 37, 62-66: free canvas, move/resize snapping, pan, culling, Home
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-40-free-canvas.txt"]}],
    },
    "follow": {
        # stories 49, 51, 53: follow links matching on frame, two-way
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-41-follow-on-frame.txt"]}],
    },
    "video": {
        # stories 41-43, 52: the video as root view, time links, galleries follow
        "seed": seed("v2-42-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-42-video-time-links.txt"]}],
    },
    "video-footer": {
        # #128: the player's footer: progress bar and its timecode tip, frame readout,
        # speed slider, the viewer row stepping through three videos
        "seed": seed("v2-128-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-128-video-footer.txt"]}],
    },
    "audio": {
        # #216: sound in the video view: the speaker and volume slider in the footer,
        # muted by default, mpv muted except in normal playback, one view with sound per
        # window, the agent's muted / volume params (two videos with a beep track)
        "seed": seed("v2-216-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-216-audio.txt"]}],
    },
    "python": {
        # stories 27, 49, 58: Python links with first-run approval
        "seed": seed("v2-43-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-43-approve-python-link.txt"]}],
    },
    "approve-203": {
        # #203: a link.approve through the endpoint only opens an approval card in the
        # agent panel; the user's click on it approves (its own project: a second session
        # on the python entry's project would find the file approved already)
        "seed": seed("v2-43-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-203-approve-request.txt"]}],
    },
    "pylink": {
        # #168: a per-pick Python link maps the recording picked in one file tree to its
        # annotation folder in a second tree (approved by mouse), the map follows
        "seed": seed("v2-168-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-168-pylink.txt"]}],
    },
    "pylink-chain": {
        # #176: a two-hop chain of per-pick Python links: a recording picked in tree A
        # selects its mirror in tree B, whose picks drive a map and a gallery built by
        # the script (one approval for one file)
        "seed": seed("v2-176-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-176-pylink-chain.txt"]}],
    },
    "overworld": {
        # stories 54, 59-61, 67-71: chains, Tab, link nodes, summary, link view, delete
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-45-overworld.txt"]}],
    },
    "overcards": {
        # #90: overworld cards with identicon ports and the #80 header; link nodes off
        # the card headers
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-90-overworld-cards.txt"]}],
    },
    "ports": {
        # #81: port identicons, no curves in the normal view, port hovers (filter, type)
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-81-ports.txt"]}],
    },
    "wiring": {
        # #82: the backtick toggles the wiring layer: dimmed views, curves, link nodes
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-82-wiring.txt"]}],
    },
    "capsule": {
        # #160: a view dragged near its partner joins their ports in a capsule, which
        # holds until the break distance; its tip; the wiring layer opens its link
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-160-capsule.txt"]}],
    },
    "tips": {
        # #111: port tips on their own background; ordinary tips show under the layer
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-111-tips.txt"]}],
    },
    "ports-121": {
        # #121: ports on the header line, one-line port tips with the link filter, the
        # root node's identicon and refresh circle, dot link nodes opening the link view
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-121-ports.txt"]}],
    },
    "tips-127": {
        # #127: port tips with DATA and FILTER hanging centred under the port, the
        # one-line link-dot tip, the root node's refresh at its bottom centre
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-127-port-tips.txt"]}],
    },
    "views": {
        # stories 38, 40, 44, 45: gallery, image, json and markdown interaction
        "seed": seed("v2-46-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-49-views.txt"]}],
    },
    "views-map": {
        # story 48: the map's point pick (the GPU tier: MapLibre needs WebGL)
        "seed": seed("v2-46-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-49-views-map.txt"]}],
    },
    "context-menu": {
        # #154: the shared context menu: Copy path / Copy relative path in the file tree
        # (one row, a selection, Shift+F10) and on a gallery tile with its own entries
        "seed": seed("v2-46-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-154-context-menu.txt"]}],
    },
    "drag-out": {
        # #185: drags out of views into a drop probe outside the app: tree rows (one, the
        # selection), a gallery tile, the image view (Ctrl+drag, a Shift crop as a PNG),
        # text selections in the markdown and json views
        "seed": seed("v2-46-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-185-drag-out.txt"]}],
    },
    "launchers": {
        # #186: launchers in the context menu (the default first, the Open with submenu,
        # an each=false launcher on three files, a program refused the system default),
        # a typo in launchers.toml as a notice and its reload, the agent's launch row
        "seed": seed("v2-186-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-186-launchers.txt"], "extra": SCRIPTED_186}],
    },
    "web-chrome": {
        # #130: no page bars; viewer footers (step, position, name, tools), the map's
        # LAYERS and the markdown's OUTLINE side panels
        "seed": seed("v2-130-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-130-web-chrome.txt"]}],
    },
    "web-zoom": {
        # #189: Ctrl+wheel is no page zoom: the side panels and footers keep the theme's
        # size; over the markdown note the note alone zooms; #202: that zoom is the view's
        # params.zoom (the agent sets it too) and a relaunch from the recent list shows it
        "seed": seed("v2-130-seed.py", "{project}"),
        "sessions": [
            {"flows": ["v2-189-web-zoom.txt"]},
            {
                "flows": ["v2-202-zoom-reopen.txt"],
                "welcome": True,
                "recent": True,
                "prefix": "202-02",
            },
        ],
    },
    "map-layers": {
        # #157: the map footer's basemap switcher (none, OSM, satellite) and layers
        # toggle; #158: every view control shows at rest, the auto-update eye first
        "seed": seed("v2-130-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-157-map-layers.txt"]}],
    },
    "map-batches": {
        # #181: over 200 GeoJSON files the map loads a batch, its footer shows loaded /
        # total with Load more; a data reload frames the map on the moved data
        "seed": seed("v2-181-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-181-map-batches.txt"]}],
    },
    "3d": {
        # stories 46, 47: the 3D view, picking, navigation, camera out
        "seed": seed("v2-47-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-47-3d-scene.txt"]}],
    },
    "3d-129": {
        # #129: the 3D view's side panels (camera speed, world origin, All / None, sizes,
        # collapse) and footer (frame all), no in-body bar
        "seed": seed("v2-47-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-129-3d-chrome.txt"]}],
    },
    "3d-152": {
        # #152: Unreal Editor viewport controls (LMB turn/walk, RMB look + WASD fly + wheel
        # speed, MMB / LMB+RMB pan, wheel dolly, Alt orbit/dolly/pan, F frames selection)
        "seed": seed("v2-47-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-152-3d-nav.txt"]}],
    },
    "3d-153": {
        # #153: view modes lit / unlit / wireframe / collision (an Unreal UCX_ hull in
        # scans/crate.gltf) from the footer's mode pill, always in sight (#158)
        "seed": seed("v2-153-3d-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-153-3d-modes.txt"]}],
    },
    "3d-hdri": {
        # #175: HDRI environments from the footer's pill (None + five bundled Poly Haven
        # HDRIs), the VIEW panel's intensity / rotation / background, wireframe without the
        # background, the agent's params; PBR spheres over a point cloud
        "seed": seed("v2-175-3d-hdri-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-175-3d-hdri.txt"]}],
    },
    "usd-139": {
        # #139: the usd view on the stub USD source: start phases, frames, the VIEW panel,
        # the PRIMS outliner, the footer, stepping through two picks, panels collapsing
        "seed": seed("v2-139-seed.py", "{project}"),
        # (the stub even where the rtx extra is installed)
        "sessions": [
            {
                "flows": ["v2-139-usd-chrome.txt"],
                "env": {
                    "SPECTO_USD_STUB_PHASE_MS": "700",
                    "SPECTO_DISABLE_CAPABILITIES": "ovrtx,storm",
                },
            }
        ],
    },
    "usd-155": {
        # #155 / #156 on the stub USD source: the loading label until the first complete
        # frame, a renderer crash said in words with its last errors, Retry, and a second
        # pick covering the first stage's picture
        "seed": seed("v2-139-seed.py", "{project}"),
        "sessions": [
            {
                "flows": ["v2-155-usd-crash-load.txt"],
                "env": {
                    "SPECTO_USD_STUB_PHASE_MS": "700",
                    "SPECTO_USD_STUB_CRASH_MS": "3000",
                    "SPECTO_DISABLE_CAPABILITIES": "ovrtx,storm",
                },
            }
        ],
    },
    "usd-180": {
        # #180 on the stub USD source: opened with the VIEW panel restored wide and PRIMS
        # collapsed, the picture keeps the viewport's aspect from the first look, untoggled
        "seed": seed("v2-139-seed.py", "{project}"),
        "sessions": [
            {
                "flows": ["v2-180-usd-aspect.txt"],
                "env": {
                    "SPECTO_USD_STUB_PHASE_MS": "700",
                    "SPECTO_DISABLE_CAPABILITIES": "ovrtx,storm",
                },
            }
        ],
    },
    "usd-39": {
        # #39 on the stub USD source: the VIEW panel's Free / Orbit control (one submit,
        # the status bar's line follows), a drag in orbit mode turns around the selected
        # block keeping its distance, F and Frame selected on a parent prim frame its
        # whole subtree (the Blocks scope: the three blocks together)
        "seed": seed("v2-139-seed.py", "{project}"),
        "sessions": [
            {
                "flows": ["v2-39-usd-orbit.txt"],
                "env": {
                    "SPECTO_USD_STUB_PHASE_MS": "200",
                    "SPECTO_DISABLE_CAPABILITIES": "ovrtx,storm",
                },
            }
        ],
    },
    "usd-137": {
        # #137: the usd view renders the depot world with RTX (needs the rtx
        # extra and the user's depot world): start phases, a non-black frame, orbit, fly, F
        "seed": seed("v2-137-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-137-usd-rtx.txt"]}],
    },
    "usd-151": {
        # #151: Unreal Editor's viewport on the depot world with RTX: LMB turn and move,
        # RMB look, RMB + W fly and wheel speed, MMB / chord pan, wheel dolly, Alt orbit
        # and dolly, a click picks, F frames the selection, then the stage
        "seed": seed("v2-137-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-151-usd-nav.txt"]}],
    },
    "usd-140": {
        # #140: selection with RTX on the depot world: an outliner double-click selects and
        # frames a property, a click picks the block, Ctrl adds, the sky clears; a json
        # view on the usd view's content port shows the selected prim's properties
        "seed": seed("v2-137-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-140-usd-select.txt"]}],
    },
    "usd-141": {
        # #141: the usd view's time with RTX: a root video scrubs an animated stage through
        # a time map; the stage's scrubber, play and readout; as the root view its play
        # drives the video (needs the rtx extra and the depot sky)
        "seed": seed("v2-141-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-141-usd-time.txt"]}],
    },
    "usd-143": {
        # #143: the usd view renders the depot world with Storm (needs the
        # OpenUSD imaging build, the storm extra and the user's depot world): a grey first look
        # with the readout's MDL note, orbit, fly, F
        "seed": seed("v2-137-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-143-usd-storm.txt"]}],
    },
    "usd-153": {
        # #153: the usd view's view modes with RTX: the footer's mode pill (always
        # shown), unlit, wireframe, collision (two colliders, a pick names one) and lit
        # again (needs the rtx extra and the depot sky)
        "seed": seed("v2-153-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-153-usd-modes.txt"]}],
    },
    "usd-153-storm": {
        # #153: the same view modes with Storm (needs the OpenUSD imaging build, the storm
        # extra and the depot sky, which Storm references but does not draw)
        "seed": seed("v2-153-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-153-usd-modes-storm.txt"]}],
    },
    "usd-42": {
        # SPEC-31: the environment light with RTX on a small stage without lights (the
        # seed writes it): Stage (the fallback dome), Sky (NVIDIA's dynamic skies, fetched
        # on first use into the session's user dir, or copied there by the seed from this
        # machine's cache: Clear sky, the time of day and cloudiness sliders, then
        # Overcast from the list) and HDRI (the 3d view's HDRIs, built by
        # packages/specto-web/build.py: sunny, then urban from the list) from the VIEW
        # panel's controls, one submit each, reported by snap (needs the rtx extra, the
        # web build and, without a cache, the network)
        "seed": seed("v2-42-usd-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-42-usd-environment.txt"]}],
    },
    "pdf": {
        # #69: the pdf view, pages, zoom, fit
        "seed": seed("v2-69-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-69-pdf.txt"]}],
    },
    "preview": {
        # #106: Ctrl+double-clicks (SPEC-32) on an image, pdf, note, video, point cloud, html
        # page and json re-point one preview, each in the renderer of its kind; an
        # unknown file opens the "Not supported" view
        "seed": seed("v2-106-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-106-preview.txt"]}],
    },
    "pdf-zoom": {
        # #94: Ctrl+wheel zooms the document about the pointer (a page corner)
        "seed": seed("v2-95-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-94-pdf-zoom.txt"]}],
    },
    "thumbs": {
        # #105: one gallery over every kind, thumbnails rendered in the worker pool
        "seed": seed("v2-105-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-105-mixed-gallery.txt"], "pool": True}],
    },
    "resume": {
        # #95: a view of every type is painted again after the overworld round trip
        "seed": seed("v2-95-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-95-overworld-resume.txt"]}],
    },
    "watch": {
        # stories 57, 74, 75: watched arrivals, locks and quotas refused with fix-its
        "seed": seed("v2-48-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-48-watch.txt"], "pool": True}],
    },
    "header": {
        # #80 (spec #78 decision 6): `<id>: <label>` headers, hints in the status bar
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-80-header-status.txt"]}],
    },
    "auto-update": {
        # #102: the auto-update switch and its status-bar hint, refresh on the view and
        # on the project node, arrivals swapping in once the switch is on
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-102-auto-update.txt"]}],
    },
    "group": {
        # #84: a marquee selects two views, dragging one header moves both (one undo
        # step, the dragged view snaps, a locked view follows), an unselected one alone
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-84-group-move.txt"]}],
    },
    "float": {
        # #112: Float keeps a view fixed in the window while the canvas pans, a narrower
        # window keeps its corner offset, floaters snap to each other inside the window,
        # the wiring layer's curve crosses the layers, the overworld's floating strip,
        # Unfloat drops it on the canvas under it
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-112-float-layer.txt"]}],
    },
    "collapse": {
        # #114: collapse to the title bar and expand, picks still reaching a collapsed
        # gallery, snapping under a title bar, the overworld's collapsed card, the health
        # dot only when a view is not healthy
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-114-collapse.txt"]}],
    },
    "floor-shown-rect": {
        # #208: the floor's footprint follows the rect a view is shown at: it stays under
        # a floater while the canvas pans, moves with the floater's drop, lands with the
        # unfloat, and shrinks to a collapsed view's title bar
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-208-floor-shown-rect.txt"]}],
    },
    "review": {
        # stories 15, 24, 28, 32, 34, 64, 72, 74: diff, unknown file, snap image, lock
        # gesture, maximise and Esc, hover thumbnails, the frame follows a pan
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-review.txt"], "extra": SCRIPTED_44}],
    },
    "tree-scroll": {
        # #110: opening and closing folders far down a scrolled file tree keeps its place
        "seed": seed("v2-110-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-110-tree-scroll.txt"]}],
    },
    "tree-details": {
        # #115: names only, a long name cut in the middle, the tree footer's details
        # (time by age, size), a file arriving on its own (auto-update on by default)
        "seed": seed("v2-115-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-115-tree-details.txt"]}],
    },
    "tree-ux": {
        # #122: type icons in a mixed tree (and at 150 %), the details toggle in the
        # tree's footer, a Ctrl+double-clicked file's preview following later clicks
        "seed": seed("v2-122-seed.py", "{project}"),
        "sessions": [
            {"flows": ["v2-122-tree-ux.txt"]},
            {"flows": ["v2-122-icons-150.txt"], "env": {"QT_SCALE_FACTOR": "1.5"}},
        ],
    },
    "open-views": {
        # SPEC-32: a double-click opens a file's dedicated view (once; a later pick leaves it,
        # the same file again reveals it), an unsupported file the "Not supported" view,
        # Ctrl+double-click the contextual preview, tagged in its header and its card;
        # the context menu offers both
        "seed": seed("v2-122-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-open-views.txt"]}],
    },
    "open-usd-twice": {
        # SPEC-34: a second stage double-clicked opens its own usd view and collapses the
        # first to make room (one live RTX view), with a notice; expanding the first
        # collapses the second (the stub USD source: no picture judged)
        "seed": seed("v2-open-usd-seed.py", "{project}"),
        "sessions": [
            {
                "flows": ["v2-open-usd-twice.txt"],
                "env": {
                    "SPECTO_USD_STUB_PHASE_MS": "200",
                    "SPECTO_DISABLE_CAPABILITIES": "ovrtx,storm",
                },
            }
        ],
    },
    "fresh-tree": {
        # #125: a new project's file tree fills the visible canvas at any window height
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-125-fresh-tree.txt"]}],
    },
    "header-footers": {
        # #126: the header standard (#158: the eye leads, every control shows at rest;
        # hover, on, selected, floating, collapsed, maximised) and the native views'
        # shared footers
        "seed": seed("v2-126-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-126-header-footers.txt"]}],
    },
    "step": {
        # #107: the image and markdown views step through their collections by keys and
        # their step controls; the image's steps are picks the gallery follows
        "seed": seed("v2-46-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-107-step.txt"]}],
    },
    "channels": {
        # #171: the image view's R G B A toggles: solo, Ctrl+click mix, the A key, the
        # toggle's menu, zoom and pan, one undo step, the agent's params.channels, and
        # the toggles an RGB, a grey+alpha and a grey file offer
        "seed": seed("v2-171-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-171-channels.txt"]}],
    },
    "fix-select": {
        # user test 1 (ADR 0014 amendment): a selection-scoped gallery follows the picks
        # (empty-state hint first), a pinned video never moves
        "seed": seed("v2-42-seed.py", "{project}", "--cam-a", "12", "--cam-b", "8"),
        "sessions": [{"flows": ["v2-fix-select.txt"], "extra": SCRIPTED_SELECT}],
    },
    "keys": {
        # #113: who owns Space and Home: Space held pans over a focused gallery, a tap
        # opens its quick look; Home / End go to the focused image first, then the canvas
        "seed": seed("v2-126-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-113-keys.txt"]}],
    },
    "fix-frames": {
        # user feedback after v2: the #45 canvas; every view wears one chain-coloured
        # frame, a dragged header moves the view, the overworld's cards and link nodes
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-fix-frames.txt"]}],
    },
    "popups": {
        # fix-mech (user test 1): hover popups draw above the neighbouring card and
        # outside the tree's clip; a move or resize ends cleanly
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["fix-mech-popups.txt"]}],
    },
    "fresh-empty": {
        # fix-mech (user test 1): an empty project folder says so and fills while the
        # app watches it (the seed runs mid-flow)
        "seed": lambda project: project.mkdir(parents=True, exist_ok=True),
        "sessions": [{"flows": ["fix-mech-empty.txt"], "fresh": True}],
    },
    "narrow-tree": {
        # #97: a deep tree in a narrow file tree: the details columns line up, deep rows
        # give up indent and cut the name, a hover shows it in full
        "seed": seed("v2-97-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-97-narrow-tree.txt"]}],
    },
    "model-chip": {
        # #73: the prompt bar's model chip and its menu; the choice is kept in the user's
        # settings
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-73-model-chip.txt"], "extra": SCRIPTED_44}],
    },
    "lazy-tree": {
        # #231: a big folder opens one level at a time; nothing under it is walked
        "seed": seed("v2-231-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-231-lazy-tree.txt"]}],
    },
    "search": {
        # #232, SPEC-38: type-to-search in the tree: the tree narrows to the hits inside
        # their folders, a click picks, Escape brings the tree back as it was, reveal
        # opens the way down; the scan backend forced, so the flow is the
        # same on a machine without Everything
        "seed": seed("v2-232-seed.py", "{project}"),
        "sessions": [
            {"flows": ["v2-232-search.txt"], "env": {"SPECTO_SEARCH_BACKEND": "scan"}},
        ],
    },
}
HEAVY = {
    "3d-heavy": {
        # #47 acceptance scene: a 20 M-point cloud and 2 M splats in one 3D view
        "seed": seed("v2-47-seed.py", "{project}", "--points", "20000000", "--splats", "2000000"),
        "sessions": [{"flows": ["v2-47-heavy.txt"]}],
    }
}
NET = {
    "html-net": {
        # #93: the html view loads its page's remote map frames; the
        # agent's network: false takes them away, true brings them back
        "seed": seed("v2-93-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-93-html-network.txt"]}],
    }
}
LIVE = {
    "live": {
        # one short run against the real agent (the user's Claude Code login)
        "seed": seed("v2-39-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-49-live.txt"], "extra": "--agent claude"}],
    },
    "fix-select-live": {
        # the fix-select user test against the real agent: one plain request, the agent
        # must wire a selection-scoped intaker so the gallery follows the picks
        "seed": seed("v2-42-seed.py", "{project}"),
        "sessions": [{"flows": ["v2-fix-select-live.txt"], "extra": "--agent claude"}],
    },
}


# -- tiers (#237) ------------------------------------------------------------------------------
#: every flow file under gui_flows/ and its tier: ``offscreen`` (the default: the Qt
#: offscreen platform with the software scene graph), ``gpu`` (a real window that never
#: takes the focus, for what needs the GPU) or ``mouse`` (pyautogui, for what Qt events
#: cannot reach). A flow outside the offscreen tier carries its reason.
#: ``test_gui_tiers.py`` checks the table covers every flow and that an entry's flows agree.
OFFSCREEN = "offscreen"
GPU = "gpu"
MOUSE = "mouse"
#: why a renderer needs the GPU
_RTX = "renders with RTX (ovrtx) on the GPU; offscreen runs the stub source"
_STORM = "renders with Hydra Storm (OpenGL); offscreen runs the stub source"
_MPV = "mpv video renders through OpenGL; offscreen has no GL context"
_FRAME = (
    "the frame item uploads the source's frames as OpenGL textures "
    "(QSGOpenGLTexture::fromNative): under the software scene graph no frame is ever "
    "drawn, so assert_painted on the viewport cannot be judged there, even with the stub"
)
_WEBGL = (
    "the page needs WebGL (Babylon / MapLibre); Qt WebEngine offscreen with the software "
    "scene graph has none, and SwiftShader blanks every page here"
)
TIERS: dict[str, tuple[str, str]] = {
    # -- GPU: RTX and Storm
    "v2-137-usd-rtx.txt": (GPU, _RTX),
    "v2-140-usd-select.txt": (GPU, _RTX + "; picks read the renderer's id buffer"),
    "v2-141-usd-time.txt": (GPU, _RTX + "; a root video (mpv) scrubs the stage"),
    "v2-151-usd-nav.txt": (GPU, _RTX),
    "v2-153-usd-modes.txt": (GPU, _RTX),
    "v2-42-usd-environment.txt": (GPU, _RTX + "; the sky's MDL compiles there"),
    "v2-143-usd-storm.txt": (GPU, _STORM),
    "v2-153-usd-modes-storm.txt": (GPU, _STORM),
    # -- GPU: the stub USD source's frames reach the frame item as OpenGL textures
    "v2-139-usd-chrome.txt": (GPU, _FRAME),
    "v2-155-usd-crash-load.txt": (GPU, _FRAME),
    "v2-180-usd-aspect.txt": (GPU, _FRAME),
    "v2-39-usd-orbit.txt": (GPU, _FRAME),
    # -- GPU: mpv video
    "v2-42-video-time-links.txt": (GPU, _MPV),
    "v2-128-video-footer.txt": (GPU, _MPV),
    "v2-216-audio.txt": (
        GPU,
        _MPV + "; the audio path is mpv's (muted to ao=null under the driver)",
    ),
    "fix-mech-video.txt": (GPU, _MPV + "; the check is the frame's orientation, a pixel result"),
    # -- GPU: WebGL pages
    "v2-47-3d-scene.txt": (GPU, _WEBGL),
    "v2-47-heavy.txt": (GPU, _WEBGL + "; 20 M points"),
    "v2-129-3d-chrome.txt": (GPU, _WEBGL + "; the panels act on the live scene"),
    "v2-152-3d-nav.txt": (GPU, _WEBGL),
    "v2-153-3d-modes.txt": (GPU, _WEBGL),
    "v2-175-3d-hdri.txt": (GPU, _WEBGL),
    "v2-66-spz-v4.txt": (GPU, _WEBGL + "; the user's own splat capture, run by hand"),
    "v2-85-splat-first-paint.txt": (GPU, _WEBGL + "; the user's own splat, run by hand"),
    "v2-49-views-map.txt": (GPU, _WEBGL + "; the point pick is on the map's body"),
    "v2-157-map-layers.txt": (GPU, _WEBGL + "; the basemap and the data layers are the map's body"),
    "v2-181-map-batches.txt": (GPU, _WEBGL + "; the batches and frame-all are the map's body"),
    # -- GPU: pixel asserts that only mean something on the real scene graph
    "v2-95-overworld-resume.txt": (
        GPU,
        "assert_painted on every view type: the gallery's custom material, mpv and WebGL "
        "draw nothing under the software scene graph, so the asserts would fail for the "
        "renderer, not the shell",
    ),
    "v2-106-preview.txt": (
        GPU,
        "assert_painted on a video and a point cloud (mpv, WebGL) among the previews",
    ),
    "v2-96-aa.txt": (
        GPU,
        "assert_smooth checks the GPU curve renderer's antialiasing (Shape.CurveRenderer); "
        "the software scene graph draws the curves another way",
    ),
    "v2-96-drag.txt": (GPU, "frame cost of the real renderer (QSG_RENDER_TIMING)"),
    # -- real mouse: what Qt events cannot reach
    "v2-185-drag-out.txt": (
        MOUSE,
        "native drag and drop into another application's window (the drop probe): the "
        "offscreen platform has no drag loop and QDrag.exec returns at once",
    ),
}
#: flows the offscreen tier covers but the suite does not run by itself
BY_HAND = {
    "fix-html-71.txt": "the user's own html reports (no seed); the html view renders offscreen",
    "v2-66-spz-v4.txt": "the user's own splat capture (no seed)",
    "v2-85-splat-first-paint.txt": "the user's own splat capture (no seed)",
}


def tier_of(flow: str) -> str:
    return TIERS.get(flow, (OFFSCREEN, ""))[0]


def reason_of(flow: str) -> str:
    return TIERS.get(flow, (OFFSCREEN, ""))[1]


def entry_tier(entry: dict) -> str:
    """The one tier of an entry's flows (they must agree; a welcome-only session has no
    flow and follows the driver: offscreen)."""
    tiers = {tier_of(f) for s in entry["sessions"] for f in s["flows"]}
    if len(tiers) > 1:
        raise ValueError(f"an entry's flows span tiers {sorted(tiers)}")
    return tiers.pop() if tiers else OFFSCREEN


def all_flows() -> list[str]:
    return sorted(p.name for p in HERE.glob("*.txt"))


def all_entries() -> dict[str, dict]:
    return {**SUITE, **HEAVY, **NET, **LIVE}


def max_jobs() -> int:
    """Parallel offscreen entries: at most 4, and the machine's cores minus 8 (#237)."""
    cores = os.cpu_count() or 1
    return max(1, min(4, cores - 8))


# -- running -----------------------------------------------------------------------------------
def session_cmd(
    tier: str, project: Path, out: Path, work: Path, name: str, i: int, sess: dict
) -> list[str]:
    """The command of one session: the Qt driver's runner for the offscreen and GPU tiers
    (``--platform window`` for the GPU), ``gui_session.py`` with this interpreter (the
    pyautogui venv's) for the mouse tier."""
    if tier == MOUSE:
        cmd = [sys.executable, str(SESSION)]
    else:
        cmd = [
            "uv",
            "run",
            "--no-sync",
            "python",
            str(QTDRIVE),
            "--platform",
            "window" if tier == GPU else "offscreen",
        ]
        cmd += ["--report", str(work / f"v49-{name}-{i}.report.json")]
    cmd += [
        "--project",
        str(project),
        "--out",
        str(out),
        "--app-log",
        str(work / f"v49-{name}-{i}.app.log"),
    ]
    if sess.get("extra"):
        cmd += ["--extra", sess["extra"]]
    if sess.get("pool"):
        cmd.append("--pool")
    if sess.get("fresh"):
        cmd.append("--fresh")
    if sess.get("welcome"):
        cmd.append("--welcome")
        cmd += ["--shot-prefix", sess.get("prefix", "49")]
    if sess.get("recent"):
        cmd.append("--welcome-recent")
    if sess.get("only"):
        cmd.append("--welcome-only")
    cmd += [str(HERE / f) for f in sess["flows"]]
    return cmd


def run_entry(name: str, entry: dict, work: Path, out: Path) -> list[dict]:
    """Seed and run one entry; one result per flow (``flow``, ``ok``, ``seconds``,
    ``error``), the seed's failure as a result of its own."""
    tier = entry_tier(entry)
    project = work / f"v49-{name}"
    t0 = time.monotonic()
    # every run starts from nothing: some seeds fill an existing project in place (their
    # try-it use), and a canvas, picks or settings left by the last run would show
    shutil.rmtree(project, ignore_errors=True)
    s = entry["seed"]
    if callable(s):
        s(project)
    else:
        cmd = [a.replace("{project}", str(project)) for a in s]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            return [{"entry": name, "flow": "seed", "ok": False, "seconds": time.monotonic() - t0,
                     "error": f"seed failed: {r.stdout}{r.stderr}"}]  # fmt: skip
    results: list[dict] = []
    for i, sess in enumerate(entry["sessions"]):
        cmd = session_cmd(tier, project, out, work, name, i, sess)
        log = work / f"v49-{name}-{i}.flow.log"
        report = work / f"v49-{name}-{i}.report.json"
        report.unlink(missing_ok=True)
        t1 = time.monotonic()
        with open(log, "w", encoding="utf-8") as fh:
            env = {**os.environ, **sess.get("env", {})}
            r = subprocess.run(
                cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, text=True, env=env
            )
        if report.exists():
            for res in json.loads(report.read_text(encoding="utf-8")):
                results.append({"entry": name, **res})
        else:
            # the mouse tier (gui_session) reports per session: its flows pass or fail together
            tail = "".join(log.read_text(encoding="utf-8", errors="replace").splitlines(True)[-12:])
            why = "" if r.returncode == 0 else f"session {i} exit {r.returncode}\n{tail}"
            for f in sess["flows"] or ["welcome"]:
                ok = r.returncode == 0
                dt = time.monotonic() - t1
                results.append({"entry": name, "flow": f, "ok": ok, "seconds": dt, "error": why})
        if r.returncode != 0:
            break  # a later session of the entry builds on this one
    return results


def report_lines(tier: str, results: list[dict]) -> list[str]:
    lines = [f"v2 seam-3 suite, {tier} tier  {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for res in results:
        mark = "OK" if res["ok"] else "ERROR"
        why = (res.get("error") or "").splitlines()
        lines.append(
            f"[{mark}] {res['entry']:16s} {res['flow']:34s} {res['seconds']:6.0f} s"
            + (f"  {why[0]}" if why else "")
        )
    failed = [r for r in results if not r["ok"]]
    lines += ["", f"{len(results) - len(failed)} flows passed, {len(failed)} failed"]
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--work", required=True, type=Path, help="scratch folder for the projects")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "verify" / "v2")  # git-ignored
    ap.add_argument("--tier", choices=TIER_NAMES, default=OFFSCREEN, help="which tier to run")
    ap.add_argument("--jobs", type=int, default=None, help="parallel entries (offscreen only)")
    ap.add_argument("--only", nargs="*", default=None, help="entry names to run")
    ap.add_argument("--heavy", action="store_true", help="add the 20 M-point 3D flow")
    ap.add_argument("--live", action="store_true", help="add the real-agent run")
    ap.add_argument("--net", action="store_true", help="add the html network flow (#93)")
    ap.add_argument("--list", action="store_true", help="list the tier's entries and stop")
    ns = ap.parse_args()
    # a flow log's tail can hold what a cp1252 console cannot print (a status line's dot)
    utf8_stdio()
    entries = dict(SUITE)
    if ns.heavy:
        entries.update(HEAVY)
    if ns.live:
        entries.update(LIVE)
    if ns.net:
        entries.update(NET)
    if ns.only:
        entries = {k: entries[k] for k in ns.only}
    entries = {k: e for k, e in entries.items() if entry_tier(e) == ns.tier}
    if ns.list:
        for k, e in entries.items():
            flows = ", ".join(f for s in e["sessions"] for f in s["flows"]) or "(welcome only)"
            print(f"{k:16s} {flows}")
        print(f"{len(entries)} entries in the {ns.tier} tier")
        return 0
    if ns.tier == MOUSE and "pyautogui" not in sys.modules:
        try:
            import pyautogui  # noqa: F401
        except ImportError:
            print(
                "[ERROR] the mouse tier runs with the pyautogui venv's interpreter (gui_drive.py)"
            )
            return 2
    ns.work.mkdir(parents=True, exist_ok=True)
    ns.out.mkdir(parents=True, exist_ok=True)
    jobs = 1 if ns.tier != OFFSCREEN else max(1, min(ns.jobs or max_jobs(), max_jobs()))
    results: list[dict] = []
    t0 = time.monotonic()

    def run(item: tuple[str, dict]) -> list[dict]:
        name, entry = item
        print(f"[..] {name}", flush=True)
        res = run_entry(name, entry, ns.work, ns.out)
        for r in res:
            print(
                f"[{'OK' if r['ok'] else 'ERROR'}] {name} {r['flow']} ({r['seconds']:.0f} s)"
                + (f"\n{r['error']}" if r.get("error") else ""),
                flush=True,
            )
        return res

    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for res in pool.map(run, entries.items()):
                results += res
    else:
        for item in entries.items():
            results += run(item)
    lines = report_lines(ns.tier, results)
    lines.append(f"wall time {time.monotonic() - t0:.0f} s, {jobs} parallel")
    text = "\n".join(lines) + "\n"
    print(text)
    if not ns.only:
        (ns.out / f"suite-{ns.tier}.txt").write_text(text, encoding="utf-8")
    return 1 if any(not r["ok"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
