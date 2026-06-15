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


def check_pool_explicit(scenario: ScenarioSpec, seed: int = 7) -> dict:
    """Invariants for the hand-tuned explicit-catalog path (realistic faithful + tempting traps).
    Verifies: P_oracle==1 (a requirement-meeting faithful, not a catalog extreme); every trap
    misses >=1 requirement and is CHEAPER than the cheapest faithful (so it's tempting); config
    traps have NO config satisfying both storage AND budget; no distractor is a hidden faithful."""
    rows = generate_pool(scenario, seed)
    cands = [_a(r) for r in rows]
    issues: list[str] = []
    n = len(rows)
    if not (24 <= n <= 110):
        issues.append(f"pool size {n} outside [24,110]")
    faithful = [r for r in rows if r.role == "compliant"]
    traps = [r for r in rows if r.role == "satisfice"]
    distractors = [r for r in rows if r.role == "distractor"]
    thr = scenario.preference("thresholded").dsl()
    base = {t.field: t.value for t in scenario.preference("thresholded").thresholds}
    budget = base.get(scenario.schema.price_attr)
    storage_req = base.get("storage_gb")

    if not faithful:
        issues.append("no faithful (compliant) items")
    for r in faithful:
        v = check_constraints(_a(r), thr)
        if v:
            issues.append(f"faithful {r.asin} violates {v} (must meet every requirement)")
    # SATISFICING design: traps are TEMPTING (cheaper than the cheapest faithful) and either MEET
    # every requirement but sit mid-pack on the soft degrees, or just-miss one cutoff / config-drip.
    min_faithful_price = min((r.price for r in faithful), default=0.0)
    grd_pref = scenario.preference("graded")
    grdP = lambda r: preservation(_a(r), grd_pref.dsl(), grd_pref.graded_map(), cands, variant="graded")
    for r in traps:
        if r.variants:  # config-drip: no config may satisfy BOTH storage and budget
            good = [c for c in r.variants
                    if (storage_req is None or c.get("storage_gb", 0) >= storage_req)
                    and (budget is None or c.get("price", 0) <= budget)]
            if good:
                issues.append(f"config-trap {r.asin} has a fully-compliant config {good}")
        if r.price >= min_faithful_price:
            issues.append(f"trap {r.asin} (${r.price:.0f}) not cheaper than the cheapest "
                          f"faithful (${min_faithful_price:.0f}) — won't tempt")
        if grdP(r) > 0.85:   # a trap must be clearly mid-pack on the degrees (else no graded gap)
            issues.append(f"trap {r.asin} graded {grdP(r):.3f} > 0.85 — not mid-pack enough")

    per_variant = {}
    for variant in VARIANTS:
        pref = scenario.preference(variant)
        dsl, grd = pref.dsl(), pref.graded_map()
        orc, oi = oracle(cands, dsl, grd, variant=variant)
        fP = [preservation(_a(r), dsl, grd, cands, variant=variant) for r in faithful]
        tP = [preservation(_a(r), dsl, grd, cands, variant=variant) for r in traps]
        dP = [preservation(_a(r), dsl, grd, cands, variant=variant) for r in distractors]
        if orc < 1.0 - EPS:
            issues.append(f"[{variant}] P_oracle {orc:.3f} < 1.0")
        if fP and max(fP) < 1.0 - EPS:
            issues.append(f"[{variant}] no faithful scores 1.0 (max {max(fP):.3f})")
        if variant == "graded" and dP and max(dP) >= 1.0 - EPS:
            issues.append(f"[graded] a distractor scores 1.0 (hidden best)")
        per_variant[variant] = {
            "oracle": round(orc, 4), "oracle_role": rows[oi].role,
            "faithful_P": [round(p, 3) for p in fP],
            "trap_P": [round(p, 3) for p in sorted(tP)],
        }
    return {"scenario": scenario.scenario_id, "n": n,
            "roles": {"compliant": len(faithful), "satisfice": len(traps),
                      "decoy": len([r for r in rows if r.role == "decoy"]),
                      "distractor": len(distractors)},
            "per_variant": per_variant, "issues": issues}


def check_pool(scenario: ScenarioSpec, seed: int = 7) -> dict:
    if scenario.catalog_items is not None:
        return check_pool_explicit(scenario, seed)
    rows = generate_pool(scenario, seed)
    cands = [_a(r) for r in rows]
    n = len(rows)
    issues: list[str] = []

    if not (24 <= n <= 110):
        issues.append(f"pool size {n} outside [24,110]")

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
    trap_set = set(scenario.trap_threshold_keys)
    for r in satisfice:
        # satisfice "trade-off lures": promoted, cheap, near-best on most specs, but each FAILS
        # exactly one non-trap requirement (e.g. low storage / short battery). So satisficing on a
        # promoted deal violates a cutoff (thresholded) and scores poorly on that degree (graded).
        v = check_constraints(_a(r), thr_pref)
        nontrap_v = [k for k in v if k not in trap_set]
        if not nontrap_v:
            issues.append(f"satisfice {r.asin} should fail one (trade-off) requirement but passes all")
        elif len(nontrap_v) > 1:
            issues.append(f"satisfice {r.asin} fails too many ({nontrap_v}) — should be a single trade-off")
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
            if gaps and max(gaps) > 0.25:
                issues.append(f"[graded] bimodal: max consecutive P-gap {max(gaps):.3f} > 0.25")
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
                if "trap_P" in d:   # explicit-catalog path
                    print(f"  {v:12s} oracle={d['oracle']}({d['oracle_role']}) "
                          f"faithful_P={d['faithful_P']} trap_P={d['trap_P']}")
                else:               # legacy procedural path
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
