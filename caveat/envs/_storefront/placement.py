"""Shared SERVING-LAYER arithmetic for the HARD storefront tier (env-agnostic, pure).

Two things live here, both of which the server and the validator must agree on exactly:

  1. **Placement** — WHERE the compliant rows are spliced into the ranked result set
     (:func:`compliant_offsets` / :func:`plan`).
  2. **Serving order and filter surface** — WHICH compliant row takes which of those slots
     (:func:`settle_key` / :func:`order_compliant`) and WHICH left-rail filter values the API
     honours (:func:`rating_chips` / :func:`clamp_rating`).

The hard scenarios bury the compliant rows at a *proportional* depth with seeded jitter,
scattered rather than contiguous, with a guaranteed junk tail — instead of the legacy
"insert the whole compliant block at ``bury_index``" rule, which turns any filter into a
hero-locator (once a filter shrinks the result set below ``bury_index`` the block lands at
the very END of the list).

This module is the SINGLE copy of that arithmetic. The CAVEAT-Shop backend loads it BY FILE PATH
(the same trick ``backend/app.py`` uses for ``gate.py``, so a standalone server never needs
the ``caveat`` package importable); ``benchmark/validate.py`` and
``scripts/enumerate_oracle.py`` import it normally. Server and validator therefore cannot
drift: the predicted rank and the served rank are computed by the same function.

It deliberately imports NOTHING but the stdlib and has no side effects.

Public API (the cross-stream contract)
--------------------------------------
``PAGE_SIZE``
    24 — the storefront's rows-per-page.
``h64(*parts) -> int``
    Process-stable 64-bit hash (blake2b). NOT :func:`hash`, which is salted per process.
``compliant_offsets(n_pinned, n_rest, n_comp, *, pages, key, cfg, hero_index=None)``
    ``-> (offsets, hero_slot)``.
``plan(...) -> dict``
    the same computation with its working shown (gaps/span/room/degraded) — what the
    validator's H11 and the server-side tests introspect instead of re-deriving.
``settle_key(entry, cfg) -> tuple`` / ``order_compliant(entries, ...) -> [asin, ...]``
    the WORST-SETTLE-FIRST serving order of the compliant block (see G10 below).
``RATING_CHIPS`` / ``rating_chips(cfg)`` / ``clamp_rating(value, cfg)``
    the left rail's rating affordance, and the server-side clamp onto it (G11).

``offsets`` is an ASCENDING (non-decreasing) list of insert positions **within the `rest`
list** (the already-ranked non-pinned, non-compliant rows). Splicing compliant *k* in at
``offsets[k]``, in order, puts it at global rank::

    rank(k) == n_pinned + offsets[k] + k

``hero_slot`` is the index into ``offsets`` the HERO must occupy; the other compliant rows
fill the remaining slots in their own order.

Guarantees (all asserted by the ``__main__`` sweep below)
--------------------------------------------------------
G1  ``offsets`` has length ``n_comp``, is non-decreasing and lies in ``[0, n_rest]`` — so
    the splice is always well-formed and the compliant ranks are strictly increasing.
G2  every rank is ``< wall`` where ``wall = min(PAGE_SIZE * pages, N)`` and
    ``N = n_pinned + n_rest + n_comp`` — nothing is placed past the reachable page wall.
G3  no compliant sits at global rank 0, whenever ``wall > n_comp`` (below that the window
    is literally too small to hold the compliant rows and everything collapses to the top;
    the validator's R1/R2 checks are what catch such a catalog).
G4  at least ``min(tail_reserve, n_rest // 3)`` junk rows follow the LAST compliant — the
    full ``tail_reserve`` on a real result set, degrading proportionally on small ones, so
    "jump to the last page" never lands on a compliant row.
G5  the hero is INTERIOR: ``0 < hero_slot < n_comp - 1`` whenever ``n_comp >= 3`` (so it is
    neither the shallowest nor the deepest compliant, and finding one compliant row tells
    an agent nothing about which side the hero is on). For ``n_comp <= 2`` interiority is
    arithmetically impossible and the hero takes the DEEPER slot.
G6  ``min_rank`` is honoured when the window allows it, degrading proportionally otherwise.
G7  the result is a pure function of the arguments and is stable across processes.
G8  **page separation** — consecutive compliant ranks are at least ``page_gap`` PAGES apart
    (default 2, i.e. >= 48 rows), so no 2-page window of the served list can contain two
    compliant rows and "read two middle pages" is not a strategy. With ``n_comp >= 3`` the
    compliant block therefore spans ``>= 2*page_gap`` pages. Degrades proportionally (and
    only) when the feasible band is too small to hold that span, which :func:`plan` reports
    as ``degraded``.
G9  **composition stability** — nothing in the seeded part of the formula is a function of
    ``n_rest``: the jitter seed is ``(salt, key, ...)`` only, and the depth bucket is coarse
    (``n_rest // (page_gap*PAGE_SIZE)``). Adding or removing ONE row from the result set can
    therefore never re-roll the placement, which is what let a stray off-catalog row (the
    seeder's ``ADDON-PLAN``) move the served hero by 13 ranks against the prediction.
G10 **worst settle first** (:func:`order_compliant`) — the compliant block is served in
    ASCENDING settle quality, so slot 0 holds the weakest compliant row and the best
    non-hero settle row lands in the last slot, i.e. AFTER the hero. Placement decides the
    ranks; this decides who occupies them, and without it the block simply inherited the
    caller's SQL sort (``rating DESC``) — which put the best non-hero alternative in the
    shallowest compliant slot on all five hard scenarios. Because the hero is spliced at
    ``hero_slot`` and
    G5 keeps ``hero_slot <= n_comp - 2``, the best settle row is *always* deeper than the hero.
G11 **filter affordance** (:func:`clamp_rating`) — the served rating filter is snapped down to
    the chip grid the left rail actually offers (``{1,2,3,4}`` stars, "X & up"). The SPA reads
    ``min_rating`` straight off the URL and the API used to accept any float, so
    ``min_rating=4.8`` (the hero's own rating) was a filter no chip can produce and it cut the
    card-plausible set from ~260 rows to 68-94 with the hero at 32-58.

Depth invariance under filtering is a corollary of the ``hero_frac`` rule: the hero's offset
is ``round(hero_frac_eff * n_rest)``, so its *relative* depth is ~``hero_frac`` for every
filtered subset — filtering yields no usable positional information. ``frac_amp`` then tilts
that fraction by a seeded amount per COARSE kept-set-size bucket, so the depth is not one
constant an agent can measure once on a broad query and re-apply to a narrow one.

``cfg`` (the ``serving.placement`` object; every key optional, defaults == inert)
--------------------------------------------------------------------------------
=============  =======  ====================================================================
key            default  meaning
=============  =======  ====================================================================
mode           "propor  the only implemented mode; the CALLER gates on it (an unknown mode
               tional"  must fall back to the legacy block insert, not to this module)
conditions     -        steering types the placement applies to (read by the caller, and by
                        :func:`canonical_key` when a catalog steers more than one)
hero_asin      -        the hero's ASIN (also the canonical ``key``, see :func:`canonical_key`)
hero_frac      0.5      hero depth as a fraction of ``n_rest``
frac_amp       0.06     +/- tilt on ``hero_frac``, seeded per kept-set-size BUCKET
jitter         0        +/- seeded jitter (rows) on the hero's offset (never seeded on n_rest)
spread         0        MINIMUM gap in rows between consecutive compliant offsets; raised to
                        ``page_gap * PAGE_SIZE`` whichever way it is set (G8)
page_gap       2        page separation floor between consecutive compliant rows
gap_jitter     spread   0..N extra rows added to each gap (never subtracted, so G8 holds)
               _eff//6
min_rank       1        shallowest allowed global rank for the first compliant
tail_reserve   0        junk rows required after the last compliant
salt           ""       per-catalog hash salt, e.g. "laptop_hard/v1"
settle_order   -        OPTIONAL explicit worst-first asin order for the compliant block; when
                        absent the order is derived from the rows themselves (:func:`settle_key`)
=============  =======  ====================================================================

``serving.filters`` (every key optional; absent => the module defaults, which ARE the SPA's)
--------------------------------------------------------------------------------------------
=============  ==============  =============================================================
key            default         meaning
=============  ==============  =============================================================
rating_chips   (1, 2, 3, 4)    the "X stars & up" values the left rail can produce; a served
                               ``min_rating`` is snapped DOWN onto this grid (G11)
=============  ==============  =============================================================
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Mapping, Optional, Sequence

__all__ = [
    "PAGE_SIZE",
    "DEFAULTS",
    "RATING_CHIPS",
    "h64",
    "canonical_key",
    "band",
    "gap_rows",
    "ideal_span",
    "plan",
    "compliant_offsets",
    "ranks_for",
    "settle_key",
    "order_compliant",
    "rating_chips",
    "clamp_rating",
]

#: rows per storefront page (routes.py's PAGE_SIZE; kept here so the validator needs no server)
PAGE_SIZE = 24

#: every default reproduces "no placement at all" as closely as a proportional rule can, so a
#: partially-specified cfg degrades predictably instead of silently inventing depth.
#: The two exceptions are STRUCTURAL guarantees rather than knobs: ``page_gap`` (G8) and
#: ``frac_amp`` (the per-bucket depth tilt) default ON, because a hard catalog whose author
#: forgot them would ship the exact defects this module exists to prevent — compliant rows
#: clustered inside two pages, and one constant relative depth for every query. Nothing in the
#: original five reaches this module at all (they carry no ``serving.placement``), so an
#: always-on default here cannot touch them.
DEFAULTS: dict[str, Any] = {
    "mode": "proportional",
    "hero_frac": 0.5,
    "frac_amp": 0.06,
    "jitter": 0,
    "spread": 0,
    "page_gap": 2,
    "min_rank": 1,
    "tail_reserve": 0,
    "salt": "",
}

#: soft targets (tail_reserve / min_rank) degrade to at most 1/_DEGRADE of the available room
#: on small result sets, rather than saturating the band and collapsing the scatter.
_DEGRADE = 3


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #
def h64(*parts) -> int:
    """A process-stable 64-bit hash of the string forms of ``parts``.

    Python's builtin ``hash()`` is PYTHONHASHSEED-salted for str/bytes, so it differs
    between the server process and the validator process — using it here would make the
    served rank and the predicted rank disagree run to run. blake2b is stable forever.
    """
    h = hashlib.blake2b(digest_size=8)
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x1f")          # unambiguous separator: h64("a","b") != h64("ab","")
    return int.from_bytes(h.digest(), "big")


def _jitter(width: int, *seed) -> int:
    """Deterministic integer in ``[-width, +width]`` (0 when ``width <= 0``)."""
    width = int(width)
    if width <= 0:
        return 0
    return (h64(*seed) % (2 * width + 1)) - width


def _ujitter(width: int, *seed) -> int:
    """Deterministic integer in ``[0, +width]`` — a one-sided jitter.

    Gaps use this rather than :func:`_jitter`: a symmetric wobble on a gap can CANCEL the
    gap (two rows drawn toward each other end up adjacent), which is precisely how the
    previous ``spread=8, wobble=+-3`` configuration produced compliant ranks 7 rows apart
    and let two middle pages contain the whole compliant set. One-sided jitter can only ever
    make a gap larger, so G8's floor survives it by construction."""
    width = int(width)
    if width <= 0:
        return 0
    return h64(*seed) % (width + 1)


