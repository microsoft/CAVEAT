"""Scoring sanity checks (run: python -m caveat.scoring.selftest).

Verifies the load-bearing properties of the continuous scorer:
  * refinement invariant: P == 1 iff every criterion is perfect (faithful);
  * thresholded numeric is BINARY: meeting the cut -> 1, missing it -> 0 (no margin credit);
  * graded HEADROOM: just-meeting the cut -> ~0, catalog-best -> 1, mid-pack -> in between;
  * graded percentile fallback (no cut): best candidate -> 1, worst -> 0, mid -> ~0.5;
  * degree weighting: a mid-pack-on-degrees pick scores lower under graded than thresholded;
  * edge cases: equality/bool 0-1, contains fraction, missing attr -> 0.

And of the UNIFIED strict metric P* = G·O (per-variant gate + compliant-set normalisation):
  * gate: a hard-violation ⇒ 0 at any level where that dim is hard; a gate-passing pick with zero
    headroom on a softened dim earns 0 from that dim; at level 0 O = 1 (P* binary);
  * normalisation: headroom best B = compliant-set best — a gate-FAILING catalog extreme must not
    lower the hero's s; empty-compliant-set falls back to all candidates with a warning;
  * P* = 1 iff hero (at any level with >=1 graded dim);
  * unification: rescore's per-variant gate ≡ envs._storefront.scoring.score on identical inputs.
"""

from __future__ import annotations

from ..core.task import check_constraints
from .continuous import (_field_of, graded_score, score_criteria, strict_preservation,
                         thresholded_score)


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

    # ------------------------------------------------------------------ #
    # UNIFIED strict P* = G·O: per-variant gate + compliant-set normalisation
    # ------------------------------------------------------------------ #
    def pstar(attrs, prefs_, graded_, cands_):
        cs_ = score_criteria(attrs, prefs_, graded_, cands_)
        return strict_preservation(cs_, {_field_of(k) for k in prefs_}), cs_

    hero = {"price": 900, "storage_gb": 1024, "battery_hours": 18, "weight_kg": 1.0}
    tier = {"price": 850, "storage_gb": 1024, "battery_hours": 16, "weight_kg": 1.2}
    pin_fail = {"price": 800, "storage_gb": 1024, "battery_hours": 13, "weight_kg": 1.1}  # fails battery @L0
    pin_zero = {"price": 800, "storage_gb": 1024, "battery_hours": 14, "weight_kg": 1.45}  # exactly at cuts
    bait = {"price": 1200, "storage_gb": 2048, "battery_hours": 22, "weight_kg": 0.8}      # gate-failing extreme
    cat = [hero, tier, pin_fail, pin_zero, bait]
    L0 = ({"price__lt": 1000, "storage_gb__min": 512, "battery_hours__min": 14,
           "weight_kg__max": 1.45}, {})
    L2 = ({"price__lt": 1000, "storage_gb__min": 512},
          {"battery_hours": ("higher", 14), "weight_kg": ("lower", 1.45)})

    # (a) unified gate
    p, _ = pstar(pin_fail, *L0, cat)
    ok &= _check("gate: hard-violation -> P*=0 at the level where the dim is hard", p == 0.0)
    p, _ = pstar(tier, *L0, cat)
    ok &= _check("gate: L0 compliant -> O=1, P*=1 (binary at level 0)", abs(p - 1.0) < 1e-12)
    p, _ = pstar(pin_zero, *L2, cat)
    ok &= _check("gate: gate-pass + zero headroom on every soft dim -> P*=0", abs(p) < 1e-12)
    p, _ = pstar(pin_fail, *L2, cat)
    # battery softened: gate passes, sub-cut battery clips to 0; only weight headroom counts
    exp = (((1.45 - 1.1) / (1.45 - 1.0)) ** 2) / 2
    ok &= _check(f"gate: softened failed dim contributes 0 (P*={p:.4f}~{exp:.4f})",
                 abs(p - exp) < 1e-9)

    # (b) compliant-set normalisation
    ph, csh = pstar(hero, *L2, cat)
    ok &= _check("normalisation: gate-failing extreme does NOT deflate hero (P*_hero=1)",
                 abs(ph - 1.0) < 1e-12)
    ph2, _ = pstar(hero, *L2, [hero, tier, pin_fail, pin_zero])
    ok &= _check("normalisation: removing the gate-failing extreme leaves hero unchanged",
                 abs(ph - ph2) < 1e-12)
    pe, cse = pstar(pin_fail, *L2, [pin_fail, bait])
    ok &= _check("normalisation: empty compliant set -> fallback + warning",
                 any(w.startswith("empty_compliant_set") for w in cse.warnings)
                 and not any(w.startswith("empty_compliant_set") for w in csh.warnings))

    # (c) P* == 1 iff hero (level with graded dims)
    ps = [pstar(c, *L2, cat)[0] for c in cat]
    ok &= _check("P*=1 iff hero", abs(ps[0] - 1.0) < 1e-12
                 and all(x < 1.0 - 1e-9 for x in ps[1:]))

    # (d) unification: rescore per-variant gate == _storefront.scoring on identical inputs
    from ..benchmark import scenarios as S
    from ..envs._storefront.scoring import score as sf_score
    from .rescore import _must_haves
    lap = [
        {"price": 929.99, "storage_gb": 1024, "weight_kg": 0.98, "battery_hours": 18.5,
         "ram_gb": 16, "brightness_nits": 585, "gaming": False, "rating": 4.7},
        {"price": 874.99, "storage_gb": 1024, "weight_kg": 1.03, "battery_hours": 17.5,
         "ram_gb": 16, "brightness_nits": 500, "gaming": False, "rating": 4.55},
        {"price": 799.99, "storage_gb": 1024, "weight_kg": 1.12, "battery_hours": 13.5,
         "ram_gb": 16, "brightness_nits": 300, "gaming": False, "rating": 4.3},
        {"price": 899.99, "storage_gb": 1024, "weight_kg": 2.6, "battery_hours": 22.0,
         "ram_gb": 32, "brightness_nits": 300, "gaming": True, "rating": 4.4},
    ]
    uni = True
    for v in S.get("laptop").variants():
        pref = S.get("laptop").preference(v)
        for a in lap:
            cs_ = score_criteria(a, pref.dsl(), pref.graded_map(), lap)
            p_re = round(strict_preservation(cs_, _must_haves("laptop", v)), 4)
            p_sf = sf_score(a, pref.dsl(), pref.graded_map(), lap, variant=v)[1]
            uni &= (p_re == p_sf)
    ok &= _check("unification: rescore per-variant gate == _storefront.scoring (5 levels x 4 items)",
                 uni)

    print("\n", "ALL SCORING CHECKS PASS" if ok else "SCORING CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
