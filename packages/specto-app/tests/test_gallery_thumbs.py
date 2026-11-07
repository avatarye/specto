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


# -- SPEC-17: the cache follow-ups --------------------------------------------------------


def cached_pngs(project: Project) -> list[Path]:
    return sorted((project.root / ".specto" / "cache" / "thumbs").rglob("*.png"))


def test_thumbnails_of_deleted_or_changed_files_are_pruned(project: Project):
    from specto_app import thumbs

    names = write_fixtures(project.root / "data")
    host, _ = open_gallery(project)
    host.receive("data", collection_of(project.root / "data", names))
    host.pump()
    assert len(cached_pngs(project)) == len(names) - 1  # every kind but the small image
    (project.root / "data" / "notes.md").unlink()
    pose = project.root / "data" / "pose.json"
    pose.write_text('{"frame": 13}', encoding="utf-8")

    kept, removed = thumbs.prune_cache(thumbs.cache_dir_for(project.root), project.root)

    assert (kept, removed) == (len(names) - 3, 2)
    sources = {cached_source(p) for p in cached_pngs(project)}
    assert str(project.root / "data" / "notes.md") not in sources
    assert str(pose) not in sources


def test_a_source_outside_the_project_on_a_missing_folder_is_kept(project: Project, tmp_path):
    """A drive that is not plugged in is no deleted file: its thumbnails stay."""
    from specto_app import thumbs

    elsewhere = tmp_path / "usb" / "data"
    write_fixtures(elsewhere)
    host, _ = open_gallery(project)
    host.receive("data", collection_of(elsewhere, ["notes.md"]))
    host.pump()
    shutil.rmtree(tmp_path / "usb")

    kept, removed = thumbs.prune_cache(thumbs.cache_dir_for(project.root), project.root)

    assert (kept, removed) == (1, 0)


def test_opening_a_gallery_prunes_the_cache_once_a_day(project: Project):
    from specto_app import thumbs

    names = write_fixtures(project.root / "data")
    host, _ = open_gallery(project)
    host.receive("data", collection_of(project.root / "data", names))
    host.pump()
    (project.root / "data" / "notes.md").unlink()
    stamp = Path(thumbs.cache_dir_for(project.root)) / thumbs.PRUNE_STAMP
    stamp.touch()  # pruned two days ago
    old = stamp.stat().st_mtime - 2 * 86400
    os.utime(stamp, (old, old))
    thumbs._pruned.clear()

    open_gallery(project)  # the prune is a job: inline here

    assert len(cached_pngs(project)) == len(names) - 2
    assert stamp.stat().st_mtime > old + 86400
    (project.root / "data" / "report.html").unlink()
    open_gallery(project)  # pruned today already: nothing more
    assert len(cached_pngs(project)) == len(names) - 2


def test_a_long_code_line_wraps_on_the_card(project: Project):
    from specto_app import thumbs

    def inked_rows(text: str) -> int:
        path = project.root / "long.txt"
        path.write_text(text, encoding="utf-8")
        arr = thumbs.excerpt_card(str(path), "text", 256, thumbs.palette())
        return int(((arr[..., :3].astype(int).sum(axis=2) < 3 * 110).sum(axis=1) > 0).sum())

    short, long = inked_rows("x" * 10), inked_rows("x" * 400)
    assert long > 2 * short, "the line stopped at the card's edge instead of wrapping"


def test_wrapped_code_keeps_every_character_and_its_indent(project: Project):
    from PIL import Image, ImageDraw

    from specto_app import thumbs

    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    font = thumbs._font("mono", 12)
    line = '    "path": "' + "/very/long/segment" * 12 + '"'
    rows = thumbs._wrap_code(d, line, font, 200.0)
    assert len(rows) > 2
    assert all(d.textlength(r, font=font) <= 200.0 for r in rows)
    assert all(r.startswith("    ") for r in rows)  # continuations keep the indent
    assert "".join(r[4:] for r in rows) == line[4:]


