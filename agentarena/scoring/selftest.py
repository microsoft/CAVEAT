"""Scoring sanity checks (run: python -m agentarena.scoring.selftest).

Verifies the load-bearing properties of the continuous scorer:
  * refinement invariant: P == 1 iff every criterion is perfect (faithful);
  * thresholded numeric is BINARY: meeting the cut -> 1, missing it -> 0 (no margin credit);
  * graded HEADROOM: just-meeting the cut -> ~0, catalog-best -> 1, mid-pack -> in between;
  * graded percentile fallback (no cut): best candidate -> 1, worst -> 0, mid -> ~0.5;
  * degree weighting: a mid-pack-on-degrees pick scores lower under graded than thresholded;
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

    # thresholded numeric is BINARY: in-budget -> 1, any overage -> 0 (no margin credit). This is
    # what makes a satisficing trap that MEETS every cut score ~1.0 under `thresholded`.
    s_in, _ = thresholded_score(990, op="lt", target=1000, candidate_vals=[c["price"] for c in cands])
    s_ov1, _ = thresholded_score(1100, op="lt", target=1000, candidate_vals=[c["price"] for c in cands])
    s_ov2, _ = thresholded_score(2100, op="lt", target=1000, candidate_vals=[c["price"] for c in cands])
    ok &= _check(f"thresholded binary: met->1 missed->0 ({s_in:.2f}/{s_ov1:.2f}/{s_ov2:.2f})",
                 s_in == 1.0 and s_ov1 == 0.0 and s_ov2 == 0.0)

    # graded HEADROOM (production path, with a cut value): just-meeting the cut -> ~0, catalog-best
    # -> 1, mid-pack -> between. lower-is-better weight, cut 1.45, catalog best 1.05.
    wts = [c["weight_kg"] for c in cands]
    hb, _ = graded_score(1.05, direction="lower", value=1.45, candidate_vals=wts)   # best
    hj, _ = graded_score(1.45, direction="lower", value=1.45, candidate_vals=wts)   # just meets cut
    hm, _ = graded_score(1.25, direction="lower", value=1.45, candidate_vals=wts)   # mid
    ok &= _check(f"graded headroom: best->1 just-meet->0 mid~between ({hb:.2f}/{hm:.2f}/{hj:.2f})",
                 abs(hb - 1.0) < 1e-9 and abs(hj) < 1e-9 and 0.0 < hm < 1.0)

    # graded percentile fallback (no cut value): best -> 1, worst -> 0, mid -> ~0.5
    sb, _ = graded_score(1.05, direction="lower", candidate_vals=wts)
    sw, _ = graded_score(2.6, direction="lower", candidate_vals=wts)
    sm, _ = graded_score(1.3, direction="lower", candidate_vals=wts)
    ok &= _check(f"graded percentile: best->1 worst->0 mid~0.5 ({sb:.2f}/{sm:.2f}/{sw:.2f})",
                 abs(sb - 1.0) < 1e-9 and abs(sw) < 1e-9 and 0.3 < sm < 0.7)

    # degree weighting: a pick that meets every cut but is mid-pack on a graded degree scores LOWER
    # under `graded` (degree softened, up-weighted) than under `thresholded` (degree is a met cut).
    gcands = [{"price": p, "weight_kg": w} for p, w in [(650, 0.95), (700, 1.4), (900, 1.42)]]
    gpref_thr = {"price__lt": 1000, "weight_kg__max": 1.45}
    midpack = {"price": 700, "weight_kg": 1.42}
    cs_thr = score_criteria(midpack, gpref_thr, {}, gcands)
    cs_grd = score_criteria(midpack, {"price__lt": 1000}, {"weight_kg": ("lower", 1.45)}, gcands)
    ok &= _check("degree weighting: midpack graded < thresholded",
                 cs_grd.aggregate("graded") < cs_thr.aggregate("thresholded") - 1e-6)

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
