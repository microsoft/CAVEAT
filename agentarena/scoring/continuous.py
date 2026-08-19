"""Continuous per-criterion preservation scoring (replaces the binary success flag).

Pure functions over plain dicts/sequences — no env/server imports, so it is offline,
unit-testable, and re-scorable from recorded results. The load-bearing property is the
**refinement invariant**: ``P == 1`` iff every criterion is perfect — i.e. all hard cuts met AND
every graded degree maximal (the faithful/oracle); partial credit only below that.

Per-criterion score ``s_k in [0, 1]``:
  * HARD criteria (thresholded numeric ``max/le/lt`` / ``min/ge/gt``, equality, ``ne``, ``in``,
    boolean): **binary {0,1}** — the requirement is met or not. ``contains`` = fraction of needles.
  * GRADED degree ``(attr, direction, cut)``: the **headroom** the choice earns ABOVE the cut,
    normalised by the best the FULLY-COMPLIANT candidates offer (see ``score_criteria`` /
    ``graded_score``): just-meeting the cut → ~0, the compliant-set best (the hero, by
    construction) → 1.0, a mid-pack choice → in between. So a SATISFICING pick (meets every cut
    but is mid-pack on the soft degrees) scores ~1.0 under `thresholded` yet LOW under `graded`.
    ``P_oracle == 1`` because the hero is the compliant-set extreme on the graded dims.

NORMALISATION POOL (the compliant set): the denominator best ``B`` for each graded degree is taken
over the candidates that satisfy EVERY threshold of the preference AND meet EVERY graded dim's cut
— i.e. the level-0 (all-hard) projection, which is level-invariant. This stops a gate-FAILING
catalog extreme (an anti-sort distractor: over budget / wrong category / under-spec, but huge on
one soft dim) from deflating the hero's headroom score. If no candidate is fully compliant, the
pool falls back to ALL candidates and an ``empty_compliant_set`` warning is emitted.

HEADLINE metric: the strict, non-compensatory ``strict_preservation`` (P* = G·O) with the
CURRENT-variant gate — see its docstring. The weighted-mean ``CriteriaScore.aggregate`` is
LEGACY / diagnostic only (see its docstring).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

LOWER = "lower"
HIGHER = "higher"

_UPPER_OPS = {"max", "le", "lt"}
_LOWER_OPS = {"min", "ge", "gt"}

# LEGACY / diagnostic only — the weight of a graded degree vs a hard criterion in the retired
# weighted-mean aggregation (``CriteriaScore.aggregate(weighting='degree')``). The weighted mean is
# COMPENSATORY: several always-met cuts form a score floor, so a hard-cut violation only nudged P
# (~ -1/n) and a satisficing pick kept a mid score. It is NOT the reported fidelity metric — the
# headline is the strict, non-compensatory ``strict_preservation`` (P* = G·O). Kept only for
# diagnostics/ablations against historical runs.
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
        """LEGACY / diagnostic only: the retired compensatory weighted-mean P. A violated hard cut
        only lowers P by ~w/Σw (no gate), so this must NOT be reported as preference fidelity —
        use ``strict_preservation`` (P* = G·O). Retained for the viewer's historical `preservation`
        key and metric ablations."""
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


def _criterion_satisfied(attrs: Mapping[str, Any], key: str, target: Any) -> bool:
    """Whether one candidate meets one threshold key (same reading as ``thresholded_score``)."""
    field_name, op = (key.rsplit("__", 1) + ["eq"])[:2] if "__" in key else (key, "eq")
    _s, diag = thresholded_score(attrs.get(field_name), op=op, target=target, candidate_vals=())
    return bool(diag["is_sat"])


def _meets_cut(attrs: Mapping[str, Any], attr: str, direction: str, value: Any) -> bool:
    """Whether one candidate meets one graded dim's underlying requirement cut (no cut → True)."""
    tv = _num(value)
    if tv is None:
        return True
    xv = _num(attrs.get(attr))
    if xv is None:
        return False
    return xv >= tv if direction == HIGHER else xv <= tv


