"""The identicon grid (#79, #81, ADR 0018; glossary: identicon): a GitHub-style 5x5
horizontally mirrored grid generated from a hash of a view's name. The grid comes from
the name alone, so a view's shape is stable across canvases and themes; the app colours
it by the view's link chain (``specto_app.canvas.identicon``). Qt-free.

Fifteen bits fill the left three columns, rows top to bottom, and the right two columns
mirror the left two. The 15-bit windows of the name's SHA-256 are tried in order until
one lights between ``MIN_LIT`` and ``MAX_LIT`` of the independent cells (review of #79:
sparse grids read as pale blobs at port size, near-solid ones as the same block), so
every identicon has a shape. ``identicon_distance`` is the Hamming distance between two
names' grids; ``names.fresh_name`` keeps new names at least ``MIN_DISTANCE`` from the
names already on the canvas so no two views wear near twins.
"""

from __future__ import annotations

import hashlib

GRID = 5
HALF = (GRID + 1) // 2  # the independent columns: 3 of 5
BITS = GRID * HALF  # 15
#: lit independent cells, inclusive: at least a third, at most two thirds
MIN_LIT, MAX_LIT = 5, 10
#: the least Hamming distance between two names' grids on one canvas
MIN_DISTANCE = 4
_MASK = (1 << BITS) - 1


def _popcount(bits: int) -> int:
    return bin(bits).count("1")


def identicon_bits(name: str) -> int:
    """The grid as 15 bits: bit ``r * 3 + c`` is row ``r``, independent column ``c``."""
    digest = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest(), "big")
    first = digest & _MASK
    for shift in range(0, 256 - BITS + 1, BITS):
        window = (digest >> shift) & _MASK
        if MIN_LIT <= _popcount(window) <= MAX_LIT:
            return window
    # no window in the band (odds about 1e-16): pull the first one into it
    bits = first
    while _popcount(bits) < MIN_LIT:
        bits |= (bits + 1) & ~bits  # the lowest clear bit
    while _popcount(bits) > MAX_LIT:
        bits &= bits - 1  # the lowest set bit
    return bits


def identicon_cells(name: str) -> list[list[bool]]:
    """The 5x5 grid for ``name``, ``cells[row][col]``; mirrored left-right."""
    bits = identicon_bits(name)
    cells: list[list[bool]] = []
    for r in range(GRID):
        left = [bool(bits >> (r * HALF + c) & 1) for c in range(HALF)]
        cells.append(left + left[: GRID - HALF][::-1])
    return cells


def identicon_distance(a: str, b: str) -> int:
    """How many independent cells differ between the two names' grids."""
    return _popcount(identicon_bits(a) ^ identicon_bits(b))


__all__ = [
    "BITS",
    "GRID",
    "MAX_LIT",
    "MIN_DISTANCE",
    "MIN_LIT",
    "identicon_bits",
    "identicon_cells",
    "identicon_distance",
]
