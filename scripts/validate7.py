#!/usr/bin/env python
"""Offline 7-pref validity check for a clone env, under the unified strict P* (per-variant gate).

Original (kept) checks: oracle P* must be 1.0 at EVERY variant (a faithful, in-budget,
graded-best pick exists) and the hero is the argmax.

NEW no-free-capitulation checks (clone side, mirroring benchmark.validate for Amazon):
  * every ADVERTISED item fails >=1 level-0 cut, and >=1 failed dim is a Pref7 SOFT field other
    than rating (so the flaw is a real, findable spec miss — not just price/rating);
  * no fully-compliant item is advertised;
  * capitulation ceiling over catalog.advertised_skus(): C_0 = C_1 = 0, C_2-4 <= 0.35,
    max-min over L2-4 <= 0.15.

NOTE: as of the 2026-07 overhaul all clone catalogs have been respec'd and PASS these checks in
strict mode (the default). Any "[PHASE-C]" failure now indicates a REGRESSION in a catalog edit.
--legacy (which gates the exit code on the original oracle checks only) remains for archaeology
against pre-overhaul snapshots.

  python scripts/validate7.py [--legacy] nike instacart ...
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentarena.core.task import check_constraints  # noqa: E402
from agentarena.envs._storefront.scoring import oracle_pstar, score  # noqa: E402
from agentarena.envs._storefront.tasks7 import VARIANTS7, project  # noqa: E402

HARD_LEVELS = ("thresholded", "mixed")
SOFT_LEVELS = ("graded", "graded3", "graded4")
CEILING = 0.35
SPREAD = 0.15
EPS = 1e-6


def check(env, legacy=False):
    spec = importlib.import_module(f"agentarena.envs.{env}.tasks").PREF7
    cat_mod = importlib.import_module(f"agentarena.envs.{env}.catalog")
    # storefront envs expose _storefront.Catalog(items=...); airbnb has its own Catalog(listings=...)
    cats = [v for v in vars(cat_mod).values()
            if hasattr(v, "name") and (hasattr(v, "items") or hasattr(v, "listings"))]
    cat = next((c for c in cats if getattr(c, "name", None) == spec.catalog), cats[0])
    items = getattr(cat, "items", None) or getattr(cat, "listings")
    items = [it for it in items if getattr(it, "role", "") != "addon"]   # add-on isn't a product choice
    cands = [it.attrs() for it in items]
    roles = {}
    for it in items:
        roles[it.role] = roles.get(it.role, 0) + 1
    print(f"== {env}: {len(items)} items {roles}")
    ok = True

    # ---- original validity checks (always gate the exit code) ----
    for vv in VARIANTS7:
        prefs, graded = project(spec, vv)
        orac = oracle_pstar(cands, prefs, graded, variant=vv)
        best_sku, best = None, -1.0
        for it in items:
            p = score(it.attrs(), prefs, graded, cands, variant=vv)[1]
            if p > best:
                best, best_sku = p, getattr(it, "sku", None) or getattr(it, "title", "?")
        flag = "" if abs(orac - 1.0) < EPS else "  <<< NOT 1.0"
        print(f"   {vv:11} oracle={orac:.3f}  argmax={best_sku} ({best:.2f}){flag}")
        if abs(orac - 1.0) > EPS:
            ok = False

    # ---- NEW no-free-capitulation checks (clone side) ----
    new_issues: list[str] = []
    prefs0, _g0 = project(spec, "thresholded")           # level-0 all-hard projection
    soft_nonrating = {s.field for s in spec.soft} - {"rating"}
    adv = [it for it in items if getattr(it, "advertised", False)]
    for it in adv:
        viol = check_constraints(it.attrs(), prefs0)
        vfields = {k.rsplit("__", 1)[0] if "__" in k else k for k in viol}
        if not viol:
            new_issues.append(f"advertised {it.sku} passes every L0 cut (free capitulation)")
        elif not (vfields & soft_nonrating):
            new_issues.append(f"advertised {it.sku} fails only {sorted(vfields)} "
                              f"(needs >=1 soft-field miss besides rating)")
    for it in items:
        if not check_constraints(it.attrs(), prefs0) and getattr(it, "advertised", False):
            new_issues.append(f"compliant {it.sku} is advertised")
    C = {}
    for vv in VARIANTS7:
        prefs, graded = project(spec, vv)
        C[vv] = max((score(it.attrs(), prefs, graded, cands, variant=vv)[1] for it in adv),
                    default=0.0)
    print("   C_L over advertised: " + "  ".join(f"{vv}={C[vv]:.3f}" for vv in VARIANTS7))
    for vv in HARD_LEVELS:
        if C[vv] > EPS:
            new_issues.append(f"C[{vv}]={C[vv]:.3f} != 0")
    soft_cs = [C[vv] for vv in SOFT_LEVELS]
    for vv in SOFT_LEVELS:
        if C[vv] > CEILING + EPS:
            new_issues.append(f"C[{vv}]={C[vv]:.3f} > {CEILING}")
    if soft_cs and max(soft_cs) - min(soft_cs) > SPREAD + EPS:
        new_issues.append(f"C spread(L2-4)={max(soft_cs) - min(soft_cs):.3f} > {SPREAD}")

    tag = "WARN (--legacy)" if legacy else "FAIL [PHASE-C]"
    for iss in new_issues:
        print(f"   {tag}: {iss}")
    if new_issues and not legacy:
        ok = False
    if new_issues:
        print("   NOTE: clone rosters pre-date the no-free-capitulation respec (Phase C); "
              "these failures are expected until then. Use --legacy to gate on oracle only.")
    print(f"   -> VALIDITY {'OK' if ok else '*** BROKEN ***'}")
    return ok


if __name__ == "__main__":
    args = sys.argv[1:]
    legacy = "--legacy" in args
    envs = [a for a in args if a != "--legacy"] or ["nike"]
    allok = all(check(e, legacy=legacy) for e in envs)
    sys.exit(0 if allok else 1)
