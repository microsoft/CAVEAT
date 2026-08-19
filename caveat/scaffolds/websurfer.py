"""Magentic-One MultimodalWebSurfer scaffold (autogen-ext 0.7.5, maintenance-mode pin).

A computer-use style web agent: each step the model sees a Set-of-Mark-annotated
screenshot (+ visible viewport text and the interactive-target list) and picks ONE
tool — click / input_text / scroll / hover / history_back / answer_question /
summarize_page. There is no model-controlled JavaScript or fetch: every
``page.evaluate`` in WebSurfer runs fixed grounding code (page_script.js), so the
only data channel is the rendered UI. That is the point of this scaffold — it
contrasts with browser-use's a11y-tree + JS extraction.

Deviations from other scaffolds, on purpose:
- Viewport is WebSurfer-native 1440x900 (its SoM geometry and 1224x765 MLM rescale
  are hard-coded to that size); forcing our usual 1280x900 would distort grounding.
- ``web_search`` and ``visit_url`` tools are REMOVED after construction: web_search
  goes to Bing (off-site), visit_url both offers a Bing escape (space-in-arg) and
  triggers full-document loads that reset the storefront DB (reset_on_load). A
  route guard additionally aborts non-local *navigations* (subresources still load),
  mirroring browseruse's prohibited_domains.
- model_info claims family=gpt-4o even for gpt-5.x: WebSurfer wipes its chat history
  every turn for families outside {gpt-4o,o1,o3,gpt-4,gpt-35} (upstream bug, frozen
  at 0.7.5) — without this the agent is memoryless.
- A short continuation message is sent EVERY turn (not just the first): WebSurfer pops
  the last incoming message into its "user request" prompt slot, so driving it with an
  empty message list makes the agent's own previous reply the request — observed as a
  degenerate answer_question echo loop (smoke test, 2026-07-17). A fresh driver message
  per turn is the orchestrator pattern WebSurfer was built for.
- The cell deadline is enforced at step boundaries: WebSurfer catches BaseException
  (including CancelledError), so asyncio.wait_for alone cannot kill a running loop.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import time

from ..core.models import ModelSpec
from ..core.scaffold import SCAFFOLDS, RawTrajectory, RunContext, Scaffold
from ..core.trajectory import Step
from ._browser import BrowserConfig

# tool turns surface as inner TextMessages shaped `name( {json-args} )`
_TOOL_RE = re.compile(r"^(\w+)\(\s*(\{.*\})\s*\)$", re.DOTALL)
_LOCAL_HOSTS = ("127.0.0.1", "localhost")
# tools that leave the site under test (Bing search / address-bar navigation)
_OFFSITE_TOOLS = ("web_search", "visit_url")


@SCAFFOLDS.register("websurfer")
class WebSurferScaffold(Scaffold):
    name = "websurfer"

    def supports(self, model: ModelSpec) -> tuple[bool, str]:
        if not model.has_vision:
            return False, "MultimodalWebSurfer needs a vision model (SoM screenshots)"
        return True, ""

    def run(self, ctx: RunContext) -> RawTrajectory:
        return asyncio.run(_run(ctx))


def _make_client(ctx: RunContext, effort_override: str | None = None, vision: bool = True,
                 timeout: float = 180.0, max_retries: int = 4):
    """Primary client on the model's pinned TRAPI region, with a sticky fallback twin
    on the 2nd pinned region (browseruse's 'max TRAPI' policy: a persistent region
    outage fails over inside the cell instead of losing it).

    ``effort_override`` substitutes the reasoning effort for THIS client only — used by
    the magentic-one scaffold to run the orchestrator (harness machinery) at a fixed
    effort while the surfer keeps the model's configured effort.
    ``vision=False`` declares the client non-vision so Magentic-One's
    ``_get_compatible_context`` strips accumulated screenshots from ledger/planning
    calls (the orchestrator context otherwise grows by ~1 image per round and later
    rounds slow to many minutes).

    The client translates autogen's create() onto the **/v1/responses** API: TRAPI
    serves gpt-5.x *function tools + reasoning_effort* only there — chat.completions
    400s on the combination ("Please use /v1/responses instead", probed 2026-07-17).
    Transport-level only: WebSurfer's prompts/tools/behavior are untouched."""
    import base64

    from autogen_core import FunctionCall
    from autogen_core.models import (AssistantMessage, CreateResult, FunctionExecutionResultMessage,
                                     ModelFamily, RequestUsage, SystemMessage, UserMessage)
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    def _to_input(messages) -> list:
        items: list = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                items.append({"role": "system",
                              "content": [{"type": "input_text", "text": msg.content}]})
            elif isinstance(msg, UserMessage):
                if isinstance(msg.content, str):
                    items.append({"role": "user",
                                  "content": [{"type": "input_text", "text": msg.content}]})
                else:
                    parts = []
                    for p in msg.content:
                        if isinstance(p, str):
                            parts.append({"type": "input_text", "text": p})
                        else:  # autogen_core.Image
                            buf = io.BytesIO()
                            p.image.save(buf, format="PNG")
                            b64 = base64.b64encode(buf.getvalue()).decode()
                            parts.append({"type": "input_image",
                                          "image_url": f"data:image/png;base64,{b64}"})
                    items.append({"role": "user", "content": parts})
            elif isinstance(msg, AssistantMessage):
                if isinstance(msg.content, str):
                    items.append({"role": "assistant",
                                  "content": [{"type": "output_text", "text": msg.content}]})
                else:  # list[FunctionCall] (WebSurfer keeps text-only history; be safe anyway)
                    for fc in msg.content:
                        items.append({"type": "function_call", "call_id": fc.id,
                                      "name": fc.name, "arguments": fc.arguments})
            elif isinstance(msg, FunctionExecutionResultMessage):
                for r in msg.content:
                    items.append({"type": "function_call_output", "call_id": r.call_id,
                                  "output": r.content})
        return items

    def _to_tools(tools) -> list:
        out = []
        for t in tools:
            s = t.schema if hasattr(t, "schema") else t
            out.append({"type": "function", "name": s["name"],
                        "description": s.get("description", ""),
                        "parameters": s.get("parameters", {})})
        return out

    class _ResponsesClient(OpenAIChatCompletionClient):
        """autogen chat client whose create() speaks /v1/responses (see _make_client
        docstring); sticky failover to a twin on the 2nd TRAPI region."""

        def __init__(self, *a, reasoning_effort=None, token_refresh=None, **kw):
            super().__init__(*a, **kw)
            self._effort = reasoning_effort
            self._token_refresh = token_refresh
            self._resp_model = kw["model"]
            self._fb = None
            self._use_fb = False

        def set_fallback(self, fb) -> None:
            self._fb = fb

        async def _create_responses(self, messages, tools, json_output, extra_create_args):
            # CU cells can run ~1h — refresh the bearer so it can't expire mid-cell
            # (AzureCliCredential provider caches and re-mints only near expiry).
            if self._token_refresh is not None:
                try:
                    self._client.api_key = self._token_refresh()
                except Exception:
                    pass
            req = dict(model=self._resp_model, input=_to_input(messages))
            if self._effort:
                # summary="auto" also gives the stream keep-alive traffic DURING reasoning
                req["reasoning"] = {"effort": self._effort, "summary": "auto"}
            if tools:
                req["tools"] = _to_tools(tools)
            if json_output is True:
                req["text"] = {"format": {"type": "json_object"}}
            for k, v in (extra_create_args or {}).items():
                req.setdefault(k, v)
            # STREAM the response: the TRAPI gateway idle-kills non-streaming requests while
            # long #high reasoning is still running server-side (observed: CLOSE-WAIT with
            # unread bytes + calls that can never complete). With streaming, bytes flow as
            # reasoning progresses and the client read-timeout applies per chunk-gap, so a
            # legitimately long think completes instead of dying at the total timeout.
            resp = None
            stream = await self._client.responses.create(stream=True, **req)
            async for event in stream:   # self._client = AsyncOpenAI
                if getattr(event, "type", "") == "response.completed":
                    resp = event.response
            if resp is None:
                raise RuntimeError("responses stream ended without response.completed")
            calls, texts = [], []
            for item in resp.output:
                if item.type == "function_call":
                    calls.append(FunctionCall(id=item.call_id, arguments=item.arguments,
                                              name=item.name))
                elif item.type == "message":
                    for c in item.content:
                        if getattr(c, "type", "") == "output_text":
                            texts.append(c.text)
            usage = RequestUsage(
                prompt_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
                completion_tokens=getattr(resp.usage, "output_tokens", 0) or 0)
            return CreateResult(finish_reason="function_calls" if calls else "stop",
                                content=calls if calls else "\n".join(texts),
                                usage=usage, cached=False)

        async def create(self, messages, *, tools=(), tool_choice="auto", json_output=None,
                         extra_create_args=None, cancellation_token=None):
            if self._use_fb:
                return await self._fb.create(messages, tools=tools, json_output=json_output)
            try:
                return await self._create_responses(messages, tools, json_output,
                                                    extra_create_args)
            except Exception:
                if self._fb is None:
                    raise
                self._use_fb = True   # the SDK already burned max_retries on the primary
                return await self._fb.create(messages, tools=tools, json_output=json_output)

    ep = ctx.model.openai_endpoint()
    # family=GPT_4O keeps history + selects the standard OpenAI multimodal transformer
    # (see module docstring); deployment names are unknown to autogen's registry, so
    # model_info is mandatory. (Only count_tokens/token bookkeeping use the chat path now.)
    # NOTE: the tools+reasoning_effort chat-completions 400 is a MODEL-level restriction
    # (PhyAGI's upstream returns the identical error), and PhyAGI proxies the streaming
    # /v1/responses API (probed 2026-07-17) — so every provider takes the responses path;
    # token refresh and the second-region fallback below are already TRAPI-conditional.
    info = {"vision": vision, "function_calling": True, "json_output": True,
            "family": ModelFamily.GPT_4O, "structured_output": False,
            "multiple_system_messages": True}
    # max_retries/timeout are AsyncOpenAI init kwargs (the SDK absorbs 429/5xx blips
    # inside each call before our region failover kicks in). 4, not browseruse's 16: a
    # hanging endpoint at 16 retries x 180s becomes a ~100-min silent black hole inside
    # one team round (observed in m1smoke2); region fallback + selfheal resume cover the
    # persistent-outage case that 16 was for.
    kw = dict(model=ep.model, base_url=ep.base_url, api_key=ep.api_key,
              max_retries=max_retries, timeout=timeout, model_info=info)
    effort = (effort_override or ep.reasoning_effort) if ep.reasoning else None
    refresh = None
    if ctx.model.provider == "trapi":
        try:
            from ..core.models import _trapi_token_provider
            refresh = _trapi_token_provider()
        except Exception:
            refresh = None
    client = _ResponsesClient(reasoning_effort=effort, token_refresh=refresh, **kw)
    try:
        from ..llm_client import TRAPI_MODEL_REGIONS, _logical, _trapi_base_url
        logical = _logical(ctx.model.deployment or ctx.model.name)
        regions = TRAPI_MODEL_REGIONS.get(logical, [])
        if ctx.model.provider == "trapi" and len(regions) >= 2:
            client.set_fallback(_ResponsesClient(
                reasoning_effort=effort, token_refresh=refresh,
                **{**kw, "base_url": _trapi_base_url(regions[1])}))
    except Exception:
        pass
    return client


