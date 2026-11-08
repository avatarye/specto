"""The pure half of the ``specto://`` scheme handler: range parsing, asset resolution,
item paths, page hosts. No Qt."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import pytest

from specto_app.web.capabilities import TILE_HOSTS, Capabilities
from specto_app.web.items import ItemTable
from specto_app.web.scheme import (
    ByteRange,
    content_type,
    item_url,
    navigation_allowed,
    net_decision,
    page_id_of_host,
    page_url,
    parse_item_path,
    parse_item_rel_path,
    parse_query,
    parse_range,
    read_source,
    record,
    resolve_asset,
    resolve_relative,
    slice_file,
    source_size,
)


@pytest.mark.parametrize(
    ("header", "total", "want"),
    [
        (None, 100, None),
        (b"", 100, None),
        (b"bytes=0-9", 100, (0, 9)),
        (b"bytes=90-", 100, (90, 99)),
        (b"bytes=90-500", 100, (90, 99)),
        (b"bytes=-10", 100, (90, 99)),
        (b"bytes=-500", 100, (0, 99)),
        (b"bytes=100-", 100, None),  # start past the end
        (b"bytes=5-2", 100, None),  # inverted
        (b"bytes=-", 100, None),
        (b"bytes=0-9", 0, None),
        (b"chunks=0-9", 100, None),
        ("bytes=1-1", 100, (1, 1)),
        (b"bytes=-0", 100, None),
    ],
)
def test_parse_range(header, total, want):
    r = parse_range(header, total)
    if want is None:
        assert r is None
    else:
        assert (r.start, r.end) == want and r.total == total
        assert r.length == want[1] - want[0] + 1
        assert r.content_range() == b"bytes %d-%d/%d" % (want[0], want[1], total)


def test_slice_file(tmp_path: Path):
    p = tmp_path / "blob.bin"
    p.write_bytes(bytes(range(256)))
    assert slice_file(p, ByteRange(10, 19, 256)) == bytes(range(10, 20))
    assert slice_file(p, parse_range(b"bytes=-4", 256)) == bytes([252, 253, 254, 255])


def test_resolve_asset_stays_under_root(tmp_path: Path):
    root = tmp_path / "web"
    (root / "json").mkdir(parents=True)
    (root / "bridge.js").write_text("//", encoding="utf-8")
    (root / "json" / "index.html").write_text("<html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    assert resolve_asset(root, "/bridge.js") == (root / "bridge.js").resolve()
    assert resolve_asset(root, "/json/") == (root / "json" / "index.html").resolve()
    assert resolve_asset(root, "/json") == (root / "json" / "index.html").resolve()
    assert resolve_asset(root, "/missing.js") is None
    assert resolve_asset(root, "/../secret.txt") is None
    assert resolve_asset(root, "/json/../../secret.txt") is None
    assert resolve_asset(root, "") is None  # root has no index.html


def test_page_hosts_and_urls():
    assert page_id_of_host("tamu.page") == "tamu"
    assert page_id_of_host("diffslot.page") == "diffslot"
    assert page_id_of_host("items") is None
    assert page_id_of_host("app") is None
    assert page_id_of_host(".page") is None
    assert page_id_of_host("bad id.page") is None
    assert page_url("tamu", "json/index.html") == "specto://tamu.page/json/index.html"
    assert page_url("tamu", "/json/index.html") == "specto://tamu.page/json/index.html"
    assert item_url("l3", "00042", ".json") == "specto://items/l3/00042/json"
    assert item_url("l3", "00042", "json") == "specto://items/l3/00042/json"
    # a key is a path stem when the intaker has no keys regex: its slashes are encoded
    # so the key stays one path segment
    assert (
        item_url("l1", "runs/front/00042", ".json") == "specto://items/l1/runs%2Ffront%2F00042/json"
    )
    assert item_url("l1", "a b", ".md") == "specto://items/l1/a%20b/md"


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("/l3/00042/json", ("l3", "00042", ".json")),
        ("/l3/00042/.JSON", ("l3", "00042", ".json")),
        ("/l1/runs%2Ffront%2F00042/json", ("l1", "runs/front/00042", ".json")),
        ("/l1/a%20b/md", ("l1", "a b", ".md")),
        ("/l3/00042", None),
        ("/l3/00042/json/extra", None),
        ("/../x/json", None),
        ("/l3/..%2Fx/json", None),
        ("/l3/00042/j s", None),
    ],
)
def test_parse_item_path(path, want):
    """Paths arrive fully encoded (``QUrl.path(FullyEncoded)``) so an encoded slash in
    the key survives; the parser decodes each segment."""
    assert parse_item_path(path) == want


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("/l1/note/md", ("l1", "note", ".md", None)),
        ("/l1/note/md/img/a.png", ("l1", "note", ".md", "img/a.png")),
        ("/l1/notes%2Fnote/md/img/a%20b.png", ("l1", "notes/note", ".md", "img/a b.png")),
        ("/l1/note/md/../x.png", None),
        ("/l1/note/md/./x.png", None),
        ("/l1/note/md//x.png", ("l1", "note", ".md", "x.png")),  # empty segments collapse
        ("/l1/note/md/C:/x.png", None),
        ("/l1/note", None),
    ],
)
def test_parse_item_rel_path(path, want):
    assert parse_item_rel_path(path) == want


def test_resolve_relative_stays_next_to_the_part(tmp_path: Path):
    notes = tmp_path / "notes"
    (notes / "img").mkdir(parents=True)
    (notes / "a.md").write_text("x", encoding="utf-8")
    (notes / "img" / "p.png").write_bytes(b"\x89PNG")
    (tmp_path / "secret.png").write_bytes(b"no")
    part = notes / "a.md"
    assert resolve_relative(part, "img/p.png") == (notes / "img" / "p.png").resolve()
    assert resolve_relative(part, "img/missing.png") is None
    assert resolve_relative(part, "../secret.png") is None
    assert resolve_relative(part, "img/../../secret.png") is None


def test_net_decision_needs_a_page_with_net_tiles_and_a_tile_host():
    caps = {"files": Capabilities.of(["net:tiles"]), "kobi": Capabilities.of(["read:link:l1"])}
    lookup = caps.get
    assert net_decision("files.page", "https", TILE_HOSTS[0], lookup) == "ok"
    assert net_decision("files.page", "https", "example.com", lookup) == "denied:host"
    assert net_decision("kobi.page", "https", TILE_HOSTS[0], lookup) == "denied:net"
    assert net_decision("lito.page", "https", TILE_HOSTS[0], lookup) == "denied:net"
    # a map page never loads itself off the scheme, tiles or not
    assert (
        net_decision(
            "", "https", TILE_HOSTS[0], lookup, first_party_host="files.page", main_frame=True
        )
        == "denied:main_frame"
    )
    assert net_decision("", "https", TILE_HOSTS[0], lookup) == "no_page"
    assert net_decision("items", "https", TILE_HOSTS[0], lookup) == "no_page"
    # every basemap provider's tile host is let out (#157: Esri World Imagery)
    assert net_decision("files.page", "https", "server.arcgisonline.com", lookup) == "ok"
    # the app's own scheme and the webchannel script never go through the net gate
    assert net_decision("", "specto", "files.page", lookup) == "ok"
    assert net_decision("", "qrc", "", lookup) == "ok"


def test_content_type_and_query():
    assert content_type(Path("a.js")) == b"text/javascript"
    assert content_type(Path("a.json")) == b"application/json"
    assert content_type(Path("a.html")) == b"text/html"
    assert content_type(Path("a.ttf")) in (
        b"font/ttf",
        b"application/font-sfnt",
        b"application/octet-stream",
    )
    assert content_type(Path("blob.unknownext")) == b"application/octet-stream"
    # WebAssembly.instantiateStreaming refuses anything else (#66)
    assert content_type(Path("spz.wasm")) == b"application/wasm"
    assert parse_query("range=0-9&x=1") == {"range": "0-9", "x": "1"}
    assert parse_query("") == {}


def test_read_source_serves_container_members(tmp_path: Path):
    import zipfile

    plain = tmp_path / "a.json"
    plain.write_bytes(bytes(range(64)))
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("front/00001.json", bytes(range(100)))
    member = f"{z}#/front/00001.json"
    assert source_size(plain) == 64 and source_size(member) == 100
    assert read_source(plain) == bytes(range(64))
    assert read_source(member) == bytes(range(100))
    assert read_source(member, ByteRange(10, 19, 100)) == bytes(range(10, 20))
    assert read_source(Path(member), parse_range(b"bytes=-4", 100)) == bytes([96, 97, 98, 99])


@pytest.mark.parametrize(
    "key",
    [
        ".claude/CLAUDE",  # a note in a dot folder (vault: .claude/, .logs/)
        ".logs/2026-05-21T16-27Z_zoom-kernel-panic",
        "example-config/Simulation – 2027 Plan",  # en dash  # noqa: RUF001
        "notes/R&D #3, Bob's draft [kobi]",
        "日本語のノート",
    ],
)
def test_item_url_round_trips_real_note_names(key):
    """User test 2: notes in dot folders or with non-ASCII names gave HTTP 404 because
    the item key was refused; the key is only a lookup into the link's items."""
    path = item_url("l2", key, ".md").split("://items", 1)[1]
    assert parse_item_path(path) == ("l2", key, ".md")


