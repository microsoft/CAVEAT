"""Orchestrate generation of one scenario end-to-end: deterministic pool -> LLM copy ->
LLM instructions (faithfulness-gated) -> steering resolution -> images -> serialized,
versioned artifacts. Numbers are deterministic; only copy/instructions/images use the LLM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from . import serialize
from .copy_gen import generate_copy
from .faithfulness import make_instruction
from .images import generate_images
from .pool import generate_pool
from .preferences import build_preferences
from .scenarios import SCENARIOS
from .steering import resolve_steering

# the Amazon server image dir (served at /images) — generated photos are written here
AMAZON_IMAGE_DIR = (Path(__file__).resolve().parents[1] / "envs" / "amazon" / "server"
                    / "backend" / "images")

# category fallbacks (existing committed PNGs) when running --no-images
_STOCK = {
    "laptop": "laptop-generic.png", "monitor": "hp-27-4k-ips-monitor.png",
    "headphones": "sony-wh-1000xm5-wireless-noise-canceling-headphones.png",
    "robot_vacuum": "home-kitchen.png",
}


async def build_scenario(scenario_id: str, *, seed: int = 7, root: Optional[Path] = None,
                         with_images: bool = True, force_images: bool = False) -> Path:
    scenario = SCENARIOS[scenario_id]
    print(f"[{scenario_id}] generating pool (seed {seed}) ...")
    rows = generate_pool(scenario, seed)

    print(f"[{scenario_id}] generating product copy ({len(rows)} items) ...")
    await generate_copy(scenario, rows)
    flagged = [r.asin for r in rows if r.copy_status != "ok"]
    if flagged:
        print(f"  copy flagged (fell back): {flagged}")

    print(f"[{scenario_id}] generating + faithfulness-checking instructions ...")
    prefs = build_preferences(scenario)
    insts = await asyncio.gather(*(make_instruction(scenario, prefs[v]) for v in prefs))
    instructions = {gi.variant: gi for gi in insts}
    for v, gi in instructions.items():
        print(f"  [{v}] {gi.status} (tries={gi.tries})")

    steering = resolve_steering(scenario, rows)

    if with_images:
        print(f"[{scenario_id}] generating images (gpt-image-1) ...")
        await generate_images(scenario, rows, image_dir=AMAZON_IMAGE_DIR,
                             mirror_dir=serialize.scenario_dir(scenario_id, root) / "images",
                             force=force_images)
    else:
        for r in rows:
            r.image = _STOCK.get(scenario_id, "laptop-generic.png")

    d = serialize.write_artifacts(scenario, rows, prefs, instructions, steering,
                                  seed=seed, root=root,
                                  extra_meta={"with_images": with_images})
    print(f"[{scenario_id}] wrote artifacts -> {d}")
    return d


async def regen_instructions(scenario_id: str, *, root=None) -> dict:
    """Regenerate the ground-truth preferences + instructions (preserving pool/copy/images/
    steering). Rewrites preferences.json + instructions.json + meta."""
    import json
    scenario = SCENARIOS[scenario_id]
    prefs = build_preferences(scenario)
    insts = await asyncio.gather(*(make_instruction(scenario, prefs[v]) for v in prefs))
    instructions = {gi.variant: gi for gi in insts}
    d = serialize.scenario_dir(scenario_id, root)
    (d / "preferences.json").write_text(
        json.dumps({v: p.to_dict() for v, p in prefs.items()}, indent=2))
    (d / "instructions.json").write_text(
        json.dumps({v: gi.to_dict() for v, gi in instructions.items()}, indent=2))
    meta = serialize.load_meta(scenario_id, root)
    meta["instruction_status"] = {v: gi.status for v, gi in instructions.items()}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta["instruction_status"]
