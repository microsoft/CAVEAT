#!/usr/bin/env python
"""Rasterize a standalone SVG to a crisp PNG via the project's headless Chromium.

Usage: python scripts/svg2png.py IN.svg OUT.png [scale=2]

Reuses BrowserConfig (executable + lib path + child env) so it works on this box's
pinned Chromium. Renders at device-scale-factor `scale` for a retina-sharp raster.
"""
import os
import re
import sys
from pathlib import Path

from agentarena.scaffolds._browser import BrowserConfig

svg_path, out_path = sys.argv[1], sys.argv[2]
scale = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
svg = Path(svg_path).read_text()
W = int(re.search(r'width="(\d+)"', svg).group(1))
H = int(re.search(r'height="(\d+)"', svg).group(1))
html = f'<!doctype html><html><head><meta charset="utf-8">' \
       f'<style>html,body{{margin:0;padding:0;background:#fff}}</style></head>' \
       f'<body>{svg}</body></html>'
htmlf = Path(out_path).with_suffix(".render.html")
htmlf.write_text(html)

bc = BrowserConfig.from_env(headless=True)
if bc.lib_path:
    os.environ["LD_LIBRARY_PATH"] = bc.lib_path + ":" + os.environ.get("LD_LIBRARY_PATH", "")

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=bc.executable, args=bc.args, env=bc.child_env())
    page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=scale)
    page.goto(htmlf.resolve().as_uri())
    page.wait_for_timeout(250)
    page.screenshot(path=out_path, clip={"x": 0, "y": 0, "width": W, "height": H})
    browser.close()
htmlf.unlink(missing_ok=True)
print(f"wrote {out_path} ({int(W*scale)}x{int(H*scale)})")