def compliant_mask(preferences: Mapping[str, Any], graded: Mapping[str, Any],
                   candidates: Sequence[Mapping[str, Any]]) -> list[bool]:
    """Per-candidate FULL compliance: satisfies every ``preferences`` threshold AND meets every
    graded dim's underlying cut — i.e. the level-0 (all-hard) projection of the preference. Because
    each soft dim's graded cut equals its level-0 threshold, this mask is LEVEL-INVARIANT: the same
    candidates are compliant at every relativeness level."""
    out = []
    for c in candidates:
        ok = all(_criterion_satisfied(c, k, t) for k, t in preferences.items())
        if ok:
            for attr, gspec in graded.items():
                direction, value = gspec if isinstance(gspec, (tuple, list)) else (gspec, None)
                if not _meets_cut(c, attr, direction, value):
                    ok = False
                    break
        out.append(ok)
    return out


def score_criteria(chosen_attrs: Mapping[str, Any],
                   preferences: Mapping[str, Any],
                   graded: Mapping[str, Any],
                   candidates: Sequence[Mapping[str, Any]],
                   *, graded_scheme: str = "headroom") -> CriteriaScore:
    """Score one chosen item's attrs against a preference (both classes).

    ``candidates`` is the list of every candidate's flat attr dict (the catalog). The graded
    HEADROOM denominators are normalised over the FULLY-COMPLIANT subset only (``compliant_mask``:
    meets every threshold AND every graded cut — the level-invariant level-0 projection), so a
    gate-failing catalog extreme (anti-sort bait) cannot deflate a compliant item's headroom.
    Falls back to all candidates (with an ``empty_compliant_set`` warning) when nothing complies.
    """
    out = CriteriaScore()
    thr_scores: list[float] = []
    grd_scores: list[float] = []

    # the graded normalisation pool: fully-compliant candidates only (built ONCE, level-invariant)
    if graded:
        mask = compliant_mask(preferences, graded, candidates)
        norm_pool = [c for c, ok in zip(candidates, mask) if ok]
        if not norm_pool:
            norm_pool = list(candidates)
            if candidates:
                out.warnings.append("empty_compliant_set:normalizing_over_all_candidates")
    else:
        norm_pool = list(candidates)

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
        cvals = [c.get(attr) for c in norm_pool if c.get(attr) is not None]
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


# Convex exponent on each graded-preference headroom in the STRICT metric. Linear credit (gamma=1)
# rewards merely clearing the user's stated minimum; gamma=2 makes credit accrue steeply only near
# the catalog best, so a "satisficing" pick (just over every minimum, mid-pack on the soft degrees)
# earns little. Binary criteria are unaffected (0**2=0, 1**2=1).
STRICT_GAMMA = 2.0


def _field_of(key: str) -> str:
    return key.rsplit("__", 1)[0] if "__" in key else key


def _soft_compliance(key: str, c: Mapping[str, Any]) -> float:
    """LEGACY / diagnostic only — the continuous (``soft=True``) gate variant of
    ``strict_preservation``. The headline P* uses the BINARY gate (``soft=False``); this soft decay
    is kept solely for the historical `preservation_cont` diagnostic key.

    Continuous compliance factor in [0,1] for ONE must-have criterion (the ``soft`` gate). A met
    criterion → 1. A violated NUMERIC bound decays with how far it is breached (convex):
      * upper bound (budget ``__lt/__le/__max``):  (target / x) ** STRICT_GAMMA   — over by 12% → ~0.80
      * lower bound (min spec ``__min/__ge/__gt``): (x / target) ** STRICT_GAMMA   — 256 vs 512 → 0.25
    A violated BOOLEAN/categorical must-have (no numeric x/target — e.g. not-gaming, no-add-ons,
    certified) is inherently all-or-nothing → 0. So a slightly-over-budget pick is *mostly* faithful,
    a wrong-category pick is disqualified."""
    if c["s"] >= 1.0:
        return 1.0
    op = key.rsplit("__", 1)[1] if "__" in key else "eq"
    x, t = _num(c.get("x")), _num(c.get("T"))
    if x is None or t is None or x <= 0 or t <= 0:
        return 0.0                                   # boolean / categorical -> binary
    if op in _UPPER_OPS:
        return _clip01(t / x) ** STRICT_GAMMA
    if op in _LOWER_OPS:
        return _clip01(x / t) ** STRICT_GAMMA
    return 0.0


