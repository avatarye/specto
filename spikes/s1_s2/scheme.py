"""specto:// URL scheme handler for QtWebEngine (S2 item: QWebEngineUrlSchemeHandler
with range requests serving a binary file to the web view).

Pure helpers (parse_range, slice_for_range) are separated from the Qt handler so
they can be unit-tested without a QApplication.
"""

from __future__ import annotations

import mimetypes
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCHEME = b"specto"
HOST = "app"

_RANGE_RE = re.compile(rb"^bytes=(\d*)-(\d*)$")


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive
    total: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def content_range(self) -> bytes:
        return b"bytes %d-%d/%d" % (self.start, self.end, self.total)


def parse_range(header: bytes | None, total: int) -> ByteRange | None:
    """Parse a single HTTP Range header against a resource of `total` bytes.

    Returns None for a missing/unsatisfiable/multi-range header (caller serves
    the whole resource, as RFC 7233 allows for ignored ranges).
    """
    if not header or total <= 0:
        return None
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    s, e = m.group(1), m.group(2)
    if s == b"" and e == b"":
        return None
    if s == b"":  # suffix range: last N bytes
        n = min(int(e), total)
        if n == 0:
            return None
        return ByteRange(total - n, total - 1, total)
    start = int(s)
    if start >= total:
        return None
    end = total - 1 if e == b"" else min(int(e), total - 1)
    if end < start:
        return None
    return ByteRange(start, end, total)


def slice_for_range(path: Path, rng: ByteRange) -> bytes:
    with path.open("rb") as fh:
        fh.seek(rng.start)
        return fh.read(rng.length)


def resolve(root: Path, url_path: str) -> Path | None:
    """Map a URL path to a file under root; None if it escapes or is missing."""
    rel = url_path.lstrip("/") or "index.html"
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def content_type(path: Path) -> bytes:
    mt, _ = mimetypes.guess_type(path.name)
    if path.suffix == ".js":
        mt = "text/javascript"
    return (mt or "application/octet-stream").encode()


# ---- Qt part -------------------------------------------------------------------


def register_scheme() -> None:
    """Must run before QGuiApplication is constructed."""
    from PySide6.QtWebEngineCore import QWebEngineUrlScheme

    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.HostAndPort)
    scheme.setDefaultPort(443)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
        | QWebEngineUrlScheme.Flag.CorsEnabled
        | QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored
        # Without FetchApiAllowed, fetch()/XHR to the scheme fail with "Failed to fetch".
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)


def make_handler(root: Path, log: list[dict] | None = None):
    from PySide6.QtCore import QBuffer, QByteArray, QFile, QIODevice
    from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlSchemeHandler

    class SpectoSchemeHandler(QWebEngineUrlSchemeHandler):
        def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
            url = job.requestUrl()
            path = resolve(root, url.path())
            if path is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            total = path.stat().st_size
            entry = {
                "path": url.path(),
                "range": None,
                "query": url.query() or None,
                "total": total,
                "served": total,
            }

            # Range semantics, measured on PySide6 6.12.0:
            # - A request carrying a Range header must get the FULL body. Chromium
            #   slices it itself and answers 206 + Content-Range. Replying with a
            #   partial body to such a request crashes the process (access violation).
            # - Chromium's slicing streams from byte 0 (O(offset)): 1 KiB at the end
            #   of 32 MB costs ~80 ms vs ~1.5 ms at the start.
            # - So large-file slicing is app-controlled: ?range=start-end in the URL,
            #   served as a 200 with just the slice (~1.7 ms for 1 KiB anywhere).
            range_hdr = None
            for k, v in job.requestHeaders().items():
                if bytes(k).lower() == b"range":
                    range_hdr = bytes(v).decode()
            entry["range"] = range_hdr
            rng = None
            if url.hasQuery():
                q = dict(part.split("=", 1) for part in url.query().split("&") if "=" in part)
                if "range" in q:
                    rng = parse_range(b"bytes=" + q["range"].encode(), total)

            # The C++ signature is QMultiMap<QByteArray, QByteArray>; PySide6 6.12.0
            # iterates each dict value, so a bare QByteArray value is split into one
            # header value per character ("s, e, t, y, b"). Wrap values in a list.
            hdrs = {QByteArray(b"Accept-Ranges"): [QByteArray(b"bytes")]}
            if rng is None:
                dev = QFile(str(path))  # streamed by Chromium, no Python copy
            else:
                dev = QBuffer()
                dev.setData(QByteArray(slice_for_range(path, rng)))
                hdrs[QByteArray(b"X-Specto-Range")] = [QByteArray(rng.content_range())]
                entry["served"] = rng.length
            dev.open(QIODevice.OpenModeFlag.ReadOnly)
            # Lifetime: parent the device to the job (Qt's documented pattern). A
            # Python-side `job.destroyed.connect(lambda ...)` to release it instead
            # crashes under load (access violation) on PySide6 6.12.0.
            dev.setParent(job)
            job.setAdditionalResponseHeaders(hdrs)
            if log is not None:
                log.append(entry)
            if os.environ.get("SPECTO_SCHEME_DEBUG"):
                print("[scheme]", entry, file=sys.stderr, flush=True)
            job.reply(content_type(path), dev)

    return SpectoSchemeHandler()
