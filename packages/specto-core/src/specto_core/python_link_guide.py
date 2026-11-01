"""What ``describe link:python`` tells the agent about writing link code (#168): the
per-pick mode (``resolves_to: value``), the ``value`` / ``items`` / ``params`` /
``root`` it is called with per source port type, what each output port type takes back,
how the code gets to run (write it, the user approves it), and one worked example: the
recording picked in one file tree selects its annotation folder in a second tree,
whose GeoJSONs a map shows. The text is the per-pick contract; the fix-it shapes are the
runtime's own (``python_links.SHAPES``).

Data only (JSON-ready); ``canvas_document.describe_link_kind`` adds it as ``guide``.
"""

from __future__ import annotations

from typing import Any

from .python_links import SHAPES

#: what ``value`` is per source port type (``python_links.value_json``)
VALUE_SHAPES: dict[str, str] = {
    "files": '{"root": "<abs folder>", "paths": ["rel/posix", ...] | null}. null means the '
    "whole folder (project.all_files). A file tree's picks expand folders to files.",
    "selection": '{"keys": [...], "last": "<key>" | null}',
    "time": "a number (seconds)",
    "content": '{"root", "entries": [{key, path, format, data, meta, error}]}',
    "collection": '{"root", "items": [{key, parts, meta}]}',
    "camera": "{position, target, up, fov_y, near, far}",
}

#: what the returned value may be per output port type (the same for all three modes)
RETURNS: dict[str, str] = {
    "files": '{"root"?, "paths": [...]} or ["path", ...] becomes a FilesValue. root '
    "defaults to the project root; a relative root joins it. (No view has a files input "
    "today: nothing takes a files output yet.)",
    "selection": '"key" | ["k1", "k2"] | {"keys": [...], "last"?} becomes a Selection. last '
    "defaults to the last key.",
    "content": '{"root"?, "paths": [...]} or ["path", ...] reads those files into a '
    "ContentValue. A folder expands to its readable files (for a map, its GeoJSON files "
    "only); geojson/json/text are told apart by extension. Alternatively "
    '{"root"?, "entries": [...]} is taken as is. A map target needs geojson entries, '
    "else a fix-it.",
    "time": 'a number or {"t", "timeline"?} becomes a TimeValue. With no timeline it is '
    "the target's own (a video's, a usd stage's, a gallery's clock).",
    "camera": "{position, target, ...} becomes a CameraValue (the 3d view's frame, Y up; "
    "a usd view takes it too).",
    "collection": "a view's whole data, built as an intaker builds it: "
    '{"root"?, '
    '"items": [...]} or a list of items. An item is a path, or {"key"?, "parts": '
    '{"<role>": "<path>"} or ["<path>", ...], "keys"?: {field: value}, "meta"?: {...}}. '
    "Paths are relative to root (the project folder) or absolute, and must exist; a "
    "part listed without a role gets its extension (.jpg) as the role. The key defaults "
    "to the first part's path without extension, keys to {stem: key}; keys must be "
    "unique. Each part is recorded with its relative path and stat, so a file tree "
    "lists it, a 3d view gets its scene, and the same result is not delivered twice. "
    "Feeds any view's data input (gallery, file tree, markdown, 3d, usd, ...).",
    "item": "JSON as is.",
    "null": "nothing is delivered (the target keeps what it has). [] clears a selection.",
}

#: the shapes a wrong return's fix-it lists, per output port type (the runtime's)
OUTPUT_SHAPES: dict[str, str] = SHAPES

EXAMPLE_NAME = "clip_to_geo"
COLLECTION_EXAMPLE_NAME = "frames"

#: a script building a gallery's whole collection from the picks: one item per frame
#: number, the picture and its JSON sidecar as its two parts
COLLECTION_EXAMPLE_CODE = r"""import re


def resolve(value, items, params, root):
    # runs/<cam>/00042_<stamp>.jpg + .json -> one item "00042" with both parts
    out = {}
    for path in value["paths"] or []:
        m = re.search(r"(\d{5})_\d+\.(jpg|json)$", path)
        if m:
            frame = m.group(1)
            item = out.setdefault(frame, {"key": frame, "parts": [], "keys": {"frame": frame}})
            item["parts"].append(path)
    return {"items": [out[k] for k in sorted(out)]}
"""

#: the worked example's code: the picked recording -> its annotation folder's geo
EXAMPLE_CODE = """\
def resolve(value, items, params, root):
    # recordings/<session>/<clip>/... -> annotations/<session>/<clip>/geo
    out = []
    for path in value["paths"] or []:
        parts = path.split("/")
        if parts[:1] == ["recordings"] and len(parts) >= 4:
            out.append("/".join(["annotations", *parts[1:3], "geo"]))
    return sorted(set(out))
"""


