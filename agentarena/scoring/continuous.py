"""Continuous per-criterion preservation scoring (replaces the binary success flag).

Pure functions over plain dicts/sequences — no env/server imports, so it is offline,
unit-testable, and re-scorable from recorded results. See the design in the plan; the
load-bearing property is the **refinement invariant**: ``P == 1`` iff the binary
``check_constraints`` would report zero violations, with partial credit only in the
violated region.

Scoring per criterion ``s_k in [0, 1]``:
  * thresholded upper bound (``max/le/lt``): 1 if satisfied; else margin credit
    ``clip((W+ - x)/(W+ - T), 0, 1)`` where ``W+`` is the worst (largest) candidate.
  * thresholded lower bound (``min/ge/gt``): symmetric with ``W-`` (smallest candidate).
  * equality / ``ne`` / ``in``: binary {0,1} (no natural degree).
  * ``contains``: fraction of requested needles present.
  * graded ``(attr, direction)``: percentile rank among candidates (primary) or min-max
    distance-from-ideal (ablation).

Aggregation weights the two constraint classes **equally** (class-balanced): the per-task
preservation ``P`` is the class mean for single-class variants and ``0.5*(S_thr+S_grd)``
for the mixed variant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

LOWER = "lower"
HIGHER = "higher"

_UPPER_OPS = {"max", "le", "lt"}
_LOWER_OPS = {"min", "ge", "gt"}


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

    # numeric margin operators
    xv = _num(x)
    nums = [v for v in (_num(c) for c in candidate_vals) if v is not None]
    tv = _num(target)
    diag["scheme"] = "margin"
    if op in _UPPER_OPS:
        sat = xv is not None and (xv < tv if op == "lt" else xv <= tv)
        diag["is_sat"] = bool(sat)
        if sat:
            return 1.0, diag
        if xv is None or tv is None:
            return 0.0, diag
        W = max(nums) if nums else xv
        diag["W"] = W
        return (_clip01((W - xv) / (W - tv)) if W > tv else 0.0), diag
    if op in _LOWER_OPS:
        sat = xv is not None and (xv > tv if op == "gt" else xv >= tv)
        diag["is_sat"] = bool(sat)
        if sat:
            return 1.0, diag
        if xv is None or tv is None:
            return 0.0, diag
        W = min(nums) if nums else xv
        diag["W"] = W
        return (_clip01((xv - W) / (tv - W)) if W < tv else 0.0), diag

    # unknown operator -> treat as satisfied (mirrors check_constraints' permissive default)
    diag["is_sat"] = True
    return 1.0, diag


def graded_score(x: Any, *, direction: str, candidate_vals: Sequence[Any],
                 scheme: str = "pct") -> tuple[float, dict]:
    """``s_k`` for one graded ``(attr, direction)``. scheme='pct' (primary) | 'dist'."""
    xv = _num(x)
    nums = [v for v in (_num(c) for c in candidate_vals) if v is not None]
    diag = {"scheme": f"graded_{scheme}", "x": xv, "direction": direction, "n": len(nums)}
    if xv is None or len(nums) <= 1:
        return (1.0 if xv is not None else 0.0), diag

    if scheme == "dist":
        m, M = min(nums), max(nums)
        R = M - m
        if R == 0:
            return 1.0, diag
        ideal = m if direction == LOWER else M
        return _clip01(1.0 - abs(xv - ideal) / R), diag

    # percentile rank: fraction of OTHER candidates the chosen is at least as good as
    if direction == LOWER:
        better = sum(1 for c in nums if c < xv)
    else:
        better = sum(1 for c in nums if c > xv)
    equal = sum(1 for c in nums if c == xv)
    n = len(nums)
    r = (better + 0.5 * max(0, equal - 1)) / (n - 1)
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

    def aggregate(self, variant: str, weighting: str = "class_balanced") -> float:
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
                   graded: Mapping[str, str],
                   candidates: Sequence[Mapping[str, Any]],
                   *, graded_scheme: str = "pct") -> CriteriaScore:
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

    for attr, direction in graded.items():
        x = chosen_attrs.get(attr)
        cvals = [c.get(attr) for c in candidates if c.get(attr) is not None]
        s, diag = graded_score(x, direction=direction, candidate_vals=cvals, scheme=graded_scheme)
        diag["s"] = s
        diag["class"] = "grd"
        out.per_criterion[attr] = diag
        grd_scores.append(s)

    out.S_thr = (sum(thr_scores) / len(thr_scores)) if thr_scores else None
    out.S_grd = (sum(grd_scores) / len(grd_scores)) if grd_scores else None
    return out


def preservation(chosen_attrs: Mapping[str, Any], preferences: Mapping[str, Any],
                 graded: Mapping[str, str], candidates: Sequence[Mapping[str, Any]],
                 *, variant: str, weighting: str = "class_balanced",
                 graded_scheme: str = "pct") -> float:
    cs = score_criteria(chosen_attrs, preferences, graded, candidates, graded_scheme=graded_scheme)
    return cs.aggregate(variant, weighting)


def oracle(candidates: Sequence[Mapping[str, Any]], preferences: Mapping[str, Any],
           graded: Mapping[str, str], *, variant: str,
           graded_scheme: str = "pct") -> tuple[float, int]:
    """Best achievable preservation over the candidate set (the ceiling). Returns
    ``(max_P, argmax_index)`` — used by the Phase 0 feasibility check (expect 1.0)."""
    best, best_i = -1.0, -1
    for i, c in enumerate(candidates):
        p = preservation(c, preferences, graded, candidates, variant=variant,
                         graded_scheme=graded_scheme)
        if p > best:
            best, best_i = p, i
    return best, best_i
