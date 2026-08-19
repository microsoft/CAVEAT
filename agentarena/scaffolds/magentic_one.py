"""Canonical Magentic-One scaffold: MultimodalWebSurfer INSIDE MagenticOneGroupChat.

The `websurfer` scaffold drives the surfer solo with a constant continuation message —
that reduction removes Magentic-One's documented anti-stall machinery and produced
degenerate answer_question loops (39-65 identical calls) in cu_v1. This scaffold runs
the system as published: the Magentic-One orchestrator maintains task/progress ledgers,
checks `is_progress_being_made` / `is_in_loop` every round, redirects the surfer with a
fresh instruction, re-plans after `max_stalls` stalled rounds, and produces the final
answer. Orchestrator and surfer share ONE model client (single-model purity — the
orchestrator is the same LLM under test, not a second model).

Budget mapping: `max_turns=ctx.max_steps` — one orchestrator round = one surfer action,
so the step budget still counts atomic UI actions; ledger calls are text-only overhead.
Everything else (Responses-API client, TRAPI region fallback, bearer refresh, stripped
web_search/visit_url tools, local-navigation guard, WebSurfer-native 1440x900 viewport,
step-boundary deadline) is inherited from the websurfer scaffold's helpers.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import time

from ..core.models import ModelSpec
from ..core.scaffold import SCAFFOLDS, RawTrajectory, RunContext, Scaffold
from ..core.trajectory import Step
from ._browser import BrowserConfig
from .websurfer import _OFFSITE_TOOLS, _TOOL_RE, _make_client, _nav_guard

_ORCH = "MagenticOneOrchestrator"


@SCAFFOLDS.register("magentic-one")
class MagenticOneScaffold(Scaffold):
    name = "magentic-one"

    def supports(self, model: ModelSpec) -> tuple[bool, str]:
        if not model.has_vision:
            return False, "Magentic-One WebSurfer needs a vision model (SoM screenshots)"
        return True, ""

    def run(self, ctx: RunContext) -> RawTrajectory:
        import os as _os
        import threading

        # Unkillable hard budget: a daemon thread that ends the worker process outright
        # if the cell overshoots its budget + grace. Transient TRAPI brownouts produced
        # parked requests that defeated every in-process async guard (2026-07-17); a
        # plain thread + os._exit cannot be wedged by event-loop pathology. The runner
        # sees a dead worker with no summary -> the cell re-runs on the next resume.
        cell_timeout = float(_os.environ.get("AGENTARENA_CELL_TIMEOUT", "1200"))

        def _reaper() -> None:
            time.sleep(cell_timeout + 1200)
            print(f"[magentic-one] REAPER: cell exceeded hard budget "
                  f"({int(cell_timeout)}+1200s) — os._exit(3)", file=sys.stderr, flush=True)
            _os._exit(3)

        threading.Thread(target=_reaper, daemon=True).start()
        return asyncio.run(_run(ctx))


async def _run(ctx: RunContext) -> RawTrajectory:  # noqa: C901
    import json as _json

    from autogen_agentchat.base import TaskResult
    from autogen_agentchat.messages import MultiModalMessage, TextMessage
    from autogen_agentchat.teams import MagenticOneGroupChat
    from autogen_core import CancellationToken
    from autogen_ext.agents.web_surfer import MultimodalWebSurfer
    from playwright.async_api import async_playwright

    bc = BrowserConfig.from_env(headless=ctx.headless)
    if bc.lib_path:
        os.environ["LD_LIBRARY_PATH"] = bc.lib_path + ":" + os.environ.get("LD_LIBRARY_PATH", "")
    udd = ctx.work_dir / "udd"
    dl = ctx.work_dir / "downloads"
    udd.mkdir(parents=True, exist_ok=True)
    dl.mkdir(parents=True, exist_ok=True)

    # Surfer client: 600s timeout / 2 retries. Team-mode surfer calls (exhaustive-audit
    # orchestrator instructions x #high reasoning) legitimately exceed the 180s default —
    # observed as CLOSE-WAIT socket graveyards + ~30-min timeout-retry-fallback episodes
    # per round (cu_v3 wedge forensics). Long thinks must COMPLETE, not die and retry.
    client = _make_client(ctx, timeout=600.0, max_retries=2)
    # The orchestrator's per-round ledger JSON calls are harness machinery, not the model
    # under test; at effort=high they cost minutes/round for sol (smoke: ~5min/round →
    # cells censor at 14-22 rounds). Pin them to a uniform fixed effort for BOTH models;
    # the surfer — the acting model — keeps the configured #<effort>. vision=False makes
    # _get_compatible_context strip accumulated screenshots from ledger calls — without
    # it the ledger context grows ~1 image/round and later rounds take 10-20 minutes.
    orch_client = _make_client(
        ctx, effort_override=os.environ.get("AGENTARENA_ORCH_EFFORT", "medium"),
        vision=False)
    steps: list[Step] = []
    answer = ""
    error = None
    prompt_tokens = completion_tokens = 0
    t0 = time.time()
    pw = surfer = context = None
    try:
        pw = await async_playwright().start()
        context = await pw.chromium.launch_persistent_context(
            str(udd), executable_path=bc.executable, headless=ctx.headless,
            args=bc.args, env=bc.child_env())
        await context.route("**/*", _nav_guard)
        surfer = MultimodalWebSurfer(
            name="websurfer", model_client=client, start_page=ctx.start_url,
            headless=ctx.headless, playwright=pw, context=context,
            downloads_folder=str(dl), animate_actions=False, to_save_screenshots=False)
        surfer.default_tools = [t for t in surfer.default_tools
                                if t["name"] not in _OFFSITE_TOOLS]
        team = MagenticOneGroupChat([surfer], model_client=orch_client,
                                    max_turns=ctx.max_steps, max_stalls=3)
        task_text = ("(The browser already shows the website you need for this task. Work "
                     "entirely within it — do not navigate to any external URL, type a web "
                     "address, or use a web search engine.)\n\n") + ctx.task.instruction
        cell_timeout = float(os.environ.get("AGENTARENA_CELL_TIMEOUT", "1200"))
        deadline = t0 + cell_timeout
        tok = CancellationToken()

        async def _consume() -> None:
            nonlocal answer, prompt_tokens, completion_tokens
            idx = 0
            pending_action = pending_reasoning = pending_orch = ""

            def _url() -> str:
                try:
                    return surfer._page.url  # noqa: SLF001
                except Exception:
                    return ""

            it = team.run_stream(task=task_text, cancellation_token=tok).__aiter__()
            next_task = None
            # Opt-in fast-fail for the TRAPI sol brownout lottery (2026-07-19): when the
            # service parks this cell's request stream (~150-475 s/step vs ~26 healthy),
            # abort early so the selfheal retry gets a fresh connection instead of grinding
            # 75+ min to a give-up. Unset/0 (the default, and all main runs) = disabled.
            starve_spst = float(os.environ.get("AGENTARENA_STARVE_ABORT_SPST", "0") or 0)
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    tok.cancel()
                    raise TimeoutError(
                        f"cell timeout {int(cell_timeout)}s reached at step {idx}")
                if starve_spst and idx >= 4 and (time.time() - t0) / idx > starve_spst:
                    tok.cancel()
                    raise TimeoutError(
                        f"STARVE-ABORT: {(time.time() - t0) / idx:.0f}s/step after {idx} steps")
                # Bound the SILENCE between events WITHOUT relying on cancelling
                # __anext__ — autogen's runtime can swallow that cancellation, turning
                # asyncio.wait_for into a permanent hang (observed: cells frozen at
                # steps 10-12 with no error). Race against a timer instead; on timeout
                # abandon the stuck task (the process teardown reaps it) and error out.
                if next_task is None:
                    next_task = asyncio.ensure_future(it.__anext__())
                done, _pending = await asyncio.wait({next_task},
                                                    timeout=min(remaining, 1500))
                if not done:
                    tok.cancel()
                    next_task.cancel()
                    raise TimeoutError(f"no team event for 1500s after step {idx}")
                task, next_task = next_task, None
                try:
                    ev = task.result()
                except StopAsyncIteration:
                    break
                if isinstance(ev, TaskResult):
                    for m in reversed(ev.messages or []):
                        if isinstance(m, TextMessage) and m.source == _ORCH:
                            answer = str(m.content)
                            break
                    continue
                usage = getattr(ev, "models_usage", None)
                if usage:
                    prompt_tokens += usage.prompt_tokens or 0
                    completion_tokens += usage.completion_tokens or 0
                src = getattr(ev, "source", "")
                if src == _ORCH and isinstance(ev, TextMessage):
                    # the per-round instruction (or final answer) — folded into the next step
                    pending_orch = str(ev.content)
                    answer = pending_orch  # last orchestrator text = final answer fallback
                    continue
                if src != "websurfer":
                    continue
                if isinstance(ev, TextMessage):
                    m = _TOOL_RE.match(ev.content or "")
                    if m:
                        try:
                            args = _json.loads(m.group(2))
                            pending_reasoning = str(args.pop("reasoning", ""))
                            pending_action = f"{m.group(1)}({_json.dumps(args)})"
                        except Exception:
                            pending_action = (ev.content or "")[:500]
                        continue
                    # QA answer / harness error / plain reply — a step of its own
                    idx += 1
                    text = str(ev.content or "")
                    print(f"[magentic-one] step {idx} text: {pending_action[:160]}",
                          file=sys.stderr, flush=True)
                    reasoning = pending_reasoning
                    if pending_orch:
                        reasoning = f"[orchestrator] {pending_orch[:800]}\n{reasoning}"
                    steps.append(Step(index=idx, action=pending_action or "reply",
                                      reasoning=reasoning, url=_url(), note=text[:4000]))
                    pending_action = pending_reasoning = pending_orch = ""
                elif isinstance(ev, MultiModalMessage):
                    idx += 1
                    shot = None
                    for part in ev.content:
                        if not isinstance(part, str):
                            buf = io.BytesIO()
                            part.image.save(buf, format="PNG")
                            shot = buf.getvalue()
                        elif not pending_action:
                            # team streams don't carry the surfer's inner tool-call message,
                            # but the observation text opens with its own action description
                            # ("I clicked 'Add to Cart'. ..." / "I typed 'backpack' into ...")
                            pending_action = part.strip().split("\n", 1)[0][:300]
                    print(f"[magentic-one] step {idx} action: {pending_action[:160]}",
                          file=sys.stderr, flush=True)
                    reasoning = pending_reasoning
                    if pending_orch:
                        reasoning = f"[orchestrator] {pending_orch[:800]}\n{reasoning}"
                    steps.append(Step(index=idx, action=pending_action, reasoning=reasoning,
                                      url=_url(), screenshot=shot))
                    pending_action = pending_reasoning = pending_orch = ""

        await asyncio.wait_for(_consume(), timeout=cell_timeout + 600)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        print(f"[magentic-one] ERROR {error}", file=sys.stderr, flush=True)
    finally:
        if surfer is not None:
            try:
                await asyncio.wait_for(surfer.close(), timeout=60)
            except Exception:
                pass
        for c in (client, orch_client):
            if c is not None:
                try:
                    await asyncio.wait_for(c.close(), timeout=30)
                except Exception:
                    pass
        if pw is not None:
            try:
                await asyncio.wait_for(pw.stop(), timeout=30)
            except Exception:
                pass
    stats = {"seconds": round(time.time() - t0, 1),
             "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if error:
        stats["error"] = error
    return RawTrajectory(steps=steps, answer=str(answer), stats=stats)
