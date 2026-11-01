"""Gallery thumbnails for every kind (#105), at the view-host seam: a collection arrives on
the gallery's ``data`` port and every tile gets a picture the native item can draw (an
image, a video's poster frame, a pdf's first page, an excerpt card for text, a kind icon
for 3D). Thumbnails are cached per file and stat, in memory and under the project's
``.specto/cache``; a changed file gets a new one.

Fixtures are generated here (Pillow images and pdfs, ffmpeg clips), never downloaded."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from gallery_helpers import FakeGallery
from PIL import Image

from specto_app.hosts import HostContext
from specto_app.theme import COLORS
from specto_app.views.gallery import GalleryHost
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager
from specto_core.kinds import Item
from specto_core.links import Collection

TEXTS = {
    "notes.md": "# Findings\n\nThe front camera saw **parked cars** on the north loop.\n\n"
    "- pose drift under 4 cm\n- exposure stable\n",
    "pose.json": json.dumps({"frame": 12, "pose": {"t": [1.0, 2.0, 0.5]}, "tags": ["front"]}),
    "report.html": "<html><head><title>Coverage</title></head><body><h1>Coverage</h1>"
    "<p>Most of the route.</p></body></html>",
}


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")[-6:]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def write_fixtures(root: Path) -> list[str]:
    """One file per kind the gallery draws without ffmpeg; returns their names."""
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), (40, 160, 60)).save(root / "frame.jpg")
    page = Image.new("RGB", (200, 300), (220, 30, 30))
    page.paste((30, 30, 220), (0, 150, 200, 300))
    page.save(root / "doc.pdf", "PDF", resolution=72)
    for name, text in TEXTS.items():
        (root / name).write_text(text, encoding="utf-8")
    (root / "model.glb").write_bytes(b"glTF\x02\x00\x00\x00" + bytes(64))
    return ["frame.jpg", "doc.pdf", *TEXTS, "model.glb"]


def collection_of(root: Path, names: list[str]) -> Collection:
    """One single-part item per file, keyed by its stem, with the stat the intaker records."""
    items = []
    for name in names:
        p = root / name
        st = p.stat()
        role = p.suffix
        items.append(
            Item(
                key=p.stem,
                parts={role: str(p)},
                meta={"rel": {role: name}, "stat": {role: [st.st_size, st.st_mtime_ns]}},
            )
        )
    return Collection(items=items, root=str(root))


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.init(tmp_path / "proj")


def open_gallery(project: Project) -> tuple[GalleryHost, FakeGallery]:
    """A gallery host in a fresh engine over ``project`` (a reopened project: nothing
    in memory but what is on disk)."""
    engine = Engine(project=project, jobs=InlineJobManager())
    ctx = HostContext(engine, None)
    fake = FakeGallery()
    return GalleryHost(ctx, "kobi", ctx.manifest_for("gallery"), gallery=fake), fake


def pixel(fake: FakeGallery, i: int, fx: float, fy: float) -> tuple[int, int, int]:
    w, h, _ = fake.pushed[i]
    x, y = min(w - 1, int(fx * w)), min(h - 1, int(fy * h))
    o = (y * w + x) * 4
    return tuple(fake.pixels[i][o : o + 3])


def colours(fake: FakeGallery, i: int) -> Counter:
    px = fake.pixels[i]
    return Counter(px[o : o + 3] for o in range(0, len(px), 4))


def close(a: tuple[int, ...], b: tuple[int, ...], tol: int = 40) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def test_every_kind_gets_a_thumbnail(project: Project):
    names = write_fixtures(project.root / "data")
    host, fake = open_gallery(project)

    host.receive("data", collection_of(project.root / "data", names))
    host.pump()

    assert fake.failed_idx == set()
    assert sorted(fake.pushed) == list(range(len(names)))
    assert host.ctx.health_of("kobi")["status"] == "ok"
    at = {k: host.index_of(k) for k in ("frame", "doc", "notes", "pose", "report", "model")}

    assert close(pixel(fake, at["frame"], 0.5, 0.5), (40, 160, 60))
    # the pdf's first page, upright, at the page's own (portrait) aspect
    w, h, _ = fake.pushed[at["doc"]]
    assert h > w
    assert close(pixel(fake, at["doc"], 0.5, 0.1), (220, 30, 30))
    assert close(pixel(fake, at["doc"], 0.5, 0.9), (30, 30, 220))
    # text kinds: a landscape card in the theme's surface colour with dark text on it
    surface = hex_rgb(COLORS["surface"])
    cards = {}
    for key in ("notes", "pose", "report", "model"):
        i = at[key]
        w, h, _ = fake.pushed[i]
        assert w > h, key
        seen = colours(fake, i)
        assert close(tuple(seen.most_common(1)[0][0]), surface, 8), key
        cards[key] = fake.pixels[i]
    for key in ("notes", "pose", "report"):
        dark = sum(n for c, n in colours(fake, at[key]).items() if sum(c) < 3 * 110)
        assert dark > 40, f"{key}: no text on the card"
    assert len(set(cards.values())) == 4  # an excerpt of each file, a kind icon for 3D


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg to make the clip")
def test_a_video_tile_shows_its_poster_frame_the_right_way_up(project: Project):
    from specto_app import thumbs

    if thumbs.libmpv() is None:
        pytest.skip("no libmpv (build the native plugin)")
    clip = project.root / "data" / "redblue.mp4"
    clip.parent.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=red:s=96x32:d=2:r=10",
         "-f", "lavfi", "-i", "color=blue:s=96x32:d=2:r=10",
         "-filter_complex", "vstack", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    host, fake = open_gallery(project)

    host.receive("data", collection_of(clip.parent, [clip.name]))
    host.pump()

    assert fake.failed_idx == set()
    w, h, _ = fake.pushed[0]
    assert (w, h) == (96, 64)  # the clip's own size: like an image, never upscaled
    assert close(pixel(fake, 0, 0.5, 0.1), (255, 0, 0), 60)
    assert close(pixel(fake, 0, 0.5, 0.9), (0, 0, 255), 60)


def test_a_second_gallery_and_a_reopened_project_reuse_the_thumbnails(project: Project):
    names = write_fixtures(project.root / "data")
    col = collection_of(project.root / "data", names)
    others = len(names) - 1  # the image decodes every time; the rest are cached
    host, fake = open_gallery(project)
    host.receive("data", col)
    host.pump()
    assert host.snapshot()["decode"]["thumbs"] == {"rendered": others, "disk": 0, "memory": 0}
    first = dict(fake.pixels)

    # a second gallery in the same session: from memory, no job
    ctx = host.ctx
    again = FakeGallery()
    second = GalleryHost(ctx, "lami", ctx.manifest_for("gallery"), gallery=again)
    second.receive("data", col)
    second.pump()
    assert second.snapshot()["decode"]["thumbs"] == {"rendered": 0, "disk": 0, "memory": others}
    assert again.pixels == first

    # the project reopened: from the PNGs under .specto/cache
    assert len(list((project.root / ".specto" / "cache" / "thumbs").rglob("*.png"))) == others
    reopened, fake3 = open_gallery(project)
    reopened.receive("data", col)
    reopened.pump()
    assert reopened.snapshot()["decode"]["thumbs"] == {"rendered": 0, "disk": others, "memory": 0}
    assert fake3.pixels == first


def test_a_changed_file_gets_a_new_thumbnail(project: Project):
    names = write_fixtures(project.root / "data")
    host, fake = open_gallery(project)
    host.receive("data", collection_of(project.root / "data", names))
    host.pump()
    notes = host.index_of("notes")
    before = fake.pixels[notes]

    md = project.root / "data" / "notes.md"
    md.write_text("# Retake\n\nEverything changed, the whole run is new.\n", encoding="utf-8")
    st = md.stat()
    os.utime(md, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    # the intaker hands over the same keys with the file's new stat
    host.receive("data", collection_of(project.root / "data", names))
    host.pump()

    assert fake.pixels[notes] != before
    counts = host.snapshot()["decode"]["thumbs"]
    assert counts["rendered"] == 1 and counts["memory"] == len(names) - 2
    # and a reopened project does not show the old picture from disk either
    reopened, fake2 = open_gallery(project)
    reopened.receive("data", collection_of(project.root / "data", names))
    reopened.pump()
    assert fake2.pixels[reopened.index_of("notes")] == fake.pixels[notes]
