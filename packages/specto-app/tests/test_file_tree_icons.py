"""The file tree's per-type icons (#122), like VS Code's: each row carries the icon of
its file (by name, then extension, then Specto's data kind of the extension) or of its
folder (by name, open or closed). The icons are a vendored, pinned subset of Material
Icon Theme (MIT), only the ones the mapping names."""

from __future__ import annotations

from specto_app.views.file_icons import ICON_DIR, icon_for, icon_names
from specto_app.views.file_tree import FileTreeModel

MIXED = [
    "images/a.jpg",
    "notes/plan.md",
    "notes/README.md",
    "notes/data.json",
    "geo/route.geojson",
    "scenes/room.ply",
    "scenes/room.splat",
    "videos/clip.mp4",
    "papers/paper.pdf",
    "runs/cam-a/00001.jpg",
    "tools/tool.py",
    "site/page.html",
    "stats.csv",
    "backup.tar.gz",
    "pyproject.toml",
    "odd.xyz",
]


def test_rows_carry_the_icon_of_their_file_or_folder():
    model = FileTreeModel()
    model.set_paths(MIXED)
    model.expanded = {p for p, n in model._nodes.items() if n.is_dir}  # none open by default (#231)
    model._set_rows(model._walk())
    icons = {r["path"]: r["icon"] for r in model.rows()}
    assert icons["images"] == "folder-images-open"  # open
    assert icons["images/a.jpg"] == "image"
    assert icons["notes/plan.md"] == "markdown"
    assert icons["notes/README.md"] == "readme"  # by its name
    assert icons["notes/data.json"] == "json"
    assert icons["geo/route.geojson"] == "json"
    assert icons["scenes/room.ply"] == "3d"
    assert icons["scenes/room.splat"] == "3d"  # the theme has none: Specto's kind (splat)
    assert icons["videos/clip.mp4"] == "video"
    assert icons["papers/paper.pdf"] == "pdf"
    assert icons["tools/tool.py"] == "python"
    assert icons["site/page.html"] == "html"
    assert icons["stats.csv"] == "table"
    assert icons["backup.tar.gz"] == "zip"  # the longest extension first
    assert icons["pyproject.toml"] in ("python-misc", "toml_light")
    assert icons["odd.xyz"] == "file"
    assert icons["runs"] == "folder-open" and icons["runs/cam-a"] == "folder-open"

    model.toggle(model.row_of("images"))  # closed: the closed icon
    assert {r["path"]: r["icon"] for r in model.rows()}["images"] == "folder-images"


def test_names_ignore_case_and_unknown_folders_get_the_plain_folder():
    assert icon_for("Photo.JPG", is_dir=False) == "image"
    assert icon_for("Docs", is_dir=True) == "folder-docs"
    assert icon_for("Docs", is_dir=True, expanded=True) == "folder-docs-open"
    assert icon_for("run-001", is_dir=True) == "folder"
    assert icon_for("noext", is_dir=False) == "file"


def test_every_icon_the_mapping_names_is_vendored_with_its_licence_and_pin():
    names = icon_names()
    assert names, "no icons mapped"
    missing = [n for n in names if not (ICON_DIR / f"{n}.svg").is_file()]
    assert missing == []
    vendored = {p.stem for p in ICON_DIR.glob("*.svg")}
    assert vendored == set(names)  # only the icons the mapping names
    licence = (ICON_DIR / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in licence
    assert "material-icon-theme@" in (ICON_DIR / "README.md").read_text(encoding="utf-8")


def test_every_icon_renders_at_16_px_and_at_150_percent(qapp):
    """Each SVG is valid and draws something at 16 px and at 24 px (16 px at 150%)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    for n in icon_names():
        renderer = QSvgRenderer(str(ICON_DIR / f"{n}.svg"))
        assert renderer.isValid(), n
        for px in (16, 24):
            img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            painter = QPainter(img)
            renderer.render(painter)
            painter.end()
            inked = sum(img.pixelColor(x, y).alpha() > 128 for x in range(px) for y in range(px))
            assert inked > px * px // 10, (n, px, inked)
