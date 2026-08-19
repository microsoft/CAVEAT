#!/usr/bin/env python
"""Probe TRAPI region health for the pilot models and emit a TRAPI_REGIONS_OVERRIDE JSON.

For each model, probe every candidate region with a tiny chat call. Build a routing list that puts the
LIVE regions first; if only ONE region is live, DUPLICATE it into the fallback slot so the browser-use
scaffold's fallback_llm (regions[1]) hits a live backend instead of a dead one. Falls back to the static
TRAPI_MODEL_REGIONS order if a model has no live region (so the run can still attempt + retry).

  eval "export TRAPI_REGIONS_OVERRIDE=$(python scripts/probe_regions.py gpt-5.5 gpt-4.1)"
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentarena.llm_client import (TRAPI_MODEL_REGIONS, TRAPI_DEPLOY, PHYAGI_MODELS,  # noqa: E402
                                   _logical, _phyagi_key, _trapi_base_url, create_client)


async def probe_one(deploy, region):
    try:
        client, _ = create_client(deploy, base_url=_trapi_base_url(region))
        await asyncio.wait_for(client.chat.completions.create(
            model=deploy, messages=[{"role": "user", "content": "ok"}],
            # This is an endpoint-health probe, not a reasoning benchmark.
            # A 2,000-token allowance made healthy low-throughput deployments
            # miss the old 40 s wall even for the one-word prompt.  Keep the
            # request genuinely tiny while allowing normal queue/first-token
            # latency to distinguish a slow live route from an outage.
            max_completion_tokens=256), timeout=90)
        return True
    except Exception:
        return False


async def probe_phyagi(logical):
    """Is PhyAGI a live fallback net for this model? (key present + serves model + answers)"""
    key = _phyagi_key()
    if not key or logical not in PHYAGI_MODELS:
        return False
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(max_retries=1, api_key=key,
                             base_url=os.environ.get("PHYAGI_GATEWAY_URL",
                                                     "http://gateway.phyagi.net/api"))
        await asyncio.wait_for(client.chat.completions.create(
            model=logical, messages=[{"role": "user", "content": "ok"}],
            max_completion_tokens=2000), timeout=40)
        return True
    except Exception:
        return False


async def main(models):
    live_only = os.environ.get("AGENTARENA_PROBE_LIVE_ONLY") == "1"
    # Large publication campaigns rotate independent browser-use workers across
    # all three TRAPI regions.  The dated TRAPI_MODEL_REGIONS pins are useful
    # defaults, but they intentionally omit historically flaky regions for some
    # models and therefore cannot discover capacity that has recovered today.
    # Opt in to probing the complete region surface without changing ordinary
    # callers or the registry defaults.
    probe_all_regions = (
        os.environ.get("AGENTARENA_PROBE_ALL_REGIONS") == "1"
    )
    include_redmond = (
        os.environ.get("AGENTARENA_PROBE_INCLUDE_REDMOND") == "1"
    )
    # First, probe live regions per model.
    info = {}
    for m in models:
        logical = _logical(m)
        deploy = TRAPI_DEPLOY.get(logical, logical)
        cand = (
            ["gcr/shared", "msraif/shared", "redmond/interactive"]
            if probe_all_regions
            else TRAPI_MODEL_REGIONS.get(logical)
            or ["msraif/shared", "gcr/shared", "redmond/interactive"]
        )
        # Redmond has historically flapped between low-rate-limit and full
        # capacity.  Ordinary callers retain the conservative exclusion;
        # campaigns that explicitly opt in must pair this health result with a
        # conservative per-region quota or a fresh concurrency probe.
        if not include_redmond:
            cand = [r for r in cand if r != "redmond/interactive"] or cand
        results = await asyncio.gather(*[probe_one(deploy, r) for r in cand])
        live = [r for r, ok in zip(cand, results) if ok]
        dead = [r for r, ok in zip(cand, results) if not ok]
        info[logical] = (cand, live, dead)

    # Assign PRIMARY regions trying to give each model a distinct primary (when it has a choice), so
    # concurrently-running model passes don't all hammer one region. Fallback slot = a live region
    # (prefer a different live one; if only one live, reuse it so the scaffold fallback still hits a
    # live backend instead of a dead one).
    taken = set()
    override = {}
    # Most-constrained model (fewest live regions) claims its primary first, so a model with a single
    # live region keeps it and a model with options is steered onto a different region.
    for logical in sorted(info, key=lambda k: len(info[k][1])):
        cand, live, dead = info[logical]
        if live_only:
            # Campaign certification consumes this as evidence.  Never retain
            # a dead candidate in that mode: an empty list must fail closed.
            order = live
        elif not live:
            order = cand                                   # nothing healthy — keep static order
        elif len(live) == 1 and await probe_phyagi(logical):
            # Single live TRAPI region + a LIVE PhyAGI net (2026-07-09 policy): emit ONLY the live
            # region. Duplicating it would make the browseruse fallback_llm a same-region twin that
            # shares the outage/rate-limit; with len(regions)<2 the scaffold instead builds its
            # PhyAGI fallback (scaffolds/browseruse.py) — TRAPI stays primary, PhyAGI unblocks.
            order = live
            print(f"# {logical}: single live region + PhyAGI net -> fallback goes to PhyAGI",
                  file=sys.stderr)
        else:
            primary = next((r for r in live if r not in taken), live[0])
            taken.add(primary)
            rest_live = [r for r in live if r != primary]
            fb = rest_live[0] if rest_live else primary    # diff live region, else reuse the live primary
            order = [primary, fb] + [r for r in (live + dead) if r not in (primary, fb)]
        override[logical] = order
        print(f"# {logical}: live={live} dead={dead} -> {order}", file=sys.stderr)
    print(json.dumps(override))


if __name__ == "__main__":
    models = sys.argv[1:] or ["gpt-5.5", "gpt-4.1"]
    asyncio.run(main(models))
