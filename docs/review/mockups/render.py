"""Render Specto GUI mockup HTML files to PNG with headless Chromium."""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "mockups"
OUT = ROOT / "img"


def main(names):
    OUT.mkdir(exist_ok=True)
    files = [SRC / f"{n}.html" for n in names] if names else sorted(SRC.glob("*.html"))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        msgs = []
        page.on("console", lambda m: msgs.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: msgs.append(f"pageerror: {e}"))
        for f in files:
            msgs.clear()
            page.goto(f.as_uri())
            page.wait_for_selector("body[data-ready='1']", timeout=15000)
            page.wait_for_timeout(150)
            out = OUT / (f.stem + ".png")
            page.screenshot(path=str(out), full_page=False)
            print(f"[OK] {out}")
            for m in msgs:
                print("   ", m)
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
