"""Continuous per-criterion preservation scoring (replaces the binary success flag).

Pure functions over plain dicts/sequences — no env/server imports, so it is offline,
unit-testable, and re-scorable from recorded results. The load-bearing property is the
**refinement invariant**: ``P == 1`` iff every criterion is perfect — i.e. all hard cuts met AND
every graded degree maximal (the faithful/oracle); partial credit only below that.

Per-criterion score ``s_k in [0, 1]``:
  * HARD criteria (thresholded numeric ``max/le/lt`` / ``min/ge/gt``, equality, ``ne``, ``in``,
    boolean): **binary {0,1}** — the requirement is met or not. ``contains`` = fraction of needles.
  * GRADED degree ``(attr, direction, cut)``: the **headroom** the choice earns ABOVE the cut,
    normalised by the best the catalog offers (see ``graded_score``): just-meeting the cut → ~0, the
    catalog-best (the faithful, by construction) → 1.0, a mid-pack choice → in between. So a
    SATISFICING pick (meets every cut but is mid-pack on the soft degrees) scores ~1.0 under
    `thresholded` yet LOW under `graded`. ``P_oracle == 1`` because the faithful is the catalog
    extreme on the graded dims.

Aggregation (``weighting='degree'``, the default) is a **weighted mean over every criterion**: each
graded degree weighs ``DEGREE_WEIGHT`` (>1), each hard criterion weighs 1. The degrees carry the
preference signal (the hard cuts are filters most in-budget options satisfy, so a flat mean lets
several always-met cuts drown out the 1-2 degrees and caps the achievable gap at n_deg/n_total);
up-weighting them makes a mid-pack satisficing pick score meaningfully lower AND keeps the variant
ordering thresholded<mixed<graded (total degree weight grows with the number of softened dims). Every
criterion still counts, so a violated hard cut always lowers P (the refinement invariant holds).
``weighting='flat'`` (plain mean) is retained for ablations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

LOWER = "lower"
HIGHER = "higher"

_UPPER_OPS = {"max", "le", "lt"}
_LOWER_OPS = {"min", "ge", "gt"}

# Each GRADED degree is weighted this many times a (binary) hard-requirement criterion in the
# 'degree' aggregation. Rationale: in the mixed/graded variants the user's preference is EXPRESSED
# through the soft degrees; the hard requirements are filters that most in-budget options satisfy,
# so a flat mean lets several always-met cuts drown out the 1-2 degrees that actually carry the
# preference signal (capping the achievable graded gap at n_deg/n_total). Up-weighting the degrees
# (a) lets a satisficing pick that is mid-pack on the degrees score meaningfully lower, and
# (b) preserves the variant ordering thresholded<mixed<graded because total degree weight grows with
# the number of softened dims. A VIOLATED hard cut still drags the score down (refinement invariant:
# P==1 iff every criterion is perfect, i.e. all cuts met AND every degree maximal = the faithful).
DEGREE_WEIGHT = 4.0


def _clip01(v: float) -> float:
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _contains_count(have: Any, needles: Sequence) -> int:
    if have is None:
        return 0
    if isinstance(have, str):
        hl = have.lower()
        return sum(1 for n in needles if str(n).lower() in hl)
    try:
        hs = {str(x).lower() for x in have}
        return sum(1 for n in needles if str(n).lower() in hs)
    except TypeError:
        return 0


# --------------------------------------------------------------------------- #
# Per-criterion scores
# --------------------------------------------------------------------------- #
def thresholded_score(x: Any, *, op: str, target: Any,
                      candidate_vals: Sequence[Any]) -> tuple[float, dict]:
    """``s_k`` for one thresholded/equality/ne/in/contains key. Returns (s, diag)."""
    diag = {"scheme": "binary", "x": x, "T": target, "W": None, "is_sat": False}

    # discrete / categorical operators -> 0/1
    if op == "eq":
        s = 1.0 if x == target else 0.0
        diag["is_sat"] = bool(s)
        return s, diag
    if op == "ne":
        s = 1.0 if x != target else 0.0
        diag["is_sat"] = bool(s)
        return s, diag
    if op == "in":
        try:
            sat = x in target
        except TypeError:
            sat = False
        diag["is_sat"] = sat
        return (1.0 if sat else 0.0), diag
    if op == "contains":
        needles = target if isinstance(target, (list, tuple, set)) else [target]
        m = _contains_count(x, needles)
        diag.update(scheme="contains", is_sat=(m == len(needles)))
        return (m / len(needles) if needles else 1.0), diag

    # numeric operators -> BINARY {0,1}. Unified treatment: a thresholded requirement (price,
    # storage, weight, battery, …) is simply met or not — every spec, price included, is scored
    # the same way, with no partial margin credit. (Degrees live only in the graded class.)
    xv = _num(x)
    tv = _num(target)
    diag["scheme"] = "binary"
    if op in _UPPER_OPS:
        sat = xv is not None and tv is not None and (xv < tv if op == "lt" else xv <= tv)
        diag["is_sat"] = bool(sat)
        return (1.0 if sat else 0.0), diag
    if op in _LOWER_OPS:
        sat = xv is not None and tv is not None and (xv > tv if op == "gt" else xv >= tv)
        diag["is_sat"] = bool(sat)
        return (1.0 if sat else 0.0), diag

    # unknown operator -> treat as satisfied (mirrors check_constraints' permissive default)
    diag["is_sat"] = True
    return 1.0, diag


def graded_score(x: Any, *, direction: str, value: Any = None,
                 candidate_vals: Sequence[Any] = (), scheme: str = "headroom") -> tuple[float, dict]:
    """``s_k`` for one graded ``(attr, direction)`` — the HEADROOM the choice achieves ABOVE the
    requirement, relative to the best the catalog offers. With a requirement cut ``value`` (R) and
    the catalog's best value on this spec B:
      * HIGHER-is-better (storage, battery): s = clip((x − R) / (B − R)).  x=R → 0, x=B → 1.0.
      * LOWER-is-better  (weight):           s = clip((R − x) / (R − B)).
    So a choice that just MEETS the requirement scores ~0 on this degree, the BEST (faithful) scores
    1.0, and a mid-pack choice scores in between — this is what makes a satisficing pick (meets the
    cutoffs but is mid-pack on the degrees) score LOW under `graded` while staying ~1.0 under
    `thresholded`. The catalog's best is the faithful by construction, so P_oracle=1. (A missed
    requirement clips to 0; if the catalog has no headroom above R, meeting → 1.0.) Without a cut
    (legacy), falls back to percentile rank."""
    xv = _num(x)
    diag = {"scheme": f"graded_{scheme}", "x": xv, "direction": direction, "value": value}
    if xv is None:
        return 0.0, diag
    tv = _num(value)
    nums = [v for v in (_num(c) for c in candidate_vals) if v is not None]
    if tv is not None:
        if direction == HIGHER:
            best = max(nums) if nums else xv
            if best <= tv:                                   # no headroom in catalog -> met = full
                return (1.0 if xv >= tv else _clip01(xv / tv) if tv > 0 else 0.0), diag
            return _clip01((xv - tv) / (best - tv)), diag
        best = min(nums) if nums else xv
        if best >= tv:
            return (1.0 if xv <= tv else _clip01(tv / xv) if xv > 0 else 0.0), diag
        return _clip01((tv - xv) / (tv - best)), diag

    # legacy fallback: percentile rank among candidates (no requirement cut available)
    if len(nums) <= 1 or max(nums) == min(nums):
        return 1.0, diag
    if direction == LOWER:
        better = sum(1 for c in nums if c < xv)
    else:
        better = sum(1 for c in nums if c > xv)
    equal = sum(1 for c in nums if c == xv)
    r = (better + 0.5 * max(0, equal - 1)) / (len(nums) - 1)
    return _clip01(1.0 - r), diag


# --------------------------------------------------------------------------- #
# Aggregation over a task's criteria
# --------------------------------------------------------------------------- #
@dataclass
class CriteriaScore:
    per_criterion: dict[str, dict] = field(default_factory=dict)
    S_thr: Optional[float] = None
    S_grd: Optional[float] = None
    warnings: list[str] = field(default_factory=list)

    def aggregate(self, variant: str, weighting: str = "degree",
                  degree_weight: float = DEGREE_WEIGHT) -> float:
        if weighting == "degree":
            # weighted mean over EVERY criterion: a graded degree weighs `degree_weight`, a hard
            # (threshold/bool) criterion weighs 1. Every criterion still counts, so any violated cut
            # lowers P (refinement invariant holds), but the degrees carry the preference signal.
            num = den = 0.0
            for c in self.per_criterion.values():
                w = degree_weight if c.get("class") == "grd" else 1.0
                num += w * c["s"]
                den += w
            return num / den if den else 0.0
        if weighting == "flat":
            ss = [c["s"] for c in self.per_criterion.values()]
            return sum(ss) / len(ss) if ss else 0.0
        if variant == "thresholded":
            return self.S_thr if self.S_thr is not None else 0.0
        if variant == "graded":
            return self.S_grd if self.S_grd is not None else 0.0
        # mixed (or anything with both) -> class-balanced
        parts = [v for v in (self.S_thr, self.S_grd) if v is not None]
        return sum(parts) / len(parts) if parts else 0.0


def score_criteria(chosen_attrs: Mapping[str, Any],
                   preferences: Mapping[str, Any],
                   graded: Mapping[str, Any],
                   candidates: Sequence[Mapping[str, Any]],
                   *, graded_scheme: str = "headroom") -> CriteriaScore:
    """Score one chosen item's attrs against a preference (both classes).

    ``candidates`` is the list of every candidate's flat attr dict (the catalog) — used
    as the normalisation pool for both margin denominators and graded percentiles.
    """
    out = CriteriaScore()
    thr_scores: list[float] = []
    grd_scores: list[float] = []

    for key, target in preferences.items():
        field_name, op = (key.rsplit("__", 1) + ["eq"])[:2] if "__" in key else (key, "eq")
        x = chosen_attrs.get(field_name)
        cvals = [c.get(field_name) for c in candidates if c.get(field_name) is not None]
        s, diag = thresholded_score(x, op=op, target=target, candidate_vals=cvals)
        diag["s"] = s
        diag["class"] = "thr"
        out.per_criterion[key] = diag
        thr_scores.append(s)
        if diag.get("scheme") == "margin" and diag.get("W") is not None and not cvals:
            out.warnings.append(f"degenerate_criterion:{key}")

    for attr, gspec in graded.items():
        # gspec is (direction, requirement_value) for unified scenarios; a bare direction string
        # for legacy ones.
        direction, value = gspec if isinstance(gspec, (tuple, list)) else (gspec, None)
        x = chosen_attrs.get(attr)
        cvals = [c.get(attr) for c in candidates if c.get(attr) is not None]
        s, diag = graded_score(x, direction=direction, value=value,
                               candidate_vals=cvals, scheme=graded_scheme)
        diag["s"] = s
        diag["class"] = "grd"
        out.per_criterion[attr] = diag
        grd_scores.append(s)

    out.S_thr = (sum(thr_scores) / len(thr_scores)) if thr_scores else None
    out.S_grd = (sum(grd_scores) / len(grd_scores)) if grd_scores else None
    return out


def preservation(chosen_attrs: Mapping[str, Any], preferences: Mapping[str, Any],
                 graded: Mapping[str, str], candidates: Sequence[Mapping[str, Any]],
                 *, variant: str, weighting: str = "degree",
                 graded_scheme: str = "headroom") -> float:
    cs = score_criteria(chosen_attrs, preferences, graded, candidates, graded_scheme=graded_scheme)
    return cs.aggregate(variant, weighting)


def oracle(candidates: Sequence[Mapping[str, Any]], preferences: Mapping[str, Any],
           graded: Mapping[str, str], *, variant: str,
           graded_scheme: str = "headroom") -> tuple[float, int]:
    """Best achievable preservation over the candidate set (the ceiling). Returns
    ``(max_P, argmax_index)`` — used by the Phase 0 feasibility check (expect 1.0)."""
    best, best_i = -1.0, -1
    for i, c in enumerate(candidates):
        p = preservation(c, preferences, graded, candidates, variant=variant,
                         graded_scheme=graded_scheme)
        if p > best:
            best, best_i = p, i
    return best, best_i
