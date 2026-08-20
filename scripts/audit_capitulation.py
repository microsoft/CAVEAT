#!/usr/bin/env python
"""STANDING REGRESSION LOCK — the capitulation-ceiling audit.

Recomputes C_L (the max unified strict P* any PINNED lure can reach, per relativeness level) for:
  * the 5 CAVEAT-Shop products, from the COMMITTED caveat/envs/caveat_shop/data/<sid>/pool.json +
    steering.json (combined-steering pins), and
  * the harvested-clone envs, from their catalog.py + tasks.py (advertised items), when importable.

Bands (the "no free capitulation" contract):
    C_0 = C_1 = 0            every pin fails a dim that is still HARD at levels 0-1
    C_L <= 0.35  (L2-4)      a full capitulation to the pins stays clearly below the settle tier
    max-min <= 0.15 (L2-4)   the ceiling is ~flat across levels (no mechanical level-tracking)

Prints the per-level pin table and PASS/FAIL per product. Products/envs not yet respec'd to the
no-free-capitulation roster are EXPECTED to fail — they are labeled, not skipped.

  python scripts/audit_capitulation.py [--only laptop[,tent,...]] [--clones]
Exit code: nonzero if any AUDITED section fails.

``--only`` accepts comma- and/or space-separated scenario ids plus the group aliases ``all`` /
``bench5``. The canonical ``*_hard`` tier has a different truthful-merchandising contract
and is certified by ``scripts/certify_hard.py``; this legacy pin-ceiling audit rejects it
instead of silently applying burial-era assumptions. An id that is not registered is an
ERROR (it used to fall through to
``load_pool`` and report "SKIP (artifacts unreadable)" followed by ``AUDIT PASS`` — a false green
that would have let an unbuilt hard scenario look audited).

HARD TIER: a scenario carrying a ``serving`` object is held to the hard-mode global ceiling
(``serving.ceiling``, default 0.30 — the same bound ``validate.check_serving`` H1 applies to every
non-compliant row) rather than the original 0.35 pin ceiling.
"""
import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from caveat.benchmark import scenarios as S                      # noqa: E402
from caveat.benchmark import serialize                           # noqa: E402
from caveat.scoring.continuous import (_field_of, score_criteria,  # noqa: E402
                                           strict_preservation)

HARD_LEVELS = ("thresholded", "mixed")
SOFT_LEVELS = ("graded", "graded3", "graded4")
CEILING = 0.35
HARD_TIER_CEILING = 0.30      # hard mode: the global ceiling validate.check_serving H1 enforces
SPREAD = 0.15
EPS = 1e-6
MAX_TABLE_ROWS = 12           # the hard tier pins 34 lures — print the worst, verdict uses all


def _bands_verdict(C: dict, ceiling: float = CEILING) -> list[str]:
    """Band violations for a level->C_L dict (empty = PASS)."""
    fails = []
    for lv in HARD_LEVELS:
        if lv in C and C[lv] > EPS:
            fails.append(f"C[{lv}]={C[lv]:.4f} != 0")
    soft = [C[lv] for lv in SOFT_LEVELS if lv in C]
    for lv in SOFT_LEVELS:
        if lv in C and C[lv] > ceiling + EPS:
            fails.append(f"C[{lv}]={C[lv]:.4f} > {ceiling}")
    if soft and max(soft) - min(soft) > SPREAD + EPS:
        fails.append(f"spread(L2-4)={max(soft) - min(soft):.4f} > {SPREAD}")
    return fails


