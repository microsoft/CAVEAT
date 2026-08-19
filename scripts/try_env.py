#!/usr/bin/env python
"""Act-as-the-shopping-agent driver for the harvested-clone envs.

Spins up an env's FastAPI backend (no browser needed) and shops it through the generic
/api the way an agent's actions translate: list products (seeing the steered ordering),
open a detail, add to cart / checkout (or submit a lead/booking), then score via the
env's own evaluate(). Lets me validate catalog design + steering + scoring fast, and
make different "decisions" (buy the hero vs take the pinned decoy) to check both paths.

Usage:
  python scripts/try_env.py <env> [--condition clean|steered] [--pick hero|decoy|top|cheapest|sku:<SKU>]
  python scripts/try_env.py doordash --condition steered --pick top
"""
import argparse
import dataclasses
import importlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from caveat.core.environment import ENVIRONMENTS, http_get, http_json  # noqa: E402


def load_env(name):
    importlib.import_module(f"caveat.envs.{name}")          # registers + exposes TASKS
    mod = sys.modules[f"caveat.envs.{name}"]
    try:
        envcls = ENVIRONMENTS.get(name)
    except Exception:
        envcls = ENVIRONMENTS[name]
    return envcls(), getattr(mod, "TASKS", [])


def show_listing(base, env):
    """Print the top of the product list the agent would see (reveals steering)."""
    data = http_get(f"{base}/api/products?sort=relevance&limit=60")
    rows = data.get("products", [])
    cat = env._catalog_obj(None)
    print(f"\n  listing ({data.get('total', len(rows))} items) — top 12 as the agent sees them:")
    print(f"    {'#':>2} {'role':10} {'ad':3} {'price':>8} {'rating':>6}  title / badges")
    for i, c in enumerate(rows[:12]):
        badges = ",".join(c.get("badges") or [])
        ad = "AD" if c.get("advertised") else ""
        it = cat.by_sku(c.get("sku", ""))
        role = it.role if it else "?"
        print(f"    {i:>2} {role:10} {ad:3} "
              f"${c.get('price',0):>7.2f} {c.get('rating',0):>6.1f}  {c.get('title','')[:42]} {('['+badges+']') if badges else ''}")
    return rows


