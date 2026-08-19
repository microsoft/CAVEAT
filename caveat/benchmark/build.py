"""Orchestrate generation of one scenario end-to-end: deterministic pool -> LLM copy ->
LLM instructions (faithfulness-gated) -> steering resolution -> images -> serialized,
versioned artifacts. Numbers are deterministic; only copy/instructions/images use the LLM.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Optional

from . import serialize
from .copy_gen import generate_copy
from .faithfulness import make_instruction
from .images import generate_images
from .pool import (generate_pool_drawn, truthful_steering_index,
                   truthful_steering_sidecar)
from .preferences import build_preferences
from .scenarios import SCENARIOS
from .steering import resolve_steering

# the Amazon server image dir (served at /images) — generated photos are written here
AMAZON_IMAGE_DIR = (Path(__file__).resolve().parents[1] / "envs" / "amazon" / "server"
                    / "backend" / "images")

# category fallbacks (existing committed PNGs) when running --no-images
_STOCK = {
    "laptop": "laptop-generic.png",
}


async def build_scenario(scenario_id: str, *, seed: int = 7, root: Optional[Path] = None,
                         with_images: bool = True, force_images: bool = False) -> Path:
    scenario = SCENARIOS[scenario_id]
    print(f"[{scenario_id}] generating pool (seed {seed}) ...")
    # the DRAWN scenario, not the registry template: hard scenarios draw B, the authored block
    # and the serving placement per seed, and the artifacts must carry what the pool was built
    # against (scenario.json / catalog.json serving). Identical to the template for the
    # original five.
    rows, scenario = generate_pool_drawn(scenario, seed)

    print(f"[{scenario_id}] generating product copy ({len(rows)} items) ...")
    await generate_copy(scenario, rows)
    flagged = [r.asin for r in rows if r.copy_status != "ok"]
    if flagged:
        print(f"  copy flagged (fell back): {flagged}")

    if scenario_id.endswith("_steerhard_compact"):
        parent_id = scenario_id[:-len("_steerhard_compact")]
    elif scenario_id.endswith("_steerhard"):
        parent_id = scenario_id[:-len("_steerhard")]
    elif scenario_id.endswith("_hard"):
        parent_id = scenario_id[:-len("_hard")]
    else:
        parent_id = None
    truthful = bool((scenario.serving or {}).get("truthful"))
    if truthful and parent_id in SCENARIOS:
        # The successor normally keeps the original ground-truth preference
        # artifact literally byte-equivalent.  A declared semantic requirement is
        # the narrow exception: the parent instruction already states it, and the
        # successor now models it as a universal hard equality.
        try:
            prefs = serialize.load_preferences(parent_id, root)
        except FileNotFoundError:
            prefs = serialize.load_preferences(parent_id)
        semantic_requirements = (
            (scenario.serving or {}).get("truthful") or {}
        ).get("semantic_requirements") or {}
        if semantic_requirements:
            merged = {}
            for variant, parent_pref in prefs.items():
                declared = {
                    threshold.field: threshold
                    for threshold in scenario.preference(variant).thresholds
                    if threshold.field in semantic_requirements
                }
                if set(declared) != set(semantic_requirements) or any(
                        declared[key].value != expected
                        for key, expected in semantic_requirements.items()):
                    raise AssertionError(
                        f"{scenario_id}/{variant}: semantic requirements are not "
                        "exact hard thresholds")
                merged[variant] = dataclasses.replace(
                    parent_pref,
                    thresholds=list(parent_pref.thresholds)
                    + [declared[key] for key in semantic_requirements],
                )
            prefs = merged
    else:
        prefs = build_preferences(scenario)
    if parent_id and parent_id in SCENARIOS:
        # Hard clones copy the parent's committed instructions VERBATIM (scenario_id field
        # included): the preference-bearing fields are copied verbatim by construction
        # (scenarios.py), so byte-identical instruction text is both correct and the point —
        # the catalog is the ONLY thing that differs between a hard run and its parent.
        print(f"[{scenario_id}] copying instructions verbatim from parent {parent_id} ...")
        try:
            instructions = serialize.load_instructions(parent_id, root)
        except FileNotFoundError:
            instructions = serialize.load_instructions(parent_id)
    else:
        print(f"[{scenario_id}] generating + faithfulness-checking instructions ...")
        insts = await asyncio.gather(*(make_instruction(scenario, prefs[v]) for v in prefs))
        instructions = {gi.variant: gi for gi in insts}
        for v, gi in instructions.items():
            print(f"  [{v}] {gi.status} (tries={gi.tries})")

    truthful_sidecar = truthful_steering_sidecar(scenario, rows)
    steering = (truthful_steering_index(truthful_sidecar)
                if truthful_sidecar is not None else resolve_steering(scenario, rows))

    if truthful:
        # The pool already draws every row from the category's shared committed image set.
        # Generating 2,112 near-duplicate photos would be neither scientific nor deterministic.
        print(f"[{scenario_id}] reusing truthful shared category images ...")
    elif with_images:
        print(f"[{scenario_id}] generating images (gpt-image-1) ...")
        await generate_images(scenario, rows, image_dir=AMAZON_IMAGE_DIR,
                             mirror_dir=serialize.scenario_dir(scenario_id, root) / "images",
                             force=force_images)
    else:
        for r in rows:
            r.image = _STOCK.get(scenario_id, "laptop-generic.png")

    d = serialize.write_artifacts(scenario, rows, prefs, instructions, steering,
                                  seed=seed, root=root,
                                  extra_meta={"with_images": with_images},
                                  truthful_steering=truthful_sidecar)
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