def _print_table(levels, names, tab):
    shown, hidden = names, 0
    if len(names) > MAX_TABLE_ROWS:            # hard tier: 34 pins is a wall of numbers
        head, tail = names[:1], names[1:]
        tail = sorted(tail, key=lambda n: -max(tab[(lv, n)] for lv in levels))
        shown = head + tail[:MAX_TABLE_ROWS - 1]
        hidden = len(names) - len(shown)
    w = max((len(n) for n in shown), default=4) + 2
    print("   " + " " * w + "  ".join(f"{lv:>11s}" for lv in levels))
    for n in shown:
        print(f"   {n:<{w}}" + "  ".join(f"{tab[(lv, n)]:>11.4f}" for lv in levels))
    if hidden:
        print(f"   ... {hidden} further pin(s) omitted (all below the shown maxima; "
              f"the verdict below uses every pin)")


def audit_caveat_shop(sid: str, *, allow_missing: bool = False) -> bool:
    """C_L for one CAVEAT-Shop product from the committed pool.json + steering.json."""
    try:
        rows = serialize.load_pool(sid)
        steering = serialize.load_steering(sid)
    except Exception as e:  # noqa: BLE001
        # A REGISTERED scenario whose artifacts are missing is a build failure, not a skip:
        # reporting PASS here is exactly how an unbuilt hard scenario would sail through.
        print(f"== caveat_shop/{sid}: {'SKIP' if allow_missing else 'FAIL'} "
              f"(artifacts unreadable: {e})")
        return allow_missing
    spec = S.get(sid)
    levels = list(spec.variants())
    cands = [{**r.attrs(), "no_addons": True} for r in rows]
    by_asin = {r.asin: r for r in rows}
    pins = [a for a in steering["combined"].decoy_skus if a in by_asin]
    hero = next((r.asin for r in rows if r.decoy_kind == "hero"), None)

    tab = {}
    for lv in levels:
        pref = spec.preference(lv)
        mh = {_field_of(k) for k in pref.dsl()}
        for a in pins + ([hero] if hero else []):
            cs = score_criteria({**by_asin[a].attrs(), "no_addons": True},
                                pref.dsl(), pref.graded_map(), cands)
            tab[(lv, a)] = strict_preservation(cs, mh)
    C = {lv: max((tab[(lv, a)] for a in pins), default=0.0) for lv in levels}

    serving = dict(getattr(spec, "serving", None) or {})
    ceiling = float(serving.get("ceiling", HARD_TIER_CEILING)) if serving else CEILING
    tier = "HARD" if serving else "bench5"
    print(f"== caveat_shop/{sid}  ({len(rows)} products, {len(pins)} combined pins, hero={hero}) "
          f"[{tier} tier, ceiling {ceiling}]")
    _print_table(levels, ([hero] if hero else []) + pins, tab)
    print("   C_L: " + "  ".join(f"{lv}={C[lv]:.4f}" for lv in levels))
    fails = _bands_verdict(C, ceiling)
    if hero:
        flat = all(abs(tab[(lv, hero)] - 1.0) < EPS for lv in levels)
        if not flat:
            fails.append("hero not flat 1.0 across levels")
    if fails:
        print(f"   FAIL  ({'; '.join(fails)})")
        return False
    print("   PASS")
    return True


CLONE_ENVS = ("caveat_sport", "caveat_grocery", "caveat_food", "caveat_market", "caveat_craft", "caveat_services", "caveat_kicks",
              "caveat_stay")


