"""The identicon grid (#79, #81, ADR 0018): a 5x5 horizontally mirrored grid from a hash
of the view's name, never too sparse or too full to read at port size, and far enough
from the other names on the canvas that no two views wear near twins."""

from __future__ import annotations

from specto_core.identicon import (
    BITS,
    GRID,
    MAX_LIT,
    MIN_DISTANCE,
    MIN_LIT,
    identicon_bits,
    identicon_cells,
    identicon_distance,
)
from specto_core.names import all_names, fresh_name, fresh_names


def test_cells_are_a_deterministic_mirrored_5x5_grid():
    cells = identicon_cells("kobi")
    assert len(cells) == GRID and all(len(row) == GRID for row in cells)
    assert all(row[c] == row[GRID - 1 - c] for row in cells for c in range(GRID))
    assert identicon_cells("kobi") == cells
    assert identicon_cells("tamu") != cells and identicon_cells("Kobi") != cells
    # the cells are the bits: row r, independent column c is bit r * 3 + c
    bits = identicon_bits("kobi")
    assert all(cells[r][c] == bool(bits >> (r * 3 + c) & 1) for r in range(GRID) for c in range(3))


def test_every_name_in_the_pool_lights_between_min_and_max_of_its_independent_cells():
    assert (MIN_LIT, MAX_LIT) == (5, 10) and BITS == 15
    for name in [*all_names(), "files", "project"]:
        lit = bin(identicon_bits(name)).count("1")
        assert MIN_LIT <= lit <= MAX_LIT, (name, lit)


def test_distance_counts_the_independent_cells_that_differ():
    assert identicon_distance("kobi", "kobi") == 0
    a, b = identicon_bits("kobi"), identicon_bits("tamu")
    assert identicon_distance("kobi", "tamu") == bin(a ^ b).count("1") > 0


def test_fresh_names_keep_their_identicons_apart_on_one_canvas():
    assert MIN_DISTANCE == 4
    for salt in range(40):
        taken = {"files"}
        names = fresh_names(taken, 10, f"canvas{salt}")
        on_canvas = sorted(taken | set(names))
        for i, a in enumerate(on_canvas):
            for b in on_canvas[i + 1 :]:
                assert identicon_distance(a, b) >= MIN_DISTANCE, (salt, a, b)


def test_a_nearly_exhausted_pool_still_hands_out_a_name():
    """Every shape has near neighbours once thousands of names are taken: the distance
    rule gives way rather than refusing a name."""
    pool = all_names()
    left = {pool[7], pool[700], pool[4200]}
    assert fresh_name(set(pool) - left, "x") in left
