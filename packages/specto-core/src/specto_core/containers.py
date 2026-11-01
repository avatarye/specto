"""Containers (HLD 3.6, M2): archives expand lazily into virtual entries.

A virtual path is ``<archive>#/<inner/path>``: ``C:/runs/drive.zip#/front/00042.jpg``.
Intake lists members without extracting (zip: the central directory; tar: the
member headers), screener rules and keys see the virtual relative path
(``drive.zip#/front/00042.jpg``), and decoders read one member by stream.

``ContainerReader`` is the extension point: ``register_container_reader`` adds a
reader for more extensions. MCAP / rosbag (M4) plug in here: a reader whose members
are ``<topic>/<index>`` entries behind a lazy message index, so ``drive.mcap#/camera/image``
needs nothing new in intake or the readers.

Standard library only; this module runs in the scan workers, the decode workers and
the GUI process alike.
"""

from __future__ import annotations

import io
import os
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePath
from typing import BinaryIO, Protocol, runtime_checkable

from .errors import NotFound

#: the separator between an archive path and the member path inside it
VIRTUAL_SEP = "#/"


@dataclass(frozen=True)
class Member:
    """One entry of a container, as the scan sees it (never extracted)."""

    path: str  # POSIX path inside the container, no leading slash
    size: int
    mtime_ns: int


@runtime_checkable
class ContainerReader(Protocol):
    name: str
    extensions: tuple[str, ...]  # lower-case, longest first (".tar.gz" before ".gz")

    def members(self, archive: str) -> Iterator[Member]:
        """List the entries of ``archive`` (files only) without extracting them."""
        ...

    def open(self, archive: str, inner: str) -> BinaryIO:
        """A readable, seekable binary stream over one member."""
        ...


class ZipReader:
    name = "zip"
    extensions = (".zip",)

    def members(self, archive: str) -> Iterator[Member]:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                yield Member(info.filename.replace("\\", "/"), info.file_size, _zip_mtime(info))

    def open(self, archive: str, inner: str) -> BinaryIO:
        zf = zipfile.ZipFile(archive)
        try:
            stream = zf.open(inner)
        except KeyError:
            zf.close()
            raise NotFound(f"{inner!r} is not in {archive}") from None
        return _Owned(stream, zf)


class TarReader:
    name = "tar"
    extensions = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar")

    def members(self, archive: str) -> Iterator[Member]:
        with tarfile.open(archive) as tf:
            for info in tf:
                if info.isfile():
                    yield Member(info.name.replace("\\", "/"), info.size, int(info.mtime * 1e9))

    def open(self, archive: str, inner: str) -> BinaryIO:
        tf = tarfile.open(archive)  # noqa: SIM115  (closed with the member stream)
        try:
            stream = tf.extractfile(inner)
        except KeyError:
            tf.close()
            raise NotFound(f"{inner!r} is not in {archive}") from None
        if stream is None:
            tf.close()
            raise NotFound(f"{inner!r} in {archive} is not a file")
        # a compressed tar member seeks by re-reading from the start: buffer it once
        if archive.lower().endswith((".gz", ".tgz", ".bz2", ".xz")):
            data = stream.read()
            tf.close()
            return io.BytesIO(data)
        return _Owned(stream, tf)


def _Owned(stream: BinaryIO, archive: zipfile.ZipFile | tarfile.TarFile) -> BinaryIO:
    """The member stream, closing its archive with it. ``ZipExtFile`` and tar's
    ``ExFileObject`` are seekable, so Pillow can read them in place."""
    inner_close = stream.close

    def close() -> None:
        inner_close()
        archive.close()

    stream.close = close  # type: ignore[method-assign]
    return stream


def _zip_mtime(info: zipfile.ZipInfo) -> int:
    import calendar

    y, mo, d, h, mi, s = info.date_time
    try:
        return int(calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) * 1_000_000_000)
    except (OverflowError, ValueError):
        return 0


_readers: list[ContainerReader] = [ZipReader(), TarReader()]


def register_container_reader(reader: ContainerReader) -> None:
    """Add a reader (M4: MCAP, rosbag). Later registrations win for shared extensions."""
    _readers.insert(0, reader)


def container_readers() -> list[ContainerReader]:
    return list(_readers)


def container_extensions() -> list[str]:
    out: list[str] = []
    for r in _readers:
        out.extend(e for e in r.extensions if e not in out)
    return out


def reader_for(archive: str) -> ContainerReader | None:
    name = PurePath(archive).name.lower()
    for r in _readers:
        if any(name.endswith(e) for e in r.extensions):
            return r
    return None


def is_container(path: str) -> bool:
    """Does ``path`` name an archive a registered reader can expand?"""
    return reader_for(path) is not None


def split_virtual(path: str) -> tuple[str, str] | None:
    """``archive#/inner`` -> ``(archive, inner)``; None for a plain path."""
    text = str(path).replace("\\", "/")
    i = text.find(VIRTUAL_SEP)
    if i <= 0:
        return None
    return text[:i], text[i + len(VIRTUAL_SEP) :]


def join_virtual(archive: str, inner: str) -> str:
    return f"{archive}{VIRTUAL_SEP}{inner.lstrip('/')}"


def is_virtual(path: str) -> bool:
    return split_virtual(path) is not None


def real_path(path: str) -> str:
    """The on-disk file behind a path: the archive of a virtual path, else itself."""
    hit = split_virtual(path)
    return hit[0] if hit else str(path)


def list_members(archive: str) -> list[Member]:
    r = reader_for(archive)
    if r is None:
        raise NotFound(f"no container reader for {archive}", fix=f"known: {container_extensions()}")
    return list(r.members(archive))


def open_member(path: str) -> BinaryIO:
    """A binary stream over the member a virtual path names (caller closes it)."""
    hit = split_virtual(path)
    if hit is None:
        raise NotFound(f"{path} is not a virtual path (archive#/member)")
    archive, inner = hit
    r = reader_for(archive)
    if r is None:
        raise NotFound(f"no container reader for {archive}", fix=f"known: {container_extensions()}")
    return r.open(archive, inner)


def open_source(path: str) -> BinaryIO:
    """``open(path, "rb")`` for a plain path, the member stream for a virtual one."""
    if is_virtual(path):
        return open_member(path)
    return open(path, "rb")


def stat_virtual(path: str) -> os.stat_result:
    """``os.stat`` of the file behind a path (the archive for a member)."""
    return os.stat(real_path(path))


__all__ = [
    "VIRTUAL_SEP",
    "ContainerReader",
    "Member",
    "TarReader",
    "ZipReader",
    "container_extensions",
    "container_readers",
    "is_container",
    "is_virtual",
    "join_virtual",
    "list_members",
    "open_member",
    "open_source",
    "reader_for",
    "real_path",
    "register_container_reader",
    "split_virtual",
    "stat_virtual",
]
