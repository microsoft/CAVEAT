"""A minimal, dependency-light reference scaffold — copy this to write your own.

It drives Playwright directly with a text observation (a numbered list of the
page's interactive elements) and asks the model for one JSON action per step via
the native ``model.achat`` (so it routes through the unified llm_client and works
with text-only models too). It is intentionally simple; it exists to show the
plug-in contract end-to-end, not to be state-of-the-art.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from ..core.scaffold import SCAFFOLDS, RawTrajectory, RunContext, Scaffold
from ..core.trajectory import Step
from ._browser import BrowserConfig

SYSTEM = (
    "You are a web agent completing a task in a browser. Each turn you receive the "
    "page URL, title, and a numbered list of interactive elements. Respond with ONLY "
    "a JSON object: {\"reason\": str, \"action\": \"click\"|\"type\"|\"scroll\"|\"done\", "
    "\"index\": int|null, \"text\": str}. Use \"type\" to fill an input (it also presses "
    "Enter). Use \"scroll\" to reveal more of the page. Use \"done\" when the task is "
    "complete. Think step by step but output only the JSON."
)

_COLLECT = """() => {
  const els = [...document.querySelectorAll('a,button,input,textarea,select,[role=button],[onclick]')];
  const out = [];
  els.forEach((e) => {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight + 600) return;
    e.setAttribute('data-aa', out.length);
    const label = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || '').trim().slice(0, 80);
    out.push(`[${out.length}] ${e.tagName.toLowerCase()} ${JSON.stringify(label)}`);
  });
  return out.slice(0, 60).join('\\n');
}"""


@SCAFFOLDS.register("simple")
class SimpleScaffold(Scaffold):
    name = "simple"

    def run(self, ctx: RunContext) -> RawTrajectory:
        return asyncio.run(_run(ctx))


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


async def _run(ctx: RunContext) -> RawTrajectory:
    from playwright.async_api import async_playwright

    bc = BrowserConfig.from_env(headless=ctx.headless)
    steps: list[Step] = []
    answer, error = "", None
    t0 = time.time()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=bc.executable, headless=bc.headless,
                                            args=bc.args, env=bc.child_env())
        page = await browser.new_page(viewport={"width": bc.width, "height": bc.height})
        try:
            await page.goto(ctx.start_url, wait_until="domcontentloaded")
            for i in range(ctx.max_steps):
                await page.wait_for_timeout(700)
                try:
                    elements = await page.evaluate(_COLLECT)
                except Exception:
                    elements = ""
                obs = (f"Task: {ctx.task.instruction}\nURL: {page.url}\n"
                       f"Title: {await page.title()}\nElements:\n{elements}")
                reply = await ctx.model.achat(obs, system=SYSTEM, max_tokens=1200)
                act = _parse(reply)
                shot = await page.screenshot()
                steps.append(Step(index=i + 1, url=page.url,
                                  action=json.dumps({k: act.get(k) for k in ("action", "index", "text")}),
                                  reasoning=act.get("reason", ""), screenshot=shot))
                a = (act.get("action") or "").lower()
                if a == "done":
                    answer = act.get("reason", "")
                    break
                try:
                    if a == "click" and act.get("index") is not None:
                        await page.click(f"[data-aa='{int(act['index'])}']", timeout=4000)
                    elif a == "type" and act.get("index") is not None:
                        sel = f"[data-aa='{int(act['index'])}']"
                        await page.fill(sel, str(act.get("text", "")), timeout=4000)
                        await page.press(sel, "Enter")
                    elif a == "scroll":
                        await page.mouse.wheel(0, 700)
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
        finally:
            await browser.close()
    stats = {"seconds": round(time.time() - t0, 1)}
    if error:
        stats["error"] = error
    return RawTrajectory(steps=steps, answer=answer, stats=stats)