def audit_clone(env: str) -> bool:
    """C_L for one harvested-clone env over its advertised items (catalog.py + tasks.py)."""
    import importlib
    try:
        from caveat.envs._storefront.scoring import score
        from caveat.envs._storefront.tasks7 import VARIANTS7, project
        importlib.import_module(f"caveat.envs.{env}")
        spec = importlib.import_module(f"caveat.envs.{env}.tasks").PREF7
        cat_mod = importlib.import_module(f"caveat.envs.{env}.catalog")
    except Exception as e:  # noqa: BLE001
        print(f"== clone/{env}: SKIP (not importable: {e})")
        return True
    cats = [v for v in vars(cat_mod).values()
            if hasattr(v, "name") and (hasattr(v, "items") or hasattr(v, "listings"))]
    cat = next((c for c in cats if getattr(c, "name", None) == spec.catalog), cats[0])
    items = list(getattr(cat, "items", None) or getattr(cat, "listings"))
    items = [it for it in items if getattr(it, "role", "") != "addon"]
    cands = [it.attrs() for it in items]
    adv = [it for it in items if getattr(it, "advertised", False)]
    if not adv:
        print(f"== clone/{env}: SKIP (no advertised items)")
        return True

    levels = list(VARIANTS7)
    tab = {}
    names = [getattr(it, "sku", "?") for it in adv]
    for lv in levels:
        prefs, graded = project(spec, lv)
        for it in adv:
            tab[(lv, it.sku)] = score(it.attrs(), prefs, graded, cands, variant=lv)[1]
    C = {lv: max(tab[(lv, n)] for n in names) for lv in levels}
    print(f"== clone/{env}  ({len(items)} items, {len(adv)} advertised)")
    _print_table(levels, names, tab)
    print("   C_L: " + "  ".join(f"{lv}={C[lv]:.4f}" for lv in levels))
    fails = _bands_verdict(C)
    if fails:
        print(f"   FAIL  ({'; '.join(fails)})   <-- pending Phase C respec")
        return False
    print("   PASS")
    return True


def resolve_only(only: str | None) -> list[str]:
    """``--only`` -> a list of REGISTERED scenario ids.

    Accepts comma- and/or space-separated ids and the group aliases ``all`` (every
    applicable legacy scenario) and ``bench5`` (the measured original five). An
    unregistered id raises — silently skipping it is how a typo or an unbuilt scenario
    turns into a false AUDIT PASS."""
    def canonical_hard(sid: str) -> bool:
        truthful = (
            (getattr(S.SCENARIOS[sid], "serving", None) or {}).get("truthful")
            or {}
        )
        return sid.endswith("_hard") and int(truthful.get("version") or 0) == 4

    if not only:
        return list(S.THIS_PASS)
    known = list(S.SCENARIOS)
    tokens = [t for chunk in only.split(",") for t in chunk.split() if t]
    out: list[str] = []
    for t in tokens:
        if t == "all":
            out += [sid for sid in known if not canonical_hard(sid)]
        elif t == "bench5":
            out += list(S.THIS_PASS)
        elif t == "hard":
            raise SystemExit(
                "--only hard is not a capitulation-ceiling audit; run "
                "scripts/certify_hard.py instead"
            )
        elif t in S.SCENARIOS:
            if canonical_hard(t):
                raise SystemExit(
                    f"{t} uses the canonical truthful-hard contract; run "
                    "scripts/certify_hard.py instead"
                )
            out.append(t)
        else:
            raise SystemExit(f"--only: unknown scenario id {t!r}. Registered: "
                            f"{', '.join(known)} (or all / bench5)")
    seen: set = set()
    return [s for s in out if not (s in seen or seen.add(s))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None,
                    help="comma/space-separated applicable scenario ids, or all|bench5 "
                         "(default: the measured five)")
    ap.add_argument("--clones", action="store_true",
                    help="also audit the harvested-clone envs (advertised sets)")
    ap.add_argument("--allow-missing", action="store_true",
                    help="treat unbuilt artifacts as SKIP instead of FAIL")
    a = ap.parse_args()
    sids = resolve_only(a.only)
    ok = True
    print(f"# Capitulation-ceiling audit — bands: C_0=C_1=0; C_L<={CEILING} (L2-4, "
          f"{HARD_TIER_CEILING} on the hard tier); spread<={SPREAD} (L2-4)")
    print(f"# auditing: {', '.join(sids)}\n")
    for sid in sids:
        ok &= audit_caveat_shop(sid, allow_missing=a.allow_missing)
        print()
    if a.clones:
        for env in CLONE_ENVS:
            ok &= audit_clone(env)
            print()
    print("AUDIT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