@pytest.mark.parametrize(
    "path",
    [
        "/l2/..%2Fx/md",
        "/l2/a%2F..%2Fb/md",
        "/l2/.%2Fa/md",
        "/l2/a%00b/md",
        "/l2/a%0Ab/md",
        "/l2/a%5Cb/md",  # backslash
    ],
)
def test_item_keys_still_refuse_traversal_and_control_characters(path):
    assert parse_item_path(path) is None


def test_relative_images_may_have_non_ascii_names():
    rel = "img/Bild äöü – 1.png"  # noqa: RUF001
    path = "/l2/note/md/" + "/".join(quote(s, safe="") for s in rel.split("/"))
    assert parse_item_rel_path(path) == ("l2", "note", ".md", rel)


def test_navigation_policy_frames_may_load_the_views_items_origin_only():
    """The main frame stays on the page's own origin. A frame inside the page (the html
    view's sandboxed iframe) may navigate to the view's own items origin, where the
    user's page and its relative assets live, and nowhere else: not the shared items
    host, not another view's origin, not the network."""
    page = "specto://kobi.page/html/index.html"
    deck = "specto://kobi.items/l2/deck/html/deck.html"
    assert navigation_allowed("kobi", page, True)
    assert navigation_allowed("kobi", page + "#reload", True)
    assert not navigation_allowed("kobi", deck, True)  # the frame's URL as a top-level page
    assert navigation_allowed("kobi", deck, False)
    assert navigation_allowed("kobi", page, False)  # a frame on the page's own origin
    assert navigation_allowed("kobi", "about:blank", False)
    assert not navigation_allowed("kobi", "specto://tamu.items/l2/deck/html/deck.html", False)
    assert not navigation_allowed("kobi", "specto://items/l2/deck/html", False)
    assert not navigation_allowed("kobi", "specto://kobi.itemsX/l2/deck/html/deck.html", False)
    assert not navigation_allowed("kobi", "https://maps.google.com/maps?q=1,2&output=embed", False)
    assert not navigation_allowed("kobi", "https://example.com/", True)
    assert not navigation_allowed("kobi", "file:///C:/x.html", False)
    # with the view's net:web grant (#93) a frame may also load http(s); never the page
    embed = "https://www.google.com/maps/embed?pb=1"
    assert navigation_allowed("kobi", embed, False, network=True)
    assert navigation_allowed("kobi", "http://example.com/", False, network=True)
    assert not navigation_allowed("kobi", embed, True, network=True)
    assert not navigation_allowed("kobi", "file:///C:/x.html", False, network=True)
    assert not navigation_allowed("kobi", "specto://items/l2/deck/html", False, network=True)


