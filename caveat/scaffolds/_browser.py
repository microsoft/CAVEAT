"""Shared Chromium launch config for browser-driven scaffolds.

The browser binary + libraries are machine-specific, so they're configurable:

    CAVEAT_CHROME       path to the chromium/chrome executable
    CAVEAT_CHROME_LIBS  extra LD_LIBRARY_PATH (needed on some WSL/headless boxes)

If unset we auto-discover Playwright's bundled Chromium under ~/.cache/ms-playwright
(install once with ``python -m playwright install chromium``).
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def find_chromium() -> Optional[str]:
    if os.environ.get("CAVEAT_CHROME"):
        return os.environ["CAVEAT_CHROME"]
    roots = [Path.home() / ".cache" / "ms-playwright",
             Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent"))]
    for root in roots:
        for pat in ("chromium-*/chrome-linux*/chrome", "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
                    "chromium-*/chrome-win/chrome.exe"):
            hits = sorted(glob.glob(str(root / pat)))
            if hits:
                return hits[-1]
    for cand in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        from shutil import which
        if which(cand):
            return which(cand)
    return None


@dataclass
class BrowserConfig:
    executable: Optional[str]
    lib_path: Optional[str]
    headless: bool = True
    width: int = 1280
    height: int = 900

    @classmethod
    def from_env(cls, headless: bool = True) -> "BrowserConfig":
        return cls(executable=find_chromium(),
                   lib_path=os.environ.get("CAVEAT_CHROME_LIBS"),
                   headless=headless)

    def child_env(self) -> dict:
        env = dict(os.environ)
        if self.lib_path:
            env["LD_LIBRARY_PATH"] = self.lib_path + ":" + env.get("LD_LIBRARY_PATH", "")
        return env

    @property
    def args(self) -> list[str]:
        # The extra flags cut chromium cold-start work during launch bursts (many browsers
        # spawning on one host): no GPU probing (headless VM rasterizes in software anyway),
        # no extensions/background services/first-run tasks. Rendering itself is unaffected
        # (vision screenshots still work).
        return ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--disable-extensions", "--disable-background-networking",
                "--no-first-run", "--no-default-browser-check", "--mute-audio"]
