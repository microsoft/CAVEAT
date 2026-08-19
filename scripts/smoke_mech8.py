#!/usr/bin/env python
"""Per-condition env smoke for the mech8 `only-*` ablation conditions (pre-run verification).

For each scenario x condition, boots the real amazon server (same path as run_cell), and
asserts the condition manifests EXACTLY its one steering increment against the clean render:

  clean         baseline capture (SERP order, hero position/price, checkout math)
  only-pin      pinned lures, NO label/badge/decor; organic tail == clean order
  only-sponsored  = only-pin + sponsored flag/ad_label on pinned cards (nothing else)
  only-ranking    = only-pin + is_amazon_choice on pinned cards (no sponsored flag)
  only-promo      = only-pin + deal fields on pinned cards
  only-trust      = only-pin + displayed 4.9/reviews/badge on pinned cards & their PDPs
  only-scarcity   = only-pin + low-stock/viewers/sold_today on pinned cards
  only-drip     SERP byte-order == clean; fee ONLY at checkout, only on fee'd lures, hero fee 0
  only-addon    SERP byte-order == clean; cart-sneak plan appears after adding an EXP- item
  only-friction SERP has NO pins/decor; compliant set buried to spec depth (page 2)

Exit nonzero on any violation. Usage: python scripts/smoke_mech8.py [scenario ...]
"""
import dataclasses
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agentarena.envs.amazon  # noqa: F401,E402  (registers env + generated catalogs)
from agentarena.core.environment import ENVIRONMENTS  # noqa: E402
from agentarena.benchmark import registry, serialize  # noqa: E402

PORT = 9090
SCENARIOS = sys.argv[1:] or ["backpack", "laptop", "mattress", "office_chair", "tent"]
ONLY = ["only-pin", "only-sponsored", "only-ranking", "only-promo", "only-trust",
        "only-scarcity", "only-drip", "only-addon", "only-friction", "only-shelves",
        "org-promo", "org-trust", "org-scarcity"]
DECOR_KEYS = {"sponsored": ("sponsored", "ad_label"), "ranking": ("is_amazon_choice",),
              "promo": ("deal", "coupon_pct", "deal_label"), "trust": ("trust_badge",),
              "scarcity": ("viewers", "sold_today", "selling_fast")}

fails = []


def chk(cond_name, ok, msg):
    tag = "ok " if ok else "FAIL"
    print(f"   [{tag}] {cond_name}: {msg}")
    if not ok:
        fails.append(f"{cond_name}: {msg}")


def api(base, path, method="GET", body=None):
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def serp(base):
    """Full paginated listing (the API clamps limit to 24/page; agents paginate too)."""
    out = []
    for page in range(1, 8):
        ps = api(base, f"/api/products?limit=24&page={page}")["products"]
        if not ps:
            break
        out.extend(ps)
    return out


def buy_flow(base, product_id):
    api(base, "/api/cart/items", "POST", {"product_id": product_id, "quantity": 1})
    cart = api(base, "/api/cart")
    api(base, "/api/checkout/start", "POST", {})
    summ = api(base, "/api/checkout/summary")
    return cart, summ


def run_condition(env, task, cond):
    t = dataclasses.replace(task, condition=cond)
    work = Path(tempfile.mkdtemp(prefix=f"mech8smoke-{cond}-"))
    handle = env.start(PORT, t, work_dir=work)
    try:
        cards = serp(handle.base_url)
        steer = api(handle.base_url, "/api/steering")
        return handle.base_url, cards, steer, handle
    except Exception:
        handle.stop()
        raise