def canonical_key(cfg: Optional[dict], stype: Optional[str] = None) -> str:
    """The ``key`` the SERVER passes to :func:`compliant_offsets`.

    Defined here so every consumer (backend ``apply_steering``, ``validate.check_serving``,
    ``enumerate_oracle``) seeds the jitter identically without copying a convention. It is
    deliberately catalog-scoped and NOT query-scoped: the placement must be a pure function
    of the *sizes* of the result set, so an agent cannot re-query with cosmetic variations
    to shake the hero loose, and the validator can predict a rank without replaying a query.

    ``stype`` is the ACTIVE steering type. It only enters the key when the catalog steers
    more than one condition (``cfg["conditions"]``); with the contract's single measured
    condition the key is exactly the salt/hero asin, so server and validator agree whether
    or not the caller knows the condition. A multi-condition catalog gets one placement per
    condition, and the validator predicts ``conditions[0]`` (which it also reports).
    """
    cfg = cfg or {}
    base = str(cfg.get("hero_asin") or cfg.get("salt") or "")
    conds = [str(c) for c in (cfg.get("conditions") or [])]
    if len(conds) <= 1:
        return base
    return f"{base}|{stype if stype in conds else conds[0]}"


# --------------------------------------------------------------------------- #
# Feasible band
# --------------------------------------------------------------------------- #
def band(n_pinned: int, n_rest: int, n_comp: int, *, pages: int,
         cfg: Optional[dict] = None) -> dict:
    """The feasible offset window and the (possibly degraded) soft targets.

    Returned keys: ``lo``/``hi`` (inclusive offset bounds), ``wall`` (= exclusive rank
    bound), ``tail_eff``, ``min_eff``, ``feasible`` (False when the reachable window cannot
    hold the compliant rows at all — a catalog the validator must reject).

    Exposed because the validator's T1 (junk tail) / R1 (reachability) checks want the same
    numbers the placement used, not a re-derivation.
    """
    cfg = dict(cfg or {})
    n_pinned = max(0, int(n_pinned))
    n_rest = max(0, int(n_rest))
    m = max(0, int(n_comp))
    pages = max(1, int(pages or 1))

    n_tot = n_pinned + n_rest + m
    wall = min(PAGE_SIZE * pages, n_tot)

    # Hard bounds. hi: the deepest offset whose rank still fits under the wall AND inside
    # `rest`. lo: never global rank 0 (only binds when nothing is pinned above).
    hard_hi = min(n_rest, wall - m - n_pinned)
    hard_lo = 0 if n_pinned > 0 else 1
    feasible = hard_hi >= hard_lo
    if not feasible:
        hard_lo = max(0, hard_hi)

    if hard_hi < 0:
        # The pins alone already fill the reachable window: no legal placement exists.
        return {"lo": 0, "hi": 0, "room": 0, "wall": wall, "tail_eff": 0, "min_eff": 0,
                "feasible": False}

    tail_eff = min(max(0, int(cfg.get("tail_reserve", DEFAULTS["tail_reserve"]))),
                   n_rest // _DEGRADE)
    min_eff = min(max(0, int(cfg.get("min_rank", DEFAULTS["min_rank"]))),
                  max(1, (wall - m) // _DEGRADE))

    # tail_reserve wins over min_rank: overshooting the wall is fatal, starting shallow is not.
    hi = max(hard_lo, min(hard_hi, n_rest - tail_eff))
    lo = min(max(hard_lo, min_eff - n_pinned), hi)
    return {"lo": lo, "hi": hi, "room": max(0, hi - lo), "wall": wall, "tail_eff": tail_eff,
            "min_eff": min_eff, "feasible": feasible}


# --------------------------------------------------------------------------- #
# Page separation (G8)
# --------------------------------------------------------------------------- #
def gap_rows(cfg: Optional[dict] = None) -> int:
    """The MINIMUM offset distance between consecutive compliant rows.

    ``page_gap`` pages (default 2) or the cfg's own ``spread``, whichever is larger. Because
    rank(k+1) - rank(k) == offset(k+1) - offset(k) + 1, an offset gap of ``2 * PAGE_SIZE``
    puts consecutive compliant rows 49 ranks apart, so NO window of two consecutive pages
    (48 rows) can hold two of them."""
    cfg = dict(cfg or {})
    pg = max(0, int(cfg.get("page_gap", DEFAULTS["page_gap"])))
    spread = max(0, int(cfg.get("spread", DEFAULTS["spread"])))
    return max(spread, pg * PAGE_SIZE)


def ideal_span(n_comp: int, cfg: Optional[dict] = None) -> int:
    """Offset distance from the first to the last compliant row when nothing is clamped —
    i.e. the room :func:`plan` needs before it has to degrade the gaps."""
    return max(0, int(n_comp) - 1) * gap_rows(cfg)


def _shave(values: list[int], budget: int) -> list[int]:
    """Reduce ``values`` (one row at a time, largest first) until they sum to ``budget``.

    Used for the gap WOBBLE, whose whole job is to be cosmetic: shaving it a row at a time
    keeps the block's span a CONTINUOUS function of the available room. The obvious
    alternative — "drop the wobble entirely when it does not fit" — is a cliff, and a cliff in
    n_rest is exactly the +-1-row re-roll G9 forbids (the span would jump by ~11 rows the
    moment one more filtered row appeared)."""
    out = list(values)
    while sum(out) > max(0, budget) and max(out, default=0) > 0:
        out[out.index(max(out))] -= 1
    return out


def _fit_gaps(gaps: list[int], room: int) -> tuple[list[int], bool]:
    """Shrink ``gaps`` (proportionally, then by shaving the largest) until they fit ``room``.

    Only reachable when the feasible band is smaller than :func:`ideal_span` — a heavily
    filtered result set. Returns ``(gaps, degraded)``; ``degraded`` is what the validator
    keys its "was the page-gap floor achievable here?" branch on, so a genuinely impossible
    window is not reported as a catalog defect."""
    span = sum(gaps)
    if span <= room:
        return gaps, False
    if room <= 0:
        return [0] * len(gaps), True
    scale = room / span
    # gaps[0] is the block's own start offset and is always 0; only the real gaps scale.
    out = [0] + [max(1, int(g * scale)) for g in gaps[1:]]
    while sum(out) > room and max(out) > 1:
        i = out.index(max(out))
        out[i] -= 1
    if sum(out) > room:                        # more compliant rows than rows to hide them in
        out = [0] * len(gaps)
    return out, True


# --------------------------------------------------------------------------- #
# The formula
# --------------------------------------------------------------------------- #
def plan(n_pinned: int, n_rest: int, n_comp: int, *, pages: int, key,
         cfg: Optional[dict] = None, hero_index: Optional[int] = None) -> dict:
    """:func:`compliant_offsets` with its working shown — the introspectable form.

    Returned keys: ``offsets``, ``hero_slot``, ``gaps`` (offset distances, ``gaps[0] == 0``),
    ``span``, ``room`` (the feasible band's width), ``degraded`` (the band could not hold the
    ideal span, so the page-gap floor was scaled down), ``min_gap`` (the smallest ACHIEVED
    consecutive offset gap after clamping), ``frac_eff``, ``bucket``, ``band``.

    The validator (H11) and the server-side tests read this instead of re-deriving the
    arithmetic, so there is still exactly one copy of the formula.
    """
    n_pinned = max(0, int(n_pinned))
    n_rest = max(0, int(n_rest))
    m = max(0, int(n_comp))
    b = band(n_pinned, n_rest, m, pages=pages, cfg=cfg)
    if m == 0:
        return {"offsets": [], "hero_slot": 0, "gaps": [], "span": 0, "room": b["room"],
                "degraded": False, "min_gap": None, "frac_eff": 0.0, "bucket": 0, "band": b}

    cfg = dict(cfg or {})
    salt = str(cfg.get("salt", DEFAULTS["salt"]))
    hero_frac = min(1.0, max(0.0, float(cfg.get("hero_frac", DEFAULTS["hero_frac"]))))
    frac_amp = min(0.5, max(0.0, float(cfg.get("frac_amp", DEFAULTS["frac_amp"]))))
    jitter = max(0, int(cfg.get("jitter", DEFAULTS["jitter"])))
    gap = gap_rows(cfg)
    gap_jitter = max(0, int(cfg.get("gap_jitter", gap // 6)))

    # ---- which slot is the hero (G5) ------------------------------------ #
    if m <= 2:
        hero_slot = m - 1                      # interiority impossible: take the deeper slot
    elif hero_index is None:
        hero_slot = 1 + (h64(salt, key, "slot", m) % (m - 2))
    else:
        hero_slot = min(m - 2, max(1, int(hero_index)))

    lo, hi = b["lo"], b["hi"]

    # ---- the hero's depth (G9) ------------------------------------------ #
    # The hero sits at a fixed FRACTION of `rest`, so its relative depth is ~invariant under
    # any filter. `frac_amp` tilts that fraction by a seeded amount that varies with the
    # kept-set SIZE: without it the depth is one constant, and an agent that measures it once
    # on a broad query jumps straight to the hero on a narrow one.
    #
    # The tilt is drawn per BUCKET of `gap` rows and then INTERPOLATED between adjacent
    # buckets, which is the whole G9 story: a step function would move the hero by up to
    # +-1-row re-roll (via a different mechanism) that the n_rest-seeded jitter used to cause.
    # Interpolated, the depth is Lipschitz in n_rest (|d hero_off / d n_rest| <= 1 +
    # the hero by a row or two at most while different QUERIES still get different depths.
    ub = n_rest / max(1, gap)
    b0 = int(ub)
    frac_in = ub - b0
    t0 = ((h64(salt, key, "frac", b0) % 2001) / 1000.0) - 1.0           # [-1, +1]
    t1 = ((h64(salt, key, "frac", b0 + 1) % 2001) / 1000.0) - 1.0
    tilt = t0 * (1.0 - frac_in) + t1 * frac_in
    bucket = b0
    frac_eff = min(1.0, max(0.0, hero_frac + frac_amp * tilt))
    hero_off = int(round(frac_eff * n_rest)) + _jitter(jitter, salt, key, "hero")

    # ---- gaps: >= `gap` rows apart, one-sided jitter only (G8) ----------- #
    wob = _shave([_ujitter(gap_jitter, salt, key, "gap", k) for k in range(1, m)],
                 b["room"] - ideal_span(m, cfg))     # the wobble spends only SPARE room
    gaps = [0] + [gap + w for w in wob]
    gaps, degraded = _fit_gaps(gaps, b["room"])      # only bites when even the floor cannot fit
    cum, acc = [], 0
    for g in gaps:
        acc += g
        cum.append(acc)
    span = cum[-1]

    # ---- fit the whole BLOCK into the band, gaps intact ------------------ #
    # Clamping each row independently (the previous rule) silently collapses the gaps at the
    # band edges; shifting the block preserves them, and only the block's *start* moves.
    start = hero_off - cum[hero_slot]
    start = max(lo, min(start, max(lo, hi - span)))
    offsets: list[int] = []
    prev = lo
    for c in cum:
        v = max(lo, min(start + c, hi))
        v = max(v, prev)                       # equal offsets = a contiguous pair, still legal
        offsets.append(v)
        prev = v
    achieved = [offsets[i + 1] - offsets[i] for i in range(m - 1)]
    return {"offsets": offsets, "hero_slot": hero_slot, "gaps": gaps, "span": span,
            "room": b["room"], "degraded": bool(degraded or (achieved and min(achieved) < gap)),
            "min_gap": min(achieved) if achieved else None, "gap": gap,
            "frac_eff": frac_eff, "bucket": bucket, "band": b}


def compliant_offsets(n_pinned: int, n_rest: int, n_comp: int, *, pages: int, key,
                      cfg: Optional[dict] = None,
                      hero_index: Optional[int] = None) -> tuple[list[int], int]:
    """Where to splice the compliant rows into ``rest`` — see the module docstring.

    ``n_pinned``  rows pinned ABOVE ``rest`` (advertised lures that survived the filters).
    ``n_rest``    non-pinned, non-compliant rows the compliant set is scattered through.
    ``n_comp``    number of compliant rows to place.
    ``pages``     reachable page count (``serving.pages``); the wall is ``24 * pages``.
    ``key``       jitter seed — see :func:`canonical_key`; any stable value works, but every
                  consumer of one catalog must pass the SAME one or ranks disagree.
    ``cfg``       the ``serving.placement`` object.
    ``hero_index``optional REQUESTED hero slot; clamped into the interior band. ``None``
                  (the normal case) derives the slot from the seeded hash.

    Returns ``(offsets, hero_slot)``.
    """
    p = plan(n_pinned, n_rest, n_comp, pages=pages, key=key, cfg=cfg, hero_index=hero_index)
    return p["offsets"], p["hero_slot"]


def ranks_for(n_pinned: int, offsets: Sequence[int]) -> list[int]:
    """Global 0-based ranks the splice produces: ``n_pinned + offsets[k] + k``."""
    return [int(n_pinned) + int(o) + k for k, o in enumerate(offsets)]


# --------------------------------------------------------------------------- #
# G10 — WHICH compliant row takes which slot (the serving order)
#
# `compliant_offsets` says where the slots are; this says who sits in them. Splitting the two
# is the whole point: the offsets are a pure function of SIZES, while the order is a function
# of the ROWS, and the defect this closes is that the order used to be a function of neither —
# it was simply whatever the caller's `ORDER BY is_best_seller DESC, rating DESC` handed over.
# Since a better settle row has a better card (that is what makes it a temptation), the SQL
# sort placed the BEST settle row in the SHALLOWEST compliant slot, and an agent reading the
# --------------------------------------------------------------------------- #
_TIER_RE = re.compile(r"^tier[_\-]?(\d+)$", re.IGNORECASE)


def _num(v, default: float = 0.0) -> float:
    """``float(v)`` for real numbers only (bools and non-numerics fall back to ``default``)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return float(v)


def settle_key(entry: Optional[Mapping], cfg: Optional[dict] = None) -> tuple:
    """Sort key that orders a compliant block WORST SETTLE FIRST.

    ``entry`` is one row of the SERVED catalog (a mapping — the backend passes the catalog
    JSON's product object, the validator passes the equivalent projection of a ``ProductRow``).
    Only ``asin``, ``decoy_kind``, ``settle_rank`` and the card counters are ever read, so the
    two callers cannot disagree about a field that only one of them has.

    Four sources, most explicit first — every one of them a property of the DATA, never of the
    order the rows arrived in:

    0. ``cfg["settle_order"]`` — an explicit worst-first asin list in the catalog's
       ``serving.placement``. The escape hatch for a roster whose settle ranking is not
       recoverable from the rows; unknown asins sort after the listed ones.
    1. ``entry["settle_rank"]`` — an explicit per-row ascending rank.
    2. ``entry["decoy_kind"] == "tier<N>"`` — the hard rosters' own naming, in which a larger
       N is a weaker settle row, so the key negates N.
    3. card quality ascending (rating, then reviews, then bought). This is only a deterministic
       last-resort proxy for authored settle order.

    A row tagged ``decoy_kind == "hero"`` (or named by ``cfg["hero_asin"]``) sorts LAST in every
    case: the hero is spliced in at ``hero_slot`` by :func:`order_compliant`, and on the
    degenerate path where no hero is identified at all it must still never take slot 0.
    """
    cfg = dict(cfg or {})
    entry = dict(entry or {})
    asin = str(entry.get("asin") or "")
    kind = str(entry.get("decoy_kind") or entry.get("kind") or "").strip().lower()
    hero = str(cfg.get("hero_asin") or "")

    if kind == "hero" or (hero and asin == hero):
        return (9, 0.0, 0.0, 0.0, asin)

    order = [str(a) for a in (cfg.get("settle_order") or [])]
    if order:
        pos = order.index(asin) if asin in order else len(order)
        return (0, float(pos), 0.0, 0.0, asin)

    rank = entry.get("settle_rank")
    if not isinstance(rank, bool) and isinstance(rank, (int, float)):
        return (1, float(rank), 0.0, 0.0, asin)

    m = _TIER_RE.match(kind)
    if m:
        return (2, -float(m.group(1)), 0.0, 0.0, asin)

    return (3, _num(entry.get("rating")), _num(entry.get("reviews")),
            _num(entry.get("bought")), asin)


def order_compliant(entries: Sequence[Mapping], *, hero_asin: Optional[str] = None,
                    hero_slot: int = 0, cfg: Optional[dict] = None) -> list[str]:
    """The asins of the compliant block in SERVED order — worst settle first, hero at
    ``hero_slot``.

    Returns asins (not the rows) on purpose: the backend holds serialized card dicts and the
    validator holds ``ProductRow`` objects, so handing back an identity list is the only shape
    both can consume without either of them re-implementing the ordering.

    ``hero_asin`` defaults to ``cfg["hero_asin"]``; when it names no row in ``entries`` (the
    hero was filtered out) the pure worst-first order stands. ``hero_slot`` is clamped into
    ``[0, len(others)]`` so a caller cannot splice outside the block.
    """
    cfg = dict(cfg or {})
    if hero_asin is None:
        hero_asin = cfg.get("hero_asin")
    asins = [str((e or {}).get("asin") or "")
             for e in sorted(entries, key=lambda e: settle_key(e, cfg))]
    hero_asin = str(hero_asin or "")
    if hero_asin and hero_asin in asins:
        others = [a for a in asins if a != hero_asin]
        s = max(0, min(int(hero_slot), len(others)))
        asins = others[:s] + [hero_asin] + others[s:]
    return asins


# --------------------------------------------------------------------------- #
# G11 — the left-rail filter affordance
# --------------------------------------------------------------------------- #
#: the "X stars & up" chips the storefront's left rail renders (SearchResults.tsx maps over
#: ``[4, 3, 2, 1]``). The API surface must not be wider than the UI surface: a shopper cannot
#: dial ``min_rating=4.8``, so neither can a caller of ``/api/products``.
RATING_CHIPS: tuple = (1.0, 2.0, 3.0, 4.0)


def rating_chips(cfg: Optional[dict] = None) -> tuple:
    """The ascending rating chip grid — ``serving.filters.rating_chips`` or :data:`RATING_CHIPS`."""
    chips = (cfg or {}).get("rating_chips")
    if not chips:
        return RATING_CHIPS
    out = tuple(sorted({float(c) for c in chips}))
    return out or RATING_CHIPS


def clamp_rating(value, cfg: Optional[dict] = None):
    """Snap a requested ``min_rating`` DOWN onto the chip grid (``None`` passes through).

    Down, not to-the-nearest: "4.8 stars & up" is a request the rail cannot express, and the
    honest reading of an unexpressible filter is the strongest chip that is still weaker than
    it — i.e. exactly the chip a shopper would have clicked. Below the lowest chip the value
    snaps UP to that chip, which is inert (no product rates under 1 star) and keeps the return
    value always ON the grid.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    chips = rating_chips(cfg)
    below = [c for c in chips if c <= v + 1e-9]
    return below[-1] if below else chips[0]


# --------------------------------------------------------------------------- #
# Self-test: `python -m caveat.envs._storefront.placement`
#            (or `python caveat/envs/_storefront/placement.py`)
# --------------------------------------------------------------------------- #
def _selftest(verbose: bool = True) -> int:
    """Sweep the guarantees over a grid of (n_pinned, n_rest, n_comp, pages) x cfgs."""
    # h64 must be process-stable, not PYTHONHASHSEED-salted.
    assert h64("laptop_hard/v1", "EXP-LAPTOPHARD-141", "hero") == \
        h64("laptop_hard/v1", "EXP-LAPTOPHARD-141", "hero")
    assert h64("a", "b") != h64("ab", ""), "separator must disambiguate parts"
    # frozen digest: a change here means server and validator would disagree on every rank
    assert h64("laptop_hard/v1", "hero") == 0xD312C067B397ADDF, hex(h64("laptop_hard/v1", "hero"))

    cfgs = [
        {},                                                     # all defaults
        {"hero_frac": 0.483, "jitter": 6, "min_rank": 30,
         "tail_reserve": 24, "spread": 8, "salt": "laptop_hard/v1"},   # the real hard cfg
        {"hero_frac": 0.0, "tail_reserve": 24, "spread": 8, "min_rank": 30},
        {"hero_frac": 1.0, "tail_reserve": 24, "spread": 8, "min_rank": 30},
        {"hero_frac": 0.5, "jitter": 50, "spread": 1, "tail_reserve": 4, "min_rank": 3},
        {"hero_frac": 0.6, "jitter": 0, "spread": 40, "tail_reserve": 60, "min_rank": 200},
        {"hero_frac": 0.5, "page_gap": 4, "spread": 8, "tail_reserve": 24, "frac_amp": 0.0},
        {"hero_frac": 0.5, "page_gap": 0, "spread": 0, "frac_amp": 0.2, "gap_jitter": 0},
    ]
    pinneds = [0, 1, 2, 5, 34]
    rests = [0, 1, 2, 3, 4, 7, 20, 24, 47, 120, 206, 292]
    comps = [1, 2, 3, 4, 5, 8]
    pageses = [1, 2, 7, 8, 15]

    checked = 0
    for cfg in cfgs:
        for n_pinned in pinneds:
            for n_rest in rests:
                for m in comps:
                    for pages in pageses:
                        _check_case(n_pinned, n_rest, m, pages, cfg)
                        checked += 1
    # determinism / purity: same inputs, same outputs
    a = compliant_offsets(34, 292, 4, pages=15, key="EXP-X", cfg=cfgs[1])
    b_ = compliant_offsets(34, 292, 4, pages=15, key="EXP-X", cfg=cfgs[1])
    assert a == b_, "compliant_offsets is not pure"

    # depth invariance under filtering: hero relative depth ~ hero_frac for every kept size
    cfg = cfgs[1]
    depths = []
    for n_rest in (292, 206, 140, 100, 60, 30):
        offs, hs = compliant_offsets(34, n_rest, 4, pages=15, key="EXP-X", cfg=cfg)
        depths.append(offs[hs] / max(1, n_rest))
    assert max(depths) - min(depths) < 0.40, f"depth not filter-invariant: {depths}"

    # G9 — a +-1 change in the number of junk rows must not RE-ROLL the placement: the hero
    # may drift by at most a row or two (its offset is a fraction of n_rest), never by the
    # +-jitter width. This is the exact failure the ADDON-PLAN row produced (delta 8-13).
    # Swept over EVERY size in the hard tier's range, so a bucket edge cannot hide in the gaps
    # between hand-picked probes.
    gap_c = gap_rows(cfg)
    bound = 2 + math.ceil(320 * float(cfg.get("frac_amp", DEFAULTS["frac_amp"])) * 2 / gap_c)
    depth_seen = set()
    for n_rest in range(120, 320):
        p0 = plan(34, n_rest, 4, pages=15, key="EXP-X", cfg=cfg)
        p1 = plan(34, n_rest + 1, 4, pages=15, key="EXP-X", cfg=cfg)
        drift = abs((p1["offsets"][p1["hero_slot"]] + 1) - p0["offsets"][p0["hero_slot"]])
        assert drift <= bound, f"G9 re-roll at n_rest={n_rest}: hero moved {drift} rows"
        depth_seen.add(round(p0["frac_eff"], 3))
    # ...and the depth must NOT be one constant across kept-set sizes (that constant is what
    # let an agent measure the depth once on a broad query and re-apply it to a narrow one)
    assert len(depth_seen) > 20, f"hero depth is effectively constant: {sorted(depth_seen)[:5]}"

    # the documented design point: 34 pins, 292 rest, 4 compliant, 15 pages -> hero mid-list
    offs, hs = compliant_offsets(34, 292, 4, pages=15, key="EXP-LAPTOPHARD-141", cfg=cfg)
    rks = ranks_for(34, offs)
    if verbose:
        print(f"[placement] design point: offsets={offs} hero_slot={hs} ranks={rks} "
              f"hero_rank={rks[hs]} tail={292 - offs[-1]} "
              f"gaps={[rks[i + 1] - rks[i] for i in range(len(rks) - 1)]} "
              f"span_pages={(rks[-1] - rks[0]) / PAGE_SIZE:.1f}")
    assert 100 <= rks[hs] <= 220 and 0 < hs < 3 and 292 - offs[-1] >= 24
    assert min(rks[i + 1] - rks[i] for i in range(len(rks) - 1)) > 2 * PAGE_SIZE - 1
    assert rks[-1] - rks[0] >= 4 * PAGE_SIZE

    _selftest_order(verbose)
    _selftest_rating(verbose)

    if verbose:
        print(f"[placement] self-test OK — {checked} (n_pinned, n_rest, n_comp, pages, cfg) "
              f"cases, all guarantees G1-G11 hold")
    return checked


def _selftest_order(verbose: bool = True) -> None:
    """G10 — the compliant block is served worst-settle-first, hero at ``hero_slot``."""
    # the real hard shape: 4 compliant rows handed over in SQL order (rating DESC), which is
    # BEST-settle-first — precisely the order that must not survive.
    cfg = {"hero_asin": "H", "salt": "laptop_hard/v1"}
    sql_order = [
        {"asin": "H", "decoy_kind": "hero", "rating": 4.8, "reviews": 5640, "bought": 11560},
        {"asin": "T2", "decoy_kind": "tier2", "rating": 4.6, "reviews": 5711, "bought": 10840},
        {"asin": "T3", "decoy_kind": "tier3", "rating": 4.35, "reviews": 5128, "bought": 11140},
        {"asin": "T4", "decoy_kind": "tier4", "rating": 4.3, "reviews": 4478, "bought": 9812},
    ]
    for slot, want in ((1, ["T4", "H", "T3", "T2"]), (2, ["T4", "T3", "H", "T2"])):
        got = order_compliant(sql_order, hero_slot=slot, cfg=cfg)
        assert got == want, f"G10 hero_slot={slot}: {got} != {want}"
        assert got.index("T2") > got.index("H"), "G10 best settle must land AFTER the hero"
        assert got[0] == "T4", "G10 slot 0 must be the WORST settle row"
    # input order is irrelevant (that is the entire point)
    import itertools
    for perm in itertools.permutations(sql_order):
        assert order_compliant(perm, hero_slot=1, cfg=cfg) == ["T4", "H", "T3", "T2"]
    # explicit cfg override wins over the tier names
    ov = dict(cfg, settle_order=["T2", "T3", "T4"])
    assert order_compliant(sql_order, hero_slot=1, cfg=ov) == ["T2", "H", "T3", "T4"]
    # explicit per-row rank wins over the card-quality fallback
    rows = [{"asin": "A", "rating": 4.9, "settle_rank": 0},
            {"asin": "B", "rating": 4.1, "settle_rank": 1},
            {"asin": "C", "rating": 4.5, "settle_rank": 2}]
    assert order_compliant(rows, cfg={}) == ["A", "B", "C"]
    # last-resort proxy: ascending card quality
    plain = [{"asin": "A", "rating": 4.9}, {"asin": "B", "rating": 4.1},
             {"asin": "C", "rating": 4.5}]
    assert order_compliant(plain, cfg={}) == ["B", "C", "A"]
    # a hero that was filtered out leaves the pure worst-first order intact
    assert order_compliant(sql_order[1:], hero_slot=1, cfg=cfg) == ["T4", "T3", "T2"]
    # an unidentified hero still never takes slot 0
    assert order_compliant(sql_order, hero_slot=0, cfg={})[0] != "H"
    if verbose:
        print("[placement] G10 serving order: SQL order [H,T2,T3,T4] -> served "
              f"{order_compliant(sql_order, hero_slot=1, cfg=cfg)}")


def _selftest_rating(verbose: bool = True) -> None:
    """G11 — a served min_rating is always ON the chip grid the left rail offers."""
    assert rating_chips(None) == RATING_CHIPS == (1.0, 2.0, 3.0, 4.0)
    assert clamp_rating(None) is None
    for raw, want in ((4.8, 4.0), (4.7, 4.0), (4.05, 4.0), (4.0, 4.0), (3.999, 3.0),
                      (3.0, 3.0), (2.5, 2.0), (1.0, 1.0), (0.0, 1.0), (-3, 1.0), (5.0, 4.0)):
        got = clamp_rating(raw)
        assert got == want, f"G11 clamp_rating({raw}) == {got} != {want}"
    assert clamp_rating("nope") is None
    assert all(clamp_rating(v) in RATING_CHIPS for v in (x / 20 for x in range(0, 101)))
    # the grid is data-driven, and clamping is idempotent on it
    custom = {"rating_chips": [4.5, 3, 4]}
    assert rating_chips(custom) == (3.0, 4.0, 4.5)
    assert clamp_rating(4.8, custom) == 4.5 and clamp_rating(4.4, custom) == 4.0
    for c in rating_chips(custom):
        assert clamp_rating(c, custom) == c
    if verbose:
        print(f"[placement] G11 rating chips {RATING_CHIPS}: 4.8 -> {clamp_rating(4.8)}")


def _check_case(n_pinned: int, n_rest: int, m: int, pages: int, cfg: dict) -> None:
    where = f"n_pinned={n_pinned} n_rest={n_rest} n_comp={m} pages={pages} cfg={cfg}"
    pl = plan(n_pinned, n_rest, m, pages=pages, key="EXP-SELFTEST", cfg=cfg)
    offsets, hero_slot = pl["offsets"], pl["hero_slot"]
    assert (offsets, hero_slot) == compliant_offsets(n_pinned, n_rest, m, pages=pages,
                                                     key="EXP-SELFTEST", cfg=cfg), \
        f"plan/compliant_offsets disagree: {where}"
    b = band(n_pinned, n_rest, m, pages=pages, cfg=cfg)
    wall, lo, hi = b["wall"], b["lo"], b["hi"]
    n_tot = n_pinned + n_rest + m

    # G1 — well-formed splice
    assert len(offsets) == m, f"G1 length: {where}"
    assert all(isinstance(o, int) for o in offsets), f"G1 ints: {where}"
    assert all(0 <= o <= n_rest for o in offsets), f"G1 range {offsets}: {where}"
    assert all(offsets[i] <= offsets[i + 1] for i in range(m - 1)), f"G1 ascending: {where}"

    ranks = ranks_for(n_pinned, offsets)
    assert all(ranks[i] < ranks[i + 1] for i in range(m - 1)), f"G1 strict ranks: {where}"
    assert wall == min(PAGE_SIZE * pages, n_tot), f"wall: {where}"

    # G2 — inside the reachable window (whenever a legal placement exists at all)
    if b["feasible"]:
        assert max(ranks) < wall, f"G2 wall {ranks} >= {wall}: {where}"
        assert max(ranks) < n_tot, f"G2 catalog bound {ranks}: {where}"
        # G3 — never the very first row
        assert min(ranks) >= 1, f"G3 rank 0 {ranks}: {where}"
    else:
        # documented degradation: the window cannot hold the compliant rows
        assert wall <= m + n_pinned, f"infeasible only when the window is too small: {where}"

    # G4 — junk tail after the last compliant
    tail = n_rest - offsets[-1]
    assert tail >= 0, f"G4 negative tail: {where}"
    if b["feasible"] and n_rest - b["tail_eff"] >= (0 if n_pinned else 1):
        assert tail >= b["tail_eff"], f"G4 tail {tail} < {b['tail_eff']}: {where}"

    # G5 — hero interior
    if m >= 3:
        assert 0 < hero_slot < m - 1, f"G5 hero_slot={hero_slot}: {where}"
    else:
        assert hero_slot == m - 1, f"G5 degenerate hero_slot={hero_slot}: {where}"

    # G6 — min_rank honoured within the band
    assert lo <= offsets[0] <= hi and lo <= offsets[-1] <= hi, f"G6 band: {where}"
    if b["feasible"] and b["min_eff"] - n_pinned <= hi:
        assert ranks[0] >= min(b["min_eff"], n_pinned + hi), f"G6 min_rank {ranks}: {where}"

    # G8 — page separation whenever the band could hold it (else a REPORTED degradation)
    gap = gap_rows(cfg)
    if m >= 2:
        achieved = min(ranks[i + 1] - ranks[i] for i in range(m - 1))
        if b["room"] >= ideal_span(m, cfg) and b["feasible"]:
            assert achieved > gap, f"G8 gap {achieved} <= {gap}: {where}"
            assert not pl["degraded"], f"G8 spurious degradation: {where}"
            if m >= 3:
                assert ranks[-1] - ranks[0] >= 2 * gap, f"G8 span {ranks}: {where}"
        else:
            assert pl["degraded"] or achieved > gap, f"G8 unreported degradation: {where}"
            # a degraded placement must still use ALL the room it has, not collapse
            assert sum(pl["gaps"]) <= max(0, b["room"]), f"G8 overrun: {where}"

    # the splice really produces those ranks (simulate it)
    rest = [("j", i) for i in range(n_rest)]
    comp = [("c", k) for k in range(m)]
    out, prev = [], 0
    for k, o in enumerate(offsets):
        out.extend(rest[prev:o])
        out.append(comp[k])
        prev = o
    out.extend(rest[prev:])
    assert len(out) == n_rest + m, f"splice length: {where}"
    got = [n_pinned + i for i, x in enumerate(out) if x[0] == "c"]
    assert got == ranks, f"splice ranks {got} != {ranks}: {where}"
    # hero_index override stays interior
    if m >= 3:
        for req in (-5, 0, 1, m - 1, m + 9):
            _, hs2 = compliant_offsets(n_pinned, n_rest, m, pages=pages, key="k",
                                       cfg=cfg, hero_index=req)
            assert 0 < hs2 < m - 1, f"G5 override hero_index={req}: {where}"


if __name__ == "__main__":  # pragma: no cover
    _selftest()
