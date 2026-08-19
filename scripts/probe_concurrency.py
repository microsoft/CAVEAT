#!/usr/bin/env python
"""Probe max safe concurrency per TRAPI region x model.

For each (region, model) fire batches of N concurrent minimal chat calls at escalating N and
record success rate + latency. The ceiling is the largest N with >= 0.9 success and no 429/503.
Emits a JSON summary {model: {region: max_ok_concurrency}} at the end.

  python scripts/probe_concurrency.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentarena.llm_client import _trapi_base_url, create_client, TRAPI_DEPLOY  # noqa: E402

REGIONS = ["msraif/shared", "gcr/shared", "redmond/interactive"]
MODELS = [m for m in (sys.argv[1:] or ["gpt-5.5", "gpt-4.1"])]
LEVELS = [4, 8, 16, 24, 32]
PROBE_CALL_TIMEOUT_SECONDS = 180


async def one_call(region, deploy):
    client, _ = create_client(model=deploy, base_url=_trapi_base_url(region))
    t = time.time()
    msgs = [{"role": "user", "content": "reply with the one word OK"}]
    # reasoning models (gpt-5.x) reject max_tokens/temperature and need room for reasoning tokens
    is_reasoning = deploy.startswith("gpt-5")
    kw = {"max_completion_tokens": 256} if is_reasoning else {"max_tokens": 5, "temperature": 0}
    try:
        await asyncio.wait_for(
            client.chat.completions.create(
                model=deploy, messages=msgs, **kw
            ),
            timeout=PROBE_CALL_TIMEOUT_SECONDS,
        )
        return ("ok", time.time() - t, None)
    except asyncio.TimeoutError:
        return (
            "err",
            time.time() - t,
            f"probe_timeout_{PROBE_CALL_TIMEOUT_SECONDS}s",
        )
    except Exception as e:
        code = getattr(e, "status_code", None) or type(e).__name__
        return ("err", time.time() - t, str(code))


async def batch(region, deploy, n):
    res = await asyncio.gather(*[one_call(region, deploy) for _ in range(n)])
    ok = sum(1 for s, _, _ in res if s == "ok")
    lats = sorted(d for s, d, _ in res if s == "ok")
    errs = {}
    for s, _, c in res:
        if s == "err":
            errs[str(c)] = errs.get(str(c), 0) + 1
    med = lats[len(lats) // 2] if lats else float("nan")
    p95 = lats[int(len(lats) * 0.95) - 1] if len(lats) > 1 else med
    return ok, n, med, p95, errs


async def main():
    summary = {}
    for model in MODELS:
        deploy = TRAPI_DEPLOY.get(model, model)
        summary[model] = {}
        for region in REGIONS:
            max_ok = 0
            print(
                f"\n=== {model} @ {region} (deploy={deploy}) ===",
                flush=True,
            )
            for n in LEVELS:
                ok, tot, med, p95, errs = await batch(region, deploy, n)
                rate = ok / tot
                print(
                    f"  N={n:>3}  ok={ok}/{tot} ({rate:.0%})  "
                    f"med={med:.1f}s p95={p95:.1f}s  errs={errs}",
                    flush=True,
                )
                if rate >= 0.9:
                    max_ok = n
                else:
                    break  # found ceiling
                await asyncio.sleep(2)  # brief cooldown between levels
            summary[model][region] = max_ok
    print(
        "\n===== MAX SAFE CONCURRENCY (>=90% ok) =====",
        flush=True,
    )
    print(json.dumps(summary, indent=2), flush=True)
    # aggregate per model across live regions
    for model in MODELS:
        tot = sum(summary[model].values())
        print(
            f"  {model}: aggregate across regions = {tot} concurrent",
            flush=True,
        )


asyncio.run(main())