def test_requests_not_served_are_warned_once_without_debug(capsys):
    """A request the scheme handler or the interceptor turned down (a page's asset that
    is not there, a denied item, a blocked network fetch) is a [WARN] line in the log,
    once per host, path and status; a served one is silent unless debugging."""
    log: list[dict] = []
    seen: set = set()
    ok = {"host": "kobi.items", "path": "/l2/deck/html/deck.html", "status": "ok"}
    missing = {"host": "kobi.items", "path": "/l2/deck/html/nope.css", "status": "not_found"}
    blocked = {"host": "fonts.googleapis.com", "path": "/css2", "status": "net_no_page"}
    assert record(log, ok, seen=seen) is None
    assert record(log, missing, seen=seen) == f"[WARN] scheme {missing}"
    assert record(log, dict(missing), seen=seen) is None  # the same miss again: once
    assert record(log, blocked, seen=seen) == f"[WARN] scheme {blocked}"
    assert log == [ok, missing, missing, blocked]
    err = capsys.readouterr().err
    assert err.count("[WARN]") == 2 and "nope.css" in err and "fonts.googleapis.com" in err
    assert record(log, dict(ok), debug=True) == f"[OK] scheme {ok}"
    assert record(log, dict(missing), debug=True, seen=seen) is not None  # debug: every one


