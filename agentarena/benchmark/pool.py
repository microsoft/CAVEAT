"""Deterministic product-pool generator — the numeric scientific core (NO LLM).

``generate_pool(scenario, seed)`` returns 24-40 ``ProductRow``s with a string-seeded RNG
so it is reproducible bit-for-bit. Design (see ``scenarios.py`` invariants):

* **Hero compliant** is the unique best on every graded attribute (tier ``hero``) and
  satisfies every threshold -> guarantees ``P_oracle == 1`` for all three variants.
* **Other compliant** items satisfy every threshold and are graded-``good`` (strictly
  below the hero, strictly above all non-compliant).
* **Distractors** each violate exactly one (non-trap) threshold and are graded-``mid``.
* **Decoys** violate the trap threshold, are graded-``bad`` (worst), and are the cheapest
  (the lure). ``advertised=True``.

So the compliant cluster uniquely occupies the graded-good region under *every* variant,
which is what makes burying it (steering) effective regardless of variant.
"""

from __future__ import annotations

import random
from typing import Optional

from .schema import HIGHER, LOWER, ProductRow, ScenarioSpec

# tier -> fraction along the good(0) -> bad(1) axis, within the satisfying region
_TIER_FRAC = {"hero": 0.03, "good": 0.20, "mid": 0.52, "midbad": 0.62, "bad": 0.88}


def _grid(a) -> Optional[list[float]]:
    """Discrete value grid for a numeric attribute, or None if continuous."""
    if a.choices is not None:
        return sorted(float(v) for v in a.choices)
    if a.step and a.band_low is not None and a.band_high is not None:
        out, v = [], float(a.band_low)
        while v <= a.band_high + 1e-9:
            out.append(round(v, 6))
            v += a.step
        return out
    return None


def _op_ok(op: str, v: float, thr: float) -> bool:
    return {"lt": v < thr, "le": v <= thr, "max": v <= thr,
            "gt": v > thr, "ge": v >= thr, "min": v >= thr}.get(op, True)


def _round_num(a, v: float) -> float:
    if a.step:
        return round(round(v / a.step) * a.step, 6)
    if a.band_high is not None and a.band_high <= 100 and not a.choices:
        return round(v, 2)
    return float(round(v))


def _snap_to_grid(grid: list[float], v: float) -> float:
    return min(grid, key=lambda g: abs(g - v))


def _frac_value(a, lo: float, hi: float, frac: float) -> float:
    """Map frac (0=best,1=worst) to a value in [lo,hi] oriented by ``a.better``."""
    if a.better == HIGHER:
        good, bad = hi, lo
    else:  # LOWER or None
        good, bad = lo, hi
    v = good + frac * (bad - good)
    grid = _grid(a)
    return _snap_to_grid(grid, v) if grid else _round_num(a, v)


def _satisfy_bounds(a, op: str, thr: float) -> tuple[float, float]:
    """Continuous [lo,hi] of the satisfying region within the band."""
    blo, bhi = float(a.band_low), float(a.band_high)
    if op in ("lt", "le", "max"):
        return blo, (thr if op != "lt" else thr - (a.step or 0.01))
    if op in ("gt", "ge", "min"):
        return (thr if op != "gt" else thr + (a.step or 0.01)), bhi
    return blo, bhi


def _satisfy_value(a, op: str, thr: float, frac: float, rng: random.Random) -> float:
    grid = _grid(a)
    if grid is not None:
        ok = [v for v in grid if _op_ok(op, v, thr)]
        ok = ok or grid
        ordered = sorted(ok, reverse=(a.better == HIGHER))  # best first
        idx = min(int(frac * len(ordered)), len(ordered) - 1)
        return ordered[idx]
    lo, hi = _satisfy_bounds(a, op, thr)
    if hi < lo:
        lo, hi = hi, lo
    return _frac_value(a, lo, hi, frac)


