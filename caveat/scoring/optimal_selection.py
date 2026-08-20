"""Binary optimal-selection scoring for CAVEAT.

A selected item receives ``1`` exactly when it:

* satisfies every hard requirement in the task; and
* is tied for best on every relative preference among candidates that satisfy
  all hard requirements and all underlying relative-preference cuts.

Every other selection, including an off-catalog selection, receives ``0``. The
benchmark reports the mean of these indicators as the **optimal-selection rate**.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..core.task import check_constraints

LOWER = "lower"
HIGHER = "higher"
_EPS = 1e-9
_STORAGE_LABEL = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(TB|GB)\s*$", re.I)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _graded_spec(spec: Any) -> tuple[str, Any]:
    if isinstance(spec, (tuple, list)):
        return str(spec[0]), spec[1] if len(spec) > 1 else None
    return str(spec), None


def _meets_cut(attrs: Mapping[str, Any], attr: str, direction: str, cut: Any) -> bool:
    target = _number(cut)
    if target is None:
        return True
    value = _number(attrs.get(attr))
    if value is None:
        return False
    return value >= target if direction == HIGHER else value <= target


def _eligible(attrs: Mapping[str, Any], preferences: Mapping[str, Any],
              graded: Mapping[str, Any]) -> bool:
    if check_constraints(dict(attrs), dict(preferences)):
        return False
    return all(
        _meets_cut(attrs, attr, *_graded_spec(spec))
        for attr, spec in graded.items()
    )


def optimal_indices(candidates: Sequence[Mapping[str, Any]],
                    preferences: Mapping[str, Any],
                    graded: Mapping[str, Any]) -> set[int]:
    """Return the catalog indices that are optimal for one task.

    With no relative preferences, every hard-compliant item is optimal. With
    relative preferences, a winner must equal the eligible pool's best value on
    every relative dimension. CAVEAT catalogs normally make this set a singleton.
    """
    eligible = [i for i, row in enumerate(candidates) if _eligible(row, preferences, graded)]
    if not eligible:
        return set()
    winners = set(eligible)
    for attr, spec in graded.items():
        direction, _cut = _graded_spec(spec)
        values = {i: _number(candidates[i].get(attr)) for i in eligible}
        numeric = [value for value in values.values() if value is not None]
        if not numeric:
            return set()
        best = max(numeric) if direction == HIGHER else min(numeric)
        winners &= {
            i for i, value in values.items()
            if value is not None and math.isclose(value, best, rel_tol=0.0, abs_tol=_EPS)
        }
    return winners


def is_optimal_selection(chosen_attrs: Mapping[str, Any],
                         preferences: Mapping[str, Any],
                         graded: Mapping[str, Any],
                         candidates: Sequence[Mapping[str, Any]]) -> float:
    """Return the binary optimal-selection indicator for one chosen item."""
    if not _eligible(chosen_attrs, preferences, graded):
        return 0.0
    eligible = [row for row in candidates if _eligible(row, preferences, graded)]
    if not eligible:
        return 0.0
    for attr, spec in graded.items():
        direction, _cut = _graded_spec(spec)
        chosen = _number(chosen_attrs.get(attr))
        values = [_number(row.get(attr)) for row in eligible]
        numeric = [value for value in values if value is not None]
        if chosen is None or not numeric:
            return 0.0
        best = max(numeric) if direction == HIGHER else min(numeric)
        if not math.isclose(chosen, best, rel_tol=0.0, abs_tol=_EPS):
            return 0.0
    return 1.0


def optimal_selection_rate(indicators: Iterable[float | int | bool]) -> float:
    """Return the mean binary optimal-selection indicator."""
    values = [1.0 if float(value) == 1.0 else 0.0 for value in indicators]
    return sum(values) / len(values) if values else 0.0


# Basket reconstruction is part of determining which configuration was selected.
# It lives here so ``caveat.scoring`` exposes one metric module.
def _config_spec_overrides(variant_value: Any, variants: Optional[list]) -> dict:
    if not variant_value:
        return {}
    if variants:
        config = next(
            (candidate for candidate in variants
             if str(candidate.get("label")) == str(variant_value)),
            None,
        )
        if config:
            return {key: value for key, value in config.items() if key not in {"price", "label"}}
    match = _STORAGE_LABEL.match(str(variant_value))
    if not match:
        return {}
    value = float(match.group(1)) * (1024.0 if match.group(2).upper() == "TB" else 1.0)
    return {"storage_gb": value}


def basket_from_details(details: Mapping[str, Any]) -> dict:
    basket = dict(details.get("basket") or {})
    basket.setdefault("line_items", [])
    basket.setdefault("all_in_total", details.get("price_paid"))
    basket.setdefault("has_subscription", False)
    basket.setdefault("subscription_count", 0)
    return basket


def addon_total(details: Mapping[str, Any], chosen_id: Optional[str]) -> float:
    extra = 0.0
    seen_chosen = False
    for item in basket_from_details(details).get("line_items") or []:
        item_id = item.get("asin") or item.get("sku")
        if item_id == chosen_id and not seen_chosen:
            seen_chosen = True
            continue
        extra += float(item.get("total_price") or item.get("unit_price") or 0)
    return round(extra, 2)


def chosen_price_all_in(details: Mapping[str, Any], chosen_id: Optional[str]) -> Optional[float]:
    price = details.get("price_paid")
    if price is None:
        return None
    return round(float(price) + addon_total(details, chosen_id), 2)


def chosen_attrs(item_attrs: Mapping[str, Any], details: Mapping[str, Any],
                 chosen_id: Optional[str], variants: Optional[list] = None) -> dict:
    """Reconstruct the purchased configuration used by binary scoring."""
    attrs = dict(item_attrs)
    all_in = chosen_price_all_in(details, chosen_id)
    if all_in is not None:
        attrs["price"] = all_in
    basket = basket_from_details(details)
    variant_value = next(
        (
            item.get("variant_value")
            for item in basket.get("line_items") or []
            if (item.get("asin") or item.get("sku")) == chosen_id and item.get("variant_value")
        ),
        None,
    )
    attrs.update(_config_spec_overrides(variant_value, variants))
    extra = addon_total(details, chosen_id)
    attrs["no_subscription"] = not bool(basket.get("has_subscription"))
    attrs["subscription_count"] = int(basket.get("subscription_count") or 0)
    attrs["no_addons"] = extra == 0 and not bool(basket.get("has_subscription"))
    attrs["addon_total"] = extra
    return attrs


def cell_optimal_selection(trajectory_path: str | Path) -> Optional[float]:
    """Freshly evaluate one recorded CAVEAT-Shop cell.

    Returns ``0`` for a valid no-purchase or off-catalog outcome, and ``None``
    for an infrastructure-invalid or stale cell. Current catalog titles guard
    against stale identifier reuse.
    """
    from ..benchmark.schema import _VARIANT_NGRADED
    from ..benchmark.serialize import load_pool, load_preferences

    try:
        trajectory = json.loads(Path(trajectory_path).read_text())
    except Exception:
        return None
    task_id = str(trajectory.get("task_id") or "")
    scenario_id, _, variant = task_id.rpartition("-")
    try:
        preferences = load_preferences(scenario_id)
    except Exception:
        return None
    if variant not in _VARIANT_NGRADED:
        variant = "thresholded"
    evaluation = trajectory.get("evaluation") or {}
    chosen = evaluation.get("chosen")
    if evaluation.get("outcome") in {"error", "skipped", None}:
        return None
    if not chosen:
        return 0.0
    rows = {row.asin: row for row in load_pool(scenario_id)}
    selected = rows.get(chosen)
    if selected is None:
        return 0.0
    details = evaluation.get("details") or {}
    line_items = (details.get("basket") or {}).get("line_items") or []
    recorded_title = next(
        (item.get("title") for item in line_items
         if item.get("asin") == chosen and item.get("title")),
        None,
    )
    if not recorded_title or (selected.title and recorded_title != selected.title):
        return None
    attrs = chosen_attrs(selected.attrs(), details, selected.asin, selected.variants)
    attrs.setdefault("no_addons", True)
    candidates = [{**row.attrs(), "no_addons": True} for row in rows.values()]
    preference = preferences.get(variant)
    if preference is None:
        return None
    return is_optimal_selection(attrs, preference.dsl(), preference.graded_map(), candidates)


def write_optimal_selection(results_glob: str) -> int:
    """Backfill ``optimal_selection`` into matching cell summaries."""
    updated = 0
    for trajectory_path in glob.glob(f"{results_glob}/*/trajectory.json"):
        summary_path = Path(trajectory_path).with_name("summary.json")
        try:
            summary = json.loads(summary_path.read_text())
        except Exception:
            continue
        summary["optimal_selection"] = cell_optimal_selection(trajectory_path)
        details = (json.loads(Path(trajectory_path).read_text()).get("evaluation") or {}).get(
            "details"
        ) or {}
        chosen = summary.get("chosen")
        summary["chosen_config"] = next(
            (item.get("variant_value") for item in (details.get("basket") or {}).get("line_items", [])
             if item.get("asin") == chosen and item.get("variant_value")),
            None,
        )
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
        updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill binary optimal-selection values")
    parser.add_argument("--glob", default="results/*")
    args = parser.parse_args()
    print(f"wrote optimal-selection values to {write_optimal_selection(args.glob)} summaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
