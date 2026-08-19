"""Stagehand scaffold (https://github.com/browserbase/stagehand).

A popular Playwright-based agent (Node). We run its agent in ``dom`` mode via the
vendored ``run_stagehand.mjs`` and normalize the output. One-time setup in this
directory:  ``npm install && node patch_stagehand.mjs``  (the patch makes it use
chat-completions so non-OpenAI endpoints work — see VENDORED.md).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from ...core.scaffold import SCAFFOLDS, RawTrajectory, RunContext, Scaffold
from ...core.trajectory import Step
from .._browser import BrowserConfig

_DIR = Path(__file__).resolve().parent


@SCAFFOLDS.register("stagehand")
class StagehandScaffold(Scaffold):
    name = "stagehand"

    def run(self, ctx: RunContext) -> RawTrajectory:
        if shutil.which("node") is None:
            return RawTrajectory(stats={"error": "node not found (install Node.js)"})
        if not (_DIR / "node_modules").exists():
            return RawTrajectory(stats={"error": "stagehand not installed: run "
                                                 "`npm install && node patch_stagehand.mjs` "
                                                 f"in {_DIR}"})
        ep = ctx.model.openai_endpoint()
        bc = BrowserConfig.from_env(headless=ctx.headless)
        sh_out = ctx.work_dir / "_sh"
        sh_out.mkdir(parents=True, exist_ok=True)

        env = bc.child_env()
        env.update({
            "OPENAI_API_KEY": ep.api_key, "OPENAI_BASE_URL": ep.base_url,
            "SH_MODEL": ep.model, "SH_KEY": ep.api_key, "SH_BASEURL": ep.base_url,
            "SH_OUT": str(sh_out), "SH_START_URL": ctx.start_url,
            "SH_TASK": ctx.task.instruction, "SH_MAX_STEPS": str(ctx.max_steps),
            "CHROME_BIN": bc.executable or "",
        })
        t0 = time.time()
        proc = subprocess.run(["node", "run_stagehand.mjs"], cwd=str(_DIR), env=env,
                              capture_output=True, text=True, timeout=1800)
        (sh_out / "node_stderr.txt").write_text(proc.stderr or "")

        steps, answer = _collect(sh_out)
        stats = {"seconds": round(time.time() - t0, 1)}
        if not steps and proc.returncode != 0:
            stats["error"] = (proc.stderr or "stagehand failed").splitlines()[-1:][0] if proc.stderr else "stagehand failed"
        return RawTrajectory(steps=steps, answer=answer, stats=stats)


def _collect(sh_out: Path) -> tuple[list[Step], str]:
    f = sh_out / "steps.json"
    if not f.exists():
        return [], ""
    data = json.loads(f.read_text())
    steps = []
    for i, s in enumerate(data.get("steps", []), 1):
        img = sh_out / s["img"] if s.get("img") else None
        steps.append(Step(index=i, url=s.get("url", ""), action=s.get("action", ""),
                          reasoning=s.get("reasoning", ""),
                          screenshot=img.read_bytes() if img and img.exists() else None))
    return steps, data.get("answer", "")
