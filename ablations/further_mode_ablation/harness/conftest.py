"""Make focused tests importable without activating the runtime hook."""

from __future__ import annotations

import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ABLATIONS = HERE.parents[1]
for path in (
    HERE,
    ABLATIONS / "prompt_only",
    ABLATIONS / "harness_components",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