def big_png(path: Path, w: int = 2400, h: int = 1600) -> None:
    import numpy as np

    rng = np.random.default_rng(1)
    small = (rng.random((h // 8, w // 8, 3)) * 255).astype("uint8")
    Image.fromarray(small).resize((w, h)).save(path)


def test_big_images_are_cached_on_disk_and_small_ones_are_not(project: Project):
    data = project.root / "data"
    names = write_fixtures(data)  # frame.jpg: 64 x 48, cheap to decode again
    big_png(data / "big.png")
    names.append("big.png")
    host, fake = open_gallery(project)
    host.receive("data", collection_of(data, names))
    host.pump()
    assert len(cached_pngs(project)) == len(names) - 1  # the small image only is not
    first = fake.pixels[host.index_of("big")]

    reopened, fake2 = open_gallery(project)
    reopened.receive("data", collection_of(data, names))
    reopened.pump()

    counts = reopened.snapshot()["decode"]["thumbs"]
    assert counts["disk"] == len(names) - 1 and counts["rendered"] == 0
    assert fake2.pixels[reopened.index_of("big")] == first


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg to make the clip")
def test_a_video_inside_an_archive_shows_its_poster_frame(project: Project):
    import zipfile

    from specto_app import thumbs
    from specto_core.containers import join_virtual

    if thumbs.libmpv() is None:
        pytest.skip("no libmpv (build the native plugin)")
    clip = project.root / "redblue.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=red:s=96x32:d=2:r=10",
         "-f", "lavfi", "-i", "color=blue:s=96x32:d=2:r=10",
         "-filter_complex", "vstack", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    archive = project.root / "data" / "clips.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as z:
        z.write(clip, "takes/redblue.mp4")
    clip.unlink()
    member = join_virtual(str(archive), "takes/redblue.mp4")
    st = archive.stat()
    col = Collection(
        items=[
            Item(
                key="redblue",
                parts={".mp4": member},
                meta={"stat": {".mp4": [st.st_size, st.st_mtime_ns]}},
            )
        ],
        root=str(archive.parent),
    )
    host, fake = open_gallery(project)

    host.receive("data", col)
    host.pump()

    assert fake.failed_idx == set()
    assert fake.pushed[0][:2] == (96, 64)  # a poster, not a 3:2 kind card
    assert close(pixel(fake, 0, 0.5, 0.1), (255, 0, 0), 60)
    assert close(pixel(fake, 0, 0.5, 0.9), (0, 0, 255), 60)
    cache = Path(thumbs.cache_dir_for(project.root))
    assert not list(cache.rglob("*.tmp"))  # the copy out of the archive is gone


# -- SPEC-17 review follow-ups ---------------------------------------------------------------


def cached_source(path: Path) -> str | None:
    """The source path a cached PNG was made of (None when it cannot be read)."""
    try:
        with Image.open(path) as im:
            return getattr(im, "text", {}).get("specto-source")
    except (OSError, ValueError):
        return None


def test_pruning_touches_only_the_caches_own_files(project: Project):
    from specto_app import thumbs

    names = write_fixtures(project.root / "data")
    host, _ = open_gallery(project)
    host.receive("data", collection_of(project.root / "data", names))
    host.pump()
    cache = Path(thumbs.cache_dir_for(project.root))
    ours = cached_pngs(project)
    sub = ours[0].parent
    # foreign files, in the cache folder and next to ours: a PNG of someone else's (no
    # specto-source), one named like ours but without a source, any .tmp, a stray PNG
    Image.new("RGB", (4, 4)).save(sub / "holiday.png")
    Image.new("RGB", (4, 4)).save(sub / f"{sub.name}{'0' * 38}.png")
    (sub / "notes.tmp").write_text("keep", encoding="utf-8")
    Image.new("RGB", (4, 4)).save(cache / "stray.png")
    # a killed writer's leftover, two hours old: ours by name, so it goes
    leftover = sub / f"{sub.name}{'1' * 38}.4242.tmp"
    leftover.write_bytes(b"half")
    old = leftover.stat().st_mtime - 7200
    os.utime(leftover, (old, old))
    (project.root / "data" / "notes.md").unlink()

    kept, removed = thumbs.prune_cache(str(cache), project.root)

    assert removed == 1 and not leftover.exists()
    assert kept == len(ours)  # ours less the deleted file's, plus the sourceless look-alike
    for p in (sub / "holiday.png", sub / f"{sub.name}{'0' * 38}.png", sub / "notes.tmp"):
        assert p.exists(), p
    assert (cache / "stray.png").exists()


def test_pruning_never_follows_a_junction(project: Project, tmp_path: Path):
    import subprocess

    from specto_app import thumbs

    if os.name != "nt":
        pytest.skip("a Windows junction")
    elsewhere = tmp_path / "elsewhere" / "ab"
    elsewhere.mkdir(parents=True)
    victim = elsewhere / f"ab{'2' * 38}.png"
    Image.new("RGB", (4, 4)).save(victim, pnginfo=_png_info(str(tmp_path / "gone.md")))
    cache = Path(thumbs.cache_dir_for(project.root))
    cache.mkdir(parents=True, exist_ok=True)
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(cache / "ab"), str(elsewhere)], capture_output=True
    )
    if made.returncode != 0:
        pytest.skip("no junction")

    thumbs.prune_cache(str(cache), project.root)

    assert victim.exists()
    os.rmdir(cache / "ab")  # the junction alone


def _png_info(source: str):
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("specto-stat", "1:1")
    info.add_text("specto-source", source)
    return info


def test_a_corrupt_cached_thumbnail_is_rendered_again(project: Project):
    names = write_fixtures(project.root / "data")
    col = collection_of(project.root / "data", names)
    host, fake = open_gallery(project)
    host.receive("data", col)
    host.pump()
    first = dict(fake.pixels)
    for p in cached_pngs(project):
        p.write_bytes(b"\x89PNG not really")

    reopened, fake2 = open_gallery(project)
    reopened.receive("data", col)
    reopened.pump()

    counts = reopened.snapshot()["decode"]["thumbs"]
    assert counts["rendered"] == len(names) - 1 and counts["disk"] == 0
    assert fake2.pixels == first


def test_an_image_cache_key_follows_the_decoder(project: Project):
    from specto_app import thumbs
    from specto_core.kinds import DecodeRequest

    plain = thumbs.image_look(DecodeRequest(purpose="thumbnail", max_size=176))
    raw = thumbs.image_look(
        DecodeRequest(purpose="thumbnail", max_size=176, options={"exif_transpose": False})
    )
    assert plain != raw and plain["decoder"] == thumbs.IMAGE_DECODER_VERSION
    a = thumbs.cache_path("c", "x.png", 176, plain)
    assert a != thumbs.cache_path("c", "x.png", 176, raw)


def _zipped_clip(project: Project) -> Collection:
    import zipfile

    from specto_core.containers import join_virtual

    clip = project.root / "redblue.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=red:s=96x32:d=2:r=10",
         "-f", "lavfi", "-i", "color=blue:s=96x32:d=2:r=10",
         "-filter_complex", "vstack", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    archive = project.root / "data" / "clips.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as z:
        z.write(clip, "takes/redblue.mp4")
    clip.unlink()
    st = archive.stat()
    member = join_virtual(str(archive), "takes/redblue.mp4")
    return Collection(
        items=[
            Item(
                key="redblue",
                parts={".mp4": member},
                meta={"stat": {".mp4": [st.st_size, st.st_mtime_ns]}},
            )
        ],
        root=str(archive.parent),
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg to make the clip")
def test_an_archived_video_over_its_cap_gets_the_kind_card(project: Project, monkeypatch):
    from specto_app import thumbs

    if thumbs.libmpv() is None:
        pytest.skip("no libmpv (build the native plugin)")
    col = _zipped_clip(project)
    monkeypatch.setattr(thumbs, "ARCHIVE_VIDEO_MAX", 1024)  # the clip is bigger
    host, fake = open_gallery(project)

    host.receive("data", col)
    host.pump()

    w, h, _ = fake.pushed[0]
    assert w > h and (w, h) != (96, 64)  # a 3:2 card, no poster
    cache = Path(thumbs.cache_dir_for(project.root))
    assert not list(cache.rglob("*.tmp"))  # no copy was made, nothing left
