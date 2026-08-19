"""Unified 7-preference × 5-variant task builder for the harvested-clone envs.

Every non-amazon env declares ONE ``Pref7`` scenario: exactly **3 always-absolute must-haves** + **4
ordered soft (gradable) dims** = 7 preference dimensions. The 5 relativeness variants soften the first
N soft dims into "best-on-X" degrees (N = 0,1,2,3,4 for thresholded / mixed / graded / graded3 /
graded4) — mirroring the Amazon benchmark (``schema._VARIANT_NGRADED``). So:

    thresholded : 7 hard, 0 degree        graded3 : 4 hard, 3 degree
    mixed       : 6 hard, 1 degree        graded4 : 3 hard, 4 degree   <- 3 absolute + 4 relative
    graded      : 5 hard, 2 degree

This makes all 10 envs' tasks structurally comparable. Scoring is unchanged: the projected hard prefs
become the P* gate (``preferences``) and the softened dims become the graded optimality term
(``metadata.graded``), both consumed by ``_storefront.scoring`` / ``scoring.continuous``.

A soft dim carries ONE number that is both its absolute cut (when hard) and its graded baseline R
(headroom is measured above it when soft) — so a satisficing pick that just meets every cut scores ~1
under thresholded but low under graded4, while the catalog-best hero scores 1 at every variant
(``oracle_pstar`` = 1, the validity invariant).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ...core.task import TaskSpec

VARIANTS7 = ("thresholded", "mixed", "graded", "graded3", "graded4")
NGRADED = {"thresholded": 0, "mixed": 1, "graded": 2, "graded3": 3, "graded4": 4}


@dataclass
class Hard:
    """An always-absolute must-have (hard at every relativeness level)."""
    field: str                  # attr key in Item.specs (or "price")
    op: str                     # "eq" | "le" | "lt" | "min" | "ge" | "gt" | "in" | "contains"
    value: Any
    phrase: str                 # firm phrasing, e.g. "men's running shoes" / "at most $130"


@dataclass
class Soft:
    """A gradable dim: an absolute cut (op+threshold) when hard, a direction when softened."""
    field: str
    direction: str              # "higher" | "lower" (which way is better)
    threshold: Any              # the absolute cut AND the graded baseline R
    phrase_hard: str            # absolute phrasing, e.g. "rated at least 4.5 stars"
    phrase_soft: str            # degree phrasing, e.g. "the highest-rated"
    op: Optional[str] = None    # threshold op; default min (higher) / max (lower)

    def thr_op(self) -> str:
        return self.op or ("min" if self.direction == "higher" else "max")


@dataclass
class Pref7:
    env: str
    scenario: str               # short id used as the task_id prefix, e.g. "running"
    noun: str                   # "running shoes" — for instructions
    persona: str                # "a marathon runner" — tone only
    catalog: str
    hard: list                  # exactly 3 Hard
    soft: list                  # exactly 4 Soft, ordered = graded_order
    params: dict = field(default_factory=dict)

    def __post_init__(self):
        assert len(self.hard) == 3, f"{self.env}: need 3 hard prefs, got {len(self.hard)}"
        assert len(self.soft) == 4, f"{self.env}: need 4 soft prefs, got {len(self.soft)}"


def _key(field_name: str, op: str) -> str:
    return field_name if op == "eq" else f"{field_name}__{op}"


def project(spec: Pref7, variant: str) -> tuple[dict, dict]:
    """(preferences, graded) for one variant: the first N soft dims go graded, the rest stay hard."""
    n = NGRADED[variant]
    graded_soft, thr_soft = spec.soft[:n], spec.soft[n:]
    preferences: dict[str, Any] = {}
    for h in spec.hard:
        preferences[_key(h.field, h.op)] = h.value
    for s in thr_soft:
        preferences[_key(s.field, s.thr_op())] = s.threshold
    graded = {s.field: (s.direction, s.threshold) for s in graded_soft}
    return preferences, graded


# --------------------------------------------------------------------------- instructions
def render_lines(spec: Pref7, variant: str) -> tuple[list[str], list[str]]:
    """(hard_phrases, soft_degree_phrases) for one variant — the requirement lists for the instruction."""
    n = NGRADED[variant]
    hard_phrases = [h.phrase for h in spec.hard] + [s.phrase_hard for s in spec.soft[n:]]
    soft_phrases = [s.phrase_soft for s in spec.soft[:n]]
    return hard_phrases, soft_phrases


def template_instruction(spec: Pref7, variant: str) -> str:
    """The deterministic shopper instruction all measured runs use. (An LLM-phrased per-env
    instruction cache — an earlier generated `instructions7.json` cache — existed
    during the build-out but was retired before the final measurements.)"""
    hard_phrases, soft_phrases = render_lines(spec, variant)
    hp = "; ".join(hard_phrases)
    msg = f"Please buy me a {spec.noun} and place the order. It must be {hp}."
    if soft_phrases:
        msg += (" Among the options that qualify, pick " +
                ", ".join(soft_phrases[:-1] + [("and " + soft_phrases[-1]) if len(soft_phrases) > 1 else soft_phrases[-1]])
                + " — that's what matters most.")
    return msg


def with_rating(spec: Pref7, *, drop: str, at: int = 2, threshold: float = 4.0,
                phrase_hard: str = "rated at least 4.0 out of 5 stars by buyers",
                phrase_soft: str = "the highest customer rating") -> Pref7:
    """Return a Pref7 with the TRUE customer ``rating`` inserted as a SCORED graded dim at position
    ``at`` (default 2 → softens at graded3, mirroring the Amazon textbook's [spec, spec, rating, spec]),
    DROPPING the soft dim named ``drop`` to keep exactly 4. rating's ``threshold`` is its HARD floor at
    thresholded/mixed/graded; under steering the decoys' DISPLAY rating is inflated while the TRUE value
    (what the scorer reads) sits at the floor — so a weak agent chasing 'highest rating' is trapped, and
    the genuine top-rated hero keeps oracle P*=1.0. See memory textbook-divergence-rating-inflation."""
    from dataclasses import replace
    rating = Soft("rating", "higher", threshold, phrase_hard, phrase_soft, op="min")
    kept = [s for s in spec.soft if s.field != drop]
    assert len(kept) == 3, f"{spec.env}: drop={drop!r} must remove exactly one of {[s.field for s in spec.soft]}"
    new_soft = kept[:at] + [rating] + kept[at:]
    return replace(spec, soft=new_soft)


def build(spec: Pref7) -> list[TaskSpec]:
    """The 5 variant TaskSpecs for one env."""
    tasks = []
    for v in VARIANTS7:
        preferences, graded = project(spec, v)
        instr = template_instruction(spec, v)
        tasks.append(TaskSpec(
            task_id=f"{spec.scenario}-{v}", env=spec.env, catalog=spec.catalog,
            instruction=instr, preferences=preferences,
            params=dict(spec.params),
            metadata={"graded": graded, "variant": v, "noun": spec.noun},
        ))
    return tasks
