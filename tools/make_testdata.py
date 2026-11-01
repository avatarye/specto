"""Generate Specto's small, git-ignored test dataset from nothing.

Every byte is procedural: no source folder, no environment variable, no network. The only
tools are the venv (numpy, Pillow) and ``ffmpeg`` + ``ffprobe`` on PATH for the video.
The output is deterministic: the same files on every run on the same ffmpeg build.

    uv run python tools/make_testdata.py [--out DIR]       # default <repo>/testdata

What it writes (``testdata/README.md`` repeats this for readers of the folder):

- ``runs/run-001/cam-a/NNNNN_<stamp_ns>.jpg`` + same-name ``.json`` sidecar: 240 frames of
  a disc and a square moving over a drifting gradient, 32 Hz, every frame its own stamp.
- ``runs/run-001/cam-b/``: 240 frames of the same scene from a second palette at 16 Hz,
  starting 50 ms after cam-a, so the two series never share a stamp and run at two
  rates. Both series share the 5-digit frame key.
- ``runs/run-001/video/cam-a.mp4``: H.264 of the cam-a frames at 32 fps (one video frame
  per image, pts equal to the stamp offset) with a quiet 220 Hz tone that beeps once a
  second; ``cam-a.timeline.json`` maps frame index -> key, stamp_ns and container pts.
- ``runs/run-001/meta/``: per-frame CSVs for both series, a 10 ms signal series, an
  event list, ``settings.yaml`` and ``summary.md``.
- ``geo/``: thirteen sites around (0, 0) as points, their close pairs as lines, a
  coverage polygon, all three together, and the run's path as a LineString.
- ``notes/``: five markdown notes (headings, lists, tables, code, Mermaid, KaTeX,
  callouts, wikilinks, a relative image under ``img/``).
- ``scans/terrain.ply``: a binary point cloud; ``models/knot.glb``: a torus-knot mesh.
- ``archives/``: the notes as a ``.zip`` and the first cam-b frames as a ``.tar.gz``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

RUN = "run-001"
N = 240  # frames per series
W, H = 640, 480
T0_NS = 1_700_000_000_000_000_000  # the run's first stamp (ns)
A_PERIOD_NS = 31_250_000  # cam-a: 1/32 s between frames
B_PERIOD_NS = 62_500_000  # cam-b: 1/16 s between frames
B_OFFSET_NS = 50_000_000  # cam-b starts 50 ms after cam-a
VIDEO_FPS = 32
ARCHIVE_TIME = (2024, 1, 1, 0, 0, 0)  # fixed archive member times, for identical bytes
GENERATOR = "tools/make_testdata.py"


# -- the scene -------------------------------------------------------------------------------
def scene_at(t: float) -> dict:
    """Where the disc and the square are ``t`` seconds into the run (a Lissajous path for
    the disc, a slower circle for the square) and the background hue, in degrees."""
    return {
        "disc": {
            "x": round(0.5 + 0.32 * math.sin(2 * math.pi * 0.21 * t + 0.6), 4),
            "y": round(0.5 + 0.28 * math.sin(2 * math.pi * 0.34 * t), 4),
            "r": round(0.06 + 0.02 * math.sin(2 * math.pi * 0.5 * t), 4),
        },
        "square": {
            "x": round(0.5 + 0.38 * math.cos(2 * math.pi * 0.11 * t), 4),
            "y": round(0.5 + 0.34 * math.sin(2 * math.pi * 0.11 * t), 4),
            "size": 0.08,
            "angle_deg": round((t * 45.0) % 360.0, 2),
        },
        "hue_deg": round((t * 12.0) % 360.0, 2),
    }


def hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
    import colorsys

    r, g, b = colorsys.hsv_to_rgb((h % 360.0) / 360.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


_FONT: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def font(size: int):
    if size not in _FONT:
        try:
            _FONT[size] = ImageFont.load_default(size=size)
        except TypeError:  # an old Pillow: the bitmap default
            _FONT[size] = ImageFont.load_default()
    return _FONT[size]


def render_frame(series: str, key: str, t: float, palette: int, rng: np.random.Generator) -> Image.Image:
    """One 640x480 frame: a diagonal gradient (hue drifting with time), a filled disc, a
    rotated square, light noise so the JPEGs have texture, and the series and key as a
    label. ``palette`` shifts the colours so cam-a and cam-b look different."""
    s = scene_at(t)
    hue = s["hue_deg"] + 140.0 * palette
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    g = (x / W * 0.6 + y / H * 0.4)[..., None]
    c0 = np.array(hsv(hue, 0.55, 0.35), np.float32)
    c1 = np.array(hsv(hue + 60, 0.45, 0.85), np.float32)
    img = c0 * (1 - g) + c1 * g
    img += rng.normal(0.0, 6.0, img.shape).astype(np.float32)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(im)
    d = s["disc"]
    cx, cy, r = d["x"] * W, d["y"] * H, d["r"] * W
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hsv(hue + 180, 0.8, 0.95), outline=(20, 20, 20), width=3)
    q = s["square"]
    half = q["size"] * W / 2
    a = math.radians(q["angle_deg"])
    pts = []
    for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        px = dx * half * math.cos(a) - dy * half * math.sin(a)
        py = dx * half * math.sin(a) + dy * half * math.cos(a)
        pts.append((q["x"] * W + px, q["y"] * H + py))
    draw.polygon(pts, fill=hsv(hue + 300, 0.7, 0.9), outline=(250, 250, 250))
    draw.rectangle([0, H - 40, 250, H], fill=(0, 0, 0))
    draw.text((8, H - 36), f"{series}  {key}", fill=(255, 255, 255), font=font(26))
    draw.text((W - 150, 8), f"t+{t:06.3f}s", fill=(255, 255, 255), font=font(20))
    return im


def frame_stamps(series: str) -> list[int]:
    if series == "cam-a":
        return [T0_NS + i * A_PERIOD_NS for i in range(N)]
    return [T0_NS + B_OFFSET_NS + i * B_PERIOD_NS for i in range(N)]


def write_frames(out: Path, series: str, palette: int) -> list[tuple[str, int, dict]]:
    """The jpg + json pairs of one series; returns ``(key, stamp_ns, scene)`` per frame."""
    dst = out / "runs" / RUN / series
    dst.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1 + palette)
    rows = []
    for i, stamp in enumerate(frame_stamps(series)):
        key = f"{i:05d}"
        t = (stamp - T0_NS) / 1e9
        im = render_frame(series, key, t, palette, rng)
        im.save(dst / f"{key}_{stamp}.jpg", "JPEG", quality=85, optimize=True)
        scene = scene_at(t)
        side = {
            "frame": i,
            "key": key,
            "series": series,
            "run": RUN,
            "stamp_ns": stamp,
            "t_s": round(t, 6),
            "shape": scene,
        }
        (dst / f"{key}_{stamp}.json").write_text(json.dumps(side, indent=2) + "\n", encoding="utf-8")
        rows.append((key, stamp, scene))
    print(f"[OK] {series}: {N} frames")
    return rows


# -- video ----------------------------------------------------------------------------------
def encode_video(out: Path, series: str = "cam-a") -> None:
    """H.264 of the series' frames at a constant 32 fps (cam-a's own rate, so every
    video frame's pts is its image's stamp offset), with an audio track: a quiet 220 Hz tone
    that beeps once a second (ffmpeg's ``sine`` with ``beep_factor``), so sound against
    the linked time is audible. The sidecar maps frame index -> key, stamp_ns and pts."""
    src = out / "runs" / RUN / series
    frames = sorted(src.glob("*.jpg"))
    stamps = [int(p.stem.split("_")[1]) for p in frames]
    vdir = out / "runs" / RUN / "video"
    vdir.mkdir(parents=True, exist_ok=True)
    mp4 = vdir / f"{series}.mp4"
    with tempfile.TemporaryDirectory(prefix="specto-video-") as td:
        for i, p in enumerate(frames):  # image2 wants a plain numbered sequence
            shutil.copy2(p, Path(td) / f"{i:06d}.jpg")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-framerate", str(VIDEO_FPS), "-i", str(Path(td) / "%06d.jpg"),
             "-f", "lavfi", "-i", "sine=frequency=220:beep_factor=4:sample_rate=48000",
             "-map", "0:v", "-map", "1:a", "-shortest",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
             "-g", "8", "-bf", "0", "-r", str(VIDEO_FPS), "-threads", "1",
             "-c:a", "aac", "-b:a", "96k", "-af", "volume=-14dB",
             "-fflags", "+bitexact", "-flags", "+bitexact",
             "-movflags", "+faststart", str(mp4)],
            check=True)  # fmt: skip
    probe = subprocess.run(
        ["ffprobe", "-hide_banner", "-loglevel", "error", "-select_streams", "v:0",
         "-show_entries", "frame=pts_time", "-of", "json", str(mp4)],
        check=True, capture_output=True, text=True)  # fmt: skip
    pts = [float(f["pts_time"]) for f in json.loads(probe.stdout)["frames"]]
    if len(pts) != len(frames):
        print(f"[WARN] {series}: {len(pts)} encoded frames for {len(frames)} images")
    side = {
        "video": mp4.name,
        "fps": float(VIDEO_FPS),
        "frame_count": len(pts),
        "timeline": "stamp",
        "stamp_unit": "ns",
        "source_frames": f"runs/{RUN}/{series}",
        "frames": [
            {"frame": i, "key": p.stem.split("_")[0], "stamp_ns": stamps[i], "pts": pts[i] if i < len(pts) else None}
            for i, p in enumerate(frames)
        ],
    }
    (vdir / f"{series}.timeline.json").write_text(json.dumps(side, indent=1) + "\n", encoding="utf-8")
    span = (stamps[-1] - stamps[0]) / 1e9
    print(f"[OK] {mp4.name}: {len(pts)} frames at {VIDEO_FPS} fps, {span:.3f} s of stamps; sidecar {series}.timeline.json")


# -- meta -----------------------------------------------------------------------------------
def write_meta(out: Path, series: dict[str, list[tuple[str, int, dict]]]) -> None:
    meta = out / "runs" / RUN / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    for name, rows in series.items():
        with (meta / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["frame", "key", "stamp_ns", "t_s", "disc_x", "disc_y", "disc_r", "square_x", "square_y", "hue_deg"])
            for i, (key, stamp, s) in enumerate(rows):
                d, q = s["disc"], s["square"]
                w.writerow([i, key, stamp, round((stamp - T0_NS) / 1e9, 6), d["x"], d["y"], d["r"], q["x"], q["y"], s["hue_deg"]])
    # a 10 ms signal series over 20 s: three smooth channels and a counter
    with (meta / "signals.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["stamp_ns", "t_s", "level", "temp_c", "phase_deg", "count"])
        for k in range(2000):
            t = k * 0.01
            w.writerow([
                T0_NS + k * 10_000_000, round(t, 2),
                round(0.5 + 0.4 * math.sin(2 * math.pi * 0.25 * t) + 0.05 * math.sin(2 * math.pi * 3 * t), 4),
                round(21.0 + 1.5 * math.sin(2 * math.pi * 0.05 * t), 3),
                round((t * 90.0) % 360.0, 2),
                k // 100,
            ])  # fmt: skip
    with (meta / "events.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["stamp_ns", "t_s", "event", "value"])
        events = [(0.0, "start", ""), (0.5, "mark", "a"), (1.25, "level-high", "0.93"), (2.0, "mark", "b"),
                  (3.0, "pause", ""), (3.5, "resume", ""), (4.75, "level-low", "0.08"), (6.0, "mark", "c"),
                  (7.46875, "last-frame", "cam-a 00239"), (14.9875, "last-frame", "cam-b 00239"),
                  (20.0, "end", "")]  # fmt: skip
        for t, ev, val in events:
            w.writerow([T0_NS + int(round(t * 1e9)), t, ev, val])
    (meta / "settings.yaml").write_text(
        "# synthetic run settings, written by tools/make_testdata.py\n"
        f"run: {RUN}\n"
        f"first_stamp_ns: {T0_NS}\n"
        "image:\n"
        f"  width: {W}\n"
        f"  height: {H}\n"
        "  format: jpeg\n"
        "series:\n"
        "  cam-a:\n"
        "    period_ms: 31.25\n"
        "    palette: 0\n"
        "  cam-b:\n"
        "    period_ms: 62.5\n"
        "    offset_ms: 50\n"
        "    palette: 1\n"
        "video:\n"
        "  source: cam-a\n"
        f"  fps: {VIDEO_FPS}\n"
        "  audio: sine 220 Hz, one beep per second\n"
        "scene:\n"
        "  disc: lissajous 0.21 x 0.34 Hz\n"
        "  square: circle 0.11 Hz, spinning 45 deg/s\n",
        encoding="utf-8",
    )
    (meta / "summary.md").write_text(
        f"# {RUN} summary\n\n"
        "| series | frames | spacing | first stamp (ns) |\n"
        "|---|---:|---|---|\n"
        f"| cam-a | {N} | 31.25 ms | {T0_NS} |\n"
        f"| cam-b | {N} | 62.5 ms | {T0_NS + B_OFFSET_NS} |\n\n"
        "The video `video/cam-a.mp4` is the cam-a frames at 32 fps. `signals.csv` samples\n"
        "three channels every 10 ms for 20 s; `events.csv` lists a few marks on the same\n"
        "clock. Everything here is invented by `tools/make_testdata.py`.\n",
        encoding="utf-8",
    )
    print(f"[OK] meta: {len(list(meta.iterdir()))} files")


# -- geo ------------------------------------------------------------------------------------
def write_geo(out: Path, path_rows: list[tuple[str, int, dict]]) -> None:
    """Thirteen sites scattered around (0, 0), their close pairs, a coverage polygon and
    the disc's path as a LineString placed at the sites' centroid (synthetic
    georeference, said so in the properties)."""
    geo = out / "geo"
    geo.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    m_per_deg = 111_320.0
    lon0, lat0 = 0.0, 0.0
    offs = rng.uniform(-900, 900, size=(13, 2))  # metres east, north
    coords = [[round(lon0 + e / m_per_deg, 7), round(lat0 + n / m_per_deg, 7)] for e, n in offs]
    dists = np.sqrt(((offs[:, None, :] - offs[None, :, :]) ** 2).sum(-1))
    np.fill_diagonal(dists, np.inf)
    nearest = dists.argmin(1)
    points, lines, paired = [], [], set()
    for i in range(13):
        j = int(nearest[i])
        mutual = nearest[j] == i
        if mutual and i < j:
            lines.append({
                "type": "Feature",
                "properties": {"name": f"pair {i + 1}-{j + 1}", "a": i + 1, "b": j + 1, "length_m": int(dists[i, j])},
                "geometry": {"type": "LineString", "coordinates": [coords[i], coords[j]]},
            })  # fmt: skip
            paired |= {i, j}
    for i in range(13):
        group = "pair" if i in paired else "single"
        points.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords[i]},
            "properties": {
                "id": i + 1, "name": f"site-{i + 1:02d}", "group": group,
                "nearest": f"site-{int(nearest[i]) + 1:02d}", "nearest_m": int(dists[i, nearest[i]]),
                "title": f"{i + 1}. site-{i + 1:02d}",
                "description": f"{group} | nearest site-{int(nearest[i]) + 1:02d} at {int(dists[i, nearest[i]])} m",
            },
        })  # fmt: skip

    def fc(features, **props):
        return {"type": "FeatureCollection", "properties": props, "features": features}

    def write(name, data):
        (geo / name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"[OK] geo/{name}: {len(data['features'])} features")

    write("sites.geojson", fc(points + lines, layer="sites", generator=GENERATOR))
    write("sites-points.geojson", fc(points, layer="site points"))
    write("sites-pairs.geojson", fc(lines, layer="close pairs"))
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    pad = 300 / m_per_deg
    ring = [
        [min(lons) - pad, min(lats) - pad], [max(lons) + pad, min(lats) - pad],
        [max(lons) + pad, max(lats) + pad], [min(lons) - pad, max(lats) + pad],
        [min(lons) - pad, min(lats) - pad],
    ]  # fmt: skip
    write("sites-coverage.geojson", fc([{
        "type": "Feature",
        "properties": {"name": "site coverage", "pad_m": 300, "sites": len(points)},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }], layer="coverage"))  # fmt: skip
    # the disc's path over the run, 1 unit = 400 m, at the sites' centroid
    clon, clat = sum(lons) / len(lons), sum(lats) / len(lats)
    path = []
    for _key, _stamp, s in path_rows[::2]:  # every other frame
        e, n = (s["disc"]["x"] - 0.5) * 400.0, (0.5 - s["disc"]["y"]) * 400.0
        path.append([round(clon + e / m_per_deg, 7), round(clat + n / m_per_deg, 7)])
    first, last = path_rows[0][1], path_rows[-1][1]
    write(f"{RUN}-path.geojson", fc([{
        "type": "Feature",
        "properties": {"name": f"{RUN} path", "run": RUN, "samples": len(path), "stride": 2,
                       "georeference": "synthetic: the disc's path, 1 unit = 400 m, at the sites' centroid",
                       "stamp_ns_first": str(first), "stamp_ns_last": str(last)},
        "geometry": {"type": "LineString", "coordinates": path},
    }, {
        "type": "Feature",
        "properties": {"name": "start", "stamp_ns": str(first)},
        "geometry": {"type": "Point", "coordinates": path[0]},
    }, {
        "type": "Feature",
        "properties": {"name": "end", "stamp_ns": str(last)},
        "geometry": {"type": "Point", "coordinates": path[-1]},
    }], layer="path"))  # fmt: skip


# -- notes ----------------------------------------------------------------------------------
OVERVIEW = f"""---
title: {RUN}, how the pieces fit
tags: [specto, testdata]
---

# {RUN}, how the pieces fit

> [!note] Synthetic data
> Everything in this folder is written by `{GENERATOR}`: the frames, the video, the
> tables and these notes. Nothing was recorded.

The run holds two frame series, `cam-a` and `cam-b`, of the same moving scene. The
numbers are in [[{RUN}-summary]], the shapes in [[shapes-reference|the shapes reference]],
the palettes in [[colour-ramps#Ramps|colour ramps, ramps section]] and the log of the
run in [[run-log]].

## Pipeline

```mermaid
flowchart LR
  scene[scene_at t] --> a[cam-a frames]
  scene --> b[cam-b frames]
  a --> side[json sidecars]
  a --> video[video/cam-a.mp4]
  video --> tl[cam-a.timeline.json]
  scene --> csv[meta/*.csv]
  scene --> path[geo/{RUN}-path.geojson]
```

## The path

The disc follows a Lissajous curve and the square a circle:

$$
x_d(t) = \\tfrac12 + 0.32\\,\\sin(2\\pi\\,0.21\\,t + 0.6),\\qquad
y_d(t) = \\tfrac12 + 0.28\\,\\sin(2\\pi\\,0.34\\,t)
$$

where $t$ is seconds since the first stamp. Frame $i$ of cam-a is taken at
$t_i = i / 32$ and frame $j$ of cam-b at $t_j = 0.05 + j / 16$.

> [!warning] Two clocks
> cam-b starts 50 ms later and ticks half as often, so no two frames share a stamp. A
> time link between the two series must look each frame up by its own stamp, never by
> index.

## Frame sidecar

Every image has a same-name `.json` sidecar:

```json
{{
  "frame": 42,
  "key": "00042",
  "series": "cam-a",
  "stamp_ns": {T0_NS + 42 * A_PERIOD_NS},
  "shape": {{"disc": {{"x": 0.61, "y": 0.47, "r": 0.07}}, "hue_deg": 15.75}}
}}
```

| Series | Frames | Spacing | Palette |
|---|---:|---|---|
| cam-a | {N} | 31.25 ms | 0 |
| cam-b | {N} | 62.5 ms, from 50 ms | 1 |
| video | {N} | 32 fps | from cam-a |

- [x] frames and sidecars
- [x] video with a beep per second
- [ ] a third series ([[run-log]] says why not)

![cam-a frame 00000](img/cam-a-00000.jpg)

> [!tip]- Collapsed callout
> Click the title to expand it. Obsidian folds `[!tip]-` callouts by default.
"""

SUMMARY = f"""# {RUN} summary

Two synthetic frame series of one scene, {N} frames each, plus a video of the first.

## Series

| name | frames | spacing | offset | stamps |
|---|---:|---|---|---|
| cam-a | {N} | 31.25 ms | 0 | one per frame |
| cam-b | {N} | 62.5 ms | +50 ms | one per frame, none shared with cam-a |

## Files

- `runs/{RUN}/cam-a/`, `runs/{RUN}/cam-b/`: `NNNNN_<stamp_ns>.jpg` and `.json`
- `runs/{RUN}/video/cam-a.mp4` and its `cam-a.timeline.json`
- `runs/{RUN}/meta/`: `cam-a.csv`, `cam-b.csv`, `signals.csv`, `events.csv`,
  `settings.yaml`, `summary.md`

## Settings

```yaml
image: {{width: {W}, height: {H}, format: jpeg}}
video: {{source: cam-a, fps: {VIDEO_FPS}}}
```

See [[{RUN}-overview]] for how the pieces fit and [[run-log]] for the log.
"""

SHAPES = """# Shapes reference

The scene has two shapes over a gradient. Positions are fractions of the frame, `x`
to the right and `y` down.

## Disc

- centre: a Lissajous curve, 0.21 Hz across and 0.34 Hz down
- radius: `0.06 + 0.02 sin(2 pi 0.5 t)` of the frame width
- fill: the background hue plus 180 degrees

## Square

1. centre: a circle of radius 0.38 (across) by 0.34 (down) at 0.11 Hz
2. side: 0.08 of the frame width
3. spin: 45 degrees per second
   - the outline is white
   - the fill is the background hue plus 300 degrees

| shape | field in the sidecar | unit |
|---|---|---|
| disc | `shape.disc.x`, `shape.disc.y` | fraction of the frame |
| disc | `shape.disc.r` | fraction of the width |
| square | `shape.square.angle_deg` | degrees |

Back to [[run-001-overview]].
"""

RAMPS = """# Colour ramps

The background is a diagonal gradient between two colours of one hue family; the hue
drifts 12 degrees per second. Each series adds a palette offset.

## Ramps

| palette | series | hue offset | dark end | light end |
|---|---|---:|---|---|
| 0 | cam-a | 0 | s 0.55, v 0.35 | s 0.45, v 0.85, hue +60 |
| 1 | cam-b | 140 | s 0.55, v 0.35 | s 0.45, v 0.85, hue +60 |

## Formula

```python
def hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, s, v)
    return int(r * 255), int(g * 255), int(b * 255)
```

The label strip at the bottom left is black with white text in every frame.
"""

RUNLOG = f"""# Run log

> Times are seconds since the first stamp ({T0_NS} ns).

| t | event | note |
|---:|---|---|
| 0.000 | start | both series armed |
| 0.050 | cam-b first frame | 50 ms after cam-a |
| 1.250 | level high | `signals.csv` peaks at 0.93 |
| 3.000 | pause | one second |
| 4.750 | level low | 0.08 |
| 7.469 | last cam-a frame | key `00239` |
| 14.988 | last cam-b frame | key `00239` |
| 20.000 | end | signals stop |

## Checklist

- [x] {N} frames per series
- [x] sidecars beside every frame
- [x] video encoded at 32 fps with sound
- [ ] a third series: not generated, two are enough for the linked views

See [[{RUN}-overview]] and [[{RUN}-summary]].
"""


def write_notes(out: Path) -> None:
    notes = out / "notes"
    (notes / "img").mkdir(parents=True, exist_ok=True)
    first = sorted((out / "runs" / RUN / "cam-a").glob("00000_*.jpg"))
    if first:
        shutil.copy2(first[0], notes / "img" / "cam-a-00000.jpg")
    for name, text in (
        (f"{RUN}-overview.md", OVERVIEW),
        (f"{RUN}-summary.md", SUMMARY),
        ("shapes-reference.md", SHAPES),
        ("colour-ramps.md", RAMPS),
        ("run-log.md", RUNLOG),
    ):
        (notes / name).write_text(text, encoding="utf-8")
    print(f"[OK] notes: {len(list(notes.glob('*.md')))} markdown files")


# -- 3D -------------------------------------------------------------------------------------
def write_terrain_ply(path: Path, n: int = 60_000) -> None:
    """A binary little-endian point cloud (x y z float32, rgb uchar): a height field of
    summed sines, coloured by height."""
    rng = np.random.default_rng(3)
    xy = rng.uniform(-50, 50, size=(n, 2)).astype(np.float32)
    x, y = xy[:, 0], xy[:, 1]
    z = (4 * np.sin(x / 9) * np.cos(y / 11) + 2 * np.sin((x + y) / 5) + 0.3 * rng.normal(size=n)).astype(np.float32)
    h = (z - z.min()) / max(float(z.max() - z.min()), 1e-6)
    rgb = np.stack([(60 + 180 * h), (90 + 120 * (1 - h)), (140 - 100 * h)], 1).clip(0, 255).astype(np.uint8)
    rec = np.empty(n, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    rec["x"], rec["y"], rec["z"] = x, y, z
    rec["r"], rec["g"], rec["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"comment {GENERATOR}\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(header.encode("ascii"))
        fh.write(rec.tobytes())
    print(f"[OK] {path.relative_to(path.parents[1])}: {n} points")


def torus_knot(p: int = 2, q: int = 3, nu: int = 160, nv: int = 16, R: float = 10.0, r: float = 2.2):
    """A (p, q) torus knot tube: positions, normals, uint16 indices."""
    u = np.linspace(0, 2 * np.pi, nu, endpoint=False)
    cu, su = np.cos(q * u), np.sin(q * u)
    centre = np.stack([(R + r * 2 * cu) * np.cos(p * u), (R + r * 2 * cu) * np.sin(p * u), r * 2 * su], 1)
    nxt = np.roll(centre, -1, 0)
    tangent = nxt - centre
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    up = np.array([0.0, 0.0, 1.0])
    n1 = np.cross(tangent, up)
    n1 /= np.linalg.norm(n1, axis=1, keepdims=True)
    n2 = np.cross(tangent, n1)
    v = np.linspace(0, 2 * np.pi, nv, endpoint=False)
    ring = np.cos(v)[:, None] * n1[:, None, :] + np.sin(v)[:, None] * n2[:, None, :]  # (nu, nv, 3)
    pos = (centre[:, None, :] + r * 0.45 * ring).reshape(-1, 3).astype(np.float32)
    nrm = ring.reshape(-1, 3).astype(np.float32)
    idx = []
    for i in range(nu):
        for j in range(nv):
            a = i * nv + j
            b = i * nv + (j + 1) % nv
            c = ((i + 1) % nu) * nv + j
            d = ((i + 1) % nu) * nv + (j + 1) % nv
            idx += [a, c, b, b, c, d]
    return pos, nrm, np.array(idx, np.uint16)


def write_knot_glb(path: Path) -> None:
    pos, nrm, idx = torus_knot()
    blob = b""
    views, accessors = [], []
    for arr, target, kind, comp in ((pos, 34962, "VEC3", 5126), (nrm, 34962, "VEC3", 5126), (idx, 34963, "SCALAR", 5123)):
        data = arr.tobytes()
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
        acc = {"bufferView": len(views) - 1, "componentType": comp, "count": int(len(arr)), "type": kind}
        if arr is pos:
            acc["min"], acc["max"] = pos.min(0).tolist(), pos.max(0).tolist()
        accessors.append(acc)
        blob += data + b"\0" * (-len(data) % 4)
    gltf = {
        "asset": {"version": "2.0", "generator": GENERATOR},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "knot", "mesh": 0}],
        "meshes": [{"name": "knot", "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"name": "knot", "pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.55, 0.85, 1], "metallicFactor": 0.1, "roughnessFactor": 0.5}}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, total))
        fh.write(struct.pack("<I4s", len(js), b"JSON") + js)
        fh.write(struct.pack("<I4s", len(blob), b"BIN\0") + blob)
    print(f"[OK] {path.relative_to(path.parents[1])}: {len(pos)} vertices, {len(idx) // 3} triangles")


# -- archives -------------------------------------------------------------------------------
def write_archives(out: Path) -> None:
    arc = out / "archives"
    arc.mkdir(parents=True, exist_ok=True)
    notes = out / "notes"
    zpath = arc / f"{RUN}-notes.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(notes.rglob("*")):
            if p.is_file():
                info = zipfile.ZipInfo(p.relative_to(notes).as_posix(), date_time=ARCHIVE_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, p.read_bytes())
    members = sorted((out / "runs" / RUN / "cam-b").glob("0000[0-3]_*.*"))
    tpath = arc / "cam-b-sample.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for p in members:
            info = tarfile.TarInfo(f"cam-b/{p.name}")
            data = p.read_bytes()
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            t.addfile(info, io.BytesIO(data))
    with tpath.open("wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue())
    print(f"[OK] archives: {zpath.name} ({len(list(notes.rglob('*.md')))} notes), {tpath.name} ({len(members)} files)")


# -- README ---------------------------------------------------------------------------------
README = f"""# Specto test data (git-ignored)

Written from nothing by `{GENERATOR}`; every file is synthetic and the generator is
deterministic. Regenerate with `uv run python tools/make_testdata.py`.

- `runs/{RUN}/cam-a/NNNNN_<stamp_ns>.jpg` + same-name `.json` sidecar (frame, key, series,
  stamp, the scene's shapes): {N} frames of a disc and a square over a drifting gradient
  at 32 Hz, every frame its own stamp.
- `runs/{RUN}/cam-b/`: {N} frames of the same scene in a second palette at 16 Hz, starting
  50 ms after cam-a (two rates, no stamp shared between the series). Both series share the
  5-digit frame key: use it to test keys, grouping and linked galleries.
- `runs/{RUN}/video/cam-a.mp4`: H.264 of the cam-a frames at 32 fps (one video frame per
  image, pts = stamp offset) with a quiet 220 Hz tone that beeps once a second;
  `cam-a.timeline.json` maps frame index -> key, stamp_ns, pts.
- `runs/{RUN}/meta/`: `cam-a.csv`, `cam-b.csv` (one row per frame), `signals.csv` (10 ms,
  20 s), `events.csv`, `settings.yaml`, `summary.md`.
- `geo/`: `sites.geojson` (13 points and their close pairs around lon 0, lat 0),
  `sites-points.geojson`, `sites-pairs.geojson`, `sites-coverage.geojson` (one polygon) and
  `{RUN}-path.geojson` (the disc's path as a LineString with start and end points).
- `notes/`: five markdown notes with headings, lists, tables, code, Mermaid, KaTeX,
  callouts and wikilinks, plus `img/cam-a-00000.jpg` for a relative image.
- `scans/terrain.ply`: a binary point cloud; `models/knot.glb`: a torus-knot mesh.
- `archives/{RUN}-notes.zip` (the notes), `archives/cam-b-sample.tar.gz` (four cam-b frames).
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "testdata",
                    help="the output folder (default <repo>/testdata)")
    ns = ap.parse_args()
    out: Path = ns.out.resolve()
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit(f"[ERROR] {tool} not on PATH; it encodes the video")
    started = time.perf_counter()
    for sub in ("runs", "geo", "notes", "scans", "models", "archives"):
        shutil.rmtree(out / sub, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    series = {"cam-a": write_frames(out, "cam-a", 0), "cam-b": write_frames(out, "cam-b", 1)}
    encode_video(out, "cam-a")
    write_meta(out, series)
    write_geo(out, series["cam-a"])
    write_notes(out)
    write_terrain_ply(out / "scans" / "terrain.ply")
    write_knot_glb(out / "models" / "knot.glb")
    write_archives(out)
    (out / "README.md").write_text(README, encoding="utf-8")
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"[OK] done {out}: {total / 1e6:.1f} MB in {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
