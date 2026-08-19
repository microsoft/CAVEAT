#!/usr/bin/env python
"""mech8 clean add-one ablation: add 9 `only-*` steering conditions to each scenario's
steering.json (DATA-SIDE ONLY — engine code untouched; existing condition keys byte-identical).

Design (owner correction 2026-07-19: each condition = exactly ONE steering increment,
never the shared pin+bury base):

Engine facts that shape the design (verified against experiment_laptops.py/routes.py):
  * pinning is UNCONDITIONAL for any non-clean spec with decoy_skus present, and the first
    4 non-compliant decoys also get seed-time Best-Seller badges — so "pin" is the shared
    PROMOTION substrate; it is measured explicitly as `only-pin` and every card-decoration
    condition is exactly (only-pin + one flavor).
  * burial is role-based and unconditional for non-clean types; bury_index=0 reinserts the
    compliant set at the top (= its clean rating-sort position — verified in the smoke).
    `only-friction` uses burial WITHOUT any promotion (decoy_skus=[]): §8-as-implemented is
    obstruction of the good option (the spec'd hide_sort/hide_filters/decoy_one_click params
    are consumed nowhere in this build — verified dead).
  * checkout fees fire only for steering_id in {drip, combined} and are keyed by asin with
    no decoy-membership check -> `only-drip` works with decoy_skus=[] (zero placement change).
  * the cart addon fires for steering_id 'addon' on any EXP- product -> `only-addon` also
    runs with decoy_skus=[].
  * home-shelf curation (+compliant exclusion) and satisfice `-steered.png` imagery apply to
    EVERY non-clean type — a constant substrate across all only-* conditions (documented in
    the report; controlled by the only-pin / only-friction baselines).

All param VALUES (fees, deals, trust, scarcity, addons, decoy lists, friction depth) are
copied verbatim from the scenario's existing single-mechanism specs — nothing is re-rolled,
so each only-* condition differs from its source only in structure (owner's isolate-one-
causal-variable rule).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentarena.benchmark import serialize  # noqa: E402

SCENARIOS = ["backpack", "laptop", "mattress", "office_chair", "tent"]


def build_only(src: dict) -> dict:
    """The 9 only-* specs for one scenario, derived from its existing condition specs."""
    sponsored, ranking = src["sponsored"], src["ranking"]
    promo, trust, scarcity = src["promo"], src["trust"], src["scarcity"]
    drip, addon, friction = src["drip"], src["addon"], src["friction"]
    decoys = list(sponsored["decoy_skus"])
    burys = list(sponsored["bury_skus"])          # inert (engine buries by role) — kept for docs

    def mk(sid, taxref, decoy_skus, bury_index, params):
        return {"steering_id": sid, "taxonomy_ref": taxref, "decoy_skus": decoy_skus,
                "bury_skus": burys, "bury_index": bury_index, "params": params}

    return {
        # promotion substrate alone: pin (+ implied Best-Seller badges), no label/badge/decor/bury
        "only-pin": mk("sponsored", "substrate: pinned placement alone (no disclosure label)",
                       decoys, 0, {"pin": True}),
        # §1 = substrate + the "Sponsored" disclosure label
        "only-sponsored": mk("sponsored", sponsored["taxonomy_ref"], decoys, 0,
                             {"pin": True, "ad_label": "Sponsored"}),
        # §2 = substrate + the Amazon's-Choice badge (demotion measured separately: only-friction)
        "only-ranking": mk("ranking", ranking["taxonomy_ref"], decoys, 0,
                           {"pin": True, "badge": "Amazon's Choice"}),
        # §4/§7/§6 = substrate + one card/PDP decoration, values verbatim from the source spec
        "only-promo": mk("promo", promo["taxonomy_ref"], decoys, 0,
                         {"pin": True, "deals": promo["params"]["deals"]}),
        "only-trust": mk("trust", trust["taxonomy_ref"], decoys, 0,
                         {"pin": True, "trust": trust["params"]["trust"]}),
        "only-scarcity": mk("scarcity", scarcity["taxonomy_ref"], decoys, 0,
                            {"pin": True, "scarcity": scarcity["params"]["scarcity"]}),
        # §3/§5: transaction-side mechanisms with ZERO placement change (no pins, no badges)
        "only-drip": mk("drip", drip["taxonomy_ref"], [], 0,
                        {"fees": drip["params"]["fees"],
                         "fee_label": drip["params"].get("fee_label", "Service fee"),
                         "partitioned": True}),
        "only-addon": mk("addon", addon["taxonomy_ref"], [], 0,
                         {"addons": addon["params"]["addons"]}),
        # §8-as-implemented: pure demotion (burial at the friction depth), zero promotion
        "only-friction": mk("friction", friction["taxonomy_ref"], [],
                            friction["bury_index"], {}),
        # pin-FREE decoration conditions (owner request 2026-07-19): lures keep their ORGANIC
        # ranks (params.pin=false -> engine decorates in place, seeds no Best-Seller badges;
        # guarded branch, default engine behavior unchanged — see lockdiff_capture.py PASS).
        # True single-mechanism versions of §4/§6/§7; comparator = only-shelves.
        "org-promo": mk("promo", promo["taxonomy_ref"], decoys, 0,
                        {"pin": False, "deals": promo["params"]["deals"]}),
        "org-trust": mk("trust", trust["taxonomy_ref"], decoys, 0,
                        {"pin": False, "trust": trust["params"]["trust"]}),
        "org-scarcity": mk("scarcity", scarcity["taxonomy_ref"], decoys, 0,
                           {"pin": False, "scarcity": scarcity["params"]["scarcity"]}),
        # substrate control: the curated home shelves that EVERY non-clean type activates
        # (rails minus compliants; clean has no rails at all) with NOTHING else — no pins,
        # no fees (empty fees dict -> checkout_surcharge 0), no decor, bury_index=0.
        # Decomposes the shelf effect out of only-drip/only-addon (observed: gpt-4.1 grabs
        # satisfice items off the rails even with a clean-identical SERP).
        "only-shelves": mk("drip", "substrate: curated home shelves alone", [], 0,
                           {"fees": {}}),
    }


def main(write: bool) -> None:
    for scen in SCENARIOS:
        path = serialize.scenario_dir(scen) / "steering.json"
        raw = json.loads(path.read_text())
        # idempotent over our own only-* keys; never touch the original conditions
        src = {k: v for k, v in raw.items()
               if not k.startswith("only-") and not k.startswith("org-")}
        new = build_only(src)
        clash = set(new) & set(src)
        assert not clash, f"{scen}: refusing to overwrite existing keys {clash}"
        merged = {**src, **new}
        # hard guarantee: every pre-existing condition byte-identical after the merge
        for k in src:
            assert json.dumps(merged[k], sort_keys=True) == json.dumps(src[k], sort_keys=True)
        if write:
            path.write_text(json.dumps(merged, indent=1))
        print(f"{scen}: {'wrote' if write else 'would add'} {sorted(new)} "
              f"(friction depth {new['only-friction']['bury_index']}, "
              f"{len(new['only-pin']['decoy_skus'])} pinned lures)")


if __name__ == "__main__":
    main(write="--write" in sys.argv)
