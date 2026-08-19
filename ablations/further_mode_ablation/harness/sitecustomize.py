"""Register all further-ablation harness arms for this Python command only."""

from __future__ import annotations

import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_ABLATIONS = _HERE.parents[1]
for _path in (
    _ABLATIONS / "prompt_only",
    _ABLATIONS / "harness_components",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# The existing P and D implementations are imported, not copied or modified.
from prompt_only_scaffold import BrowserUsePromptOnlyScaffold  # noqa: E402,F401
from no_coverage_scaffold import (  # noqa: E402,F401
    BrowserUseDeliberativeNoCoverageScaffold,
)
from component_scaffolds import (  # noqa: E402,F401
    BrowserUseDeliberativeContractOnlyScaffold,
    BrowserUseDeliberativeCoverageAdvisoryScaffold,
    BrowserUseDeliberativeCoverageOnlyScaffold,
    BrowserUseDeliberativeFeasibilityScaffold,
)
from arm_registry import validate_command_local_registration  # noqa: E402


validate_command_local_registration()
