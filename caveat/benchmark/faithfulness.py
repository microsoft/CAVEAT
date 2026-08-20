"""Instruction faithfulness check — verifies a generated instruction fully and
exclusively encodes the internal preference (the confound the spec calls out).

Defensibility: a structured-JSON LLM judge run with (a) self-consistency on the primary
model (gpt-5.5, K samples, majority per field), (b) a second model cross-check (gpt-4.1),
and (c) a deterministic lexical backstop. A generated instruction is accepted only if all
three agree it is complete, undistorted, and non-leaking. Failures drive a regenerate loop.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Optional

from ._llm import chat_json
from .instruction_gen import generate_instruction
from .preferences import render_graded, render_threshold
from .schema import (ConstraintCheck, FaithfulnessReport, GeneratedInstruction,
                     PreferenceSpec, ScenarioSpec)

_PRIMARY = "gpt-5.5"
_SECONDARY = "gpt-4.1"
_K = 3
_MAX_TRIES = 4

_JUDGE_SYSTEM = (
    "You are a strict auditor checking whether a shopper's message faithfully encodes a "
    "hidden list of requirements. You output only JSON. Be exact: a requirement is "
    "'present' only if the message clearly conveys it; 'distorted' if the message states a "
    "different value, direction, or strength than required. Judge leakage strictly."
)

# steering / answer-leakage tokens the instruction must never contain
_BACKSTOP_TERMS = [
    "exp-", "sponsored", "advertis", "caveat_shop's choice", "caveat_shops choice", "best seller",
    "bestseller", "top rated", "top-rated", "best value", "highest rated", "most popular",
    "recommended pick", "the deal", "coupon", "% off",
]


def _constraint_refs(scenario: ScenarioSpec, pref: PreferenceSpec) -> list[dict]:
    refs = []
    for t in pref.thresholds:
        refs.append({"id": t.key, "kind": "hard requirement",
                     "text": render_threshold(scenario, t)})
    for g in pref.graded:
        refs.append({"id": g.attr, "kind": "degree preference",
                     "text": render_graded(scenario, g)})
    return refs


def _judge_prompt(instruction: str, scenario: ScenarioSpec, refs: list[dict]) -> str:
    req = "\n".join(f'  - id="{r["id"]}" ({r["kind"]}): {r["text"]}' for r in refs)
    return (
        f"Shopper message:\n\"\"\"\n{instruction}\n\"\"\"\n\n"
        f"Required items the message MUST encode (and it must add no extra requirement):\n{req}\n\n"
        f"Return JSON exactly of the form:\n"
        f'{{"constraints": [{{"id": "<id>", "present": true/false, "distorted": true/false, '
        f'"note": "<short>"}} ...],\n'
        f'  "extra_requirements": true/false,   // TRUE only if the message states an extra PRODUCT '
        f'requirement (a spec, feature, price limit, brand, or attribute) that is NOT in the list above. '
        f'A purchase instruction ("buy it", "place the order now"), a generic reason or persona, and '
        f'restating/summarising the listed items are NOT extra requirements.\n'
        f'  "leakage": true/false,              // names a specific product/brand/model, OR tells which to pick, '
        f'OR mentions sponsored/deals/ranking/ratings\n'
        f'  "naturalness": true/false}}\n'
        f"Include one constraints entry per required id above. Return ONLY the JSON."
    )


def _backstop(instruction: str) -> list[str]:
    low = instruction.lower()
    return [t for t in _BACKSTOP_TERMS if t in low]


async def _judge_once(instruction: str, scenario: ScenarioSpec, refs: list[dict],
                      model: str, seed: int) -> Optional[dict]:
    try:
        return await chat_json(_judge_prompt(instruction, scenario, refs),
                               system=_JUDGE_SYSTEM, model=model, seed=seed, max_tokens=900)
    except Exception:
        return None


def _sample_pass(j: dict, ref_ids: list[str]) -> tuple[bool, dict]:
    """Per-sample verdict + per-constraint booleans, robust to missing ids."""
    by = {str(c.get("id")): c for c in j.get("constraints", []) if isinstance(c, dict)}
    present, distorted = {}, {}
    for rid in ref_ids:
        c = by.get(rid, {})
        present[rid] = bool(c.get("present", False))
        distorted[rid] = bool(c.get("distorted", False))
    complete = all(present.values())
    undistorted = not any(distorted.values())
    leak = bool(j.get("leakage", False)) or bool(j.get("extra_requirements", False))
    ok = complete and undistorted and not leak
    return ok, {"present": present, "distorted": distorted, "leakage": leak,
                "natural": bool(j.get("naturalness", True))}


async def judge(instruction: str, scenario: ScenarioSpec, pref: PreferenceSpec) -> FaithfulnessReport:
    refs = _constraint_refs(scenario, pref)
    ref_ids = [r["id"] for r in refs]

    prim = await asyncio.gather(*[_judge_once(instruction, scenario, refs, _PRIMARY, s)
                                  for s in range(_K)])
    sec = await _judge_once(instruction, scenario, refs, _SECONDARY, 0)
    prim = [p for p in prim if p]
    samples = [_sample_pass(p, ref_ids) for p in prim]

    # majority over primary samples, per field
    def majority(getter) -> dict:
        out = {}
        for rid in ref_ids:
            votes = Counter(getter(s)[rid] for s in samples)
            out[rid] = votes.most_common(1)[0][0] if votes else False
        return out

    present = majority(lambda s: s[1]["present"]) if samples else {r: False for r in ref_ids}
    distorted = majority(lambda s: s[1]["distorted"]) if samples else {r: True for r in ref_ids}
    leak_votes = [s[1]["leakage"] for s in samples]
    prim_leak = sum(leak_votes) > len(leak_votes) / 2 if leak_votes else True
    natural = sum(s[1]["natural"] for s in samples) >= len(samples) / 2 if samples else True

    backstop_hits = _backstop(instruction)
    sec_ok = _sample_pass(sec, ref_ids)[0] if sec else False

    completeness_ok = all(present.values())
    no_distortion_ok = not any(distorted.values())
    no_leakage_ok = (not prim_leak) and (not backstop_hits) and (not (sec and not sec_ok and
                    _sample_pass(sec, ref_ids)[1]["leakage"]))
    # require BOTH models to pass overall
    prim_pass = completeness_ok and no_distortion_ok and not prim_leak
    verdict = "pass" if (prim_pass and sec_ok and not backstop_hits) else "fail"

    pc = [ConstraintCheck(ref=r, present=present.get(r, False),
                          distorted=distorted.get(r, False)) for r in ref_ids]
    flags = []
    if backstop_hits:
        flags.append("backstop:" + ",".join(backstop_hits))
    if sec and not sec_ok:
        flags.append("secondary_judge_fail")
    if not prim_pass:
        flags.append("primary_judge_fail")
    return FaithfulnessReport(
        completeness_ok=completeness_ok, no_leakage_ok=no_leakage_ok,
        no_distortion_ok=no_distortion_ok, naturalness_ok=natural,
        per_constraint=pc, flags=flags,
        judge_models=[_PRIMARY] * len(prim) + ([_SECONDARY] if sec else []),
        verdict=verdict)


def _failure_feedback(report: FaithfulnessReport) -> str:
    bits = []
    missing = [c.ref for c in report.per_constraint if not c.present]
    distorted = [c.ref for c in report.per_constraint if c.distorted]
    if missing:
        bits.append(f"missing requirements: {missing}")
    if distorted:
        bits.append(f"distorted requirements: {distorted}")
    if not report.no_leakage_ok:
        bits.append("it leaked the answer, named a product, mentioned steering, OR added/implied "
                    "an extra requirement not in the list (e.g. a motivation implying an unlisted "
                    "feature like quiet/noise-cancelling, or a price mention)")
    return "; ".join(bits) or "it did not faithfully encode the requirements"


async def make_instruction(scenario: ScenarioSpec, pref: PreferenceSpec) -> GeneratedInstruction:
    feedback = None
    last: Optional[FaithfulnessReport] = None
    text = ""
    for attempt in range(_MAX_TRIES):
        text = await generate_instruction(scenario, pref, seed=attempt, feedback=feedback)
        last = await judge(text, scenario, pref)
        if last.verdict == "pass":
            return GeneratedInstruction(scenario.scenario_id, pref.variant, text,
                                        tries=attempt + 1, report=last, status="ok")
        feedback = _failure_feedback(last)
    return GeneratedInstruction(scenario.scenario_id, pref.variant, text,
                                tries=_MAX_TRIES, report=last, status="flagged_after_max_tries")