def example_edits(tree: str = "fege") -> dict[str, Any]:
    """submit_canvas edits for the worked example on a canvas whose file tree (showing
    the recordings) is ``tree``: tree B on annotations, the per-pick link from tree A's
    picks to tree B's selection input, and a map reading tree B's picks."""
    return {
        "views": {
            "new1": {
                "type": "file_tree",
                "label": "annotations",
                "layout": {"position": [330, 0], "size": [320, 600]},
            },
            "new2": {
                "type": "map",
                "label": "geo data",
                "layout": {"position": [670, 0], "size": [640, 600]},
            },
        },
        "links": {
            "new3": {
                "kind": "intaker",
                "scope": "pinned",
                "source": {"view": "project", "port": "all_files"},
                "target": {"view": "new1", "port": "data"},
                "params": {"sources": ["annotations"]},
                "summary": {"sentence": "Tree B always shows the source folder."},
            },
            "new4": {
                "kind": "python",
                "source": {"view": tree, "port": "selected_files"},
                "target": {"view": "new1", "port": "selection"},
                "python": {
                    "path": f".specto/links/{EXAMPLE_NAME}.py",
                    "entry": "resolve",
                    "resolves_to": "value",
                    "output": "selection",
                },
                "summary": {
                    "sentence": "Tree B selects the source folder mirroring the world you pick."
                },
            },
            "new5": {
                "kind": "read_contents",
                "source": {"view": "new1", "port": "selected_files"},
                "target": {"view": "new2", "port": "features"},
                "params": {"sources": ["."]},
                "summary": {"sentence": "The map shows the GeoJSONs of tree B's picks."},
            },
        },
    }


def python_link_guide() -> dict[str, Any]:
    return {
        "per_pick": 'resolves_to: "value" means per pick: the source\'s current value is '
        "part of the resolve key, and the script returns the output value itself. "
        "Signature: entry(value, items, params, root). It runs out of process for each "
        "new value of the source (debounced: a burst of picks runs only the last). With "
        "no source value yet, the script does not run (the link waits). This is the link "
        "for anything an intaker, follow or read_contents link cannot express: a mapping "
        "between folders, a lookup across files, a join.",
        "table_and_samples": "resolves_to: table | samples keep entry(items, params, root): "
        "{source key: output value} looked up by the picked key, or [[number, output "
        "value], ...] picked by the nearest number.",
        "python": {
            "path": f".specto/links/{EXAMPLE_NAME}.py",
            "entry": "resolve",
            "resolves_to": "value",
            "output": "selection",
        },
        "value": VALUE_SHAPES,
        "items": "the source view's collection items (empty for a file tree)",
        "params": "the link params",
        "root": "the project folder",
        "returns": RETURNS,
        "status": "canvas.state's links[<id>] is {state, message} (waiting, resolving, "
        "resolved, failed, needs_approval; path and hash for a python link). A failed "
        "run's message is one line (<path>:<entry>: <error>, or the fix-it for a wrong "
        "shape); trace is the script's own traceback (its frames only, paths relative to "
        "the project): read it to fix the code. A failure stays until the code, the "
        "params or the source value change; a run longer than 120 s fails and is killed. "
        "The code runs as python -I -X utf8 (files are UTF-8; print goes to a log, not "
        "the result) and sees only the app's installed packages.",
        "fix_it": 'a wrong shape fails the link with "<path>:<entry> returned <value> for '
        'a <type> output; return <shapes>", the shapes being these per type',
        "shapes": OUTPUT_SHAPES,
        "map": "a map's features input takes content and shows GeoJSON files, not inline "
        "data: feed it tree B's selected_files through read_contents, or return the "
        '.geojson paths or their folders ({"paths": [...]}) from a python link with '
        'output content. A map fed non-GeoJSON says "<path>:<entry>: the map shows '
        'GeoJSON files, but <file> is <format>; return the .geojson paths or their folders"',
        "trees": "a file tree's data input is a collection (an intaker), not files; its "
        "selection input (keys: relative paths of files or folders) selects and reveals "
        "them and sends their files on selected_files. No follow link works between two "
        "trees: drive a second tree with a per-pick python link (output selection) into "
        "its selection input.",
        "code": "write the file with write_link_code {name, code} as "
        ".specto/links/<name>.py, then submit the python link on it in one submit; the "
        "code runs only after the user approves its exact content (the view's Approve "
        "button); any change to the file needs approval again. read_link_code shows the "
        "code and whether it is approved. Plain Python 3 and its standard library, JSON "
        "in and out; it must return quickly (it runs per pick).",
        "example": {
            "prompt": "picking a world in the file tree selects its source folder in a "
            "second tree, and a map shows that folder's GeoJSONs",
            "chain": 'tree A.selected_files -> python "value" (output selection) -> tree '
            "B.selection (recordings/<session>/<clip>/... -> "
            "annotations/<session>/<clip>/geo). Then tree B.selected_files "
            '-> read_contents (or python "value", output content) -> map.features.',
            "write_link_code": {"name": EXAMPLE_NAME, "code": EXAMPLE_CODE},
            "submit_canvas": {
                "edits": example_edits(),
                "label": "annotation tree and map follow the picked recording",
            },
        },
        "collection_example": {
            "prompt": "a gallery of the frames I pick in the file tree, each frame's "
            "picture and its JSON sidecar as one item",
            "write_link_code": {"name": COLLECTION_EXAMPLE_NAME, "code": COLLECTION_EXAMPLE_CODE},
            "link": {
                "kind": "python",
                "source": {"view": "<the file tree>", "port": "selected_files"},
                "target": {"view": "<a gallery>", "port": "data"},
                "python": {
                    "path": f".specto/links/{COLLECTION_EXAMPLE_NAME}.py",
                    "entry": "resolve",
                    "resolves_to": "value",
                    "output": "collection",
                },
                "summary": {"sentence": "The gallery shows the frames you pick, one per number."},
            },
        },
    }


__all__ = [
    "COLLECTION_EXAMPLE_CODE",
    "COLLECTION_EXAMPLE_NAME",
    "EXAMPLE_CODE",
    "EXAMPLE_NAME",
    "OUTPUT_SHAPES",
    "RETURNS",
    "VALUE_SHAPES",
    "example_edits",
    "python_link_guide",
]
