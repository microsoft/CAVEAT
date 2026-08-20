"""Scaffold interface + registry — the seam for plugging in agent harnesses.

A scaffold is one way of turning a model into a web agent. The release includes the
BrowserUse baseline and CAVEAT-Harness. To add another locally, implement one method::

    from caveat.core.scaffold import Scaffold, RunContext, RawTrajectory, SCAFFOLDS

    @SCAFFOLDS.register("my-agent")
    class MyAgent(Scaffold):
        name = "my-agent"
        def run(self, ctx: RunContext) -> RawTrajectory:
            # drive ctx.start_url with ctx.model, capturing steps; return them.
            ...

The runner gives you a ``RunContext`` (task, start_url, model, budget, scratch dir)
and takes back a ``RawTrajectory`` (the steps + final answer). Everything else —
servers, evaluation, persistence, and parallelism — is handled for you.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from .models import ModelSpec
from .registry import Registry
from .task import TaskSpec
from .trajectory import Step

SCAFFOLDS: Registry["Scaffold"] = Registry("scaffold")


@dataclass
class RunContext:
    task: TaskSpec
    start_url: str
    model: ModelSpec
    work_dir: Path                 # scratch dir for this single run
    max_steps: int = 30
    headless: bool = True
    extra: dict = field(default_factory=dict)


@dataclass
class RawTrajectory:
    steps: list[Step] = field(default_factory=list)
    answer: str = ""
    stats: dict = field(default_factory=dict)   # seconds, error, tokens, ...


class Scaffold(ABC):
    name: str = "scaffold"

    @abstractmethod
    def run(self, ctx: RunContext) -> RawTrajectory:
        ...

    def supports(self, model: ModelSpec) -> tuple[bool, str]:
        """Return (ok, reason). Override to reject incompatible models up front
        (e.g. a vision-only scaffold rejecting a text-only model)."""
        return True, ""