_UNREADABLE_RELS = (
    r"\\localhost\C$\x.png",  # UNC
    "//localhost/C$/x.png",
    r"\??\UNC\localhost\C$\x.png",  # NT object paths
    "/??/UNC/localhost/C$/x.png",
    r"\\?\UNC\localhost\C$\x.png",  # long UNC
    r"\\.\pipe\x.png",  # device
    r"\Device\HarddiskVolume1\x.png",  # drive-less rooted
    r"\localhost\C$\x.png",
    "C:x.png",  # drive-relative
    "a.txt:b.png",  # stream
    "NUL.png",  # device name
)


@pytest.mark.skipif(os.name != "nt", reason="Windows path forms")
@pytest.mark.parametrize("anchor", ["", "@root/"])
@pytest.mark.parametrize("rel", _UNREADABLE_RELS)
def test_resolve_relative_never_hands_a_network_or_device_path_to_the_disk(
    tmp_path, monkeypatch, anchor, rel
):
    """SPEC-61: a UNC rel replaced the base in ``(base / rel).resolve()`` and Windows
    connected out over SMB (an untrusted note's image, an MTL's map). The rel, and the
    text after ``@root/``, go through the shared allowlist before any filesystem call."""
    import pathlib

    from specto_app.web.scheme import resolve_relative

    part = tmp_path / "note.md"
    part.write_text("x", encoding="utf-8")
    calls = []

    def boom(*_a, **_k):
        calls.append(1)
        raise AssertionError("a filesystem call")

    for name in ("resolve", "is_file", "stat", "exists", "absolute"):
        monkeypatch.setattr(pathlib.Path, name, boom)
    for name in ("stat", "lstat", "open", "listdir", "scandir"):
        monkeypatch.setattr(os, name, boom)
    monkeypatch.setattr(os.path, "realpath", boom)
    try:
        got = resolve_relative(part, anchor + rel, tmp_path)
    except AssertionError as e:
        got = e
    finally:
        monkeypatch.undo()  # before anything reports: pytest itself stats files
    assert calls == [] and got is None


@pytest.mark.skipif(os.name != "nt", reason="Windows path forms")
def test_a_percent_encoded_unc_segment_never_reaches_the_disk(tmp_path, monkeypatch):
    """The same through the items host: one URL segment ``%2F%2Flocalhost%2FC$%2Fx.png``
    decodes to a UNC path (``//localhost/...``; a ``%5C`` form never parses, since a
    segment may hold no backslash), and ``@root/%2F%3F%3F%2FUNC%2F...`` to an NT object
    path after the anchor; both parse, reach ``resolve_relative`` and are refused before
    anything resolves."""
    import pathlib

    from specto_app.web.scheme import locate_item, parse_item_rel_path

    part = tmp_path / "model.obj"
    part.write_text("v 0 0 0\n", encoding="utf-8")
    items = ItemTable()
    items.set_items("l1", [{"key": "m", "parts": {".obj": str(part)}}])
    caps = Capabilities.of({"read:link:l1": None})
    calls = []

    def boom(*_a, **_k):
        calls.append(1)
        raise AssertionError("a filesystem call")

    nt_object = "%2F%3F%3F%2FUNC%2Flocalhost%2FC$%2Fx.png"
    paths = {
        "/l1/m/obj/%2F%2Flocalhost%2FC$%2Fx.png": "//localhost/C$/x.png",
        f"/l1/m/obj/@root/{nt_object}": "@root//??/UNC/localhost/C$/x.png",
    }
    for path, rel in paths.items():
        assert parse_item_rel_path(path)[3] == rel  # the segment parses: the guard is reached
    monkeypatch.setattr(pathlib.Path, "resolve", boom)
    monkeypatch.setattr(os.path, "realpath", boom)
    got_all = []
    for path in paths:
        try:
            got_all.append(
                locate_item(
                    "items", path, "v1.page", caps_of=lambda _p: caps, items=items, entry={}
                )
            )
        except AssertionError as e:
            got_all.append(e)
    monkeypatch.undo()
    assert calls == [] and got_all == [None, None]