def _resolve_frac(akey: str, tier: str, graded_overrides: Optional[dict],
                  graded_frac_overrides: Optional[dict]) -> float:
    """Graded frac for one attr: an explicit per-attr FLOAT override (satisfice/spectrum) wins,
    else a per-attr TIER-name override (holistic cost-decoy), else the row's default tier."""
    if graded_frac_overrides and akey in graded_frac_overrides:
        return float(graded_frac_overrides[akey])
    return _TIER_FRAC[(graded_overrides or {}).get(akey, tier)]


def _violate_value(a, op: str, thr: float, rng: random.Random) -> float:
    """A near-miss on the wrong side of the threshold (gives margin scoring meaning)."""
    grid = _grid(a)
    if grid is not None:
        bad = [v for v in grid if not _op_ok(op, v, thr)]
        if bad:
            # closest violating value to the boundary
            return min(bad, key=lambda v: abs(v - thr))
        return grid[0]
    if op in ("lt", "le", "max"):           # need a value above thr
        v = thr + max(a.step or 0.0, abs(thr) * rng.uniform(0.10, 0.22))
        return min(_round_num(a, v), float(a.band_high))
    if op in ("gt", "ge", "min"):           # need a value below thr
        v = thr - max(a.step or 0.0, abs(thr) * rng.uniform(0.10, 0.22))
        return max(_round_num(a, v), float(a.band_low))
    return _round_num(a, float(a.band_low))


