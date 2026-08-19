#!/usr/bin/env python
"""Realistic LARGE-request probe for gpt-5.6-sol: the small-chat concurrency probe reports
healthy while the brownout parks big vision+reasoning requests (2026-07-18/19 signature).
This sends ONE WebSurfer-shaped request per region — ~1MB screenshot image + tools +
reasoning effort high, streaming /v1/responses — and times it.

Prints per-region seconds (or TIMEOUT/ERROR); exits 0 if ANY region completes under
HEALTHY_S (default 90s), else 1. Emits the healthy region list on the last line as JSON.
"""
import asyncio
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentarena.llm_client import TRAPI_DEPLOY, _trapi_base_url  # noqa: E402
from agentarena.core.models import _trapi_token_provider  # noqa: E402

REGIONS = ["gcr/shared", "msraif/shared", "redmond/interactive"]
DEPLOY = TRAPI_DEPLOY.get("gpt-5.6-sol", "gpt-5.6-sol")
HEALTHY_S = float(os.environ.get("SOL_PROBE_HEALTHY_S", "90"))
TIMEOUT_S = float(os.environ.get("SOL_PROBE_TIMEOUT_S", "240"))


def big_png() -> str:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1224, 765), "#f8f8f8")
    d = ImageDraw.Draw(img)
    for i in range(0, 760, 24):
        d.rectangle([8, i, 1216, i + 18], outline="#888")
        d.text((14, i + 3), f"Row {i}: product card placeholder text with some detail", fill="#222")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


TOOLS = [{"type": "function", "name": f"tool_{i}", "description": "browser action " * 6,
          "parameters": {"type": "object", "properties": {
              "reasoning": {"type": "string"}, "target": {"type": "string"}},
              "required": ["reasoning"]}} for i in range(8)]


async def probe(region: str, b64: str) -> float:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=_trapi_token_provider()(), base_url=_trapi_base_url(region),
                         max_retries=0, timeout=TIMEOUT_S)
    t0 = time.time()
    stream = await client.responses.create(
        model=DEPLOY, stream=True, reasoning={"effort": "high", "summary": "auto"},
        tools=TOOLS,
        input=[{"role": "user", "content": [
            {"type": "input_text",
             "text": "You see a marketplace search page screenshot. Pick ONE tool call "
                     "that best continues inspecting products. Be brief."},
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"}]}])
    async for ev in stream:
        if getattr(ev, "type", "") == "response.completed":
            break
    return time.time() - t0


async def main() -> int:
    b64 = big_png()
    healthy = []
    for region in REGIONS:
        try:
            dt = await asyncio.wait_for(probe(region, b64), timeout=TIMEOUT_S)
            ok = dt < HEALTHY_S
            print(f"{region:22s} {dt:7.1f}s {'HEALTHY' if ok else 'SLOW'}", flush=True)
            if ok:
                healthy.append(region)
        except Exception as e:  # noqa: BLE001
            print(f"{region:22s} {'TIMEOUT' if isinstance(e, asyncio.TimeoutError) else type(e).__name__}",
                  flush=True)
    print(json.dumps(healthy))
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
