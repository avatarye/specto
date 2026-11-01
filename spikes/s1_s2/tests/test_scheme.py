"""Unit tests for the pure parts of the specto:// scheme handler (no Qt needed)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scheme import ByteRange, content_type, parse_range, resolve, slice_for_range


@pytest.mark.parametrize(
    ("header", "total", "expected"),
    [
        (None, 100, None),
        (b"", 100, None),
        (b"bytes=0-9", 100, ByteRange(0, 9, 100)),
        (b"bytes=10-", 100, ByteRange(10, 99, 100)),
        (b"bytes=-16", 100, ByteRange(84, 99, 100)),
        (b"bytes=-200", 100, ByteRange(0, 99, 100)),
        (b"bytes=90-500", 100, ByteRange(90, 99, 100)),
        (b"bytes=100-", 100, None),  # start past the end: unsatisfiable
        (b"bytes=5-4", 100, None),
        (b"bytes=-0", 100, None),
        (b"bytes=0-9,20-29", 100, None),  # multi-range ignored: full body
        (b"items=0-9", 100, None),
        (b"bytes=0-9", 0, None),
    ],
)
def test_parse_range(header, total, expected):
    assert parse_range(header, total) == expected


def test_content_range_and_length():
    r = ByteRange(1048576, 2097151, 33554432)
    assert r.length == 1048576
    assert r.content_range() == b"bytes 1048576-2097151/33554432"


def test_slice_for_range(tmp_path: Path):
    p = tmp_path / "blob.bin"
    p.write_bytes(bytes(range(256)) * 4)
    assert slice_for_range(p, ByteRange(254, 258, 1024)) == bytes([254, 255, 0, 1, 2])


def test_resolve_blocks_traversal(tmp_path: Path):
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("x")
    (tmp_path / "secret.txt").write_text("s")
    assert resolve(root, "/") == (root / "index.html").resolve()
    assert resolve(root, "/index.html") == (root / "index.html").resolve()
    assert resolve(root, "/../secret.txt") is None
    assert resolve(root, "/missing.js") is None


def test_content_type():
    assert content_type(Path("a.js")) == b"text/javascript"
    assert content_type(Path("a.html")) == b"text/html"
    assert content_type(Path("blob.bin")) == b"application/octet-stream"
