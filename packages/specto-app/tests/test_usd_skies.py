"""The dynamic skies' fetch (SPEC-31): a sky's files come from the bucket pinned by
sha256 into the user's cache, packed as one stored ``.usdz`` with the stage first; a hash
mismatch is refused and nothing kept, offline is said in words, a cached sky is never
fetched again; the worker fetches the sky asked for first, then the rest, CloudySky
last; the panel's values come from the cached files. The bucket is a local server."""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
import time
import zipfile
from functools import partial
from pathlib import Path

import pytest

from specto_app.usd import skies

WORLD = """#usda 1.0
(
    defaultPrim = "World"
)

def Xform "World"
{
    double DayOfYear = 100 (
        customData = {}
    )
    double Latitude = 51.426 (
    )
    double Longitude = -0.985 (
    )
    double TimeOfDay = 6.93 (
    )
}
"""
THIN_MDL = """mdl 1.4;

import ProceduralSky::ProceduralSky;

export material {name}(*) = ProceduralSky::ProceduralSky(
haze : 0.0010000000474974513,
CloudCoverage : 0.22599999606609344,
CumulusEnabled : {cumulus},
TimeOfDay : 6.3
);
"""


class Hits(http.server.SimpleHTTPRequestHandler):
    count = 0

    def do_GET(self):  # http.server's name
        Hits.count += 1
        super().do_GET()

    def log_message(self, *args):  # quiet
        pass


