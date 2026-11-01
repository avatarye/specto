"""Prefilter HDRIs into Babylon .env files (#175), run by build.py at build time.

    python packages/specto-web/bake_env.py --babylon <vendor/babylon/babylon.js> \\
        --size 256 <in.hdr> <out.env> [<in.hdr> <out.env> ...]

Babylon's own tools do the work in a real WebGL2 context: an ``HDRCubeTexture`` turns
the equirectangular .hdr into a cube of ``--size`` px per face and prefilters it on the
GPU, then ``EnvironmentTextureTools.CreateEnvTextureAsync`` packs its mips (RGBD in
PNG) with the irradiance harmonics into one .env. A page then loads that in tens of ms
(``CubeTexture.CreateFromPrefilteredData``) where the .hdr would be parsed and filtered
on every load. The page runs headless in Qt WebEngine (offscreen platform, GPU
compositing off: the offscreen platform loses WebGL contexts otherwise). Exits 1 with
``[ERROR]`` when no WebGL2 context is to be had; build.py then ships the .hdr itself.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="%(babylon)s"></script></head><body>
<canvas id="c" width="64" height="64"></canvas><script>
(async function () {
  const jobs = %(jobs)s, size = %(size)d;
  try {
    const canvas = document.getElementById("c");
    const engine = new BABYLON.Engine(canvas, false, { preserveDrawingBuffer: false });
    if (engine.webGLVersion < 2) throw new Error("WebGL2 is not available");
    const scene = new BABYLON.Scene(engine);
    window.baked = [];
    for (const src of jobs) {
      const t0 = performance.now();
      // size, no mipmaps, harmonics, linear, prefiltered on load
      const tex = new BABYLON.HDRCubeTexture(src, scene, size, false, true, false, true);
      await new Promise(function (ok, fail) {
        if (tex.isReady()) ok(); else tex.onLoadObservable.addOnce(ok);
        setTimeout(function () { fail(new Error("timed out loading " + src)); }, 60000);
      });
      const tools = BABYLON.EnvironmentTextureTools;
      const buf = await tools.CreateEnvTextureAsync(tex, { imageType: "image/png" });
      const bytes = new Uint8Array(buf);
      let bin = "";
      for (let i = 0; i < bytes.length; i += 32768) {
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
      }
      window.baked.push({ src: src, b64: btoa(bin), ms: performance.now() - t0 });
      tex.dispose();
    }
    document.title = "done";
  } catch (e) {
    document.title = "error:" + String(e && e.message || e);
  }
})();
</script></body></html>
"""


def bake(babylon: Path, size: int, pairs: list[tuple[Path, Path]], timeout: float = 180.0) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for var in ("QT_QUICK_BACKEND", "QSG_RHI_BACKEND"):  # software Qt Quick: no WebGL
        os.environ.pop(var, None)
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        flags + " --disable-gpu-compositing --in-process-gpu"
    ).strip()
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication

    jobs = [src.resolve().as_uri() for src, _ in pairs]
    page = PAGE % {"babylon": babylon.resolve().as_uri(), "jobs": json.dumps(jobs), "size": size}
    tmp = Path(tempfile.mkdtemp(prefix="specto-bake-"))
    html = tmp / "bake.html"
    html.write_text(page, encoding="utf-8")

    app = QApplication.instance() or QApplication([sys.argv[0]])
    view = QWebEngineView()
    view.settings().setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
    )
    view.resize(64, 64)
    view.show()
    view.load(QUrl.fromLocalFile(str(html)))
    state = {"rc": 1}
    t0 = time.monotonic()

    def take(i: int) -> None:
        """Bake ``i`` back from the page (one JSON string per call: a list of them all is
        too big for one runJavaScript answer), then the next."""
        if i == len(pairs):
            state["rc"] = 0
            app.quit()
            return

        def write(text: object) -> None:
            src, dst = pairs[i]
            got = json.loads(text) if isinstance(text, str) and text else None
            if not got or got.get("src") != jobs[i]:
                print(f"[ERROR] {src.name}: nothing baked")
                app.quit()
                return
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(base64.b64decode(got["b64"]))
            kb = dst.stat().st_size // 1024
            print(f"[OK] baked {src.name} -> {dst.name} ({kb} KB, {got['ms']:.0f} ms)")
            take(i + 1)

        view.page().runJavaScript(f"JSON.stringify(window.baked[{i}] || null)", 0, write)

    def poll() -> None:
        title = view.title()
        if title == "done":
            timer.stop()
            take(0)
        elif title.startswith("error:"):
            timer.stop()
            print(f"[ERROR] bake failed: {title[6:]}")
            app.quit()
        elif time.monotonic() - t0 > timeout:
            timer.stop()
            print(f"[ERROR] bake timed out after {timeout:.0f} s")
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    app.exec()
    view.close()
    html.unlink(missing_ok=True)
    tmp.rmdir()
    return state["rc"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--babylon", type=Path, required=True, help="the vendored babylon.js")
    ap.add_argument("--size", type=int, default=256, help="cube face size in px")
    ap.add_argument("files", nargs="+", type=Path, help="pairs: <in.hdr> <out.env>")
    args = ap.parse_args(argv)
    if len(args.files) % 2:
        ap.error("files come in pairs: <in.hdr> <out.env>")
    pairs = list(zip(args.files[::2], args.files[1::2], strict=True))
    return bake(args.babylon, args.size, pairs)


if __name__ == "__main__":
    sys.exit(main())