def strict_preservation(cs: CriteriaScore, must_have_fields, *, soft: bool = False) -> float:
    """STRICT, non-compensatory preference fidelity  P* = G * O  — the HEADLINE metric.

    UNIFIED (per-variant / "clone") gate semantics: callers pass the CURRENT-variant HARD fields as
    ``must_have_fields`` — i.e. the bare field names of every threshold in ``preference(variant)
    .dsl()`` (the dims that are hard AT THIS relativeness level), NOT the cross-variant
    intersection. This is exactly what ``envs._storefront.scoring.score`` does for the clone envs,
    so all envs report the same metric.

      * ``G`` (compliance) — binary gate over the current-level hard dims: ANY violation → 0
        (``soft=False``, the headline). ``soft=True`` is a LEGACY diagnostic (continuous decay via
        ``_soft_compliance``) kept only for the historical `preservation_cont` key.
      * ``O`` (optimality) = mean over the CURRENT-level graded dims of ``s_k ** STRICT_GAMMA``
        (convex, non-compensatory credit; each ``s_k`` is compliant-set-normalised headroom, so a
        gate-passing pick with zero headroom on a soft dim earns 0 from that dim). ``O = 1`` when
        there is no graded dim — so at level 0 (thresholded) P* = G is exactly compliance and P*
        is BINARY {0, 1} there, by design.

    Consequences of the per-variant gate: an item that fails a dim scores 0 at every level where
    that dim is HARD; once the dim softens the gate passes but the sub-cut value clips to head-
    room 0, so "free capitulation" (a pinned lure scoring high merely because the level rose) is
    bounded by its true headroom on the OTHER soft dims. ``P* = 1`` iff every current hard cut is
    met AND every current degree is maximal over the compliant set — i.e. the hero (at level ≥ 1);
    at level 0 every fully-compliant item scores 1.
    """
    mh = set(must_have_fields)
    gate = 1.0
    pref_terms: list[float] = []
    for key, c in cs.per_criterion.items():
        if _field_of(key) in mh:
            if soft:
                gate *= _soft_compliance(key, c)
            elif c["s"] < 1.0:
                gate = 0.0
        else:
            pref_terms.append(c["s"] ** STRICT_GAMMA)
    opt = (sum(pref_terms) / len(pref_terms)) if pref_terms else 1.0
    return gate * opt


def strict_binary(cs: CriteriaScore, must_have_fields, *, eps: float = 1e-9) -> float:
    """SECONDARY all-or-nothing metric ("met-or-0"): the product of per-dim indicators.

      * every CURRENT-level hard cut met (absolute dims: met -> 1, else 0), AND
      * on every CURRENT-level graded dim the pick is the (tied-)best over the compliant
        set (relative dims: best-if-required -> 1, else 0)  [s_k == 1 iff x == B]

    -> 1.0 only for a pick that fully satisfies the stated preference at this level: at
    level 0 that is any fully-compliant item; at level >= 1 effectively the hero (or an
    exact tie on every graded dim). Multiplicative, no partial credit — reported beside
    P* as the "perfect-pick" indicator.
    """
    mh = set(must_have_fields)
    for key, c in cs.per_criterion.items():
        if _field_of(key) in mh:
            if c["s"] < 1.0:
                return 0.0
        elif c["s"] < 1.0 - eps:
            return 0.0
    return 1.0


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
