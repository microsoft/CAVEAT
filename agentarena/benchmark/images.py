"""Product images (gpt-image-1). Hero items (compliant + decoy) get a unique generated
photo; distractors reuse a small generated generic set per scenario (cost control). Images
are brand-neutral with no on-image text (so they cannot leak the answer), generated once and
committed. They are written into the Amazon server's ``backend/images`` dir (served at
``/images``) and mirrored into the versioned artifact tree.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from ..llm_client import generate_image
from .schema import ProductRow, ScenarioSpec

_COLORS = ["space gray", "silver", "matte black", "white", "navy blue", "graphite",
           "dark teal", "sand beige"]
_N_GENERIC = 4

# category-appropriate existing stock PNGs (committed) to fall back to if gen fails
_STOCK_FALLBACK = {
    "laptop": "laptop-generic.png",
}


def _photo_prompt(noun: str, descriptor: str) -> str:
    return (f"Professional e-commerce studio product photograph of a {descriptor} {noun}, "
            f"centered on a plain white seamless background, soft even lighting, high detail, "
            f"no text, no logos, no watermark, no people.")


def _hero_descriptor(scenario: ScenarioSpec, row: ProductRow, k: int) -> str:
    color = _COLORS[k % len(_COLORS)]
    sid = scenario.scenario_id
    if sid == "laptop":
        return f"modern thin {color} clamshell"
    return color


async def _gen(prompt: str, out: Path, *, force: bool, retries: int = 4) -> bool:
    if out.exists() and not force:
        return True
    for attempt in range(retries + 1):
        try:
            await generate_image(prompt, image_model="gpt-image-1", size="1024x1024",
                                 out_path=str(out))
            return True
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg or "Rate" in msg or "rate" in msg:
                await asyncio.sleep(2 * (attempt + 1) + 0.5 * attempt)  # backoff on rate limit
                continue
            print(f"    image gen failed for {out.name}: {e}")
            return False
    print(f"    image gen gave up (rate limited) for {out.name}")
    return False


async def generate_images(scenario: ScenarioSpec, rows: list[ProductRow], *,
                          image_dir: Path, mirror_dir: Optional[Path] = None,
                          concurrency: int = 2, force: bool = False) -> None:
    image_dir = Path(image_dir)
    image_dir.mkdir(parents=True, exist_ok=True)
    noun = scenario.noun.split()[-1]
    fallback = _STOCK_FALLBACK.get(scenario.scenario_id, "laptop-generic.png")
    sem = asyncio.Semaphore(concurrency)

    heroes = [r for r in rows if r.role in ("compliant", "decoy", "satisfice")]
    distractors = [r for r in rows if r.role == "distractor"]

    async def hero(r: ProductRow, k: int):
        async with sem:
            fn = f"{r.asin.lower()}.png"
            ok = await _gen(_photo_prompt(noun, _hero_descriptor(scenario, r, k)),
                           image_dir / fn, force=force)
            r.image = fn if ok else fallback
            r.image_tier = "hero" if ok else "stock"

    async def generic(k: int):
        async with sem:
            fn = f"{scenario.scenario_id}-generic-{k}.png"
            ok = await _gen(_photo_prompt(noun, _COLORS[k % len(_COLORS)]),
                           image_dir / fn, force=force)
            return fn if ok else fallback

    await asyncio.gather(*(hero(r, k) for k, r in enumerate(heroes)))
    generic_names = await asyncio.gather(*(generic(k) for k in range(_N_GENERIC)))
    for i, r in enumerate(distractors):
        r.image = generic_names[i % len(generic_names)]
        r.image_tier = "stock"

    if mirror_dir:
        mirror_dir = Path(mirror_dir)
        mirror_dir.mkdir(parents=True, exist_ok=True)
        seen = {r.image for r in rows} | set(generic_names)
        for fn in seen:
            src = image_dir / fn
            if src.exists():
                (mirror_dir / fn).write_bytes(src.read_bytes())
