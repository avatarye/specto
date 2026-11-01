"""Shared helpers for the web-view tests (headless, no browser): a project with frames,
notes and GeoJSON, compositions submitted to ``engine.live``, a headless host registry,
and a sink that collects a bridge's outgoing signals as parsed JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import specto_intake  # noqa: F401  (registers the intaker and read_contents resolvers)
from specto_app.hosts.base import HostContext
from specto_app.hosts.registry import HostRegistry, clear_pending
from specto_app.views import install
from specto_app.web.runtime import reset_runtime
from specto_core import CallContext, Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.links import FilesValue

AGENT = CallContext(connection="mcp:1", origin="agent:s1")
USER = CallContext(connection="gui", origin="user")
FRAME_KEYS = r"(?P<frame>\d{5})_\d+\.(jpg|json)$"

POINTS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "depot", "zip": "98008"},
            "geometry": {"type": "Point", "coordinates": [-122.12, 47.64]},
        },
        {
            "type": "Feature",
            "properties": {"name": "stop 1"},
            "geometry": {"type": "Point", "coordinates": [-122.10, 47.62]},
        },
    ],
}
ROUTE = {
    "type": "Feature",
    "properties": {"name": "route"},
    "geometry": {"type": "LineString", "coordinates": [[-122.12, 47.64], [-122.10, 47.62]]},
}


def make_run(root: Path, n: int = 5, *, with_json: bool = True) -> Path:
    """A fake ``runs/<cam>`` directory: ``NNNNN_<stamp>.jpg`` (+ ``.json``) per frame."""
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        stamp = 1700000000000000000 + i * 60_000_000
        (root / f"{i:05d}_{stamp}.jpg").write_bytes(b"\xff\xd8\xff" + bytes(16) + b"\xff\xd9")
        if with_json:
            (root / f"{i:05d}_{stamp}.json").write_text(
                json.dumps(
                    {"frame": i, "camera": root.name, "stamp_ns": stamp, "pose": {"t": [i, 0, 1]}}
                ),
                encoding="utf-8",
            )
    return root


def make_notes(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "img").mkdir(exist_ok=True)
    (root / "img" / "a.png").write_bytes(b"\x89PNG\r\n")
    (root / "alpha.md").write_text(
        "# Alpha\n\nSee [[beta]] and ![](img/a.png)\n\n## Part\n\n```\n# not a heading\n```\n",
        encoding="utf-8",
    )
    (root / "beta.md").write_text("# Beta\n\nBack to [[alpha|the first]]\n", encoding="utf-8")
    return {"alpha": root / "alpha.md", "beta": root / "beta.md"}


def make_geo(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, data in (("points", POINTS), ("route", ROUTE)):
        p = root / f"{name}.geojson"
        p.write_text(json.dumps(data), encoding="utf-8")
        out[name] = p
    (root / "readme.md").write_text("# geo\n", encoding="utf-8")
    return out


def new_engine(tmp_path: Path, *, frames: int = 5) -> tuple[Engine, Project]:
    """An engine on a fresh project (``runs/cam-a`` frames with sidecars, ``notes/``,
    ``geo/``) with inline jobs, its canvas open (the file tree is fed), and a fresh web
    runtime."""
    proj = Project.init(tmp_path / "proj", "demo")
    make_run(proj.root / "runs" / "cam-a", frames)
    make_notes(proj.root / "notes")
    make_geo(proj.root / "geo")
    reset_runtime()
    engine = Engine(project=proj, jobs=InlineJobManager())
    engine.links  # noqa: B018  (the project intaker resolves the file tree's collection)
    return engine, proj


def tree_emits_all(engine: Engine) -> FilesValue:
    """The user picks everything: what the file tree host emits on ``selected_files``
    for every file of its collection, relative to the project. (All files as such come
    from the project source, #83, and resolve at submit; the selection-scoped views the
    web tests compose wait for this.)"""
    from specto_intake.listing import TreeLister

    col = engine.links.input_of("fege", "data")
    paths, _ = TreeLister(col.root).files_under([""])  # a pick of the root (#231)
    value = FilesValue(root=col.root, paths=paths)
    engine.links.emit("fege", "selected_files", value)
    return value


def intaker(source_port: str, target: str, sources: list[str], screener: list[str]) -> dict:
    return {
        "kind": "intaker",
        "scope": "selection" if source_port == "selected_files" else "pinned",
        "source": {
            "view": "fege" if source_port == "selected_files" else "project",
            "port": source_port,
        },
        "target": {"view": target, "port": "data"},
        "params": {"sources": sources, "screener": screener, "keys": FRAME_KEYS},
        "summary": {"sentence": f"{target} shows the {screener[0]} files under {sources[0]}."},
    }


def follow(src: str, src_port: str, dst: str, dst_port: str, *, two_way: bool = False) -> dict:
    return {
        "kind": "follow",
        "source": {"view": src, "port": src_port},
        "target": {"view": dst, "port": dst_port},
        "two_way": two_way,
        "summary": {"sentence": f"{dst} follows the {src_port} of {src}."},
    }


def compose_json(engine: Engine, *, label: str = "sidecar") -> dict[str, str]:
    """A gallery ``kobi`` and a json view ``tamu`` on the cam-a frames; the json view follows
    the gallery's selection. Returns the ids."""
    doc = engine.live.document()
    doc["views"]["kobi"] = {"type": "gallery", "label": "Cam A", "layout": {"size": [600, 400]}}
    doc["views"]["tamu"] = {
        "type": "json",
        "label": label,
        "layout": {"position": [620, 0], "size": [500, 400]},
    }
    doc["links"]["l2"] = intaker("selected_files", "kobi", ["runs/cam-a"], ["@images"])
    doc["links"]["l3"] = intaker("selected_files", "tamu", ["runs/cam-a"], ["@json"])
    doc["links"]["l4"] = follow("kobi", "selection", "tamu", "selection")
    engine.live.submit(doc, origin="agent:s1", label="json beside the gallery")
    return {"gallery": "kobi", "json": "tamu", "data": "l3", "selection": "l4"}


