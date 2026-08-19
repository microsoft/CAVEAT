"""Command-local registration for harness-component ablations.

Activate this directory only through one command's ``PYTHONPATH``.  The hook
registers the no-coverage component and imports the existing prompt-only
wrapper from its sibling package; neither scaffold is added to the production
registry imports.
"""

from __future__ import annotations

import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_PROMPT_ONLY_DIR = _HERE.parent / "prompt_only"
if str(_PROMPT_ONLY_DIR) not in sys.path:
    sys.path.insert(0, str(_PROMPT_ONLY_DIR))

from no_coverage_scaffold import (  # noqa: E402,F401
    BrowserUseDeliberativeNoCoverageScaffold,
    SCAFFOLD_NAME as NO_COVERAGE_SCAFFOLD_NAME,
)
from prompt_only_scaffold import (  # noqa: E402,F401
    BrowserUsePromptOnlyScaffold,
    SCAFFOLD_NAME as PROMPT_ONLY_SCAFFOLD_NAME,
)

from agentarena.core.scaffold import SCAFFOLDS  # noqa: E402


for _scaffold_name in (
    NO_COVERAGE_SCAFFOLD_NAME,
    PROMPT_ONLY_SCAFFOLD_NAME,
):
    if _scaffold_name not in SCAFFOLDS:
        raise RuntimeError(
            f"harness-component scaffold failed to register: {_scaffold_name}"
        )
