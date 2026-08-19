"""Prompt-only ablation over the unchanged browser-use baseline scaffold."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from agentarena.core.scaffold import (
    SCAFFOLDS,
    RawTrajectory,
    RunContext,
    Scaffold,
)
from agentarena.scaffolds.browseruse import BrowserUseScaffold


SCAFFOLD_NAME = "browseruse-prompt-only"
PROMPT_ONLY_VERSION = "prompt-only-v1"
PROMPT_ONLY_GUIDANCE = (
    "When choosing among alternatives, first separate mandatory constraints "
    "from comparative preferences, and preserve only priorities the user "
    "actually stated. "
    "Keep plausible alternatives as qualifying, disqualified by observed "
    "evidence, or unresolved; do not treat missing or conflicting evidence as "
    "favorable. "
    "Do not commit merely because one acceptable option appears: use a "
    "defensible stopping reason, compare every still-plausible alternative "
    "under the same criteria, and immediately before any consequential action "
    "recheck the exact choice and resulting state against the original request."
)
PROMPT_ONLY_SHA256 = hashlib.sha256(
    PROMPT_ONLY_GUIDANCE.encode("utf-8")
).hexdigest()


@SCAFFOLDS.register(SCAFFOLD_NAME)
class BrowserUsePromptOnlyScaffold(Scaffold):
    """Run baseline browser-use with only general guidance added to the task."""

    name = SCAFFOLD_NAME

    def supports(self, model) -> tuple[bool, str]:
        return BrowserUseScaffold().supports(model)

    def run(self, ctx: RunContext) -> RawTrajectory:
        prompted_task = replace(
            ctx.task,
            instruction=(
                f"{ctx.task.instruction}\n\n{PROMPT_ONLY_GUIDANCE}"
            ),
        )
        prompted_ctx = replace(ctx, task=prompted_task)
        return BrowserUseScaffold().run(prompted_ctx)


__all__ = [
    "BrowserUsePromptOnlyScaffold",
    "PROMPT_ONLY_GUIDANCE",
    "PROMPT_ONLY_SHA256",
    "PROMPT_ONLY_VERSION",
    "SCAFFOLD_NAME",
]
