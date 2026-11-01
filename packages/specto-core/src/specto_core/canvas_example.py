"""The worked example canvas document (from the #29 prototype, in v2 shape): the run-001
review. ``describe canvas`` returns it next to the schema, and tests build on it. Its
intakers show both scopes: pinned off the project source to run run-001 (galleries,
video, inspector) and a selection-scoped image view off the file tree's picks. The file
tree sits right of the project node, which the app draws at the canvas origin."""

from __future__ import annotations

import copy
from typing import Any

_FRONT_KEYS = r"(?P<frame>\d{5})_\d+\.jpg$"

_EXAMPLE: dict[str, Any] = {
    "schema_version": 2,
    "project": {"label": "run-001 review"},
    "window_frame": {"rect": {"x": 0, "y": 0, "w": 2120, "h": 820}},
    "root_view": "rena",
    "views": {
        "fege": {
            "type": "file_tree",
            "label": "Files",
            "layout": {"position": [80, 0], "size": [280, 800]},
        },
        "kobi": {
            "type": "gallery",
            "label": "Cam A",
            "layout": {"position": [380, 0], "size": [640, 400]},
            "params": {"columns": 6},
        },
        "tamu": {
            "type": "gallery",
            "label": "Cam B",
            "layout": {"position": [380, 420], "size": [640, 380]},
            "params": {"columns": 6},
        },
        "rena": {
            "type": "video",
            "label": "Cam A video",
            "layout": {"position": [1040, 0], "size": [640, 400]},
        },
        "pola": {
            "type": "map",
            "label": "Trajectory",
            "layout": {"position": [1040, 420], "size": [640, 380]},
            "params": {"base_layer": "osm"},
        },
        "mihu": {
            "type": "json",
            "label": "Frame metadata",
            "layout": {"position": [1700, 420], "size": [400, 380]},
        },
        "vesa": {
            "type": "image",
            "label": "Picked image",
            "layout": {"position": [1700, 0], "size": [400, 400]},
        },
    },
    "links": {
        "l1": {
            "kind": "intaker",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "fege", "port": "data"},
            "params": {"sources": ["."], "watch": True},
            "summary": {"sentence": "The file tree shows every file in the project folder."},
        },
        "l2": {
            "kind": "intaker",
            "scope": "pinned",
            "label": "cam-a frames",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "kobi", "port": "data"},
            "params": {
                "sources": ["runs/run-001/cam-a"],
                "screener": ["@images"],
                "keys": _FRONT_KEYS,
                "group_by": ["frame"],
                "sort": "natural",
            },
            "summary": {
                "sentence": "The cam-a gallery shows the cam-a images of run run-001, "
                "one item per frame."
            },
        },
        "l3": {
            "kind": "intaker",
            "scope": "pinned",
            "label": "cam-b frames",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "tamu", "port": "data"},
            "params": {
                "sources": ["runs/run-001/cam-b"],
                "screener": ["@images"],
                "keys": _FRONT_KEYS,
                "group_by": ["frame"],
                "sort": "natural",
            },
            "summary": {
                "sentence": "The cam-b gallery shows the cam-b images of run run-001, "
                "one item per frame."
            },
        },
        "l4": {
            "kind": "intaker",
            "scope": "pinned",
            "label": "cam-a video",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "rena", "port": "data"},
            "params": {"sources": ["runs/run-001/video/cam-a.mp4"], "timeline": "video"},
            "summary": {"sentence": "The video player plays the cam-a video of run-001."},
        },
        "l5": {
            "kind": "read_contents",
            "label": "path geojson",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "pola", "port": "features"},
            "params": {"sources": ["geo/run-001-path.geojson"], "format": "geojson"},
            "summary": {"sentence": "The map draws the run's path read from the GeoJSON file."},
        },
        "l6": {
            "kind": "intaker",
            "scope": "pinned",
            "label": "front metadata",
            "source": {"view": "project", "port": "all_files"},
            "target": {"view": "mihu", "port": "data"},
            "params": {
                "sources": ["runs/run-001/cam-a"],
                "screener": ["@json"],
                "keys": r"(?P<frame>\d{5})_\d+\.json$",
                "group_by": ["frame"],
                "sort": "natural",
            },
            "summary": {"sentence": "The inspector holds the per-frame metadata files of cam-a."},
        },
        "l7": {
            "kind": "follow",
            "two_way": True,
            "source": {"view": "kobi", "port": "selection"},
            "target": {"view": "tamu", "port": "selection"},
            "params": {"match": "frame"},
            "summary": {
                "sentence": "Selecting a frame in one gallery selects the same frame in the other."
            },
        },
        "l8": {
            "kind": "follow",
            "source": {"view": "kobi", "port": "selection"},
            "target": {"view": "mihu", "port": "selection"},
            "params": {"match": "frame"},
            "summary": {
                "sentence": "The inspector shows the metadata of the frame selected in the "
                "cam-a gallery."
            },
        },
        "l9": {
            "kind": "python",
            "label": "image to video moment",
            "source": {"view": "kobi", "port": "selection"},
            "target": {"view": "rena", "port": "time"},
            "python": {
                "path": ".specto/links/image_to_video.py",
                "entry": "resolve",
                "resolves_to": "table",
                "output": "time",
            },
            "params": {"timeline": "runs/run-001/video/cam-a.timeline.json"},
            "summary": {
                "sentence": "Selecting a cam-a image seeks the video to the moment it was taken.",
                "diagram": "cam-a gallery.selection --[frame -> video pts]--> video.time",
            },
        },
        "l10": {
            "kind": "time_map",
            "source": {"view": "rena", "port": "time"},
            "target": {"view": "kobi", "port": "time"},
            "params": {"from_timeline": "video", "to_timeline": "stamp", "offset_ms": 0},
            "summary": {
                "sentence": "The cam-a gallery highlights the frame nearest the video's "
                "current time."
            },
        },
        "l11": {
            "kind": "time_map",
            "source": {"view": "rena", "port": "time"},
            "target": {"view": "tamu", "port": "time"},
            "params": {"from_timeline": "video", "to_timeline": "stamp", "offset_ms": 0},
            "summary": {
                "sentence": "The cam-b gallery highlights the frame nearest the video's "
                "current time."
            },
        },
        "l12": {
            "kind": "time_map",
            "source": {"view": "rena", "port": "time"},
            "target": {"view": "pola", "port": "time"},
            "params": {"from_timeline": "video", "to_timeline": "stamp", "offset_ms": 0},
            "summary": {
                "sentence": "The map marks where the robot was at the video's current time."
            },
        },
        "l13": {
            "kind": "intaker",
            "scope": "selection",
            "label": "picked images",
            "source": {"view": "fege", "port": "selected_files"},
            "target": {"view": "vesa", "port": "data"},
            "params": {"screener": ["@images"]},
            "summary": {
                "sentence": "The image view shows whichever image the user picks in the "
                "file tree (any folder)."
            },
        },
    },
}


def worked_example() -> dict[str, Any]:
    """A fresh copy of the example document (callers may edit it)."""
    return copy.deepcopy(_EXAMPLE)
