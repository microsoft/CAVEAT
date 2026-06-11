"""Scoring sanity checks (run: python -m agentarena.scoring.selftest).

Verifies the load-bearing properties of the continuous scorer:
  * refinement invariant: P == 1 iff binary check_constraints reports no violation;
  * margin monotonicity: s decreases as a violation grows;
  * graded percentile: best candidate -> 1, worst -> 0, mid -> ~0.5;
  * edge cases: equality/bool 0-1, contains fraction, missing attr -> 0.
"""

from __future__ import annotations

from ..core.task import check_constraints
from .continuous import graded_score, score_criteria, thresholded_score


def _check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    return cond


def main() -> int:
    ok = True
    cands = [{"price": p, "weight_kg": w} for p, w in
             [(650, 1.05), (700, 1.1), (900, 1.3), (1300, 1.7), (2100, 2.6)]]
    prefs = {"price__lt": 1000, "weight_kg__max": 1.45}

    # refinement: a fully-satisfying item scores 1 and has no binary violations
    good = {"price": 650, "weight_kg": 1.05}
    cs = score_criteria(good, prefs, {}, cands)
    ok &= _check("refinement: P==1 for compliant", abs(cs.aggregate("thresholded") - 1.0) < 1e-9)
    ok &= _check("refinement: no binary violations for compliant",
                 check_constraints(good, prefs) == [])

    # a violating item scores <1 and has binary violations
    bad = {"price": 1300, "weight_kg": 1.7}
    csb = score_criteria(bad, prefs, {}, cands)
    ok &= _check("refinement: P<1 for violator", csb.aggregate("thresholded") < 1.0)
    ok &= _check("refinement: binary violations present", check_constraints(bad, prefs) != [])

    # margin monotonicity (price over budget): bigger overage -> lower s
    s1, _ = thresholded_score(1100, op="lt", target=1000, candidate_vals=[c["price"] for c in cands])
    s2, _ = thresholded_score(1300, op="lt", target=1000, candidate_vals=[c["price"] for c in cands])
    s3, _ = thresholded_score(2100, op="lt", target=1000, candidate_vals=[c["price"] for c in cands])
    ok &= _check(f"margin monotone decreasing ({s1:.2f}>{s2:.2f}>{s3:.2f})", s1 > s2 > s3 - 1e-9)
    ok &= _check("margin: at-threshold credit high, worst-candidate -> 0",
                 s1 > 0.8 and abs(s3) < 1e-9)

    # graded percentile: best -> 1, worst -> 0
    wts = [c["weight_kg"] for c in cands]
    sb, _ = graded_score(1.05, direction="lower", candidate_vals=wts)
    sw, _ = graded_score(2.6, direction="lower", candidate_vals=wts)
    sm, _ = graded_score(1.3, direction="lower", candidate_vals=wts)
    ok &= _check(f"graded: best->1 worst->0 mid~0.5 ({sb:.2f}/{sm:.2f}/{sw:.2f})",
                 abs(sb - 1.0) < 1e-9 and abs(sw) < 1e-9 and 0.3 < sm < 0.7)

    # equality / bool / contains / missing
    se, _ = thresholded_score(False, op="eq", target=False, candidate_vals=[])
    sne, _ = thresholded_score(True, op="eq", target=False, candidate_vals=[])
    ok &= _check("equality 0/1", se == 1.0 and sne == 0.0)
    sc, _ = thresholded_score(["Wifi", "TV"], op="contains", target=["Wifi", "Pool"], candidate_vals=[])
    ok &= _check(f"contains = fraction of needles ({sc:.2f})", abs(sc - 0.5) < 1e-9)
    sm2, _ = thresholded_score(None, op="lt", target=1000, candidate_vals=[c["price"] for c in cands])
    ok &= _check("missing attr -> 0", sm2 == 0.0)

    print("\n", "ALL SCORING CHECKS PASS" if ok else "SCORING CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
