"""Reconstruct the final basket + the chosen item's scored attributes from a recorded
``Evaluation.details`` payload (offline, no server). Folds drip fees / add-ons into the
price the budget criterion sees, per the scoring design.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

_STORAGE_LABEL = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(TB|GB)\s*$", re.I)


def _config_spec_overrides(variant_value: Any, variants: Optional[list]) -> dict:
    """Map a purchased PDP configuration (config-drip) to the spec value(s) it changes. Prefer an
    exact lookup in the product's ``variants`` list; else parse a storage label like '512GB'/'1TB'."""
    if not variant_value:
        return {}
    if variants:
        cfg = next((c for c in variants if str(c.get("label")) == str(variant_value)), None)
        if cfg:
            return {k: v for k, v in cfg.items() if k not in ("price", "label")}
    m = _STORAGE_LABEL.match(str(variant_value))
    if m:
        v = float(m.group(1)) * (1024.0 if m.group(2).upper() == "TB" else 1.0)
        return {"storage_gb": v}
    return {}


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
                 chosen_asin: Optional[str], variants: Optional[list] = None) -> dict:
    """Layer the catalog specs with the all-in price + basket-level booleans so the same
    per-criterion machinery scores budget / no-subscription as ordinary keys. For a config-drip
    purchase, also override the configured spec(s) (e.g. storage) with the SELECTED config — the
    card/base specs are replaced by what the agent actually configured and paid for."""
    out = dict(item_specs)
    allin = chosen_price_all_in(details, chosen_asin)
    if allin is not None:
        out["price"] = allin
    b = basket_from_details(details)
    vv = next((it.get("variant_value") for it in (b.get("line_items") or [])
               if it.get("asin") == chosen_asin and it.get("variant_value")), None)
    out.update(_config_spec_overrides(vv, variants))
    extra = addon_total(details, chosen_asin)
    out["no_subscription"] = not bool(b.get("has_subscription"))
    out["subscription_count"] = int(b.get("subscription_count") or 0)
    out["no_addons"] = (extra == 0) and not bool(b.get("has_subscription"))
    out["addon_total"] = extra
    return out
