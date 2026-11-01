"""The frame-source protocol (#136, HLD 4.5): how an out-of-process renderer (ovrtx,
Storm, Blender) sends frames into a view and takes control back.

Three channels between the GUI process and one source process:

- **frames**, :mod:`.ring`: a shared-memory ring of three RGBA8 slots, written by the
  source and read on the render thread by the native ``FrameItem``;
- **wake**, :mod:`.server`: a local socket (a named pipe on Windows) on which the source
  sends a byte after each frame, so the item calls ``update()`` without Python per frame
  (ADR 0004);
- **control**, :mod:`.control`: JSON lines on the source's stdin and stdout, requests from
  the host, results and events back.

:mod:`.ring`, :mod:`.server` and :mod:`.control` use only the standard library, so a
source running in another environment (ovrtx's venv, Blender's Python) can import them
with this package's ``src`` directory on ``sys.path``. :mod:`.stub_source` serves a
moving test pattern; :mod:`.offscreen` renders a ``FrameItem`` with no window for tests
and the bench (``tools/bench_frames.py``).
"""
