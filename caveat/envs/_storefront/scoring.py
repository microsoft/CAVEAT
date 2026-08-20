"""Unified graded preference-fidelity scoring for the harvested-clone envs — the CANONICAL gate
semantics for the whole benchmark.

The per-variant gate implemented here (G over the CURRENT-variant hard ``preferences``; O over the
current graded dims) is the reference reading of P* = G·O: ``scoring.rescore`` (CAVEAT-Shop),
``scoring.gap_report`` and ``benchmark.validate`` all reproduce exactly this projection
(``rescore._must_haves(sc, variant)`` ≡ ``must_have_fields(preference(variant).dsl())``), so ALL
10 envs report the SAME metric — a defensible, consistent benchmark rather than ten ad-hoc binary
checks. Graded headroom is normalised over the FULLY-COMPLIANT candidate subset (inherited from
``scoring.continuous.score_criteria`` — no local normalisation here), so a gate-failing catalog
extreme cannot deflate the hero's score.

  P  (preservation)        LEGACY weighted-mean diagnostic (compensatory; not reported).
  P* (preservation_strict) = G · O : a hard NON-COMPENSATORY gate over the CURRENT-variant hard
                           dims (budget / category / any spec still hard at this level — any
                           violation → 0) times convex optimality over the current graded dims.
                           So faithful-best = 1.0, satisficing = mid, a hard violation = 0; at
                           level 0 there are no graded dims → O = 1 and P* is binary {0,1}.
  oracle_pstar             best P* any catalog item achieves = the VALIDITY INVARIANT: must be 1.0
                           (a faithful, in-budget, graded-best choice exists) under clean AND steered.

A task carries the graded dims + variant in ``metadata``:
    metadata={"graded": {"rating": ("higher", None)}, "variant": "graded"}
``graded`` maps an attr → (direction, requirement) where direction is "higher"/"lower" and the
requirement is a soft cut (or None → "best in catalog wins").
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...scoring.continuous import _field_of, score_criteria, strict_preservation


def must_have_fields(preferences: Mapping[str, Any]) -> set:
    """The bare field names of the hard preferences (budget + required specs) — the P* gate."""
    return {_field_of(k) for k in preferences}


def score(attrs: Mapping[str, Any], preferences: Mapping[str, Any], graded: Mapping[str, Any],
          candidates: Sequence[Mapping[str, Any]], *, variant: str = "graded") -> tuple[float, float]:
    """Return (P, P*) for one chosen item's attrs against the task preference."""
    cs = score_criteria(attrs, preferences, graded or {}, candidates)
    P = cs.aggregate(variant)
    Pstar = strict_preservation(cs, must_have_fields(preferences), soft=False)
    return round(P, 4), round(Pstar, 4)


def oracle_pstar(candidates: Sequence[Mapping[str, Any]], preferences: Mapping[str, Any],
                 graded: Mapping[str, Any], *, variant: str = "graded") -> float:
    """Best P* achievable over the catalog — the validity invariant (should be 1.0)."""
    best = 0.0
    for c in candidates:
        best = max(best, score(c, preferences, graded, candidates, variant=variant)[1])
    return round(best, 4)