def registry(engine: Engine) -> HostRegistry:
    """A headless host registry (no QML engine) with the built-in view factories."""
    clear_pending()
    install()
    return HostRegistry(HostContext(engine, None), load_entry_points=False)


class Sink:
    """Collects a bridge's outgoing signals as parsed JSON."""

    def __init__(self, bridge: Any) -> None:
        self.configured: list[dict] = []
        self.ports: list[dict] = []
        self.params: list[dict] = []
        self.notifies: list[tuple[str, Any]] = []
        self.results: list[tuple[str, dict]] = []
        bridge.configured.connect(lambda j: self.configured.append(json.loads(j)))
        bridge.port.connect(lambda j: self.ports.append(json.loads(j)))
        bridge.params.connect(lambda j: self.params.append(json.loads(j)))
        bridge.notify.connect(lambda k, j: self.notifies.append((k, json.loads(j))))
        bridge.callResult.connect(lambda i, j: self.results.append((i, json.loads(j))))

    def last(self, kind: str) -> Any:
        for k, v in reversed(self.notifies):
            if k == kind:
                return v
        return None

    def last_port(self, port: str) -> Any:
        for p in reversed(self.ports):
            if p["port"] == port:
                return p["value"]
        return None


def make_deck(root: Path) -> dict[str, Path]:
    """A small HTML deck with the relative assets a real one has (a stylesheet, a
    script, an image in a subfolder) and a second page."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "img").mkdir(exist_ok=True)
    (root / "img" / "a.png").write_bytes(b"\x89PNG\r\n")
    (root / "style.css").write_text("body { color: #123; }\n", encoding="utf-8")
    (root / "deck.js").write_text("document.title = 'deck';\n", encoding="utf-8")
    (root / "deck.html").write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="style.css">'
        '<script src="deck.js"></script></head><body><h1>Deck</h1>'
        '<img src="img/a.png"></body></html>\n',
        encoding="utf-8",
    )
    (root / "notes.html").write_text("<!doctype html><p>notes</p>\n", encoding="utf-8")
    return {"deck": root / "deck.html", "notes": root / "notes.html"}
