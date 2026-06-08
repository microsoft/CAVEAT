"""browser-use scaffold (https://github.com/browser-use/browser-use).

A popular CDP-based web agent. We point its ``ChatOpenAI`` at the model's
OpenAI-compatible endpoint, launch the configured Chromium, run the agent on the
task, and normalize ``agent.history`` into the shared trajectory format.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path
from typing import Optional

from ..core.models import ModelSpec
from ..core.scaffold import SCAFFOLDS, RawTrajectory, RunContext, Scaffold
from ..core.trajectory import Step
from ._browser import BrowserConfig


@SCAFFOLDS.register("browseruse")
class BrowserUseScaffold(Scaffold):
    name = "browseruse"

    def supports(self, model: ModelSpec) -> tuple[bool, str]:
        return True, ""

    def run(self, ctx: RunContext) -> RawTrajectory:
        return asyncio.run(_run(ctx))


def _img_bytes(shot) -> Optional[bytes]:
    if not shot:
        return None
    if isinstance(shot, (bytes, bytearray)):
        return bytes(shot)
    if isinstance(shot, str):
        s = shot.split(",", 1)[1] if shot.startswith("data:") else shot
        try:
            return base64.b64decode(s)
        except Exception:
            p = Path(shot)
            return p.read_bytes() if p.exists() else None
    return None


async def _run(ctx: RunContext) -> RawTrajectory:
    from browser_use import Agent, BrowserProfile, BrowserSession, ChatOpenAI

    ep = ctx.model.openai_endpoint()
    bc = BrowserConfig.from_env(headless=ctx.headless)
    if bc.lib_path:
        os.environ["LD_LIBRARY_PATH"] = bc.lib_path + ":" + os.environ.get("LD_LIBRARY_PATH", "")

    llm_kwargs = dict(model=ep.model, base_url=ep.base_url, api_key=ep.api_key,
                      dont_force_structured_output=True, add_schema_to_system_prompt=True)
    if ep.reasoning:
        llm_kwargs["reasoning_models"] = [ep.model]   # max_completion_tokens, no temperature
    else:
        llm_kwargs["temperature"] = 0.0
    llm = ChatOpenAI(**llm_kwargs)

    profile = BrowserProfile(executable_path=bc.executable, headless=bc.headless,
                             args=bc.args, env=bc.child_env(),
                             window_size={"width": bc.width, "height": bc.height})
    bs = BrowserSession(browser_profile=profile)
    steps: list[Step] = []
    answer = ""
    t0 = time.time()
    error = None
    try:
        await bs.start()
        await bs.navigate_to(ctx.start_url)
        agent = Agent(task=ctx.task.instruction, llm=llm, browser_session=bs,
                      use_vision=ctx.model.has_vision)
        try:
            await agent.run(max_steps=ctx.max_steps)
        finally:
            h = agent.history
            shots = h.screenshots() or []
            acts = h.model_actions() or []
            thoughts = h.model_thoughts() or []
            urls = h.urls() or []
            answer = h.final_result() or ""
            n = max(len(shots), len(acts), len(urls))
            for i in range(n):
                steps.append(Step(
                    index=i + 1,
                    action=str(acts[i]) if i < len(acts) else "",
                    reasoning=str(thoughts[i]) if i < len(thoughts) else "",
                    url=urls[i] if i < len(urls) else "",
                    screenshot=_img_bytes(shots[i]) if i < len(shots) else None))
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    finally:
        try:
            await bs.kill()
        except Exception:
            pass
    stats = {"seconds": round(time.time() - t0, 1)}
    if error:
        stats["error"] = error
    return RawTrajectory(steps=steps, answer=str(answer), stats=stats)
