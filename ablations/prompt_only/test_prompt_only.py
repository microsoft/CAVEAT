from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import prompt_only_scaffold as prompt_only
from agentarena.core.models import ModelSpec
from agentarena.core.scaffold import RawTrajectory, RunContext, SCAFFOLDS
from agentarena.core.task import TaskSpec


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXPECTED_PROMPT_SHA256 = (
    "2726e702eb85a074bd0ede327ff482f657d0563af922d526b47466354fac5284"
)
FORBIDDEN_PROMPT_WORDS = {
    "api",
    "category",
    "extras",
    "hero",
    "marketplace",
    "pagination",
    "product",
    "promotion",
    "quantity",
    "score",
    "site",
    "steering",
}


def _context() -> RunContext:
    task = TaskSpec(
        task_id="general-task",
        env="example",
        instruction="Choose an option and complete the request.",
        preferences={"cost__max": 25},
        catalog="catalog-a",
        condition="combined",
        start_path="/start",
        params={"date": "tomorrow"},
        metadata={"source": "test"},
    )
    model = ModelSpec(
        name="test-model",
        provider="openai",
        base_url="http://127.0.0.1:1/v1",
        api_key="unused",
    )
    return RunContext(
        task=task,
        start_url="http://127.0.0.1:9999/start",
        model=model,
        work_dir=Path("/tmp/prompt-only-test"),
        max_steps=1234,
        headless=True,
        extra={"opaque": object()},
    )


def test_prompt_is_exact_short_general_guidance() -> None:
    assert prompt_only.PROMPT_ONLY_VERSION == "prompt-only-v1"
    assert prompt_only.PROMPT_ONLY_SHA256 == EXPECTED_PROMPT_SHA256
    assert prompt_only.PROMPT_ONLY_GUIDANCE.count(".") == 3
    words = {
        token.strip(".,;:!?()[]{}").casefold()
        for token in prompt_only.PROMPT_ONLY_GUIDANCE.split()
    }
    assert words.isdisjoint(FORBIDDEN_PROMPT_WORDS)


def test_scaffold_is_registered() -> None:
    assert SCAFFOLDS.get(prompt_only.SCAFFOLD_NAME) is (
        prompt_only.BrowserUsePromptOnlyScaffold
    )


def test_only_instruction_changes_before_unchanged_delegate(
    monkeypatch,
) -> None:
    original_ctx = _context()
    original_task = original_ctx.task
    original_instruction = original_task.instruction
    sentinel = RawTrajectory(answer="unchanged delegate result")
    captured = {}

    def fake_baseline_run(self, received_ctx):
        captured["ctx"] = received_ctx
        return sentinel

    monkeypatch.setattr(
        prompt_only.BrowserUseScaffold,
        "run",
        fake_baseline_run,
    )

    result = prompt_only.BrowserUsePromptOnlyScaffold().run(original_ctx)
    prompted_ctx = captured["ctx"]

    assert result is sentinel
    assert original_ctx.task is original_task
    assert original_task.instruction == original_instruction
    assert prompted_ctx is not original_ctx
    assert prompted_ctx.task is not original_task
    assert prompted_ctx.task.instruction == (
        f"{original_instruction}\n\n{prompt_only.PROMPT_ONLY_GUIDANCE}"
    )

    for field in fields(RunContext):
        if field.name == "task":
            continue
        assert getattr(prompted_ctx, field.name) is getattr(
            original_ctx, field.name
        )
    for field in fields(TaskSpec):
        if field.name == "instruction":
            continue
        assert getattr(prompted_ctx.task, field.name) is getattr(
            original_task, field.name
        )


def test_support_check_delegates_to_baseline(monkeypatch) -> None:
    model = _context().model
    marker = (False, "baseline marker")

    def fake_supports(self, received_model):
        assert received_model is model
        return marker

    monkeypatch.setattr(
        prompt_only.BrowserUseScaffold,
        "supports",
        fake_supports,
    )
    assert (
        prompt_only.BrowserUsePromptOnlyScaffold().supports(model)
        == marker
    )


def test_registration_is_command_local() -> None:
    check = (
        "import json, agentarena.scaffolds;"
        "from agentarena.core.scaffold import SCAFFOLDS;"
        "print(json.dumps(SCAFFOLDS.names()))"
    )
    clean_env = dict(os.environ)
    clean_env.pop("PYTHONPATH", None)
    clean = subprocess.run(
        [sys.executable, "-c", check],
        cwd=ROOT,
        env=clean_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert prompt_only.SCAFFOLD_NAME not in json.loads(clean.stdout)

    isolated_env = dict(clean_env)
    isolated_env["PYTHONPATH"] = str(HERE)
    isolated = subprocess.run(
        [sys.executable, "-c", check],
        cwd=ROOT,
        env=isolated_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert prompt_only.SCAFFOLD_NAME in json.loads(isolated.stdout)
