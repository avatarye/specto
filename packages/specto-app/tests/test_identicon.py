"""Identicons (#79, #81, ADR 0018): a 5x5 horizontally mirrored grid from a hash of the
view's name, coloured by the view's link chain, served to QML as
``image://identicon/<name>?chain=<n>`` (``&round=1`` for the port circles). Every
identicon meets one contrast floor against its ground (review of #79)."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor

from specto_app.canvas.chains import N_CHAINS
from specto_app.canvas.identicon import (
    CONTRAST_FLOOR,
    GRID,
    PROVIDER_ID,
    IdenticonProvider,
    contrast_ratio,
    identicon_cells,
    identicon_colors,
    identicon_url,
    parse_image_id,
)
from specto_app.theme import COLORS


def test_cells_are_the_core_grid():
    from specto_core.identicon import identicon_cells as core_cells

    assert identicon_cells("kobi") == core_cells("kobi")
    assert all(row[c] == row[GRID - 1 - c] for row in identicon_cells("kobi") for c in range(GRID))


def test_the_url_and_its_parse_agree():
    assert identicon_url("kobi", 2) == f"image://{PROVIDER_ID}/kobi?chain=2"
    assert identicon_url("kobi", None) == f"image://{PROVIDER_ID}/kobi"
    assert identicon_url("kobi", 2, round=True) == f"image://{PROVIDER_ID}/kobi?chain=2&round=1"
    assert identicon_url("kobi", None, round=True) == f"image://{PROVIDER_ID}/kobi?round=1"
    assert parse_image_id("kobi?chain=2") == ("kobi", 2, False)
    assert parse_image_id("kobi") == ("kobi", None, False)
    assert parse_image_id("kobi?chain=x") == ("kobi", None, False)
    assert parse_image_id("kobi?chain=2&round=1") == ("kobi", 2, True)
    assert parse_image_id("kobi?round=1") == ("kobi", None, True)


def test_contrast_ratio_is_wcag():
    assert contrast_ratio("#000000", "#ffffff") == 21.0
    assert contrast_ratio("#ffffff", "#ffffff") == 1.0
    assert abs(contrast_ratio("#777777", "#ffffff") - 4.48) < 0.01


def test_every_chain_and_the_neutral_meet_the_contrast_floor_on_their_ground():
    assert CONTRAST_FLOOR == 3.0
    for chain in [None, *range(N_CHAINS)]:
        fg, bg = identicon_colors(chain)
        assert contrast_ratio(fg, bg) >= CONTRAST_FLOOR, (chain, fg, bg)
        # the ground is the theme's: the cell colour is what moves
        assert bg == (COLORS["surface_alt"] if chain is None else COLORS[f"chain_{chain}_soft"])
    # a chain colour that already clears the floor is kept as the theme has it
    fg, bg = identicon_colors(0)
    if contrast_ratio(COLORS["chain_0"], COLORS["chain_0_soft"]) >= CONTRAST_FLOOR:
        assert fg == COLORS["chain_0"]
    # the neutral token is pale on the alternate surface: it is darkened, same hue
    fg, bg = identicon_colors(None)
    assert fg != COLORS["chain_none"]
    assert abs(QColor(fg).hueF() - QColor(COLORS["chain_none"]).hueF()) < 0.02
    assert QColor(fg).lightnessF() < QColor(COLORS["chain_none"]).lightnessF()


def test_the_provider_paints_the_chain_colour_on_its_soft_tone(qapp):
    provider = IdenticonProvider()
    size = QSize()
    img = provider.requestImage("kobi?chain=2", size, QSize(40, 40))
    assert (img.width(), img.height()) == (40, 40) and (size.width(), size.height()) == (40, 40)
    cells = identicon_cells("kobi")
    px = 40 // GRID
    fg, bg = identicon_colors(2)
    assert bg == COLORS["chain_2_soft"]
    for r in range(GRID):
        for c in range(GRID):
            got = QColor(img.pixel(c * px + px // 2, r * px + px // 2)).name()
            assert got == (fg if cells[r][c] else bg)
    # chains wrap on the six theme tokens; no chain paints the neutral token
    assert provider.requestImage("kobi?chain=8", QSize(), QSize(40, 40)) == img
    neutral = provider.requestImage("kobi", QSize(), QSize(40, 40))
    middle = QColor(neutral.pixel(2 * px + px // 2, 2 * px + px // 2)).name()
    assert middle in identicon_colors(None)
    # the same request twice is the same image (cached)
    assert provider.requestImage("kobi?chain=2", QSize(), QSize(40, 40)) == img


def test_the_round_image_is_clipped_to_a_circle(qapp):
    provider = IdenticonProvider()
    img = provider.requestImage("kobi?chain=2&round=1", QSize(), QSize(40, 40))
    assert img.hasAlphaChannel()
    assert img.pixelColor(0, 0).alpha() == 0 and img.pixelColor(39, 39).alpha() == 0
    assert img.pixelColor(20, 20).alpha() == 255 and img.pixelColor(20, 2).alpha() == 255
    square = provider.requestImage("kobi?chain=2", QSize(), QSize(40, 40))
    assert img != square


def test_the_round_image_insets_the_whole_grid_inside_the_circle(qapp):
    """#92: the port circle's disc is the identicon's ground and the 5x5 grid sits
    inside it, smaller, so no corner cell is cut ("tamu" has all four corners on)."""
    cells = identicon_cells("tamu")
    assert cells[0][0] and cells[0][-1] and cells[-1][0] and cells[-1][-1]
    fg, bg = identicon_colors(1)
    provider = IdenticonProvider()
    for px in (17, 26, 34):  # the inner disc at 100%, 150% and 200% scaling
        img = provider.requestImage("tamu?chain=1&round=1", QSize(), QSize(px, px))
        assert (img.width(), img.height()) == (px, px)
        ink = [
            (x, y)
            for y in range(px)
            for x in range(px)
            if img.pixelColor(x, y).alpha() == 255 and img.pixelColor(x, y).name() == fg
        ]
        xs, ys = [x for x, _ in ink], [y for _, y in ink]
        side = max(xs) - min(xs) + 1
        assert side == max(ys) - min(ys) + 1 and side % GRID == 0, px
        cell = side // GRID
        assert cell >= 2, px  # crisp cells, never single pixels
        # every on cell is whole (none clipped by the circle) ...
        assert len(ink) == sum(map(sum, cells)) * cell * cell, px
        # ... and the grid's corners sit inside the disc, clear of its antialiased edge
        r = px / 2
        for x, y in ((min(xs), min(ys)), (max(xs) + 1, max(ys) + 1)):
            assert ((x - r) ** 2 + (y - r) ** 2) ** 0.5 <= r - 0.5, px
        # the disc around the grid is the identicon's ground
        assert img.pixelColor(px // 2, 1).name() == bg and img.pixelColor(1, px // 2).name() == bg


def test_install_qml_registers_the_provider(qapp):
    from PySide6.QtQml import QQmlEngine

    from specto_app.qt_setup import install_qml

    engine = QQmlEngine()
    install_qml(engine)
    assert engine.imageProvider(PROVIDER_ID) is not None
    engine.deleteLater()