@pytest.mark.skipif(os.name != "nt", reason="Windows path forms")
@pytest.mark.parametrize(
    "url_path",
    [r"/\\localhost\C$\x.png", r"/\??\UNC\localhost\C$\x.png", "/C:/x.png", r"/\.\pipe\x"],
)
def test_resolve_asset_never_hands_a_rooted_path_to_the_disk(tmp_path, monkeypatch, url_path):
    """SPEC-61, the page host: ``url.path()`` arrives decoded, so a note's
    ``![x](/%5C%5Chost%5Cshare%5Cx.png)`` reached ``(root / rel).resolve()`` as a UNC path
    that replaced the root (realpath ran on ``\\\\localhost\\C$\\x.png`` on main)."""
    import pathlib

    from specto_app.web.scheme import resolve_asset

    calls = []

    def boom(*_a, **_k):
        calls.append(1)
        raise AssertionError("a filesystem call")

    for name in ("resolve", "is_file", "is_dir", "stat", "exists", "absolute"):
        monkeypatch.setattr(pathlib.Path, name, boom)
    for name in ("stat", "lstat", "open", "listdir", "scandir"):
        monkeypatch.setattr(os, name, boom)
    monkeypatch.setattr(os.path, "realpath", boom)
    try:
        got = resolve_asset(tmp_path, url_path)
    except AssertionError as e:
        got = e
    finally:
        monkeypatch.undo()
    assert calls == [] and got is None


@pytest.mark.parametrize(
    "part",
    [
        "//host/share/x.png",
        "\\\\host\\share\\x.png",
        "/??/UNC/host/share/x.png",
        "//host/share/a.zip#/x.png",
    ],
)
def test_locate_item_refuses_a_network_part_before_it_is_stated(part, monkeypatch):
    """The backstop for every host that serves through items (SPEC-75): whatever produced
    the item (a Python link's collection, a derived file), a part whose recorded path is
    a network or NT object path is refused by its text before ``is_file``; a container
    member is judged by its archive."""
    import pathlib

    from specto_app.web.scheme import locate_item, part_refused

    if os.name != "nt" and "\\\\" in part:
        pytest.skip("a backslash form is Windows only")
    items = ItemTable()
    items.set_items("l1", [{"key": "m", "parts": {".png": part}}])
    caps = Capabilities.of({"read:link:l1": None})
    calls = []

    def boom(*_a, **_k):
        calls.append(1)
        raise AssertionError("a filesystem call")

    monkeypatch.setattr(pathlib.Path, "stat", boom)
    monkeypatch.setattr(pathlib.Path, "is_file", boom)
    monkeypatch.setattr(pathlib.Path, "exists", boom)
    monkeypatch.setattr(os, "stat", boom)
    monkeypatch.setattr(os.path, "isfile", boom)
    entry: dict = {}
    try:
        got = locate_item(
            "items", "/l1/m/png", "v1.page", caps_of=lambda _p: caps, items=items, entry=entry
        )
    finally:
        monkeypatch.undo()
    assert got is None and calls == [] and entry["status"] == "refused"
    assert part_refused(part, None) is not None
    # a project on that share serves its own files, and nothing on another share
    assert part_refused("//host/share/proj/x.png", "//host/share/proj") is None
    assert part_refused("//other/share/x.png", "//host/share/proj") is not None