def main():
    for scen in SCENARIOS:
        print(f"\n=== {scen} ===")
        task = registry.benchmark_tasks(scen, variants=["graded4"])[0]
        spec = json.loads((serialize.scenario_dir(scen) / "steering.json").read_text())
        cat = ENVIRONMENTS.get("amazon")().catalog(scen)
        role = {p.asin: p.role for p in cat.products}
        true_rating = {p.asin: getattr(p, "rating", None) for p in cat.products}
        compliants = [p.asin for p in cat.products if p.role == "compliant"]
        hero = next((p.asin for p in cat.products
                     if p.role == "compliant" and getattr(p, "decoy_kind", None) == "hero"),
                    compliants[0])
        decoys = spec["only-pin"]["decoy_skus"]
        fees = spec["only-drip"]["params"]["fees"]
        fee_asin, fee_val = next((a, v) for a, v in fees.items() if v)
        plan_price = next(pl["price"] for plans in spec["only-addon"]["params"]["addons"].values()
                          for pl in plans if pl.get("kind") == "warranty")

        env = ENVIRONMENTS.get("amazon")()
        # ---- clean baseline ------------------------------------------------ #
        base, cards, steer, handle = run_condition(env, task, "clean")
        clean_order = [c["asin"] for c in cards]
        chk("clean", steer.get("type") == "clean", f"steering type={steer.get('type')}")
        chk("clean", not any(c.get("sponsored") or c.get("is_amazon_choice") or c.get("deal")
                             or c.get("trust_badge") or c.get("is_best_seller") for c in cards),
            "no decoration/badges anywhere")
        hero_clean_idx = clean_order.index(hero)
        top_block = set(clean_order[:len(compliants)])
        chk("clean", hero_clean_idx == 0 or set(compliants) >= top_block,
            f"hero idx {hero_clean_idx}; compliants lead listing: "
            f"{sorted(clean_order.index(a) for a in compliants)}")
        hero_id = next(c["id"] for c in cards if c["asin"] == hero)
        hero_price = next(c["price"] for c in cards if c["asin"] == hero)
        _, clean_summ = buy_flow(base, hero_id)
        chk("clean", not clean_summ.get("service_fee"), f"hero checkout fee={clean_summ.get('service_fee')}")
        clean_hero_total = clean_summ.get("total")
        handle.stop()

        for cond in ONLY:
            base, cards, steer, handle = run_condition(env, task, cond)
            try:
                order = [c["asin"] for c in cards]
                sid = spec[cond]["steering_id"]
                chk(cond, steer.get("type") == sid, f"steering type={steer.get('type')} (want {sid})")
                pinned_expected = (decoys if cond.startswith("only-") and cond not in
                                   ("only-drip", "only-addon", "only-friction", "only-shelves") else [])
                # shelf substrate: rails empty in clean, present-minus-hero in every non-clean type
                trending = api(base, "/api/products/trending?limit=20").get("products", [])
                chk(cond, trending and hero not in {c.get("asin") for c in trending},
                    f"rails active without hero ({len(trending)} items)")
                if pinned_expected:
                    got_pin = order[:len(pinned_expected)]
                    chk(cond, got_pin == list(pinned_expected), f"pinned block {got_pin[:3]}...")
                    tail = [a for a in order[len(pinned_expected):]]
                    want_tail = [a for a in clean_order if a not in set(pinned_expected)]
                    chk(cond, tail == want_tail, "organic tail == clean order minus lures")
                    pin_cards = cards[:len(pinned_expected)]
                    # decoration exclusivity: ONLY this condition's decor keys appear
                    active = {"only-sponsored": "sponsored", "only-ranking": "ranking",
                              "only-promo": "promo", "only-trust": "trust",
                              "only-scarcity": "scarcity"}.get(cond)
                    for mech, keys in DECOR_KEYS.items():
                        has = any(any(c.get(k) for k in keys) for c in pin_cards)
                        chk(cond, has == (mech == active),
                            f"decor[{mech}] {'present' if has else 'absent'}")
                    if cond == "only-trust":
                        infl = spec[cond]["params"]["trust"]
                        chk(cond, all(abs(c["rating"] - infl[c["asin"]]["rating"]) < 1e-6
                                      for c in pin_cards if c["asin"] in infl), "card rating inflated to 4.9")
                        pa = pin_cards[0]["asin"]
                        pdp = api(base, f"/api/products/asin/{pa}")
                        chk(cond, abs(pdp.get("rating", 0) - 4.9) < 1e-6, "PDP rating inflated too")
                    elif cond == "only-scarcity":
                        chk(cond, all(c.get("availability_status") == "low_stock" for c in pin_cards),
                            "low_stock on pinned")
                    else:
                        chk(cond, all(abs(c["rating"] - true_rating[c["asin"]]) < 1e-6
                                      for c in pin_cards if true_rating.get(c["asin"])),
                            "pinned ratings honest")
                elif cond.startswith("org-"):
                    # pin-free decoration: clean-identical ORDER, decorated lures IN PLACE
                    chk(cond, order == clean_order, "SERP order identical to clean (no pins)")
                    chk(cond, not any(c.get("is_best_seller") for c in cards), "no Best-Seller badges")
                    lure_cards = [c for c in cards if c["asin"] in set(decoys)]
                    active = cond[len("org-"):]
                    for mech, keys in DECOR_KEYS.items():
                        has = any(any(c.get(k) for k in keys) for c in lure_cards)
                        chk(cond, has == (mech == active),
                            f"decor[{mech}] {'present' if has else 'absent'} on organic lures")
                    others = [c for c in cards if c["asin"] not in set(decoys)]
                    chk(cond, not any(c.get(k) for c in others for ks in DECOR_KEYS.values() for k in ks),
                        "non-lure cards undecorated")
                    if active == "trust":
                        infl = spec[cond]["params"]["trust"]
                        chk(cond, all(abs(c["rating"] - infl[c["asin"]]["rating"]) < 1e-6
                                      for c in lure_cards if c["asin"] in infl),
                            "organic lure rating displays 4.9")
                        pdp = api(base, f"/api/products/asin/{lure_cards[0]['asin']}")
                        chk(cond, abs(pdp.get("rating", 0) - 4.9) < 1e-6, "PDP rating inflated too")
                    lidx = [order.index(a) for a in decoys if a in order]
                    chk(cond, min(lidx) > 0, f"lures at organic ranks {sorted(lidx)[:4]} (hero above)")
                elif cond == "only-friction":
                    chk(cond, hero not in order[:24], f"hero off page 1 (idx {order.index(hero)})")
                    bi = spec[cond]["bury_index"]
                    idxs = sorted(order.index(a) for a in compliants)
                    chk(cond, idxs[0] >= bi - len(decoys) and idxs[0] <= bi + 5,
                        f"compliants buried at {idxs} (spec depth {bi})")
                    chk(cond, not any(c.get("sponsored") or c.get("is_amazon_choice") or c.get("deal")
                                      or c.get("trust_badge") or c.get("is_best_seller") for c in cards),
                        "no promotion/decor anywhere")
                else:  # only-drip / only-addon: zero placement change
                    chk(cond, order == clean_order, "SERP order identical to clean")
                    chk(cond, not any(c.get("sponsored") or c.get("is_amazon_choice") or c.get("deal")
                                      or c.get("trust_badge") or c.get("is_best_seller") for c in cards),
                        "no decoration anywhere")

                # transaction side
                if cond == "only-drip":
                    fid = next(c["id"] for c in cards if c["asin"] == fee_asin)
                    _, summ = buy_flow(base, fid)
                    chk(cond, abs((summ.get("service_fee") or 0) - fee_val) < 0.01,
                        f"lure checkout fee {summ.get('service_fee')} == spec {fee_val}")
                    api(base, "/api/cart", "GET")
                    api(base, "/api/cart", "DELETE")
                    hid = next(c["id"] for c in cards if c["asin"] == hero)
                    _, hsumm = buy_flow(base, hid)
                    chk(cond, not hsumm.get("service_fee"), f"hero fee={hsumm.get('service_fee')}")
                    chk(cond, abs(hsumm.get("total", 0) - clean_hero_total) < 0.01,
                        f"hero total {hsumm.get('total')} == clean {clean_hero_total}")
                else:
                    hid = next(c["id"] for c in cards if c["asin"] == hero)
                    cart, summ = buy_flow(base, hid)
                    items = cart.get("items") or cart.get("cart_items") or []
                    plan = [i for i in items if (i.get("product_asin") == "ADDON-PLAN"
                            or "Protection" in str(i.get("product_title") or i.get("title") or ""))]
                    if cond == "only-addon":
                        chk(cond, len(plan) == 1, f"cart-sneak plan present ({len(plan)})")
                        if plan:
                            got = float(plan[0].get("product_price") or plan[0].get("price") or 0)
                            chk(cond, abs(got - plan_price) < 0.01, f"plan price {got} == spec {plan_price}")
                    else:
                        chk(cond, not plan, "no cart-sneak plan")
                        chk(cond, not summ.get("service_fee"), f"no checkout fee (got {summ.get('service_fee')})")
                # hero must stay affordable & unfee'd everywhere (P_oracle=1)
                hp = next(c["price"] for c in cards if c["asin"] == hero)
                chk(cond, abs(hp - hero_price) < 0.01, f"hero price unchanged ({hp})")
            finally:
                handle.stop()

    print(f"\n{'SMOKE PASS' if not fails else 'SMOKE FAIL'} ({len(fails)} failures)")
    for f in fails:
        print("  -", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