@pytest.fixture
def bucket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A local bucket with fake bytes for every file, the pins replaced to match;
    yields ``(base_url, root)``."""
    root = tmp_path / "bucket"
    pins: dict[str, tuple[str, int]] = {}
    for rel in skies.FILES:
        if rel.endswith(".usd") and rel.startswith("Dynamic/"):
            data = WORLD.encode()
        elif rel.endswith(".mdl") and "procedural/" in rel and "ProceduralSky" not in rel:
            name = rel.rsplit("/", 1)[1][:-4]
            data = THIN_MDL.format(
                name=name, cumulus="false" if name == "ClearSky" else "true"
            ).encode()
        else:
            data = f"# fake {rel}\n".encode() * (3 if "textures" in rel else 1)
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        pins[rel] = (hashlib.sha256(data).hexdigest(), len(data))
    monkeypatch.setattr(skies, "FILES", pins)
    Hits.count = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Hits, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/", root
    finally:
        server.shutdown()


def test_the_catalogue_names_seven_skies_with_their_files_and_sizes():
    assert list(skies.SKIES) == [
        "ClearSky", "Cirrus", "CumulusLight", "CumulusHeavy", "Overcast", "NightSky", "CloudySky",
    ]  # fmt: skip
    assert skies.SKIES["ClearSky"] == "Clear sky" and skies.DEFAULT_SKY == "ClearSky"
    files = skies.sky_files("ClearSky")
    assert files[0] == "Dynamic/ClearSky.usd"  # the usdz's default layer
    assert "Sky_Elements/materials/procedural/ProceduralSky.mdl" in files
    assert "Sky_Elements/materials/procedural/ClearSky.mdl" in files
    assert set(skies.SHARED) <= set(files)
    assert skies.sky_bytes("ClearSky") < 400_000
    cloudy = skies.sky_files("CloudySky")
    assert "Sky_Elements/materials/cloudy/SunsetSkyMat.mdl" in cloudy
    assert 60_000_000 < skies.sky_bytes("CloudySky") < 70_000_000  # its textures
    for rel, (pin, size) in skies.FILES.items():
        assert len(pin) == 64 and size > 0, rel
    assert skies.BASE_URL.startswith("https://omniverse-content-production.s3")


def test_a_sky_is_fetched_once_checked_and_packed_as_a_stored_usdz(tmp_path, bucket):
    base, _ = bucket
    cache = tmp_path / "cache"
    assert skies.package("ClearSky", cache) is None
    seen: list[tuple[int, int]] = []
    pkg = skies.fetch_sky("ClearSky", cache, base, progress=lambda d, t: seen.append((d, t)))
    assert pkg == cache / "ClearSky.usdz" and skies.package("ClearSky", cache) == pkg
    assert skies.reference("ClearSky", cache) == f"{pkg.as_posix()}[Dynamic/ClearSky.usd]"
    assert seen[-1][0] == seen[-1][1] == skies.sky_bytes("ClearSky")
    with zipfile.ZipFile(pkg) as zf:
        names = zf.namelist()
        assert names[0] == "Dynamic/ClearSky.usd"
        assert set(names) == set(skies.sky_files("ClearSky"))
        assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
    manifest = json.loads((cache / "ClearSky.json").read_text(encoding="utf-8"))
    assert manifest["files"] == {rel: skies.FILES[rel][0] for rel in skies.sky_files("ClearSky")}
    assert manifest["entry"] == "Dynamic/ClearSky.usd"
    notice = (cache / skies.NOTICE).read_text(encoding="utf-8")
    assert "stand-alone" in notice and "publicly accessible software repositories" in notice
    hits = Hits.count
    assert skies.fetch_sky("ClearSky", cache, base) == pkg  # cached: nothing fetched again
    assert Hits.count == hits
    # the shared files are kept for the next sky: only its own come over the wire
    skies.fetch_sky("Overcast", cache, base)
    assert Hits.count == hits + 2  # Dynamic/Overcast.usd and Overcast.mdl


def test_a_file_whose_hash_differs_is_refused_and_nothing_of_it_kept(tmp_path, bucket):
    base, root = bucket
    (root / "Sky_Elements/materials/procedural/Cirrus.mdl").write_bytes(b"tampered")
    cache = tmp_path / "cache"
    with pytest.raises(skies.SkyFetchError) as e:
        skies.fetch_sky("Cirrus", cache, base)
    assert "Cirrus.mdl" in str(e.value) and "not kept" in str(e.value)
    assert not (cache / "src/Sky_Elements/materials/procedural/Cirrus.mdl").exists()
    assert skies.package("Cirrus", cache) is None and not (cache / "Cirrus.usdz").exists()


def test_offline_is_said_in_words(tmp_path):
    import socket

    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(skies.SkyFetchError) as e:
        skies.fetch_sky("ClearSky", tmp_path / "cache", f"http://127.0.0.1:{port}/")
    assert "offline" in str(e.value) and "Clear sky" in str(e.value)


def test_the_cache_is_the_users_specto_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    assert skies.cache_dir() == tmp_path / "user" / "skies"


def test_what_a_cached_sky_authors_feeds_the_panels_controls(tmp_path, bucket):
    base, _ = bucket
    cache = tmp_path / "cache"
    assert skies.authored("ClearSky", cache) == {}
    skies.fetch_sky("ClearSky", cache, base)
    assert skies.authored("ClearSky", cache) == {
        "time_of_day": 6.93,
        "latitude": 51.426,
        "longitude": -0.985,
        "day_of_year": 100.0,
        "cloud_coverage": pytest.approx(0.226),
        "cumulus": False,
        "haze": pytest.approx(0.001),
    }
    skies.fetch_sky("CloudySky", cache, base)
    assert "cloud_coverage" not in skies.authored("CloudySky", cache)  # a textured sky


def wait(cond, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.02)
    assert cond(), "timed out"


def test_the_worker_fetches_the_sky_asked_first_then_the_rest_cloudy_last(tmp_path, bucket):
    base, _ = bucket
    cache = tmp_path / "cache"
    changes: list[str] = []
    order: list[str] = []
    real = skies.fetch_sky

    def fetch(name, folder, url, progress):
        order.append(name)
        return real(name, folder, url, progress)

    fetcher = skies.SkyFetcher(lambda: changes.append("x"), cache, base, fetch=fetch)
    assert fetcher.status("Overcast")["cached"] is False
    fetcher.ask("Overcast")
    wait(lambda: not fetcher.busy and fetcher.cached("CloudySky"))
    assert order[0] == "Overcast" and order[-1] == "CloudySky"
    assert set(order) == set(skies.SKIES)
    assert all(fetcher.cached(n) for n in skies.SKIES)
    assert fetcher.note("Overcast") == "" and changes


def test_the_panels_note_says_fetching_queued_or_why_it_failed(tmp_path, bucket):
    base, root = bucket
    (root / "Dynamic/NightSky.usd").write_bytes(b"tampered")
    cache = tmp_path / "cache"
    gate = threading.Event()
    real = skies.fetch_sky

    def fetch(name, folder, url, progress):
        if name == "ClearSky":
            progress(1000, 300_000)
            gate.wait(10)
        return real(name, folder, url, progress)

    fetcher = skies.SkyFetcher(None, cache, base, fetch=fetch)
    fetcher.ask("ClearSky")
    wait(lambda: fetcher.status("ClearSky")["done"] == 1000)
    assert fetcher.note("ClearSky") == "fetching Clear sky 0.0 of 0.3 MB"
    assert fetcher.note("Cirrus") == "Cirrus is queued to fetch"
    gate.set()
    wait(lambda: not fetcher.busy and fetcher.cached("CloudySky"))
    assert fetcher.note("ClearSky") == ""
    assert not fetcher.cached("NightSky")
    assert "NightSky.usd" in fetcher.note("NightSky") and "not kept" in fetcher.note("NightSky")
    fetcher.ask("NightSky")  # asked again: tried again, the same answer
    assert fetcher.busy and fetcher.note("NightSky").startswith("fetching Night")
    wait(lambda: not fetcher.busy)
    assert "not kept" in fetcher.note("NightSky")