def pick_sku(rows, env, strategy):
    cat = env._catalog_obj(None)
    if strategy.startswith("sku:"):
        return strategy[4:]
    if strategy == "top":
        return rows[0]["sku"] if rows else None
    if strategy == "cheapest":
        return min(rows, key=lambda c: c["price"])["sku"] if rows else None
    if strategy == "decoy":
        ad = [c for c in rows if c.get("advertised")]
        return (ad or rows)[0]["sku"] if rows else None
    if strategy == "hero":
        comp = [c for c in rows if (cat.by_sku(c["sku"]) or None) and cat.by_sku(c["sku"]).role == "compliant"]
        return comp[0]["sku"] if comp else (rows[0]["sku"] if rows else None)
    return rows[0]["sku"] if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("env")
    ap.add_argument("--condition", default="clean", choices=["clean", "steered"])
    ap.add_argument("--pick", default="hero")
    ap.add_argument("--port", type=int, default=8731)
    args = ap.parse_args()

    env, tasks = load_env(args.env)
    if not tasks:
        print(f"!! {args.env}: no TASKS defined"); return 1
    task = dataclasses.replace(tasks[0], condition=args.condition)
    print(f"== {args.env} | condition={args.condition} | pick={args.pick}")
    print(f"   task: {task.instruction[:140]}")
    print(f"   preferences: {task.preferences}")

    from caveat.envs._storefront.scoring import oracle_pstar
    meta = getattr(task, "metadata", None) or {}
    work = Path(tempfile.mkdtemp(prefix=f"tryenv-{args.env}-"))
    handle = env.start(args.port, task, work_dir=work)
    try:
        if args.env == "airbnb":                                  # booking flow (no cart/checkout)
            cat = env._catalog_obj(None)
            rows = http_get(f"{handle.base_url}/api/listings?limit=60").get("listings", [])
            print(f"\n  listings ({len(rows)}) — top 10 as the agent sees them:")
            for i, r in enumerate(rows[:10]):
                l = cat.by_title(r.get("title", "")); role = l.role if l else "?"
                pool = "Pool" in (l.amenities if l else [])
                print(f"    {i:>2} {role:10} ${r.get('price_per_night',0):>5.0f}/nt BR={r.get('bedrooms')} "
                      f"pool={pool} {r.get('avg_rating')}★  {r.get('title','')[:34]}")
            adv = lambda r: bool((cat.by_title(r.get('title','')) or 0) and cat.by_title(r.get('title','')).advertised)
            comp = lambda r: bool((cat.by_title(r.get('title','')) or 0) and cat.by_title(r.get('title','')).role == "compliant")
            if args.pick == "hero": cand = [r for r in rows if comp(r)]
            elif args.pick in ("decoy", "top"): cand = [r for r in rows if adv(r)] or rows
            elif args.pick.startswith("title:"): cand = [r for r in rows if r.get("title") == args.pick[6:]]
            else: cand = rows
            sel = (cand or rows)[0]
            g = task.preferences.get("max_guests__min", 2)
            res = http_json(f"{handle.base_url}/api/bookings", method="POST",
                            body={"listing_id": sel.get("id"), "check_in": "2026-08-01",
                                  "check_out": "2026-08-04", "num_guests": g})
            print(f"\n  booked: '{sel.get('title')}' (id {sel.get('id')}) total={res.get('total_price')}")
            ev = env.evaluate(handle, task)
            orac = oracle_pstar([l.attrs() for l in cat.listings], task.preferences,
                                meta.get("graded", {}), variant=meta.get("variant", "graded"))
            print(f"\n  >>> OUTCOME: {ev.outcome}  P*={ev.details.get('preservation_strict')}  chosen={ev.chosen_label}")
            print(f"      validity: oracle P* = {orac}  (must be 1.0)")
            print(f"      details: {ev.details}")
            return 0
        rows = show_listing(handle.base_url, env)
        sku = pick_sku(rows, env, args.pick)
        if not sku:
            print("!! nothing to pick"); return 1
        detail = http_get(f"{handle.base_url}/api/products/{sku}")
        print(f"\n  opened: {sku}  '{detail.get('title')}'  ${detail.get('price')}  "
              f"specs={detail.get('specs')}  advertised={detail.get('advertised')}")
        if getattr(env, "transaction", "order") == "lead":
            res = http_json(f"{handle.base_url}/api/leads", method="POST", body={"sku": sku})
            print(f"  submitted lead -> {res.get('id') or res}")
        else:
            quote = http_get(f"{handle.base_url}/api/checkout/quote")
            res = http_json(f"{handle.base_url}/api/checkout", method="POST", body={"sku": sku})
            print(f"  checkout: subtotal {quote.get('subtotal')} + fees {quote.get('fees')} "
                  f"= total {res.get('total')}  (order {res.get('order_number')})")
        ev = env.evaluate(handle, task)
        from caveat.envs._storefront.scoring import oracle_pstar
        cat = env._catalog_obj(None)
        meta = getattr(task, "metadata", None) or {}
        orac = oracle_pstar([it.attrs() for it in cat.items], task.preferences,
                            meta.get("graded", {}), variant=meta.get("variant", "graded"))
        print(f"\n  >>> OUTCOME: {ev.outcome}  P*={ev.details.get('preservation_strict')}  "
              f"success={ev.success}  chosen={ev.chosen_label}")
        print(f"      validity: oracle P* = {orac}  (must be 1.0 — a faithful best choice exists)")
        print(f"      details: {ev.details}")
    finally:
        handle.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
