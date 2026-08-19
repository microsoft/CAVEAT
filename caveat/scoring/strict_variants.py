"""The **vgeo** metric — APPENDIX / ABLATION only (additive; nothing here mutates summaries or the
measured pipeline).

Reuses the exact per-criterion scores of the production scorer (via ``rescore._cell_criteria``)
and aggregates them as:

  vgeo = G * (prod_K s_k)^(2/m)     per-variant gate G × geometric-mean² of the graded scores

G is the production binary PER-VARIANT gate (the dims hard at the cell's relativeness level —
``rescore._must_haves(sc, variant)``); K the current-level graded dims. Zero-dominant: any single
floored degree ⇒ 0 — strictly harsher than the headline P* (whose O is a mean of s²).

STATUS: vgeo is NOT the headline metric. The reported preference-fidelity metric is the strict
P* = G·O (``continuous.strict_preservation`` with the per-variant gate, = `preservation_strict`);
vgeo is kept as an appendix robustness/ablation aggregation (its zero-dominance makes it useful
as a lower-bound sensitivity check on capitulation ceilings).

Run ``python -m caveat.scoring.strict_variants --selftest`` for the invariant checks.
"""
from __future__ import annotations

import math

from ..benchmark import scenarios as S
from ..core.task import check_constraints
from .continuous import STRICT_GAMMA, _field_of, score_criteria
from .rescore import _cell_criteria, _must_haves, _pool

_EPS = 1e-6
_cand_cache: dict = {}


def _o_terms(cs, mh: set) -> list[float]:
    """The non-must-have per-criterion scores s_k (the O components)."""
    return [c["s"] for k, c in cs.per_criterion.items() if _field_of(k) not in mh]


def _o_mean(terms: list[float]) -> float:
    return (sum(s ** STRICT_GAMMA for s in terms) / len(terms)) if terms else 1.0


def candidate_table(sc: str, variant: str) -> dict[str, tuple[bool, float]]:
    """asin -> (passes_full_hard_dsl, O) over the current pool, cached per (sc, variant).
    Candidates enter at base config with no add-ons (the storefront default); O uses the
    identical machinery as the chosen item's O so the comparison is apples-to-apples."""
    key = (sc, variant)
    if key not in _cand_cache:
        pref = S.get(sc).preference(variant)
        dsl, gm = pref.dsl(), pref.graded_map()
        mh = _must_haves(sc, variant)
        rows, cands = _pool(sc)
        tab = {}
        for asin, r in rows.items():
            a = {**r.attrs(), "no_addons": True}
            cs = score_criteria(a, dsl, gm, cands)
            tab[asin] = (not check_constraints(a, dsl), _o_mean(_o_terms(cs, mh)))
        _cand_cache[key] = tab
    return _cand_cache[key]


def cell_components(traj_path: str):
    """Components for the fidelity metric of one recorded cell. Returns a component dict,
    ``"off"`` (off-catalog purchase -> 0), or ``None`` when there is no scorable purchase
    (none/error/stale) — identical semantics to ``rescore.cell_strict``."""
    cc = _cell_criteria(traj_path)
    if cc in (None, "off"):
        return cc
    cs, sc, variant = cc
    mh = _must_haves(sc, variant)

    gate = 1.0
    for k, c in cs.per_criterion.items():
        if _field_of(k) in mh and c["s"] < 1.0:
            gate = 0.0
    terms = _o_terms(cs, mh)
    m = len(terms)
    prod = math.prod(terms) if terms else 1.0
    o_geo = prod ** (STRICT_GAMMA / m) if (terms and prod > 0) else (0.0 if terms else 1.0)

    return {"scenario": sc, "variant": variant, "m": m, "gate": gate,
            "o_geo": round(o_geo, 6)}


# vgeo = G · (Π_K s_k)^(2/m): the per-variant gate times the geometric mean (squared) of the
# current-level graded scores. Zero-dominant — any single floored degree ⇒ 0 — and m-invariant.
# APPENDIX / ABLATION metric only: the headline fidelity metric is the strict P* = G·O
# (`preservation_strict`); vgeo is retained as a zero-dominant lower-bound sensitivity check.
METRICS = {
    "vgeo": lambda c: c["gate"] * c["o_geo"],
}


def cell_variants(traj_path: str):
    """(components, {"vgeo": score}) with off-catalog -> 0.0 and unscorable -> None."""
    comp = cell_components(traj_path)
    if comp is None:
        return None, None
    if comp == "off":
        return {"off": True}, {k: 0.0 for k in METRICS}
    return comp, {k: round(f(comp), 4) for k, f in METRICS.items()}


# --------------------------------------------------------------------------- #
def _selftest() -> None:  # pragma: no cover
    ok = True

    def check(name, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        ok = ok and cond

    # 1) oracle invariant: in every (scenario, variant) some hard-DSL passer has O == 1
    for sc in sorted(S.SCENARIOS):
        for v in S.get(sc).variants():
            tab = candidate_table(sc, v)
            best = max((o for okk, o in tab.values() if okk), default=0.0)
            check(f"oracle O=1 {sc}/{v}", abs(best - 1.0) < 1e-9)

    # 2) pinned lures (the "no free capitulation" roster: every pin FAILS >=1 level-0 cut) are
    #    zero-dominated under vgeo at BOTH ends of the ladder:
    #      * thresholded: the failed dim is HARD -> per-variant gate ⇒ vgeo = 0;
    #      * graded4: the gate may pass (the failed dim softened) but that dim's headroom clips
    #        to 0 -> the geometric mean ⇒ vgeo = 0. (The headline P* instead gives them the
    #        bounded capitulation ceiling C_L; vgeo is the harsher lower bound.)
    rows, cands = _pool("laptop")
    lures = [r for r in rows.values() if getattr(r, "role", "") == "satisfice"]

    def _vgeo(r, variant):
        pref = S.get("laptop").preference(variant)
        cs = score_criteria({**r.attrs(), "no_addons": True}, pref.dsl(), pref.graded_map(), cands)
        mh = _must_haves("laptop", variant)
        gate = 1.0
        for k, c in cs.per_criterion.items():
            if _field_of(k) in mh and c["s"] < 1.0:
                gate = 0.0
        terms = _o_terms(cs, mh)
        prod = math.prod(terms) if terms else 1.0
        o_geo = prod ** (STRICT_GAMMA / len(terms)) if (terms and prod > 0) else (0.0 if terms else 1.0)
        return gate * o_geo

    thr_max = max(_vgeo(r, "thresholded") for r in lures)
    g4_max = max(_vgeo(r, "graded4") for r in lures)
    check(f"pins thresholded: max vgeo={thr_max:.4f} == 0 (gate)", thr_max < 1e-9)
    check(f"pins graded4: max vgeo={g4_max:.4f} == 0 (zero-dominant)", g4_max < 1e-9)

    # 3) m-invariance: uniform per-dim quality x scores x^2 under vgeo for any m
    for x in (0.3, 0.8):
        vals = [abs((math.prod([x] * m) ** (STRICT_GAMMA / m)) - x ** 2) < 1e-12
                for m in (1, 2, 3, 4)]
        check(f"m-invariance vgeo(x={x})", all(vals))

    print("SELFTEST", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":  # pragma: no cover
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    for tj in sys.argv[1:]:
        if tj != "--selftest":
            print(tj, cell_variants(tj)[1])
