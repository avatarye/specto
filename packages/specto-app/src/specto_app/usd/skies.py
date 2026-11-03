"""NVIDIA's dynamic skies for the usd view's Sky environment (SPEC-31): the seven
procedural skies of the Omniverse sample assets on NVIDIA's public bucket, fetched on first
use into the user's Specto data folder, each pinned by sha256 and packed as its own
``.usdz`` that keeps its MDL.

They are **not committed**: the NVIDIA Omniverse License Agreement grants a
"non-exclusive, non-transferable, non-sublicensable" licence to "Distribute Content
(unmodified or as modified by you) as incorporated into your products or services for the
purpose of enhancing your work ... but not deploy or distribute the Content on a
stand-alone basis", and forbids copies "in any publicly accessible software repositories";
so each user's own Specto fetches them for local use (``THIRD_PARTY_NOTICES.md`` 2.5).

- :data:`SKIES`: the catalogue, name -> label, in the panel's order; :func:`sky_files`
  the files a sky needs (the six ``ProceduralSky`` skies share two MDL modules and two
  invisible helpers, about 0.3 MB each; ``CloudySky`` is a textured sky with 67 MB of
  textures, so it is fetched last, or when it is picked);
- :data:`FILES`: every file's sha256 and size, the bucket's bytes of 2026-11-03 (the
  ETags are their md5s, unchanged since 2020-12 and 2021-05);
- :func:`cache_dir`: ``<user dir>/skies`` (``specto_core.instances.user_dir``:
  ``SPECTO_USER_DIR`` overrides it); :func:`package`: a sky's ``.usdz`` once fetched;
- :func:`fetch_sky`: download what a sky needs (files already verified are kept), check
  each against its pin (a mismatch is removed and refused), pack and write the manifest
  and the notice; a network failure is a :class:`SkyFetchError` saying it is offline;
- :class:`SkyFetcher`: the same off the GUI thread, one worker: the sky asked for first,
  then every other sky once (CloudySky last), reporting progress for the panel;
- :func:`authored`: what a cached sky authors for the panel's controls (its ``/World``
  TimeOfDay, Latitude, Longitude, DayOfYear and the thin MDL's CloudCoverage,
  CumulusEnabled and haze), read from the text files without pxr.

The ``.usdz`` is a stored zip with the sky stage first, as the usdz format asks; ovrtx
resolves ``<name>.usdz[Dynamic/<name>.usd]`` and compiles the MDL from inside it
(measured 2026-11-03: the same frame as from the folder). Nothing fetched is executed.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from specto_core.instances import user_dir

BASE_URL = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Skies/"
BASE_URL_ENV = "SPECTO_SKY_BASE_URL"  # tests: a local server
#: the skies, name -> the panel's label, in its order; the textured one last
SKIES = {
    "ClearSky": "Clear sky",
    "Cirrus": "Cirrus",
    "CumulusLight": "Light cumulus",
    "CumulusHeavy": "Heavy cumulus",
    "Overcast": "Overcast",
    "NightSky": "Night",
    "CloudySky": "Cloudy (textured)",
}
DEFAULT_SKY = "ClearSky"
PROCEDURAL = ("ClearSky", "Cirrus", "CumulusLight", "CumulusHeavy", "Overcast", "NightSky")
TEXTURED = ("CloudySky",)
_P = "Sky_Elements/materials/procedural/"
_C = "Sky_Elements/materials/cloudy/"
#: rel path -> (sha256, bytes)
FILES: dict[str, tuple[str, int]] = {
    "Dynamic/ClearSky.usd": (
        "9cb4fc1eebca06ea3e49a05ea5aba1eeb616417cf9c15c7d6851fd2090c9d219", 17399),
    "Dynamic/Cirrus.usd": (
        "9ab561f4370c4fe8c92677ebd744162919edc8fc8c48b75d51abf0a167945a37", 17395),
    "Dynamic/CumulusLight.usd": (
        "20b7feb5878600b3bc0d57ed79548803e602b31c1dcee841fba852a731f44dbe", 17407),
    "Dynamic/CumulusHeavy.usd": (
        "e1b84f3e1d6ada7c2cf4736557a5b9ebf50b9ace148bcac737acc761500987ac", 17407),
    "Dynamic/Overcast.usd": (
        "e54aa7afa0a93a724812c98d7d7e00ce3948f1a19e8a4b95b48315fc31950ad8", 17399),
    "Dynamic/NightSky.usd": (
        "7032955ff1fc6a08ea672267b8ffb634ab31d66657c69d1dc7059943af16dc46", 17460),
    "Dynamic/CloudySky.usd": (
        "3736df5c85017ee941f2899e20f9a7fc5a2c7fb6cde309f33e45a724c196c492", 17403),
    "Sky_Elements/SphereInverseLow.usd": (
        "3706afb9f125eaa2131e28b15950f159f6d37f17c21b32c30dbda357d869aa21", 5874),
    "Sky_Elements/ArrowsNWSE.usd": (
        "3fe2d9574b3a510748b29e2131b98cc6bff83fa416a3948d1106c78b377c3167", 105539),
    _P + "ProceduralSky.mdl": (
        "6090f7a6868035dfc3ce9e2a020f5b07074bdd79db50d419d9feb9c40e4b24d7", 160319),
    _P + "ClearSky.mdl": (
        "d883b06045701d56e21b5d4ccea4512c5d7bdc02b2c7d921d5d8bf5f645e4cb9", 5596),
    _P + "Cirrus.mdl": (
        "2d0655a4a1dcb02b81b7ee8ed5f9cc14dd137061657c4009dcae5f7547289b79", 5570),
    _P + "CumulusLight.mdl": (
        "02d882b019a15be19e9a1700b2e5fec3b5011339904f3c4a8ebb7695a07b41c6", 5574),
    _P + "CumulusHeavy.mdl": (
        "47ec00fad728e4df8821354f6916d865b120d6c5e6410e7ca30165b605ddaec6", 5592),
    _P + "Overcast.mdl": (
        "6baf30f28e240f41ac947b587313584452768db1105a679009f216cbe8ba8776", 5607),
    _P + "NightSky.mdl": (
        "2264e7e0701130205bfc959f8e0fa173cd95981dcb8558ea0a3ac9023db188d3", 5454),
    _C + "SunsetSkyMat.mdl": (
        "9a7420f0da153ed54ae56f381a59797add878b9164203b8b232bc86e3727b5b4", 31331),
    _C + "textures/ClearSky_v12_64_1_256_4096_1_64.dds": (
        "59ca5b0f2425a30421aada28489114d30388e1e735a85ed1ab02566ee1da6ffb", 8388736),
    _C + "textures/stars_4k.hdr": (
        "371597b62025afad7c5aa907d146876b3f2d4ef47a6d26dcf964f6c5b4b521b9", 34088719),
    _C + "textures/cloudnoise3normal.png": (
        "3594a5765d5655ddd8a4ec4dd5b4d90a8c101a4ed167ff1eade80f2147819d19", 25076914),
    _C + "textures/2k_moon.jpg": (
        "1a14415b11a964089e6d94918bedd88f3019a88d730631481e1266a04258e0aa", 62970),
}  # fmt: skip
SHARED = ("Sky_Elements/SphereInverseLow.usd", "Sky_Elements/ArrowsNWSE.usd")
CLOUDY_FILES = tuple(rel for rel in FILES if rel.startswith(_C))
MANIFEST_SUFFIX = ".json"
NOTICE = "NVIDIA-SKY-NOTICE.txt"
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


class SkyFetchError(Exception):
    """A sky could not be fetched: the message says why, in the panel's words."""