async def _nav_guard(route, request) -> None:
    """Abort NAVIGATIONS to non-local hosts (subresources and data: URLs pass), so a
    stray link/redirect can't take the agent off the clone under test."""
    try:
        url = request.url
        if request.is_navigation_request() and not url.startswith(("data:", "about:")):
            host = url.split("/")[2].split(":")[0]
            if host not in _LOCAL_HOSTS:
                await route.abort()
                return
    except Exception:
        pass
    try:
        await route.continue_()
    except Exception:
        pass


async def _run(ctx: RunContext) -> RawTrajectory:  # noqa: C901
    from autogen_agentchat.messages import MultiModalMessage
    from autogen_agentchat.messages import TextMessage
    from autogen_core import CancellationToken
    from autogen_ext.agents.web_surfer import MultimodalWebSurfer
    from playwright.async_api import async_playwright

    bc = BrowserConfig.from_env(headless=ctx.headless)
    if bc.lib_path:
        os.environ["LD_LIBRARY_PATH"] = bc.lib_path + ":" + os.environ.get("LD_LIBRARY_PATH", "")
    # profile + downloads live on the cell's disk-backed work dir, not tmpfs /tmp
    udd = ctx.work_dir / "udd"
    dl = ctx.work_dir / "downloads"
    udd.mkdir(parents=True, exist_ok=True)
    dl.mkdir(parents=True, exist_ok=True)

    client = _make_client(ctx)
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
        # default_tools is a plain instance list; the model can only call what we pass.
        surfer.default_tools = [t for t in surfer.default_tools
                                if t["name"] not in _OFFSITE_TOOLS]
        task_text = ("(You are already on the website you need for this task. Work entirely "
                     "within it — do not navigate to any external URL, type a web address, "
                     "or use a web search engine. When the task is complete, reply with a "
                     "short final answer in plain text instead of calling a tool.)\n\n"
                     ) + ctx.task.instruction
        cell_timeout = float(os.environ.get("CAVEAT_CELL_TIMEOUT", "1200"))
        # WebSurfer pops the LAST incoming message into its "user request" prompt slot,
        # so every turn needs a fresh driver instruction (see module docstring).
        cont_text = ("Continue the task. If you have already successfully placed the order "
                     "(order confirmed), reply with a short plain-text final answer — no tool "
                     "call — summarizing what you purchased. Otherwise, take the next best "
                     "action toward completing the task.")

        async def _loop() -> None:
            nonlocal answer, prompt_tokens, completion_tokens
            pending = [TextMessage(content=task_text, source="user")]
            consecutive_errors = 0
            deadline = t0 + cell_timeout
            for i in range(1, ctx.max_steps + 1):
                if time.time() > deadline:
                    raise TimeoutError(f"cell timeout {int(cell_timeout)}s reached at step {i}")
                resp = await surfer.on_messages(pending, CancellationToken())
                pending = [TextMessage(content=cont_text, source="user")]
                cm = resp.chat_message
                if getattr(cm, "models_usage", None):
                    prompt_tokens += cm.models_usage.prompt_tokens or 0
                    completion_tokens += cm.models_usage.completion_tokens or 0
                action = reasoning = ""
                inner = (resp.inner_messages or [None])[0]
                m = None
                if inner is not None and isinstance(getattr(inner, "content", None), str):
                    m = _TOOL_RE.match(inner.content)
                if m:
                    try:
                        args = json.loads(m.group(2))
                        reasoning = str(args.pop("reasoning", ""))
                        action = f"{m.group(1)}({json.dumps(args)})"
                    except Exception:
                        action = inner.content[:500]
                url = ""
                try:
                    url = surfer._page.url  # noqa: SLF001 (no public accessor)
                except Exception:
                    pass
                # heartbeat into run.log: the watcher uses log freshness to spot wedged cells,
                # and the crash sweep greps these lines for infra signatures post-mortem.
                _kind = "action" if isinstance(cm, MultiModalMessage) else "text"
                print(f"[websurfer] step {i}/{ctx.max_steps} {_kind}: {action[:200]}",
                      file=sys.stderr, flush=True)
                if isinstance(cm, MultiModalMessage):
                    consecutive_errors = 0
                    shot = None
                    for part in cm.content:
                        if not isinstance(part, str):
                            buf = io.BytesIO()
                            part.image.save(buf, format="PNG")
                            shot = buf.getvalue()
                    steps.append(Step(index=i, action=action, reasoning=reasoning,
                                      url=url, screenshot=shot))
                    continue
                # TextMessage: a recoverable harness error, a QA-tool result, or the final answer
                text = str(cm.content)
                if text.startswith("Web surfing error:"):
                    consecutive_errors += 1
                    print(f"[websurfer] step {i} surf-error: {text.splitlines()[-1][:300]}",
                          file=sys.stderr, flush=True)
                    steps.append(Step(index=i, action=action or "error", reasoning=reasoning,
                                      url=url, note=text[:2000]))
                    if consecutive_errors >= 5:
                        raise RuntimeError("websurfer: 5 consecutive action errors; last: "
                                           + text.splitlines()[-1][:500])
                    continue
                consecutive_errors = 0
                if m:  # answer_question / summarize_page mid-task: record and keep going
                    steps.append(Step(index=i, action=action, reasoning=reasoning,
                                      url=url, note=text[:4000]))
                    continue
                answer = text
                steps.append(Step(index=i, action="final_answer", reasoning=reasoning,
                                  url=url, note=text[:4000]))
                return

        # step-boundary deadline above is the real cap (WebSurfer swallows CancelledError,
        # so wait_for's cancellation cannot reliably kill a turn); +600s backstop for wedges.
        await asyncio.wait_for(_loop(), timeout=cell_timeout + 600)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        print(f"[websurfer] ERROR {error}", file=sys.stderr, flush=True)
    finally:
        if surfer is not None:
            try:
                await surfer.close()   # closes page + the context/playwright we handed in
            except Exception:
                pass
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
        if pw is not None:
            try:
                await pw.stop()        # belt-and-braces for init failures before surfer exists
            except Exception:
                pass
    stats = {"seconds": round(time.time() - t0, 1),
             "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if error:
        stats["error"] = error
    return RawTrajectory(steps=steps, answer=str(answer), stats=stats)
