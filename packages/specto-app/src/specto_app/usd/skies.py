"""The usd view's environment assets, fetched at run time (SPEC-31): NVIDIA's dynamic
skies and the 3d view's HDRIs, downloaded on first use into the user's Specto data folder,
each file pinned by sha256 and size.

**The skies** are the seven dynamic skies of the Omniverse sample assets on NVIDIA's public
bucket, each packed as its own ``.usdz`` that keeps its MDL. They are **not committed**:
the NVIDIA Omniverse License Agreement grants a "non-exclusive, non-transferable,
non-sublicensable" licence to "Distribute Content (unmodified or as modified by you) as
incorporated into your products or services for the purpose of enhancing your work ...
but not deploy or distribute the Content on a stand-alone basis", and forbids copies "in
any publicly accessible software repositories"; so each user's own Specto fetches them
for local use (``THIRD_PARTY_NOTICES.md`` section 3).

**The HDRIs** are the 3d view's five Poly Haven environments (CC0,
``web/3d/environments.json``: URL, sha256 and size each). An installed app has no web
build, so the same fetcher brings the ``.hdr`` the usd view's dome light reads into the
user's folder; a source checkout's build cache (``specto_web.hdri_source``) is used when
it is there.

- :data:`SKIES`: the catalogue, name -> :class:`Sky` (label, textured), in the panel's
  order; :func:`sky_files` the files a sky needs: its stage first, its MDL, the two
  invisible helpers and the preview scene with its floor (the six ``ProceduralSky`` skies
  share their modules, about 1 MB each with the preview scene; ``CloudySky`` is textured,
  with 67 MB of textures, so it is fetched last, or when it is picked);
- :data:`FILES`: every sky file's :class:`Pin`, the bucket's bytes of 2026-11-03 (the
  ETags are their md5s, unchanged since 2020-12 and 2021-05);
- :func:`cache_dir`, :func:`hdri_dir`: ``<user dir>/skies`` and ``<user dir>/hdri``
  (``specto_core.instances.user_dir``: ``SPECTO_USER_DIR`` overrides it);
  :func:`package`: a sky's ``.usdz`` once fetched; :func:`hdri_path`: an HDRI's ``.hdr``;
- :func:`fetch_sky`, :func:`fetch_hdri`: download what is missing under the folder's lock
  (two Specto processes never write the same file), each file into a ``.part`` that is
  cut off past its pinned size and checked against its hash before it is moved into
  place (a mismatch is removed and refused); a network failure is a
  :class:`SkyFetchError` saying it is offline;
- :class:`AssetFetcher`: the same off the GUI thread, one worker: what is asked for first;
  the first sky asked queues every other sky once (CloudySky last); reports progress for
  the panel as :class:`FetchStatus`;
- :func:`authored`: what a cached sky authors for the panel's controls (its ``/World``
  TimeOfDay, Latitude, Longitude, DayOfYear and the thin MDL's CloudCoverage,
  CumulusEnabled and haze), read from the text files without pxr.

The ``.usdz`` is a stored zip with the sky stage first, as the usdz format asks; ovrtx
resolves ``<name>.usdz[Dynamic/<name>.usd]`` and compiles the MDL from inside it
(measured 2026-11-03: the same frame as from the folder). Nothing fetched is executed.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import specto_web
from specto_core.instances import user_dir

BASE_URL = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Skies/"
BASE_URL_ENV = "SPECTO_SKY_BASE_URL"  # tests: a local server
HDRI_BASE_URL_ENV = "SPECTO_HDRI_BASE_URL"  # tests: a local server in place of Poly Haven's


class Sky(NamedTuple):
    label: str
    textured: bool = False  # a texture-based material (no procedural clouds or haze)


#: the skies in the panel's order; the textured one last
SKIES: dict[str, Sky] = {
    "ClearSky": Sky("Clear sky"),
    "Cirrus": Sky("Cirrus"),
    "CumulusLight": Sky("Light cumulus"),
    "CumulusHeavy": Sky("Heavy cumulus"),
    "Overcast": Sky("Overcast"),
    "NightSky": Sky("Night"),
    "CloudySky": Sky("Cloudy (textured)", textured=True),
}
DEFAULT_SKY = "ClearSky"
PROCEDURAL = tuple(name for name, sky in SKIES.items() if not sky.textured)
TEXTURED = tuple(name for name, sky in SKIES.items() if sky.textured)
PROCEDURAL_MDL_DIR = "Sky_Elements/materials/procedural/"
CLOUDY_MDL_DIR = "Sky_Elements/materials/cloudy/"
PREVIEW_SCENE = "Sky_Elements/PreviewScene.usda"  # the floor (a cube at z 0) and spheres


class Pin(NamedTuple):
    sha256: str
    size: int


#: rel path -> its pin
FILES: dict[str, Pin] = {
    "Dynamic/ClearSky.usd": Pin(
        "9cb4fc1eebca06ea3e49a05ea5aba1eeb616417cf9c15c7d6851fd2090c9d219", 17399),
    "Dynamic/Cirrus.usd": Pin(
        "9ab561f4370c4fe8c92677ebd744162919edc8fc8c48b75d51abf0a167945a37", 17395),
    "Dynamic/CumulusLight.usd": Pin(
        "20b7feb5878600b3bc0d57ed79548803e602b31c1dcee841fba852a731f44dbe", 17407),
    "Dynamic/CumulusHeavy.usd": Pin(
        "e1b84f3e1d6ada7c2cf4736557a5b9ebf50b9ace148bcac737acc761500987ac", 17407),
    "Dynamic/Overcast.usd": Pin(
        "e54aa7afa0a93a724812c98d7d7e00ce3948f1a19e8a4b95b48315fc31950ad8", 17399),
    "Dynamic/NightSky.usd": Pin(
        "7032955ff1fc6a08ea672267b8ffb634ab31d66657c69d1dc7059943af16dc46", 17460),
    "Dynamic/CloudySky.usd": Pin(
        "3736df5c85017ee941f2899e20f9a7fc5a2c7fb6cde309f33e45a724c196c492", 17403),
    "Sky_Elements/SphereInverseLow.usd": Pin(
        "3706afb9f125eaa2131e28b15950f159f6d37f17c21b32c30dbda357d869aa21", 5874),
    "Sky_Elements/ArrowsNWSE.usd": Pin(
        "3fe2d9574b3a510748b29e2131b98cc6bff83fa416a3948d1106c78b377c3167", 105539),
    PREVIEW_SCENE: Pin(
        "c97e531183e7f989c9ced535e1cb1d60bb6b7d562595e5c7b606491333d3f078", 45444),
    "Sky_Elements/uvsphere.usd": Pin(
        "e900d5f18c7de54173a9334af52d4ebc93d331b264fef83bf594a89fde62dd81", 650593),
    PROCEDURAL_MDL_DIR + "ProceduralSky.mdl": Pin(
        "6090f7a6868035dfc3ce9e2a020f5b07074bdd79db50d419d9feb9c40e4b24d7", 160319),
    PROCEDURAL_MDL_DIR + "ClearSky.mdl": Pin(
        "d883b06045701d56e21b5d4ccea4512c5d7bdc02b2c7d921d5d8bf5f645e4cb9", 5596),
    PROCEDURAL_MDL_DIR + "Cirrus.mdl": Pin(
        "2d0655a4a1dcb02b81b7ee8ed5f9cc14dd137061657c4009dcae5f7547289b79", 5570),
    PROCEDURAL_MDL_DIR + "CumulusLight.mdl": Pin(
        "02d882b019a15be19e9a1700b2e5fec3b5011339904f3c4a8ebb7695a07b41c6", 5574),
    PROCEDURAL_MDL_DIR + "CumulusHeavy.mdl": Pin(
        "47ec00fad728e4df8821354f6916d865b120d6c5e6410e7ca30165b605ddaec6", 5592),
    PROCEDURAL_MDL_DIR + "Overcast.mdl": Pin(
        "6baf30f28e240f41ac947b587313584452768db1105a679009f216cbe8ba8776", 5607),
    PROCEDURAL_MDL_DIR + "NightSky.mdl": Pin(
        "2264e7e0701130205bfc959f8e0fa173cd95981dcb8558ea0a3ac9023db188d3", 5454),
    CLOUDY_MDL_DIR + "SunsetSkyMat.mdl": Pin(
        "9a7420f0da153ed54ae56f381a59797add878b9164203b8b232bc86e3727b5b4", 31331),
    CLOUDY_MDL_DIR + "textures/ClearSky_v12_64_1_256_4096_1_64.dds": Pin(
        "59ca5b0f2425a30421aada28489114d30388e1e735a85ed1ab02566ee1da6ffb", 8388736),
    CLOUDY_MDL_DIR + "textures/stars_4k.hdr": Pin(
        "371597b62025afad7c5aa907d146876b3f2d4ef47a6d26dcf964f6c5b4b521b9", 34088719),
    CLOUDY_MDL_DIR + "textures/cloudnoise3normal.png": Pin(
        "3594a5765d5655ddd8a4ec4dd5b4d90a8c101a4ed167ff1eade80f2147819d19", 25076914),
    CLOUDY_MDL_DIR + "textures/2k_moon.jpg": Pin(
        "1a14415b11a964089e6d94918bedd88f3019a88d730631481e1266a04258e0aa", 62970),
}  # fmt: skip
#: what every sky references beside its own files: two invisible helpers, and the preview
#: scene (the ground toggle's floor) with the sphere its balls reference
SHARED = (
    "Sky_Elements/SphereInverseLow.usd",
    "Sky_Elements/ArrowsNWSE.usd",
    PREVIEW_SCENE,
    "Sky_Elements/uvsphere.usd",
)
CLOUDY_FILES = tuple(rel for rel in FILES if rel.startswith(CLOUDY_MDL_DIR))
MANIFEST_SUFFIX = ".json"
NOTICE = "NVIDIA-SKY-NOTICE.txt"
HDRI_NOTICE = "POLYHAVEN-CC0.txt"
LOCK = ".lock"
LICENCE_URL = (
    "https://docs.omniverse.nvidia.com/extensions/latest/common/"
    "NVIDIA_Omniverse_License_Agreement_2022-2023.html"
)
NOTICE_TEXT = f"""NVIDIA Omniverse sample skies (Assets/Skies/Dynamic and Sky_Elements)

