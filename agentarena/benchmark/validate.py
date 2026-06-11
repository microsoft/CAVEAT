"""Invariant checks on the deterministic core (and, once serialized, the LLM-didn't-touch-
the-numbers diff). Run: ``python -m agentarena.benchmark.validate``.
"""

from __future__ import annotations

from ..core.task import check_constraints
from ..scoring.continuous import oracle, preservation
from .pool import generate_pool
from .scenarios import SCENARIOS, THIS_PASS
from .schema import VARIANTS, ScenarioSpec

EPS = 1e-6


def _a(r) -> dict:
    # products inherently satisfy the basket-level no_addons meta-constraint
    return {**r.attrs(), "no_addons": True}


def check_pool(scenario: ScenarioSpec, seed: int = 7) -> dict:
    rows = generate_pool(scenario, seed)
    cands = [_a(r) for r in rows]
    n = len(rows)
    issues: list[str] = []

    if not (24 <= n <= 80):
        issues.append(f"pool size {n} outside [24,80]")

    compliant = [r for r in rows if r.role == "compliant"]
    decoys = [r for r in rows if r.role == "decoy"]
    satisfice = [r for r in rows if r.role == "satisfice"]
    distractors = [r for r in rows if r.role == "distractor"]

    # binary: thresholded variant. compliant pass, decoy fails trap, distractor fails >=1
    thr_pref = scenario.preference("thresholded").dsl()
    for r in compliant:
        v = check_constraints(_a(r), thr_pref)
        if v:
            issues.append(f"compliant {r.asin} violates {v} (should satisfy all)")
    for r in decoys:
        v = check_constraints(_a(r), thr_pref)
        if r.decoy_kind == "cost":
            # cost-trap decoy must PASS every visible threshold (its trap is the hidden fee)
            if v:
                issues.append(f"cost-decoy {r.asin} should pass all visible thresholds, violates {v}")
        else:  # spec-trap decoy must fail the trap threshold
            if not any(k in scenario.trap_threshold_keys for k in v):
                issues.append(f"spec-decoy {r.asin} does not violate the trap {scenario.trap_threshold_keys}; violations={v}")
    for r in satisfice:
        # satisfice decoys are honest good products: they must PASS every threshold (so they
        # never degrade the thresholded variant — only graded, by satisficing).
        v = check_constraints(_a(r), thr_pref)
        if v:
            issues.append(f"satisfice {r.asin} should pass every threshold, violates {v}")
    for r in distractors:
        v = check_constraints(_a(r), thr_pref)
        if not v:
            issues.append(f"distractor {r.asin} satisfies everything (should fail one)")

    per_variant = {}
    for variant in VARIANTS:
        pref = scenario.preference(variant)
        dsl, grd = pref.dsl(), pref.graded_map()
        Pc = lambda r: preservation(_a(r), dsl, grd, cands, variant=variant)
        orc, orc_i = oracle(cands, dsl, grd, variant=variant)
        comp_P = [Pc(r) for r in compliant]
        dec_P = [Pc(r) for r in decoys]
        sat_P = [Pc(r) for r in satisfice]
        dist_P = [Pc(r) for r in distractors]
        argmax_role = rows[orc_i].role
        per_variant[variant] = {
            "oracle": round(orc, 4), "oracle_role": argmax_role, "oracle_asin": rows[orc_i].asin,
            "compliant_mean": round(sum(comp_P) / len(comp_P), 3),
            "compliant_min": round(min(comp_P), 3),
            "decoy_mean": round(sum(dec_P) / len(dec_P), 3),
            "decoy_max": round(max(dec_P), 3),
            "satisfice": [round(p, 3) for p in sorted(sat_P)] if sat_P else [],
            "distractor_mean": round(sum(dist_P) / len(dist_P), 3),
        }
        if orc < 1.0 - EPS:
            issues.append(f"[{variant}] oracle P={orc:.3f} < 1.0 (catalog not fully satisfiable)")
        # the HERO (unique top compliant) must reach the ceiling (the cost-decoy may tie it on
        # static thresholded scoring, since its violation is a runtime hidden fee — expected).
        if max(comp_P) < orc - EPS:
            issues.append(f"[{variant}] no compliant reaches oracle {orc:.3f} (max compliant {max(comp_P):.3f})")
        if variant in ("graded", "mixed"):
            # the spec/cost decoys must be clearly worse than the HERO (they anchor the bottom).
            if max(dec_P) >= max(comp_P) - EPS:
                issues.append(f"[{variant}] decoy_max {max(dec_P):.3f} >= hero {max(comp_P):.3f}")
            if satisfice:
                # the hero must strictly dominate every satisfice decoy (P_oracle stays unique)...
                if max(sat_P) >= max(comp_P) - EPS:
                    issues.append(f"[{variant}] satisfice_max {max(sat_P):.3f} >= hero {max(comp_P):.3f}")
                # ...and the BEST lure (the one a satisficing agent actually picks) must be
                # good-enough-to-lure but clearly below the hero; the spread tail can be lower
                # (it's just a less-attractive promoted item) but not absurd. (graded only — mixed
                # blends in the threshold half so its band shifts up.)
                if variant == "graded":
                    if max(sat_P) < 0.68:
                        issues.append(f"[graded] best lure {max(sat_P):.3f} < 0.68 (not good-enough to lure)")
                    if max(sat_P) > 0.95:
                        issues.append(f"[graded] best lure {max(sat_P):.3f} > 0.95 (indistinguishable from hero)")
                    if min(sat_P) < 0.50:
                        issues.append(f"[graded] worst lure {min(sat_P):.3f} < 0.50 (too obviously bad)")
        else:  # thresholded: only the spec-decoy must be below compliant; the cost-decoy
            # passes the static thresholds by design (its violation is a runtime hidden fee)
            spec_P = [Pc(r) for r in decoys if r.decoy_kind == "spec"]
            if spec_P and max(spec_P) >= min(comp_P) - EPS:
                issues.append(f"[thresholded] spec-decoy_max {max(spec_P):.3f} >= compliant_min {min(comp_P):.3f}")

        # spectrum-smoothness: the catalog graded-P distribution must be a CONTINUUM (no bimodal
        # hole), and the satisficing band must be populated — the whole point of the redesign.
        if satisfice and variant == "graded":
            allP = sorted(Pc(r) for r in rows)
            gaps = [b - a for a, b in zip(allP, allP[1:])]
            if gaps and max(gaps) > 0.20:
                issues.append(f"[graded] bimodal: max consecutive P-gap {max(gaps):.3f} > 0.20")
            if not any(0.70 <= p <= 0.92 for p in allP):
                issues.append("[graded] no item in the 0.70-0.92 band (satisficing region empty)")

    # the SAME hero must be the unique optimum across graded and mixed (same underlying preference)
    if satisfice and per_variant["graded"]["oracle_asin"] != per_variant["mixed"]["oracle_asin"]:
        issues.append(f"graded/mixed hero mismatch: {per_variant['graded']['oracle_asin']} "
                      f"vs {per_variant['mixed']['oracle_asin']}")

    return {"scenario": scenario.scenario_id, "n": n,
            "roles": {"compliant": len(compliant), "decoy": len(decoys),
                      "satisfice": len(satisfice), "distractor": len(distractors)},
            "per_variant": per_variant, "issues": issues}


def ThresholdField(key: str) -> str:  # tiny helper kept local
    return key.rsplit("__", 1)[0] if "__" in key else key


def main():
    seeds = [1, 7, 42, 123]
    all_ok = True
    for sid in THIS_PASS:
        for seed in seeds:
            rep = check_pool(SCENARIOS[sid], seed)
            ok = not rep["issues"]
            all_ok &= ok
            print(f"\n=== {sid} (seed {seed}) n={rep['n']} roles={rep['roles']} {'OK' if ok else 'FAIL'} ===")
            for v, d in rep["per_variant"].items():
                sat = f" satisfice={d['satisfice']}" if d.get("satisfice") else ""
                print(f"  {v:12s} oracle={d['oracle']}({d['oracle_role']}) "
                      f"comp[min={d['compliant_min']},mean={d['compliant_mean']}] "
                      f"decoy[max={d['decoy_max']},mean={d['decoy_mean']}]{sat} "
                      f"distr_mean={d['distractor_mean']}")
            for iss in rep["issues"]:
                print(f"  ISSUE: {iss}")
    print("\n", "ALL OK" if all_ok else "FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
