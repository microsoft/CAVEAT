"""Command-local registration hook for the prompt-only scaffold.

Activate only for an ablation command by placing this directory first on
``PYTHONPATH``.  Python imports ``sitecustomize`` in both the benchmark launcher
and its inherited worker process, so no core registry file needs to change.
"""

from prompt_only_scaffold import (  # noqa: F401
    BrowserUsePromptOnlyScaffold,
    SCAFFOLD_NAME,
)

from agentarena.core.scaffold import SCAFFOLDS


if SCAFFOLD_NAME not in SCAFFOLDS:
    raise RuntimeError(
        f"prompt-only scaffold failed to register: {SCAFFOLD_NAME}"
    )