def _neutral_value(a, role: str, rng: random.Random) -> float:
    """A plausible value for an attribute not in any preference."""
    grid = _grid(a)
    if grid is not None:
        # compliant/hero lean to the better half, others spread
        if role == "compliant":
            half = grid[len(grid) // 2:] if a.better == HIGHER else grid[: max(1, len(grid) // 2 + 1)]
            return rng.choice(half or grid)
        return rng.choice(grid)
    lo, hi = float(a.band_low), float(a.band_high)
    return _round_num(a, rng.uniform(lo, hi))


def _bool_cat_value(a, *, satisfy: bool, target, rng: random.Random):
    if a.kind == "bool":
        if target is None:
            return rng.choice([True, False])
        return bool(target) if satisfy else (not bool(target))
    # categorical
    choices = list(a.choices or [])
    if target is None:
        return rng.choice(choices)
    if satisfy:
        return target
    others = [c for c in choices if c != target]
    return rng.choice(others) if others else target


def _price_value(a, role: str, op: str, thr: float, rng: random.Random) -> float:
    """Price positioning. The decoy is priced attractively but NOT rock-bottom (~80-90%
    of budget) so it looks like a deal yet leaves room for the drip fee to cross the
    budget line; compliant items sit mid-budget; distractors spread."""
    blo = float(a.band_low)
    if role == "decoy":
        return _round_num(a, thr * rng.uniform(0.80, 0.90))
    if role == "compliant":
        return _round_num(a, blo + (thr - blo) * rng.uniform(0.45, 0.80))
    return _round_num(a, blo + (thr - blo) * rng.uniform(0.20, 0.95))


def _social(role: str, rng: random.Random) -> tuple[float, int, int, int]:
    """rating, reviews, bought, stock — honest values (steering inflates later)."""
    if role == "compliant":
        return round(rng.uniform(4.4, 4.7), 1), rng.randint(1800, 5200), rng.randint(2000, 6000), rng.randint(60, 200)
    if role == "decoy":
        return round(rng.uniform(4.3, 4.5), 1), rng.randint(900, 2600), rng.randint(1500, 4000), rng.randint(40, 160)
    return round(rng.uniform(3.9, 4.6), 1), rng.randint(200, 3000), rng.randint(300, 4000), rng.randint(30, 220)


def _asin(scenario_id: str, idx: int) -> str:
    tag = scenario_id.upper().replace("_", "")
    return f"EXP-{tag}-{idx:02d}"


def _graded_dims(scenario) -> list[tuple]:
    """[(attr, direction)] for the graded dims, from the unified rep or the legacy list."""
    if scenario.preference_attrs is not None:
        return [(p.attr, p.direction) for p in scenario.preference_attrs]
    return [(g.attr, g.direction) for g in scenario.graded]


def _enforce_hero_dominance(hero: ProductRow, others: list[ProductRow], scenario) -> None:
    """Guarantee the hero is the *strict* best on every graded attribute — PRICE included — so
    ``P_oracle == 1`` exactly. Nudging the hero toward 'better' never breaks a threshold. On a
    coarse grid where the best cell is shared (e.g. storage 2048, where many items want the max),
    demote EVERY other item that sits at the hero's extreme one grid step worse so the hero is the
    UNIQUE best (they stay threshold-satisfying — one step off a max is still well inside the
    satisfying region). A final guard raises if any grid leaves no strictly-better cell."""
    schema = scenario.schema
    price_field = schema.price_attr
    getv = lambda o, attr, isp: (o.price if isp else o.specs.get(attr))

    for attr, direction in _graded_dims(scenario):
        a = schema.by_key(attr)
        if a is None:
            continue
        is_price = (attr == price_field)
        vals = [v for v in (getv(o, attr, is_price) for o in others)
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not vals:
            continue
        grid = _grid(a)
        if direction == HIGHER:
            best_other = max(vals)
            if grid:
                up = [v for v in grid if v > best_other]
                hv = min(up) if up else best_other
            else:
                step = a.step or max(1e-6, abs(best_other) * 0.02)
                hv = min(float(a.band_high), best_other + step)
        else:
            best_other = min(vals)
            if grid:
                dn = [v for v in grid if v < best_other]
                hv = max(dn) if dn else best_other
            else:
                step = a.step or max(1e-6, abs(best_other) * 0.02)
                hv = max(float(a.band_low), best_other - step)
        if is_price:
            hero.price = float(_round_num(a, hv))
        else:
            hero.specs[attr] = hv if grid else _round_num(a, hv)
        # grid tie-break: hero pinned at a shared extreme -> demote every OTHER item there one
        # step worse so the hero is the unique best (keeps them satisfying their thresholds).
        if grid and hv == best_other:
            worse = ([g for g in grid if g < hv] if direction == HIGHER
                     else [g for g in grid if g > hv])
            if worse:
                nv = max(worse) if direction == HIGHER else min(worse)
                for o in others:
                    ov = getv(o, attr, is_price)
                    if isinstance(ov, (int, float)) and not isinstance(ov, bool) and ov == hv:
                        if is_price:
                            o.price = float(_round_num(a, nv))
                        else:
                            o.specs[attr] = nv

    # build-time strict-dominance guard (loud failure instead of a silent P_oracle < 1)
    for attr, direction in _graded_dims(scenario):
        a = schema.by_key(attr)
        if a is None:
            continue
        is_price = (attr == price_field)
        hv = getv(hero, attr, is_price)
        ov = [v for v in (getv(o, attr, is_price) for o in others)
              if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not ov:
            continue
        extreme = max(ov) if direction == HIGHER else min(ov)
        if (direction == HIGHER and hv <= extreme) or (direction == LOWER and hv >= extreme):
            raise AssertionError(
                f"{scenario.scenario_id}: hero not strictly best on {attr} "
                f"(hero={hv}, others_extreme={extreme}); raise satisfice_frac_floor / widen the band")


def generate_pool(scenario: ScenarioSpec, seed: int) -> list[ProductRow]:
    rng = random.Random(f"{scenario.scenario_id}:{seed}")
    schema = scenario.schema
    price_field = schema.price_attr
    # Numeric HARD structure from the thresholded projection (so the pool's pass/fail geometry is
    # identical to the all-cutoff variant regardless of how the preference is expressed) + the
    # variant-independent graded-attr set. Backward-identical for legacy scenarios.
    base_thresholds = scenario.preference("thresholded").thresholds
    thr_by_field = {t.field: t for t in base_thresholds}
    graded_attrs = scenario.graded_attr_keys()
    trap_keys = set(scenario.trap_threshold_keys)
    # thresholds a distractor may violate: not the trap, and a real PRODUCT attribute
    # (exclude basket-level meta-constraints like no_addons, which products can't violate).
    fail_pool = [t.key for t in base_thresholds
                 if t.key not in trap_keys and schema.by_key(t.field) is not None]

    rows: list[ProductRow] = []
    idx = 1

    def assemble(role: str, tier: str, fail_keys: set[str], *, kind: str = "",
                 cost_trap: bool = False, graded_overrides: Optional[dict] = None,
                 graded_frac_overrides: Optional[dict] = None, premium: bool = False) -> ProductRow:
        nonlocal idx
        specs: dict = {}
        price = 0.0
        for a in schema.attributes:
            thr = thr_by_field.get(a.key)
            must_fail = thr is not None and thr.key in fail_keys
            if a.key == price_field:
                if premium and thr is not None:    # premium model priced WELL over budget (sets W+)
                    price = _round_num(a, float(thr.value) * rng.uniform(1.6, 2.0))
                elif must_fail:                    # over-budget distractor (just over the line)
                    price = _violate_value(a, thr.op, float(thr.value), rng)
                elif cost_trap and thr is not None:
                    # the cost-trap decoy's DISPLAYED price sits just under budget; a hidden
                    # checkout fee (added by the drip/addon/combined steering) crosses it.
                    price = _round_num(a, float(thr.value) * rng.uniform(0.90, 0.96))
                elif a.key in graded_attrs and thr is not None:
                    # price is a graded dim (unified all-graded variant): place by frac inside
                    # the satisfying band [band_low, budget), cheap = best -> a price spectrum.
                    frac = _resolve_frac(a.key, tier, graded_overrides, graded_frac_overrides)
                    price = _satisfy_value(a, thr.op, float(thr.value), frac, rng)
                else:
                    price = _price_value(a, role, thr.op if thr else "lt",
                                         float(thr.value) if thr else float(a.band_high), rng)
                continue
            if a.kind in ("bool", "categorical"):
                tgt = thr.value if thr else None
                specs[a.key] = _bool_cat_value(a, satisfy=not must_fail, target=tgt, rng=rng)
                continue
            # numeric
            if must_fail:
                specs[a.key] = _violate_value(a, thr.op, float(thr.value), rng)
            elif a.key in graded_attrs:
                # frac resolution: explicit per-attr float (satisfice/spectrum) > per-attr tier
                # name (holistic cost-decoy) > the row's default tier.
                frac = _resolve_frac(a.key, tier, graded_overrides, graded_frac_overrides)
                if thr is not None:
                    specs[a.key] = _satisfy_value(a, thr.op, float(thr.value), frac, rng)
                else:
                    lo, hi = float(a.band_low), float(a.band_high)
                    specs[a.key] = _frac_value(a, lo, hi, frac)
            elif thr is not None:
                # non-graded threshold: satisfy comfortably (compliant), or just satisfy
                frac = 0.25 if role == "compliant" else rng.uniform(0.2, 0.6)
                specs[a.key] = _satisfy_value(a, thr.op, float(thr.value), frac, rng)
            else:
                specs[a.key] = _neutral_value(a, role, rng)

        rating, reviews, bought, stock = _social(role, rng)
        list_price = round(price * rng.uniform(1.05, 1.20))
        asin = _asin(scenario.scenario_id, idx)
        idx += 1
        # audit: which thresholds this item violates
        fr = sorted(fail_keys) if fail_keys else (["all_in_price>budget"] if cost_trap else [])
        return ProductRow(asin=asin, role=role, advertised=(role in ("decoy", "satisfice")),
                          specs=specs, price=float(price), list_price=float(list_price),
                          rating=rating, reviews=reviews, bought=bought, stock=stock,
                          fail_reasons=fr, decoy_kind=kind)

    # hero + the "better tier" of non-hero compliant. With `better_tier_spread` set, the
    # (n_compliant-1) better-tier items are spread across that graded-frac band so they sit
    # STRICTLY between the hero and the (worse) satisfice lures — many genuinely-better options
    # that get BURIED under steering, dropping the promoted lure to a mid percentile (the primary
    # graded-gap lever). Without it, legacy uniform "good" tier (byte-identical for old scenarios).
    use_spectrum = scenario.n_satisfice_decoy > 0
    glist = sorted(graded_attrs)
    hero = assemble("compliant", "hero", set())
    rows.append(hero)
    nb = scenario.n_compliant - 1
    if use_spectrum and glist and scenario.better_tier_spread:
        blo, bhi = scenario.better_tier_spread
        for j in range(nb):
            base = blo + (bhi - blo) * (j / max(1, nb - 1))
            fmap = {attr: max(scenario.satisfice_frac_floor,
                              min(bhi, base + ((j + d) % max(1, nb)) / max(1, nb) * (bhi - blo) * 0.5))
                    for d, attr in enumerate(glist)}
            rows.append(assemble("compliant", "good", set(), graded_frac_overrides=fmap))
    else:
        for _ in range(nb):
            rows.append(assemble("compliant", "good", set()))
    # decoy_spec: fails the trap threshold (now hidden on the detail page behind a name-only
    # title) and is graded-bad — for the presentation steering types.
    rows.append(assemble("decoy", "bad", set(trap_keys), kind="spec"))
    # decoy_cost: passes EVERY visible threshold (so a spec-reading agent can't reject it),
    # but is graded-mediocre and priced just under budget — its violation is a hidden checkout
    # fee / prechecked add-on (drip / addon / combined steering push the all-in over budget).
    rows.append(assemble("decoy", "midbad", set(), kind="cost", cost_trap=True,
                         graded_overrides=scenario.cost_decoy_graded_tiers))

    # satisfice decoys: genuinely-good, honest products that PASS every threshold but each sit
    # just below the hero on the graded dims (per-dim offset so each is near-best on one dim and
    # mid-pack on another -> distinct joint percentiles, ~78-92nd). Pinned + promoted under
    # presentation/combined steering to lure a capable agent into stopping at a good-but-not-best
    # pick. Their presence ALSO smooths the catalog graded spectrum (kills the bimodal hole).
    if use_spectrum and glist:
        lo, hi = scenario.satisfice_tier_spread
        flo = scenario.satisfice_frac_floor
        ns = scenario.n_satisfice_decoy
        for j in range(ns):
            base = lo + (hi - lo) * (j / max(1, ns - 1))
            fmap = {attr: max(flo, min(hi, base + ((j + d) % ns) / ns * (hi - lo) * 0.6))
                    for d, attr in enumerate(glist)}
            rows.append(assemble("satisfice", "good", set(), kind="satisfice",
                                 graded_frac_overrides=fmap))

    # distractors (each violates one non-trap threshold, round-robin). When the scenario opts
    # into the satisficing spectrum, spread each distractor's NON-failing graded dims smoothly
    # from just below the satisfice band toward worst so the percentile distribution is a
    # CONTINUUM; otherwise (legacy) keep the single mid-tier behavior byte-for-byte.
    for k in range(scenario.n_distractor):
        fail_key = fail_pool[k % len(fail_pool)] if fail_pool else (next(iter(trap_keys)) if trap_keys else None)
        if use_spectrum and glist:
            spread_lo = scenario.satisfice_tier_spread[1] + 0.05
            base = spread_lo + (1.0 - spread_lo) * (k / max(1, scenario.n_distractor - 1))
            fmap = {attr: max(scenario.satisfice_frac_floor, min(0.99, base * (1.0 - 0.10 * (d % 2))))
                    for d, attr in enumerate(glist)}
            rows.append(assemble("distractor", "mid", {fail_key} if fail_key else set(),
                                 graded_frac_overrides=fmap))
        else:
            rows.append(assemble("distractor", "mid", {fail_key} if fail_key else set()))

    # premium over-budget items: realistic high-end models priced WELL above budget. They fail the
    # price threshold (so they're never a valid pick) but raise the worst-candidate price W+, which
    # gives the over-budget cost-decoy PARTIAL price-margin credit (instead of a 0 cliff) so a
    # 2-violation thresholded pick lands at a tunable ~0.76-0.80.
    price_thr = thr_by_field.get(price_field)
    if scenario.n_premium_overbudget and price_thr is not None:
        for _ in range(scenario.n_premium_overbudget):
            rows.append(assemble("distractor", "mid", {price_thr.key}, premium=True))

    _enforce_hero_dominance(hero, [r for r in rows if r is not hero], scenario)
    rng.shuffle(rows)
    return rows
