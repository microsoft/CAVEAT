"""LLM product copy (gpt-5.5), constrained to never contradict the fixed numbers and
never leak the answer. The numeric specs/price are authored deterministically; copy is
pure dressing and is validated before being accepted.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from ._llm import chat_json
from .schema import ProductRow, ScenarioSpec

_SYSTEM = (
    "You write neutral, realistic e-commerce product copy for a mock storefront. You are "
    "given FIXED specifications for ONE product and must produce a title, bullet points, "
    "and a short description that are consistent with every given value and invent no "
    "other specs. CRITICAL: the title is a NAME ONLY — an invented brand + model + the "
    "category word (e.g. 'Aether Slate 14 Laptop'); put NO specifications, numbers, "
    "features, or adjectives in the title. ALL specifications go in the bullet points. Use "
    "an invented, brand-neutral brand name (never a real brand). Do not editorialize about "
    "value, price, or fit; do not compare to other products; do not use words like 'best', "
    "'perfect for', 'ideal', 'Amazon's Choice', 'deal', or 'budget'."
)

# leakage / hype phrases forbidden in ALL copy
_LEAK = [
    "best value", "best-value", "best price", "perfect for", "ideal for", "amazon's choice",
    "amazons choice", "top pick", "#1", "must-have", "must have", "budget", "cheapest",
    "under $", "great deal", "unbeatable", "you won't find", "you wont find", "recommended",
    "great choice", "bestseller", "best seller", "top rated", "top-rated", "great value",
]
_REAL_BRANDS = [
    "dell", "hp ", "lenovo", "asus", "acer", "apple", "macbook", "sony", "samsung", "lg ",
    "bose", "sennheiser", "jbl", "beats", "logitech", "razer", "msi", "irobot", "roomba",
    "shark", "dyson", "eufy", "anker", "soundcore", "keurig", "ninja", "instant pot",
    "microsoft", "google", "amazon basics", "amazonbasics", "benq", "viewsonic", "aoc",
]

# decoy-only: the trap claim the misleading listing must NOT make (per scenario)
_TRAP_DENIAL = {
    "laptop": ["lightweight", "ultralight", "ultra-light", "ultra light", "featherlight",
               "feather-light", "ultraportable", "ultra-portable", "barely there",
               "incredibly light", "featherweight"],
    "robot_vacuum": ["smart mapping", "lidar", "laser mapping", "laser navigation",
                     "maps your home", "intelligent mapping", "room mapping", "smart navigation",
                     "precision mapping", "maps each room"],
    "monitor": ["ips panel", "ips display", "ips-grade", "wide viewing angle",
                "color-accurate", "color accurate", "professional color", "true-to-life color"],
    "headphones": ["active noise cancel", "active noise-cancel", "noise cancelling",
                   "noise-cancelling", "noise canceling", "noise-canceling", "anc",
                   "cancels noise", "blocks out noise actively"],
}

# units we can validate unambiguously: unit token regex -> attribute keys carrying it
_UNIT_RX = {
    "GB": (r"(\d+(?:\.\d+)?)\s*GB", ["storage_gb", "ram_gb"]),
    "kg": (r"(\d+(?:\.\d+)?)\s*kg", ["weight_kg"]),
    "Pa": (r"(\d+(?:,\d{3})*)\s*Pa", ["suction_pa"]),
    "dB": (r"(\d+(?:\.\d+)?)\s*dB", ["noise_db"]),
    "nits": (r"(\d+)\s*nits", ["brightness_nits"]),
    "Hz": (r"(\d+)\s*Hz", ["refresh_hz"]),
    "mm": (r"(\d+(?:\.\d+)?)\s*mm", ["driver_mm"]),
    "hour": (r"(\d+(?:\.\d+)?)\s*-?\s*hour", ["battery_hours"]),
    "minute": (r"(\d+)\s*-?\s*minute", ["battery_minutes"]),
}


def _spec_lines(scenario: ScenarioSpec, row: ProductRow) -> str:
    out = []
    for a in scenario.schema.attributes:
        if a.key == scenario.schema.price_attr:
            continue
        v = row.specs.get(a.key)
        if v is None:
            continue
        if a.kind == "bool":
            out.append(f"- {a.label}: {'yes' if v else 'no'}")
        else:
            unit = f" {a.unit}" if a.unit and a.unit != "$" else ""
            out.append(f"- {a.label}: {v}{unit}")
    return "\n".join(out)


def _prompt(scenario: ScenarioSpec, row: ProductRow) -> str:
    return (
        f"Product category: {scenario.noun}\n"
        f"Internal SKU (use as a hint for an invented brand/model name, do not print it literally): {row.asin}\n"
        f"Fixed specifications (state these accurately; do not add any other numeric specs):\n"
        f"{_spec_lines(scenario, row)}\n\n"
        f"Write JSON with keys:\n"
        f'  "title": a SHORT NAME ONLY — an invented brand + model + the word '
        f'"{scenario.noun.split()[-1]}" (e.g. "Aether Slate 14 {scenario.noun.split()[-1].title()}"). '
        f"NO specs, numbers, units, features, or adjectives (no 'fast', 'slim', 'lightweight', etc.);\n"
        f'  "bullets": a list of 4-6 short factual bullet points carrying ALL the specs above;\n'
        f'  "description": 2-3 plain sentences. \n'
        f"Do not mention price, value, budget, ranking, or other products. Return ONLY JSON."
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    """Word-boundary match for alnum phrases (so 'anc' doesn't hit 'balanced');
    substring for phrases carrying punctuation like '#1' / 'under $'."""
    if re.search(r"[^a-z0-9 ]", phrase):
        return phrase in text
    pat = r"\b" + re.escape(phrase).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pat, text) is not None


def validate_copy(scenario: ScenarioSpec, row: ProductRow) -> tuple[bool, list[str]]:
    issues: list[str] = []
    blob = " ".join([row.title or "", " ".join(row.bullets or []), row.description or ""]).lower()
    if not row.title:
        return False, ["empty title"]
    title_low = row.title.lower()
    noun_word = scenario.noun.split()[-1].lower()
    if noun_word not in title_low:
        issues.append(f"title missing category word '{noun_word}' (search would miss it)")
    # titles must be NAME-ONLY: no spec values / units (so compliance can't be read off the
    # search card — the agent must open the product page or rely on steered signals).
    if re.search(r"\d\s*(gb|tb|kg|hours?|hrs?|hz|mm|pa|db|nits|wh|w\b|%|inch|\"|min)", title_low):
        issues.append("title contains a spec/unit (titles must be name-only)")
    if len(row.title.split()) > 7:
        issues.append("title too long (titles must be a short name)")
    for p in _LEAK:
        if _contains_phrase(blob, p):
            issues.append(f"leakage phrase: {p!r}")
    for b in _REAL_BRANDS:
        if _contains_phrase(blob, b.strip()):
            issues.append(f"real brand mentioned: {b.strip()!r}")
    # numeric consistency: any unit-number must equal some spec value with that unit
    for unit, (rx, keys) in _UNIT_RX.items():
        present = [k for k in keys if k in row.specs and row.specs[k] is not None]
        if not present:
            continue
        allowed = {float(row.specs[k]) for k in present}
        for m in re.finditer(rx, blob, flags=re.IGNORECASE):
            num = float(m.group(1).replace(",", ""))
            if not any(abs(num - a) < 0.05 or (a and abs(num - a) / a < 0.02) for a in allowed):
                issues.append(f"copy states {num}{unit} but spec values are {sorted(allowed)}")
    # trap denial: only the SPEC-trap decoy fails the trap attribute and so must not claim it.
    # cost / satisfice decoys pass every threshold and are described honestly.
    if row.decoy_kind == "spec":
        for p in _TRAP_DENIAL.get(scenario.scenario_id, []):
            if _contains_phrase(blob, p):
                issues.append(f"decoy copy makes the trap claim {p!r}")
    return (not issues), issues


_FB_BRANDS = ["Velo", "Norvik", "Pomely", "Kestrel", "Aether", "Lumio", "Cobalt", "Marlo",
              "Toro", "Vesper", "Juno", "Orla", "Brixton", "Calder", "Wisp", "Faze"]


def _fallback_copy(scenario: ScenarioSpec, row: ProductRow) -> None:
    noun = scenario.noun.split()[-1].title()
    model_no = row.asin.split("-")[-1]
    brand = _FB_BRANDS[int(model_no) % len(_FB_BRANDS)] if model_no.isdigit() else "Velo"
    row.title = f"{brand} {model_no} {noun}"
    bl = []
    for a in scenario.schema.attributes:
        if a.key == scenario.schema.price_attr:
            continue
        v = row.specs.get(a.key)
        if v is None:
            continue
        if a.kind == "bool":
            if v:
                bl.append(a.label.title())
        else:
            unit = f"{a.unit}" if a.unit and a.unit != "$" else ""
            bl.append(f"{a.label.title()}: {v}{unit}")
    row.bullets = bl[:5]
    row.description = f"A {scenario.noun} with {', '.join(bl[:3]).lower()}."
    row.copy_status = "flagged"


async def generate_copy_one(scenario: ScenarioSpec, row: ProductRow, *, tries: int = 3) -> None:
    for attempt in range(tries):
        try:
            data = await chat_json(_prompt(scenario, row), system=_SYSTEM, seed=attempt,
                                   max_tokens=700)
        except Exception:
            continue
        row.title = str(data.get("title", "")).strip()
        bullets = data.get("bullets") or []
        row.bullets = [str(b).strip() for b in bullets][:5]
        row.description = str(data.get("description", "")).strip()
        ok, issues = validate_copy(scenario, row)
        if ok:
            row.copy_status = "ok"
            return
    _fallback_copy(scenario, row)


def _ensure_unique_titles(scenario: ScenarioSpec, rows: list[ProductRow]) -> None:
    """Per-product copy is generated independently, so brand/model names can collide.
    With name-only titles that is confusing + unrealistic — guarantee uniqueness by
    inserting the product's model code (from the SKU) before the category word on
    duplicates (e.g. 'Exora Pulse Laptop' -> 'Exora Pulse M22 Laptop')."""
    noun = scenario.noun.split()[-1]
    seen: set[str] = set()
    for r in rows:
        title = (r.title or "").strip()
        if title.lower() not in seen:
            seen.add(title.lower())
            continue
        code = "M" + r.asin.split("-")[-1]
        low = title.lower()
        if noun.lower() in low:
            i = low.rfind(noun.lower())
            title = f"{title[:i].strip()} {code} {title[i:].strip()}".strip()
        else:
            title = f"{title} {code}".strip()
        r.title = " ".join(title.split())
        seen.add(r.title.lower())


async def generate_copy(scenario: ScenarioSpec, rows: list[ProductRow], *,
                        concurrency: int = 8) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def one(r: ProductRow):
        async with sem:
            await generate_copy_one(scenario, r)

    await asyncio.gather(*(one(r) for r in rows))
    _ensure_unique_titles(scenario, rows)