Source: {BASE_URL} (NVIDIA's public Omniverse content bucket), fetched by Specto's usd
view on first use of its Sky environment, each file checked against a pinned sha256,
and packed here as one .usdz per sky for this installation's own use.

These files are NVIDIA "Content" under the NVIDIA Omniverse License Agreement
({LICENCE_URL}): a "non-exclusive, non-transferable, non-sublicensable" licence to
"Distribute Content (unmodified or as modified by you) as incorporated into your
products or services for the purpose of enhancing your work, subject to the
distribution requirements below, but not deploy or distribute the Content on a
stand-alone basis", with no copies "in any publicly accessible software repositories".
They are not part of the Specto repository (MIT) and must not be redistributed from
here on their own.
"""
HDRI_NOTICE_TEXT = """Poly Haven HDRIs (https://polyhaven.com), CC0 1.0 (https://polyhaven.com/license)

Fetched by Specto's usd view on first use of its HDRI environment, each file checked
against the sha256 and size in packages/specto-web/web/3d/environments.json.
"""


class SkyFetchError(Exception):
    """An asset could not be fetched: the message says why, in the panel's words."""


def label(name: str) -> str:
    """A sky's label in the panel's words (the name itself for an unknown one)."""
    sky = SKIES.get(name)
    return sky.label if sky is not None else name


def base_url() -> str:
    url = os.environ.get(BASE_URL_ENV) or BASE_URL
    return url if url.endswith("/") else url + "/"


def cache_dir() -> Path:
    """Where the skies live: ``<user dir>/skies``."""
    return user_dir() / "skies"


def hdri_dir() -> Path:
    """Where fetched HDRIs live: ``<user dir>/hdri``."""
    return user_dir() / "hdri"


def entry(name: str) -> str:
    """The sky's stage inside its package (the package's default layer)."""
    return f"Dynamic/{name}.usd"


def sky_files(name: str) -> list[str]:
    """The files sky ``name`` needs, its stage first (the usdz's default layer)."""
    if name not in SKIES:
        raise KeyError(name)
    if SKIES[name].textured:
        own = [entry(name), *CLOUDY_FILES]
    else:
        mdl = f"{PROCEDURAL_MDL_DIR}{name}.mdl"
        own = [entry(name), mdl, f"{PROCEDURAL_MDL_DIR}ProceduralSky.mdl"]
    return [*own, *SHARED]


def sky_pins(name: str) -> dict[str, str]:
    """The sha256 of each file sky ``name`` needs: what its manifest records."""
    return {rel: FILES[rel].sha256 for rel in sky_files(name)}


def sky_bytes(name: str) -> int:
    return sum(FILES[rel].size for rel in sky_files(name))


def package(name: str, folder: Path | None = None) -> Path | None:
    """Sky ``name``'s ``.usdz`` under the cache, once fetched (its manifest beside it
    holds the pins it was packed from); None before."""
    folder = folder or cache_dir()
    pkg, manifest = folder / f"{name}.usdz", folder / f"{name}{MANIFEST_SUFFIX}"
    if not pkg.is_file() or not manifest.is_file():
        return None
    try:
        info = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return pkg if info.get("files") == sky_pins(name) else None


def reference(name: str, folder: Path | None = None) -> str | None:
    """The sky as a reference path, ``<name>.usdz[Dynamic/<name>.usd]``, or None."""
    pkg = package(name, folder)
    return f"{pkg.as_posix()}[{entry(name)}]" if pkg is not None else None


def preview_reference(name: str, folder: Path | None = None) -> str | None:
    """The preview scene inside sky ``name``'s package (the ground toggle's floor)."""
    pkg = package(name, folder)
    return f"{pkg.as_posix()}[{PREVIEW_SCENE}]" if pkg is not None else None


# -- downloading -----------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def folder_lock(folder: Path, timeout: float = 600.0) -> Iterator[None]:
    """Hold ``folder``'s lock file: one Specto process at a time writes its ``.part`` and
    ``.tmp`` files (another waits, then finds the work done)."""
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / LOCK, "a+b")  # noqa: SIM115 (held across the yield)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if time.monotonic() > deadline:
                    raise SkyFetchError(f"{folder} is locked by another Specto") from e
                time.sleep(0.1)
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def download(url: str, dest: Path, pin: Pin, progress: Callable[[int], None]) -> None:
    """``url`` into ``dest``: written to ``dest.part``, cut off once it passes the pinned
    size, its hash checked, then moved into place; refused (nothing kept) otherwise."""
    req = urllib.request.Request(url, headers={"User-Agent": "specto-usd-environment"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    written = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            while chunk := r.read(1 << 18):
                written += len(chunk)
                if written > pin.size:
                    raise SkyFetchError(
                        f"{dest.name} from {url} is larger than the {pin.size} bytes pinned: "
                        "not kept"
                    )
                f.write(chunk)
                h.update(chunk)
                progress(len(chunk))
        sha = h.hexdigest()
        if sha != pin.sha256 or _sha256(part) != pin.sha256:
            raise SkyFetchError(
                f"{dest.name} from {url} is not the file pinned (sha256 {sha[:12]}...): not kept"
            )
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)


def _fetch_files(
    files: list[tuple[str, Path, Pin]],
    what: str,
    progress: Callable[[int, int], None] | None,
) -> None:
    """Each ``(url, dest, pin)`` not already there (a file of the pinned hash is kept),
    reporting ``progress(done, total)`` in bytes."""
    total = sum(pin.size for _, _, pin in files)
    done = 0

    def report(now: int) -> None:
        if progress is not None:
            progress(now, total)

    for url, dest, pin in files:
        if dest.is_file() and dest.stat().st_size == pin.size and _sha256(dest) == pin.sha256:
            done += pin.size
            report(done)
            continue
        file_start = done
        received = 0

        def on_chunk(n: int, file_start: int = file_start) -> None:
            nonlocal received
            received += n
            report(file_start + received)

        try:
            download(url, dest, pin, on_chunk)
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as e:
            reason = getattr(e, "reason", None) or e
            raise SkyFetchError(f"cannot fetch {what}: offline? ({reason})") from e
        done = file_start + pin.size
        report(done)


def pack(folder: Path, files: list[str], out: Path) -> Path:
    """``files`` (relative to ``folder``, the first the default layer) as one stored zip
    at ``out``."""
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
        for rel in files:
            zf.write(folder / rel, rel)
    tmp.replace(out)
    return out


def fetch_sky(
    name: str,
    folder: Path | None = None,
    url: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Fetch sky ``name`` into ``folder`` (the cache) from ``url`` (the bucket) and pack
    it, under the folder's lock; answer the package. Files already there with the right
    hash are kept; a file whose hash or size differs from its pin is refused; a network
    failure is refused as offline. ``progress(done, total)`` is called as bytes arrive."""
    if name not in SKIES:
        raise SkyFetchError(f"no such sky: {name}")
    folder = folder or cache_dir()
    base = url or base_url()
    pkg = package(name, folder)
    if pkg is not None:
        return pkg
    with folder_lock(folder):
        pkg = package(name, folder)  # another process may have made it meanwhile
        if pkg is not None:
            return pkg
        files = sky_files(name)
        src = folder / "src"
        _fetch_files([(base + rel, src / rel, FILES[rel]) for rel in files], label(name), progress)
        out = pack(src, files, folder / f"{name}.usdz")
        notice = folder / NOTICE
        if not notice.exists():
            notice.write_text(NOTICE_TEXT, encoding="utf-8")
        manifest = {
            "sky": name,
            "base_url": base,
            "files": sky_pins(name),
            "package": out.name,
            "entry": entry(name),
            "notice": NOTICE,
        }
        (folder / f"{name}{MANIFEST_SUFFIX}").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    return out


# -- the HDRIs ------------------------------------------------------------------------------
def hdri_entry(name: str) -> dict[str, Any] | None:
    """The 3d view's manifest entry of HDRI ``name`` (url, sha256, size, label)."""
    return next((e for e in specto_web.environments() if e.get("name") == name), None)


def hdri_label(name: str) -> str:
    entry_ = hdri_entry(name) or {}
    return str(entry_.get("label") or name)


def _hdri_file_name(entry_: dict[str, Any]) -> str:
    return str(entry_.get("url", "")).rsplit("/", 1)[-1]


def hdri_path(name: str, folder: Path | None = None) -> Path | None:
    """HDRI ``name``'s ``.hdr``: the web build's cache in a source checkout when it is
    there, else the one fetched into the user's folder; None before either."""
    built = specto_web.hdri_source(name)
    if built is not None:
        return built
    entry_ = hdri_entry(name)
    if entry_ is None:
        return None
    path = (folder or hdri_dir()) / _hdri_file_name(entry_)
    return path if path.is_file() and path.stat().st_size == int(entry_.get("size", -1)) else None


def fetch_hdri(
    name: str,
    folder: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Fetch HDRI ``name`` (Poly Haven's URL, or ``SPECTO_HDRI_BASE_URL`` + its file name)
    into ``folder`` (``<user dir>/hdri``) under the folder's lock, pinned by the manifest's
    sha256 and size; answer its path (the build cache's when there is one)."""
    entry_ = hdri_entry(name)
    if entry_ is None:
        raise SkyFetchError(f"no such HDRI: {name}")
    found = hdri_path(name, folder)
    if found is not None:
        return found
    folder = folder or hdri_dir()
    file_name = _hdri_file_name(entry_)
    override = os.environ.get(HDRI_BASE_URL_ENV)
    url = (override.rstrip("/") + "/" + file_name) if override else str(entry_["url"])
    pin = Pin(str(entry_["sha256"]), int(entry_["size"]))
    with folder_lock(folder):
        found = hdri_path(name, folder)
        if found is not None:
            return found
        _fetch_files([(url, folder / file_name, pin)], f"the {hdri_label(name)} HDRI", progress)
        notice = folder / HDRI_NOTICE
        if not notice.exists():
            notice.write_text(HDRI_NOTICE_TEXT, encoding="utf-8")
    return folder / file_name


# -- off the GUI thread -------------------------------------------------------------------
HDRI_PREFIX = "hdri:"


def hdri_key(name: str) -> str:
    """The fetcher's key of HDRI ``name`` (a sky's key is its name)."""
    return HDRI_PREFIX + name


@dataclass(frozen=True)
class FetchStatus:
    """Where one asset is: ``cached``; ``fetching`` with ``done`` of ``total`` bytes;
    ``queued``; or ``error``, why the last try failed."""

    cached: bool
    fetching: bool = False
    queued: bool = False
    done: int = 0
    total: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "cached": self.cached,
            "fetching": self.fetching,
            "queued": self.queued,
            "done": self.done,
            "total": self.total,
            "error": self.error,
        }


class AssetFetcher:
    """Fetches skies and HDRIs on one worker thread. ``ask(key)`` (a sky's name, or
    :func:`hdri_key`) fetches it next, ahead of anything queued; the first sky asked also
    queues every other sky after it (the procedural ones, then the textured CloudySky);
    ``status(key)`` is what the panel shows; ``on_change`` is called from the worker
    thread on every change (the host marshals it to the GUI thread). What is cached is
    never refetched; a failure is kept for the asset it hit and the queue goes on."""

    def __init__(
        self,
        on_change: Callable[[], None] | None = None,
        folder: Path | None = None,
        url: str | None = None,
        fetch: Callable[..., Path] = fetch_sky,
        fetch_hdri_: Callable[..., Path] = fetch_hdri,
        hdri_folder: Path | None = None,
    ) -> None:
        self._on_change = on_change
        self._folder = folder
        self._url = url
        self._fetch = fetch
        self._fetch_hdri = fetch_hdri_
        self._hdri_folder = hdri_folder
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._thread: threading.Thread | None = None
        self._swept = False
        self.current: str | None = None
        self.progress: tuple[int, int] = (0, 0)
        self.errors: dict[str, str] = {}

    def cached(self, key: str) -> bool:
        if key.startswith(HDRI_PREFIX):
            return hdri_path(key[len(HDRI_PREFIX) :], self._hdri_folder) is not None
        return package(key, self._folder) is not None

    def _size(self, key: str) -> int:
        if key.startswith(HDRI_PREFIX):
            return int((hdri_entry(key[len(HDRI_PREFIX) :]) or {}).get("size", 0))
        return sky_bytes(key)

    def _known(self, key: str) -> bool:
        if key.startswith(HDRI_PREFIX):
            return hdri_entry(key[len(HDRI_PREFIX) :]) is not None
        return key in SKIES

    def ask(self, key: str) -> None:
        """Fetch ``key`` next; the first sky asked also queues the other skies."""
        if not self._known(key) or self.cached(key):
            return
        with self._lock:
            self.errors.pop(key, None)
            if key in self._queue:
                self._queue.remove(key)
            if self.current != key:
                self._queue.insert(0, key)
            if key in SKIES and not self._swept:
                self._swept = True
                rest = [*PROCEDURAL, *TEXTURED]
                self._queue += [s for s in rest if s != key and s not in self._queue]
            if self._thread is None or not self._thread.is_alive():
                self.current = self._queue[0] if self._queue else key  # busy from the ask on
                self.progress = (0, self._size(self.current))
                self._thread = threading.Thread(target=self._run, name="usd-env-fetch", daemon=True)
                self._thread.start()

    @property
    def busy(self) -> bool:
        return self.current is not None

    def status(self, key: str) -> FetchStatus:
        with self._lock:
            fetching = self.current == key
            done, total = self.progress if fetching else (0, 0)
            return FetchStatus(
                cached=self.cached(key),
                fetching=fetching,
                queued=key in self._queue,
                done=done,
                total=total,
                error=self.errors.get(key, ""),
            )

    def note(self, key: str) -> str:
        """The panel's line for ``key`` while it is not there yet: fetching with its
        progress, waiting its turn, or why it failed."""
        s = self.status(key)
        if s.cached:
            return ""
        name = (
            f"the {hdri_label(key[len(HDRI_PREFIX) :])} HDRI"
            if key.startswith(HDRI_PREFIX)
            else label(key)
        )
        if s.fetching:
            mb = f"{s.done / 1e6:.1f} of {s.total / 1e6:.1f} MB" if s.total else ""
            return f"fetching {name} {mb}".rstrip()
        if s.queued:
            return f"{name} is queued to fetch"
        return s.error

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self.current = None
                    break
                key = self._queue.pop(0)
                self.current = key
                self.progress = (0, self._size(key))
            self._changed()
            if not self.cached(key):

                def on_progress(done: int, total: int) -> None:
                    with self._lock:
                        self.progress = (done, total)
                    self._changed()

                try:
                    if key.startswith(HDRI_PREFIX):
                        self._fetch_hdri(key[len(HDRI_PREFIX) :], self._hdri_folder, on_progress)
                    else:
                        self._fetch(key, self._folder, self._url, on_progress)
                except SkyFetchError as e:
                    with self._lock:
                        self.errors[key] = str(e)
                except Exception as e:  # a bad zip, a full disk: said, not raised
                    with self._lock:
                        self.errors[key] = f"cannot fetch {key}: {e}"
            with self._lock:
                self.current = None
            self._changed()


# -- what a sky authors, for the panel's controls ------------------------------------------
_WORLD_ATTR = re.compile(r"^\s*double (TimeOfDay|Latitude|Longitude|DayOfYear) = ([-\d.e]+)", re.M)
_MDL_ARG = re.compile(r"^\s*(CloudCoverage|haze|CumulusEnabled)\s*:\s*([-\d.e]+|true|false)", re.M)
_WORLD_KEYS = {
    "TimeOfDay": "time_of_day",
    "Latitude": "latitude",
    "Longitude": "longitude",
    "DayOfYear": "day_of_year",
}


def authored(name: str, folder: Path | None = None) -> dict[str, Any]:
    """What sky ``name`` authors, read from the cached files: ``time_of_day``,
    ``latitude``, ``longitude``, ``day_of_year`` (its ``/World``), and for a procedural
    sky ``cloud_coverage``, ``cumulus`` and ``haze`` (its thin MDL). Empty before it is
    fetched; the textured sky has no cloud or haze values."""
    folder = folder or cache_dir()
    src = folder / "src"
    out: dict[str, Any] = {}
    stage = src / entry(name)
    if not stage.is_file():
        return out
    for key, value in _WORLD_ATTR.findall(stage.read_text(encoding="utf-8", errors="replace")):
        out[_WORLD_KEYS[key]] = float(value)
    mdl = src / f"{PROCEDURAL_MDL_DIR}{name}.mdl"
    if name in PROCEDURAL and mdl.is_file():
        for key, value in _MDL_ARG.findall(mdl.read_text(encoding="utf-8", errors="replace")):
            if key == "CumulusEnabled":
                out["cumulus"] = value == "true"
            else:
                out["cloud_coverage" if key == "CloudCoverage" else "haze"] = float(value)
    return out


__all__ = [
    "BASE_URL",
    "BASE_URL_ENV",
    "CLOUDY_FILES",
    "DEFAULT_SKY",
    "FILES",
    "HDRI_BASE_URL_ENV",
    "NOTICE",
    "PREVIEW_SCENE",
    "PROCEDURAL",
    "SHARED",
    "SKIES",
    "TEXTURED",
    "AssetFetcher",
    "FetchStatus",
    "Pin",
    "Sky",
    "SkyFetchError",
    "authored",
    "base_url",
    "cache_dir",
    "download",
    "entry",
    "fetch_hdri",
    "fetch_sky",
    "folder_lock",
    "hdri_dir",
    "hdri_entry",
    "hdri_key",
    "hdri_label",
    "hdri_path",
    "label",
    "pack",
    "package",
    "preview_reference",
    "reference",
    "sky_bytes",
    "sky_files",
    "sky_pins",
]
