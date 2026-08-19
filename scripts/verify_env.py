#!/usr/bin/env python
"""READ-ONLY verification of a storefront env's CURRENT catalog data (no mutation), under the
unified strict P* (per-variant gate). Prints the per-level pin table and a verdict enforcing the
"no free capitulation" contract:

  * oracle P* = 1.0 at every one of the 5 levels;
  * the hero curve is flat 1.0;
  * capitulation ceiling over the ADVERTISED set: C_0 = C_1 = 0 and C_2-4 <= 0.35;
  * the best un-advertised mid item lands in [0.20, 0.48] at graded4;
  * the best pin stays at or below that mid item at graded4.

(The old trap-monotone-decline check asserted the retired confound — a pin whose score mechanically
tracked the level — and is deliberately gone.)

  python scripts/verify_env.py <env>
"""
import importlib
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CEILING = 0.35
EPS = 1e-6


def main():
    env = sys.argv[1]
    from agentarena.envs._storefront.scoring import score, oracle_pstar
    import agentarena.envs._storefront.tasks7 as t7
    importlib.import_module(f"agentarena.envs.{env}")
    cm = importlib.import_module(f"agentarena.envs.{env}.catalog")
    tm = importlib.import_module(f"agentarena.envs.{env}.tasks")
    cat = [v for v in vars(cm).values() if hasattr(v, "items") and hasattr(v, "name")][0]
    tasks = {x.task_id.rsplit("-", 1)[1]: x for x in tm.TASKS}
    items = [it for it in cat.items if getattr(it, "role", "") != "addon"]
    cands = [it.attrs() for it in items]
    LV = list(t7.VARIANTS7)

    def ps(it, v):
        return score(it.attrs(), tasks[v].preferences, tasks[v].metadata["graded"], cands, variant=v)[1]

    def curve(it):
        return [round(ps(it, v), 3) for v in LV]

    orac = {v: oracle_pstar(cands, tasks[v].preferences, tasks[v].metadata["graded"], variant=v) for v in LV}
    oracle_ok = all(abs(o - 1.0) < EPS for o in orac.values())
    hero = max(items, key=lambda it: ps(it, "graded4"))
    advs = [it for it in items if getattr(it, "advertised", False)]
    mids = [it for it in items if it.sku != hero.sku and not getattr(it, "advertised", False)
            and ps(it, "graded4") > 0]
    mid = max(mids, key=lambda it: ps(it, "graded4")) if mids else None

    hc = curve(hero)
    mc = curve(mid) if mid else None
    print(f"env={env}")
    print(f"  oracle_ok={oracle_ok}  ({orac})")
    print(f"  HERO {hero.sku}: {hc}")
    if mc:
        print(f"  MID  {mid.sku}: {mc}  graded4={mc[-1]}")
    # per-level PIN table over the advertised set + the capitulation ceiling C_L
    C = {v: 0.0 for v in LV}
    if advs:
        print("  PINS (advertised):")
        for it in advs:
            cv = curve(it)
            print(f"    {it.sku}: {cv}")
            for v, x in zip(LV, cv):
                C[v] = max(C[v], x)
        print(f"  C_L = {[round(C[v], 3) for v in LV]}")
    cap_ok = (C["thresholded"] <= EPS and C["mixed"] <= EPS
              and all(C[v] <= CEILING + EPS for v in ("graded", "graded3", "graded4")))
    mid_ok = bool(mc) and 0.20 <= mc[-1] <= 0.48
    trap_below_mid = (not advs) or (bool(mc) and C["graded4"] <= mc[-1] + EPS)
    # verdict: oracle=1 x5, hero flat 1.0, C_0=C_1=0 & C_2-4<=0.35, mid graded4 in [0.20,0.48],
    # best pin <= mid at graded4
    verdict = (oracle_ok and all(abs(x - 1) < EPS for x in hc)
               and cap_ok and mid_ok and trap_below_mid)
    print(f"  cap_ok={cap_ok}  mid_ok={mid_ok}  trap<=mid={trap_below_mid}")
    print(f"  VERDICT={'PASS' if verdict else 'CHECK'}")


if __name__ == "__main__":
    main()
