"""Normalized trajectory format shared by every scaffold and environment.

A run is stored as a self-contained directory:

    <run_dir>/
        step_001.png ...        # one screenshot per step (optional)
        trajectory.json         # full record: task + steps + result (lazy-loaded)
        summary.json            # compact result header

Every scaffold produces a ``Trajectory`` (via ``RawTrajectory`` + the runner's
evaluation), making runs directly comparable across harnesses.
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 2


@dataclass
class Step:
    """One agent step: what it saw (screenshot/url), thought, and did."""

    index: int
    action: str = ""
    reasoning: str = ""
    url: str = ""
    note: str = ""
    # In-memory screenshot bytes; persisted to step_<index>.png and dropped from json.
    screenshot: Optional[bytes] = field(default=None, repr=False)

    def to_json(self) -> dict[str, Any]:
        return {"index": self.index, "action": self.action, "reasoning": self.reasoning,
                "url": self.url, "note": self.note, "has_image": self.screenshot is not None}


@dataclass
class Evaluation:
    """The verdict for a run, produced by the environment's evaluator."""

    outcome: str                       # e.g. "compliant" | "violation" | "decoy" | "none" | "error"
    chosen: Optional[str] = None       # identifier of what the agent picked (asin / listing id)
    chosen_label: Optional[str] = None # human-readable name of the choice
    success: bool = False              # did the agent satisfy the user's preferences?
    took_bait: bool = False            # did the agent pick the steered/advertised decoy?
    details: dict[str, Any] = field(default_factory=dict)  # price paid, violated constraints, ...

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Trajectory:
    """A complete agent run: identity, what happened, and the verdict."""

    env: str
    scaffold: str
    model: str
    task_id: str
    condition: str
    instruction: str = ""
    preferences: dict[str, Any] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    answer: str = ""
    evaluation: Optional[Evaluation] = None
    stats: dict[str, Any] = field(default_factory=dict)   # seconds, num_steps, tokens, error, ...

    # -- identity / display ------------------------------------------------- #
    @property
    def cell_key(self) -> str:
        return f"{self.env}|{self.scaffold}|{self.model}|{self.task_id}|{self.condition}"

    @property
    def dir_name(self) -> str:
        safe = lambda s: str(s).replace("/", "-").replace("|", "-")
        return "__".join(safe(x) for x in
                         (self.env, self.scaffold, self.model, self.task_id, self.condition))

    # -- persistence -------------------------------------------------------- #
    def save(self, run_dir: Path | str) -> Path:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        # CAVEAT_NO_SHOT_PERSIST=1: skip writing per-step PNGs (2026-07-25 — screenshot
        # persistence was the dominant disk-write load at scale, ~180MB/s saturating the disk
        # across 30+ concurrent 250-step cells and destabilizing runs). Scores, actions,
        # reasoning, URLs and the evaluation are unaffected; steps are marked has_image=False
        # so persisted trajectories remain compact. The agent still SEES
        # screenshots in-run (vision input is untouched) — only trajectory persistence changes.
        _no_shots = os.environ.get("CAVEAT_NO_SHOT_PERSIST", "") == "1"
        if _no_shots:
            for s in self.steps:
                s.screenshot = None
        for s in self.steps:
            if s.screenshot is not None:
                (run_dir / f"step_{s.index:03d}.png").write_bytes(_png_bytes(s.screenshot))
        full = {
            "schema": SCHEMA_VERSION,
            "env": self.env, "scaffold": self.scaffold, "model": self.model,
            "task_id": self.task_id, "condition": self.condition,
            "instruction": self.instruction, "preferences": self.preferences,
            "answer": self.answer,
            "evaluation": self.evaluation.to_json() if self.evaluation else None,
            "stats": self.stats,
            "steps": [s.to_json() for s in self.steps],
        }
        (run_dir / "trajectory.json").write_text(json.dumps(full, indent=2, default=str))
        (run_dir / "summary.json").write_text(json.dumps(self.summary(), indent=2, default=str))
        return run_dir

    def summary(self) -> dict[str, Any]:
        ev = self.evaluation
        optimal_selection = ev.details.get("optimal_selection") if ev else None
        if ev and optimal_selection is None and ev.outcome in {
            "none", "other", "violation", "decoy"
        }:
            optimal_selection = 0.0
        return {
            "schema": SCHEMA_VERSION,
            "env": self.env, "scaffold": self.scaffold, "model": self.model,
            "task_id": self.task_id, "condition": self.condition,
            "num_steps": len(self.steps),
            "outcome": ev.outcome if ev else "error",
            "success": ev.success if ev else False,
            "took_bait": ev.took_bait if ev else False,
            "chosen": ev.chosen if ev else None,
            "chosen_label": ev.chosen_label if ev else None,
            "optimal_selection": optimal_selection,
            "seconds": self.stats.get("seconds"),
            "error": self.stats.get("error"),
        }

    @classmethod
    def load(cls, run_dir: Path | str) -> "Trajectory":
        run_dir = Path(run_dir)
        d = json.loads((run_dir / "trajectory.json").read_text())
        steps = []
        for s in d.get("steps", []):
            img = run_dir / f"step_{s['index']:03d}.png"
            steps.append(Step(index=s["index"], action=s.get("action", ""),
                              reasoning=s.get("reasoning", ""), url=s.get("url", ""),
                              note=s.get("note", ""),
                              screenshot=img.read_bytes() if img.exists() else None))
        ev = d.get("evaluation")
        return cls(
            env=d["env"], scaffold=d["scaffold"], model=d["model"], task_id=d["task_id"],
            condition=d["condition"], instruction=d.get("instruction", ""),
            preferences=d.get("preferences", {}), steps=steps, answer=d.get("answer", ""),
            evaluation=Evaluation(**ev) if ev else None, stats=d.get("stats", {}))


def _png_bytes(img: Any) -> bytes:
    """Accept raw png bytes, a data-uri/base64 string, a file path, or a PIL image."""
    import base64
    if isinstance(img, (bytes, bytearray)):
        return bytes(img)
    if isinstance(img, str):
        s = img.split(",", 1)[1] if img.startswith("data:") else img
        try:
            return base64.b64decode(s)
        except Exception:
            p = Path(img)
            if p.exists():
                return p.read_bytes()
            raise
    # PIL.Image
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
