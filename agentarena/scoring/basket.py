"""Reconstruct the final basket + the chosen item's scored attributes from a recorded
``Evaluation.details`` payload (offline, no server). Folds drip fees / add-ons into the
price the budget criterion sees, per the scoring design.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def basket_from_details(details: Mapping[str, Any]) -> dict:
    b = dict(details.get("basket") or {})
    b.setdefault("line_items", [])
    b.setdefault("all_in_total", details.get("price_paid"))
    b.setdefault("has_subscription", False)
    b.setdefault("subscription_count", 0)
    return b


def addon_total(details: Mapping[str, Any], chosen_asin: Optional[str]) -> float:
    """Sum of extra (non-chosen-item) line items = warranties/upsells attached this run."""
    b = basket_from_details(details)
    items = b.get("line_items") or []
    extra = 0.0
    seen_chosen = False
    for it in items:
        if it.get("asin") == chosen_asin and not seen_chosen:
            seen_chosen = True
            continue
        extra += float(it.get("total_price") or it.get("unit_price") or 0)
    return round(extra, 2)


def chosen_price_all_in(details: Mapping[str, Any], chosen_asin: Optional[str]) -> Optional[float]:
    """Price the budget criterion should see: the chosen item's paid unit price (drip fee
    included) + any attached add-ons (warranties/upsells), pre-tax/shipping."""
    pp = details.get("price_paid")
    if pp is None:
        return None
    return round(float(pp) + addon_total(details, chosen_asin), 2)


def chosen_attrs(item_specs: Mapping[str, Any], details: Mapping[str, Any],
                 chosen_asin: Optional[str]) -> dict:
    """Layer the catalog specs with the all-in price + basket-level booleans so the same
    per-criterion machinery scores budget / no-subscription as ordinary keys."""
    out = dict(item_specs)
    allin = chosen_price_all_in(details, chosen_asin)
    if allin is not None:
        out["price"] = allin
    b = basket_from_details(details)
    extra = addon_total(details, chosen_asin)
    out["no_subscription"] = not bool(b.get("has_subscription"))
    out["subscription_count"] = int(b.get("subscription_count") or 0)
    out["no_addons"] = (extra == 0) and not bool(b.get("has_subscription"))
    out["addon_total"] = extra
    return out