def base_url() -> str:
    url = os.environ.get(BASE_URL_ENV) or BASE_URL
    return url if url.endswith("/") else url + "/"


def cache_dir() -> Path:
    """Where the skies live: ``<user dir>/skies``."""
    return user_dir() / "skies"


def sky_files(name: str) -> list[str]:
    """The files sky ``name`` needs, its stage first (the usdz's default layer)."""
    if name not in SKIES:
        raise KeyError(name)
    own = [f"Dynamic/{name}.usd"]
    if name in TEXTURED:
        own += list(CLOUDY_FILES)
    else:
        own += [f"{_P}{name}.mdl", f"{_P}ProceduralSky.mdl"]
    return [*own, *SHARED]


def sky_bytes(name: str) -> int:
    return sum(FILES[rel][1] for rel in sky_files(name))


def entry(name: str) -> str:
    return f"Dynamic/{name}.usd"


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
    pins = {rel: FILES[rel][0] for rel in sky_files(name)}
    return pkg if info.get("files") == pins else None


def reference(name: str, folder: Path | None = None) -> str | None:
    """The sky as a reference path, ``<name>.usdz[Dynamic/<name>.usd]``, or None."""
    pkg = package(name, folder)
    return f"{pkg.as_posix()}[{entry(name)}]" if pkg is not None else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, progress: Callable[[int], None]) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "specto-usd-sky"})
    h = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 18):
            f.write(chunk)
            h.update(chunk)
            progress(len(chunk))
    part.replace(dest)
    return h.hexdigest()


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
    it; answer the package. Files already there with the right hash are kept; a file
    whose hash differs from its pin is removed and refused; a network failure is
    refused as offline. ``progress(done, total)`` is called as bytes arrive."""
    if name not in SKIES:
        raise SkyFetchError(f"no such sky: {name}")
    folder = folder or cache_dir()
    base = url or base_url()
    files = sky_files(name)
    pkg = package(name, folder)
    if pkg is not None:
        return pkg
    src = folder / "src"
    total = sky_bytes(name)
    done = 0

    def tell() -> None:
        if progress is not None:
            progress(done, total)

    for rel in files:
        pin, size = FILES[rel]
        target = src / rel
        if target.is_file() and _sha256(target) == pin:
            done += size
            tell()
            continue
        got = [0]

        def advance(n: int, got=got, base=done) -> None:
            got[0] += n
            if progress is not None:
                progress(base + got[0], total)

        try:
            sha = _download(base + rel, target, advance)
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as e:
            reason = getattr(e, "reason", None) or e
            raise SkyFetchError(f"cannot fetch {SKIES[name]}: offline? ({reason})") from e
        if sha != pin:
            target.unlink(missing_ok=True)
            raise SkyFetchError(
                f"{rel} from {base} is not the file pinned (sha256 {sha[:12]}...): not kept"
            )
        done += size
        tell()
    folder.mkdir(parents=True, exist_ok=True)
    out = pack(src, files, folder / f"{name}.usdz")
    notice = folder / NOTICE
    if not notice.exists():
        notice.write_text(NOTICE_TEXT, encoding="utf-8")
    (folder / f"{name}{MANIFEST_SUFFIX}").write_text(
        json.dumps(
            {
                "sky": name,
                "base_url": base,
                "files": {rel: FILES[rel][0] for rel in files},
                "package": out.name,
                "entry": entry(name),
                "notice": NOTICE,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return out


# -- off the GUI thread -------------------------------------------------------------------
class SkyFetcher:
    """Fetches skies on one worker thread. ``ask(name)`` fetches that sky next (ahead of
    anything queued) and, the first time, queues every other sky after it (the
    procedural ones, then the textured CloudySky); ``status(name)`` is what the panel
    shows; ``on_change`` is called from the worker thread on every change (the host
    marshals it to the GUI thread). A sky already cached is never refetched; a failure
    is kept for the sky it hit and the sweep goes on with the next."""

    def __init__(
        self,
        on_change: Callable[[], None] | None = None,
        folder: Path | None = None,
        url: str | None = None,
        fetch: Callable[..., Path] = fetch_sky,
    ) -> None:
        self._on_change = on_change
        self._folder = folder
        self._url = url
        self._fetch = fetch
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._thread: threading.Thread | None = None
        self._swept = False
        self.current: str | None = None
        self.progress: tuple[int, int] = (0, 0)
        self.errors: dict[str, str] = {}

    def cached(self, name: str) -> bool:
        return package(name, self._folder) is not None

    def ask(self, name: str) -> None:
        """Fetch ``name`` next; the first ask also queues the rest."""
        if name not in SKIES or self.cached(name):
            return
        with self._lock:
            self.errors.pop(name, None)
            if name in self._queue:
                self._queue.remove(name)
            self._queue.insert(0, name)
            if not self._swept:
                self._swept = True
                rest = [*PROCEDURAL, *TEXTURED]
                self._queue += [s for s in rest if s != name and s not in self._queue]
            if self._thread is None or not self._thread.is_alive():
                self.current = name  # busy from the ask on, before the worker takes it
                self.progress = (0, sky_bytes(name))
                self._thread = threading.Thread(target=self._run, name="usd-sky-fetch", daemon=True)
                self._thread.start()

    @property
    def busy(self) -> bool:
        return self.current is not None

    def status(self, name: str) -> dict[str, Any]:
        """``{cached, fetching, queued, done, total, error}`` for sky ``name``."""
        with self._lock:
            fetching = self.current == name
            queued = name in self._queue
            done, total = self.progress if fetching else (0, 0)
            return {
                "cached": self.cached(name),
                "fetching": fetching,
                "queued": queued,
                "done": done,
                "total": total,
                "error": self.errors.get(name, ""),
            }

    def note(self, name: str) -> str:
        """The panel's line for sky ``name`` while it is not there yet: fetching with
        its progress, waiting its turn, or why it failed."""
        s = self.status(name)
        if s["cached"]:
            return ""
        if s["fetching"]:
            done, total = s["done"], s["total"]
            mb = f"{done / 1e6:.1f} of {total / 1e6:.1f} MB" if total else ""
            return f"fetching {SKIES[name]} {mb}".rstrip()
        if s["queued"]:
            return f"{SKIES[name]} is queued to fetch"
        return s["error"]

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self.current = None
                    break
                name = self._queue.pop(0)
                self.current = name
                self.progress = (0, sky_bytes(name))
            self._changed()
            if not self.cached(name):

                def tick(done: int, total: int) -> None:
                    with self._lock:
                        self.progress = (done, total)
                    self._changed()

                try:
                    self._fetch(name, self._folder, self._url, tick)
                except SkyFetchError as e:
                    with self._lock:
                        self.errors[name] = str(e)
                except Exception as e:  # a bad zip, a full disk: said, not raised
                    with self._lock:
                        self.errors[name] = f"cannot fetch {SKIES[name]}: {e}"
            with self._lock:
                self.current = None
            self._changed()


# -- what a sky authors, for the panel's controls ------------------------------------------
_WORLD_ATTR = re.compile(r"^\s*double (TimeOfDay|Latitude|Longitude|DayOfYear) = ([-\d.e]+)", re.M)
_MDL_ARG = re.compile(r"^\s*(CloudCoverage|haze|CumulusEnabled)\s*:\s*([-\d.e]+|true|false)", re.M)


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
    keys = {"TimeOfDay": "time_of_day", "Latitude": "latitude", "Longitude": "longitude",
            "DayOfYear": "day_of_year"}  # fmt: skip
    for key, value in _WORLD_ATTR.findall(stage.read_text(encoding="utf-8", errors="replace")):
        out[keys[key]] = float(value)
    mdl = src / f"{_P}{name}.mdl"
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
    "NOTICE",
    "PROCEDURAL",
    "SHARED",
    "SKIES",
    "TEXTURED",
    "SkyFetchError",
    "SkyFetcher",
    "authored",
    "base_url",
    "cache_dir",
    "entry",
    "fetch_sky",
    "pack",
    "package",
    "reference",
    "sky_bytes",
    "sky_files",
]
