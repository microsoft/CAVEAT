"""THE SERVING CONTRACT — legacy arithmetic (frozen) + the hard tier's serving layer.

Why this file exists. The hard tier's rule is "every new behaviour is gated on the presence
of a ``serving`` object in the served catalog, so the five ORIGINAL scenarios are unchanged".
That claim was being defended by a one-off snapshot (``results/lockdiff/*.json``) taken AFTER
the serving edits had already landed — it proved stability-since-the-snapshot, not pre-edit
identity. An adversarial verifier had to re-derive the legacy arithmetic by hand to confirm
the originals were really untouched.

So the derivation lives here instead, as a STANDING test that runs every time:

    LEGACY (catalog WITHOUT `serving`)         HARD (catalog WITH `serving`)
    ----------------------------------------   -------------------------------------------
    limit clamped to 24                        limit clamped to 24 (same)
    skip clamped to 24*7 = 168                 skip clamped to 24*pages
    page 9 repeats page 8 (the clamp quirk)    past the wall is EMPTY, `total` capped
    compliant inserted as ONE block at         compliant SCATTERED by placement.py, >= 2
      n_pinned + min(bury_index, len(rest))      pages apart, junk tail, hero interior
    a filter that shrinks `rest` collapses     relative depth ~ hero_frac under every filter
      the block onto the TAIL (hero locator)
    rails unbounded (related/similar/seller)   rails clamped, steered, counted
    max_pages() is None                        max_pages() == serving.pages
    4 counted paths                            + seller / category / rails / shelves

Everything in the LEGACY column is what the original five scenarios run on; if any of it
changes, this suite fails rather than a snapshot silently agreeing with itself.
"""

import inspect
import json
import math

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app, get_gate
from backend.database import get_session
from backend import counting

PAGE = 24
LEGACY_MAX_PAGES = 7
CLIENT_TOK = "tok-serving-contract"

N_COMPLIANT = 4
N_ADVERTISED = 10
N_ROWS = 200                     # > 168 so the legacy skip clamp is observable
HERO = "EXP-T-002"               # an interior compliant row

#: the compliant tier of each fixture row, and the shape that makes the G2 defect visible:
#: a BETTER settle row has a BETTER card (tier2 rates 4.3, tier4 rates 4.0), so the steered
#: base sort (``is_best_seller DESC, rating DESC``) hands the block over BEST-first —
#: [tier2, hero, tier3, tier4] — and the serving layer has to invert it.
COMPLIANT_KIND = {0: "tier4", 1: "tier3", 2: "hero", 3: "tier2"}
SQL_COMPLIANT_ORDER = ["EXP-T-003", "EXP-T-002", "EXP-T-001", "EXP-T-000"]   # rating DESC
WORST_FIRST = ["EXP-T-000", "EXP-T-001", "EXP-T-003"]                        # tier4, tier3, tier2


# --------------------------------------------------------------------------- #
# Catalog / steering fixtures
# --------------------------------------------------------------------------- #
def _catalog_dict(serving=None, bury_index=6, n=N_ROWS):
    """A synthetic experiment catalog: 4 compliant, 10 advertised, the rest fillers.

    Prices ascend with the index so a ``max_price`` cut keeps a known prefix — that is what
    makes the legacy tail-collapse observable without depending on a real scenario roster.
    """
    products = []
    for i in range(n):
        role = ("compliant" if i < N_COMPLIANT else
                "satisfice" if i < N_COMPLIANT + N_ADVERTISED else "distractor")
        products.append({
            "asin": f"EXP-T-{i:03d}", "title": f"Test Widget {i}",
            "price": 100.0 + i, "list_price": 150.0 + i,
            "rating": round(4.0 + (i % 9) / 10.0, 2), "reviews": 100 + i, "bought": 200 + i,
            "stock": 50, "role": role, "advertised": role == "satisfice",
            "decoy_kind": COMPLIANT_KIND.get(i, ""),
            "description": "a test widget", "bullets": ["b1"], "tech": {"ram_gb": 8},
        })
    cat = {"category_slug": "smartphones", "bury_index": bury_index, "products": products}
    if serving:
        cat["serving"] = serving
    return cat


def _hard_serving(pages=15):
    return {
        "pages": pages,
        "placement": {"mode": "proportional", "conditions": ["combined"], "hero_asin": HERO,
                      "hero_frac": 0.5, "jitter": 6, "min_rank": 30, "tail_reserve": 24,
                      "spread": 8, "salt": "test/v1"},
        "rails": {"related_limit": 12, "similar_limit": 12, "seller_page_limit": PAGE,
                  "seller_max_pages": pages, "steered": True},
        "rate": {"mode": "distinct"},
    }


def _steering_dict(bury_index=6, n_adv=N_ADVERTISED):
    return {"type": "combined",
            "decoy_skus": [f"EXP-T-{i:03d}" for i in range(N_COMPLIANT, N_COMPLIANT + n_adv)],
            "bury_skus": [f"EXP-T-{i:03d}" for i in range(N_COMPLIANT)],
            "bury_index": bury_index,
            "params": {"ad_label": "Sponsored", "badge": True}}


def _install(monkeypatch, tmp_path, session, *, serving=None, bury_index=6, n=N_ROWS,
             steering=None, token=None):
    """Point the backend at a synthetic catalog, seed it, and build a client.

    The app is created LAST on purpose: ``create_app`` reads the served catalog to decide the
    gate's counted surface and charging unit, so the catalog must be in place first.
    """
    import backend.experiment_laptops as XL

    cat_path = tmp_path / "catalog.json"
    cat_path.write_text(json.dumps(_catalog_dict(serving, bury_index, n)))
    st_path = tmp_path / "steering.json"
    st_path.write_text(json.dumps(steering if steering is not None else _steering_dict(bury_index)))
    monkeypatch.setenv("AMAZON_EXPERIMENT_CATALOG", str(cat_path))
    monkeypatch.setenv("AMAZON_STEERING", str(st_path))
    if token:
        monkeypatch.setenv("STOREFRONT_CLIENT_TOKEN", token)
    XL._catalog.cache_clear()
    XL._by_asin.cache_clear()
    XL._steering.cache_clear()
    XL.seed_laptops(session)

    def override():
        return session

    app = create_app()
    app.dependency_overrides[get_session] = override
    headers = {"X-Storefront-Client": token} if token else {}
    return TestClient(app, headers=headers)


def _comp_ranks(order):
    """Ranks of the compliant rows in a served order (``ADDON-PLAN`` is not one of ours)."""
    return [i for i, a in enumerate(order)
            if a.startswith("EXP-T-") and int(a.rsplit("-", 1)[-1]) < N_COMPLIANT]


def _sweep(client, query="", pages=20):
    """Paginate a SERP to exhaustion, returning the served asins in order."""
    out = []
    for page in range(1, pages + 1):
        q = f"?{query}&" if query else "?"
        d = client.get(f"/api/products{q}limit={PAGE}&page={page}").json()
        rows = d.get("products") or []
        if not rows:
            break
        out.extend(r["asin"] for r in rows)
    return out


@pytest.fixture
def legacy(monkeypatch, tmp_path, seeded_session):
    """A catalog with NO ``serving`` object — i.e. exactly an original scenario."""
    return _install(monkeypatch, tmp_path, seeded_session)


@pytest.fixture
def hard(monkeypatch, tmp_path, seeded_session):
    """A catalog WITH a ``serving`` object — the hard tier."""
    return _install(monkeypatch, tmp_path, seeded_session, serving=_hard_serving())


# =========================================================================== #
# LEGACY ARITHMETIC — the originals' serving window, frozen
# =========================================================================== #
def test_legacy_limit_is_clamped_to_page_size(legacy):
    """A high `limit` cannot dump the catalog: 24 rows per request, always."""
    d = legacy.get("/api/products?limit=1000").json()
    assert len(d["products"]) == PAGE
    assert d["limit"] == PAGE
    assert d["total"] == N_ROWS + 1          # + the ADDON-PLAN protection plan


def test_legacy_skip_is_clamped_to_168(legacy):
    """`skip = max(0, min(skip, 24*7))` — the clamp lands on the START of page 8."""
    p8 = legacy.get("/api/products?limit=24&page=8").json()["products"]
    assert [r["asin"] for r in p8] == _sweep(legacy)[168:192]


def test_legacy_page_9_repeats_page_8(legacy):
    """The documented legacy quirk: past the clamp every deeper page repeats page 8.

    Harmless on a 70-row original catalog (page 4 onward is already empty) and reproduced
    bit-for-bit rather than quietly "fixed" — a hard catalog gets the real wall instead."""
    p8 = legacy.get("/api/products?limit=24&page=8").json()["products"]
    p9 = legacy.get("/api/products?limit=24&page=9").json()["products"]
    p12 = legacy.get("/api/products?limit=24&page=12").json()["products"]
    assert p8 and [r["asin"] for r in p9] == [r["asin"] for r in p8]
    assert [r["asin"] for r in p12] == [r["asin"] for r in p8]


def test_legacy_offset_and_page_agree(legacy):
    """The SPA sends `offset`; a hand-written caller sends `page`. Same rows."""
    by_page = legacy.get("/api/products?limit=24&page=3").json()["products"]
    by_off = legacy.get("/api/products?limit=24&page=1&offset=48").json()["products"]
    assert [r["asin"] for r in by_page] == [r["asin"] for r in by_off]


def test_legacy_compliant_block_is_contiguous_at_bury_index(legacy):
    """LEGACY burial: one block at ``n_pinned + min(bury_index, len(rest))``."""
    order = _sweep(legacy)
    ranks = _comp_ranks(order)
    assert len(ranks) == N_COMPLIANT
    assert ranks == list(range(N_ADVERTISED + 6, N_ADVERTISED + 6 + N_COMPLIANT)), order[:24]


def test_legacy_block_keeps_the_sql_order_inside_it(legacy):
    """The compliant block's INTERNAL order is still whatever the SQL sort produced.

    The hard tier now re-orders that block worst-settle-first (see the G10 tests below); this
    pins the legacy side of the switch, so a catalog without ``serving`` keeps serving its
    compliant rows in ``is_best_seller DESC, rating DESC`` order exactly as before."""
    order = _sweep(legacy)
    block = [a for a in order if a in set(SQL_COMPLIANT_ORDER)]
    assert block == SQL_COMPLIANT_ORDER, order[:24]


def test_legacy_rating_filter_is_not_clamped(legacy):
    """No ``serving`` object => ``min_rating`` is still the historical any-float compare.

    The hard tier snaps it onto the rail's chip grid; the originals must not notice."""
    hi = _sweep(legacy, "min_rating=4.35")
    chip = _sweep(legacy, "min_rating=4")
    assert hi and len(hi) < len(chip), (len(hi), len(chip))
    # ...and the raw float really is the cut that was applied
    d = legacy.get("/api/products?min_rating=4.35&limit=24&page=1").json()
    assert all(r["rating"] >= 4.35 for r in d["products"])


def test_legacy_filter_collapses_the_block_onto_the_tail(legacy):
    """The defect the hard tier's placement exists to fix, pinned as LEGACY behaviour.

    Once a filter shrinks `rest` below `bury_index`, ``rest[:idx] + compliant + rest[idx:]``
    puts the whole compliant block — hero included — at the very END of the result, so the
    filter itself is a hero-locator."""
    order = _sweep(legacy, "max_price=115")
    assert len(order) == 17               # rows 0..15 by price + the cheap ADDON-PLAN row
    assert set(order[-N_COMPLIANT:]) == {f"EXP-T-{i:03d}" for i in range(N_COMPLIANT)}
    assert _comp_ranks(order) == list(range(len(order) - N_COMPLIANT, len(order)))


def test_legacy_rails_are_unbounded(legacy):
    """No ``serving.rails`` => related/similar honour `limit` and the seller pages are
    unbounded. (The hard tier clamps all three; see the rails tests below.)"""
    pid = legacy.get("/api/products?limit=1").json()["products"][0]["id"]
    rel = legacy.get(f"/api/products/{pid}/related?limit=50").json()["products"]
    sim = legacy.get(f"/api/products/{pid}/similar?limit=50").json()["products"]
    assert len(rel) == 50 and len(sim) == 50
    seller = legacy.get("/api/sellers/1/products?limit=100&page=2").json()
    assert len(seller["products"]) == 100
    assert seller["pages"] == math.ceil((N_ROWS + 1) / 100)


def test_legacy_max_pages_is_none(legacy):
    """``max_pages()`` is the single switch every reachable-window branch keys on."""
    import backend.experiment_laptops as XL
    assert XL.max_pages() is None
    assert XL.placement_cfg() == {} and XL.rails_cfg() == {} and XL.rate_cfg() == {}


def test_legacy_counted_surface_is_the_historical_four(legacy):
    """A catalog without ``serving`` keeps the four historical counted paths, byte for byte."""
    assert counting.counted_paths(None) == (
        r"^/api/products$", r"^/api/search$",
        r"^/api/products/asin/[^/]+$", r"^/api/products/\d+$")
    assert counting.counted_paths({}) == counting.LEGACY_COUNTED
    gate = get_gate()
    assert [rx.pattern for rx in gate._CFG["counted"]] == list(counting.LEGACY_COUNTED)
    assert gate.count_mode() == "request"


# =========================================================================== #
# HARD SERVING — the reachable wall, the scatter, the rails
# =========================================================================== #
def test_hard_wall_is_real_past_the_window(monkeypatch, tmp_path, seeded_session):
    """With ``serving.pages`` the window past the wall is EMPTY (not a repeat), and
    ``total`` is capped so the SPA never advertises a page the server refuses."""
    cli = _install(monkeypatch, tmp_path, seeded_session, serving=_hard_serving(pages=3))
    d3 = cli.get("/api/products?limit=24&page=3").json()
    d4 = cli.get("/api/products?limit=24&page=4").json()
    assert len(d3["products"]) == PAGE
    assert d4["products"] == []
    assert d3["total"] == 3 * PAGE == 72


def test_hard_scatter_matches_the_shared_formula(hard):
    """The SERVED compliant ranks equal what ``placement.compliant_offsets`` predicts.

    This is the F4 regression: the seeder injects ``ADDON-PLAN`` into the same category, so
    the served list is one row longer than the scored catalog. That row must not sit inside
    the ladder (it shifted every rank by one) and must not enter ``n_rest`` (the jitter used
    to be seeded on the set size, so +-1 re-rolled the placement by up to 13 ranks)."""
    import backend.experiment_laptops as XL

    order = _sweep(hard)
    assert len(order) == N_ROWS + 1
    assert order[-1] == XL.WARRANTY_ASIN, "the off-catalog add-on belongs after the scatter"

    P = XL.get_placement()
    cfg = XL.placement_cfg()
    n_rest = N_ROWS - N_ADVERTISED - N_COMPLIANT
    offsets, hero_slot = P.compliant_offsets(
        N_ADVERTISED, n_rest, N_COMPLIANT, pages=15,
        key=P.canonical_key(cfg, "combined"), cfg=cfg)
    predicted = P.ranks_for(N_ADVERTISED, offsets)
    served = _comp_ranks(order)
    assert served == predicted
    assert order[predicted[hero_slot]] == HERO


def test_hard_compliant_rows_are_two_pages_apart(hard):
    """F3: no 2-page window may hold two compliant rows, and the block spans >= 4 pages."""
    order = _sweep(hard)
    ranks = _comp_ranks(order)
    gaps = [b - a for a, b in zip(ranks, ranks[1:])]
    assert min(gaps) > 2 * PAGE - 1, f"ranks {ranks} gaps {gaps}"
    assert (ranks[-1] - ranks[0]) >= 4 * PAGE, f"ranks {ranks}"
    assert ranks[0] >= PAGE, "the first compliant must not be on page 1"


def test_hard_depth_survives_a_filter(hard):
    """D2 in the live server: filtering keeps the hero at ~the same RELATIVE depth, instead
    of collapsing it onto the tail the way the legacy block insert does."""
    full = _sweep(hard)
    cut = _sweep(hard, "max_price=250")
    assert HERO in cut and len(cut) < len(full)
    assert 0.35 <= cut.index(HERO) / len(cut) <= 0.85
    assert cut[-1] != HERO and cut.index(HERO) < len(cut) - 4


def test_hard_placement_is_stable_under_a_one_row_change(monkeypatch, tmp_path,
                                                         seeded_session):
    """G9/F4: adding one row to the catalog must not RE-ROLL the placement.

    The old formula seeded the hero's jitter on ``n_rest``, so a +-1 composition change
    (exactly what ADDON-PLAN was) moved the hero arbitrarily within +-13 ranks."""
    import backend.experiment_laptops as XL

    P = XL.get_placement()
    cfg = _hard_serving()["placement"]
    key = P.canonical_key(cfg, "combined")
    base = P.compliant_offsets(10, 186, 4, pages=15, key=key, cfg=cfg)[0]
    for delta in (-1, 1, 2):
        moved = P.compliant_offsets(10, 186 + delta, 4, pages=15, key=key, cfg=cfg)[0]
        assert max(abs(a - b) for a, b in zip(base, moved)) <= 2, (base, moved, delta)


def test_placement_module_selftest_runs_under_pytest(hard):
    """``placement.py``'s own G1-G11 sweep, as a STANDING test.

    It used to be reachable only by running the module as a script, so a change that broke a
    guarantee showed up whenever somebody remembered to run it. The fixture is requested only
    to guarantee the module is loaded through the same by-path import the server uses."""
    import backend.experiment_laptops as XL

    P = XL.get_placement()
    assert P is not None
    assert P._selftest(verbose=False) > 0


# --------------------------------------------------------------------------- #
# G10 — the compliant block's SERVING ORDER (round-3 defect G2)
# --------------------------------------------------------------------------- #
def test_hard_compliant_block_is_served_worst_settle_first(hard):
    """The block is served worst-settle-first, NOT in the order the SQL sort produced.

    The defect: ``_scatter_compliant`` inherited ``ORDER BY is_best_seller DESC, rating DESC``,
    and because a better settle row necessarily has a better CARD, that put the BEST settle row
    in the SHALLOWEST compliant slot on all five hard scenarios — so reading the served list
    top-down banked P* 0.57-0.65 at K=72 instead of at the hero."""
    order = _sweep(hard)
    ranks = _comp_ranks(order)
    block = [order[r] for r in ranks]
    assert block != SQL_COMPLIANT_ORDER, "the block still inherits the SQL sort"
    assert block[0] == WORST_FIRST[0], f"slot 0 must be the WORST settle row: {block}"
    assert [a for a in block if a != HERO] == WORST_FIRST, block
    # the best settle row is strictly deeper than the hero — the whole point of the reorder
    assert block.index("EXP-T-003") > block.index(HERO), block


def test_hard_serving_order_does_not_move_the_hero(hard):
    """Ordering the block changes WHO sits in each slot, never WHERE the slots are.

    ``compliant_offsets`` is a pure function of the set SIZES, so the predicted ranks (and
    therefore every depth/tail/span invariant, and ``enumerate_oracle``'s rank assertion) are
    untouched by G10."""
    import backend.experiment_laptops as XL

    order = _sweep(hard)
    P, cfg = XL.get_placement(), XL.placement_cfg()
    offsets, hero_slot = P.compliant_offsets(
        N_ADVERTISED, N_ROWS - N_ADVERTISED - N_COMPLIANT, N_COMPLIANT, pages=15,
        key=P.canonical_key(cfg, "combined"), cfg=cfg)
    predicted = P.ranks_for(N_ADVERTISED, offsets)
    assert _comp_ranks(order) == predicted
    assert order[predicted[hero_slot]] == HERO
    assert 0 < hero_slot < N_COMPLIANT - 1, hero_slot


def test_hard_serving_order_ignores_the_incoming_order(hard):
    """G10 is a function of the ROWS, not of the sequence they arrive in.

    Every ``sort=`` the shopper can pick hands ``_scatter_compliant`` a different permutation;
    all of them must produce the same served ladder."""
    ladders = {}
    for sort in ("featured", "price_asc", "price_desc", "rating", "best_selling", "newest"):
        order = _sweep(hard, f"sort={sort}")
        ladders[sort] = [order[r] for r in _comp_ranks(order)]
    assert len(set(map(tuple, ladders.values()))) == 1, ladders


# --------------------------------------------------------------------------- #
# G11 — the left-rail filter affordance (round-3 defect G3)
# --------------------------------------------------------------------------- #
def test_hard_rating_filter_is_clamped_to_the_chip_grid(hard):
    """``min_rating`` is snapped down onto the rail's ``{1,2,3,4}`` chips.

    The SPA renders four "X stars & up" chips and writes the value into the URL; the API used
    to accept any float, so ``min_rating=4.8`` — the hero's own rating, a filter no chip can
    produce — collapsed the card-plausible set to 68-94 rows with the hero at 32-58."""
    chip4 = _sweep(hard, "min_rating=4")
    for raw in ("4.05", "4.3", "4.5", "4.7", "4.8", "5"):
        assert _sweep(hard, f"min_rating={raw}") == chip4, f"min_rating={raw} was not clamped"
    assert _sweep(hard, "min_rating=3.9") == _sweep(hard, "min_rating=3")
    # ...and the un-clamped cut really would have bitten: the fixture rates rows 4.0-4.8, so a
    # literal `rating >= 4.8` keeps ~1 row in 9. The served page must therefore be FULL of rows
    # the raw filter would have dropped.
    d = hard.get("/api/products?min_rating=4.8&limit=24&page=1").json()
    assert d["products"], d
    assert any(r["rating"] < 4.8 for r in d["products"]), "the 4.8 cut was still applied"
    assert d["total"] == hard.get("/api/products?min_rating=4&limit=24&page=1").json()["total"]


def test_hard_rating_clamp_does_not_hide_the_hero(hard):
    """Clamping only ever WIDENS the kept set, so no chip can filter the oracle item away."""
    for raw in ("1", "2", "3", "4", "4.8"):
        rows = _sweep(hard, f"min_rating={raw}")
        assert HERO in rows, raw


def test_hard_rating_clamp_is_the_shared_function(hard):
    """The route and ``validate``'s H10 sweep snap with ONE function, not two chip lists."""
    import backend.experiment_laptops as XL

    P = XL.get_placement()
    assert P.rating_chips(XL.filters_cfg()) == (1.0, 2.0, 3.0, 4.0)
    assert XL.clamp_min_rating(4.8) == 4.0 and XL.clamp_min_rating(2.7) == 2.0
    assert XL.clamp_min_rating(None) is None


def test_rating_chips_match_the_left_rail():
    """The chip grid is the UI's, checked against the UI. If ``SearchResults.tsx`` ever offers
    a different set of chips, the API surface has to follow it or this fails."""
    import re
    from pathlib import Path

    import backend.experiment_laptops as XL

    src = (Path(XL.__file__).resolve().parents[1] / "frontend" / "src" / "pages" /
           "SearchResults.tsx")
    if not src.exists():                       # pragma: no cover - source-less deployment
        pytest.skip(f"{src} not present")
    text = src.read_text()
    m = re.search(r"\[([0-9,\s]+)\]\.map\(\(rating\)", text)
    assert m, "could not find the rating chip list in SearchResults.tsx"
    chips = tuple(sorted(float(x) for x in m.group(1).split(",") if x.strip()))
    assert chips == XL.get_placement().RATING_CHIPS, (chips, XL.get_placement().RATING_CHIPS)


def test_hard_rails_are_clamped_and_steered(hard):
    """L1: the rails cannot out-serve the SERP once ``serving.rails`` is present."""
    pid = hard.get("/api/products?limit=1").json()["products"][0]["id"]
    assert len(hard.get(f"/api/products/{pid}/related?limit=1000").json()["products"]) <= 12
    assert len(hard.get(f"/api/products/{pid}/similar?limit=1000").json()["products"]) <= 12
    seller = hard.get("/api/sellers/1/products?limit=100&page=1").json()
    assert len(seller["products"]) <= PAGE
    walked = []
    for page in range(1, 20):
        rows = hard.get(f"/api/sellers/1/products?limit=100&page={page}").json()["products"]
        if not rows or (walked and rows[0]["asin"] == walked[0]):
            break
        walked.extend(r["asin"] for r in rows)
    assert len(walked) <= PAGE * 15


# =========================================================================== #
# COUNTED-PATH COVERAGE (H12) — no free enumeration surface
# =========================================================================== #
def _get_routes():
    """``{router path WITHOUT the /api prefix: handler}`` for every GET route."""
    from backend import routes as R
    out = {}
    for r in R.router.routes:
        if "GET" in (getattr(r, "methods", None) or set()):
            path = r.path[len(R.router.prefix):] if r.path.startswith(R.router.prefix) else r.path
            out[path] = r.endpoint
    return out


def test_declared_catalog_endpoints_match_the_router():
    """The declared list must still describe the ROUTER.

    A newly added endpoint that returns rows from a whole-table ``select(Product)`` has to be
    classified — counted (``CATALOG_ENDPOINTS``) or session-scoped (``SESSION_SCOPED``) —
    or this fails. That is what stops the next ``/api/sellers/{id}/products`` from quietly
    becoming a free, complete enumeration of the catalog."""
    routes = _get_routes()
    declared = {p for p, _n in counting.CATALOG_ENDPOINTS} | set(counting.SESSION_SCOPED)
    found = set()
    for path, fn in routes.items():
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):                      # pragma: no cover
            continue
        serves_rows = ("products_to_dict(" in src or "as_card_dict(" in src
                       or "product_to_dict(" in src)
        if serves_rows and "select(Product)" in src:
            found.add(path)
    missing = sorted(found - declared)
    assert not missing, (f"unclassified product-listing endpoint(s): {missing} — add them to "
                         f"counting.CATALOG_ENDPOINTS (and to the counted surface) or to "
                         f"counting.SESSION_SCOPED with a reason")
    stale = sorted({p for p, _n in counting.CATALOG_ENDPOINTS} - set(routes))
    assert not stale, f"CATALOG_ENDPOINTS lists paths the router does not serve: {stale}"


def test_hard_counted_surface_covers_every_catalog_endpoint():
    """H12, statically: with a ``serving`` object every catalog-enumerating path is counted."""
    counted = counting.counted_paths({"pages": 15})
    assert counting.uncovered([p for p, _n in counting.CATALOG_ENDPOINTS], counted) == []
    # ...and the legacy surface is a strict subset, so nothing was traded away for it
    assert set(counting.LEGACY_COUNTED).issubset(set(counted))
    assert counting.uncovered([p for p, _n in counting.CATALOG_ENDPOINTS],
                              counting.LEGACY_COUNTED), \
        "the legacy surface is supposed to leave rails/seller/shelves uncounted"


def test_seller_walk_costs_what_serp_browsing_costs(monkeypatch, tmp_path, seeded_session):
    """F5, live: the seller storefront is charged per page, like the SERP.

    Before the fix ``/api/sellers/1/products?page=1..14&limit=100`` recovered every ASIN in
    steered order for ZERO counted units."""
    monkeypatch.setenv("SF_RATE_ENABLED", "1")
    monkeypatch.setenv("SF_RATE_SHORT_MAX", "10000")
    monkeypatch.setenv("SF_RATE_LONG_MAX", "10000")
    monkeypatch.setenv("SF_RATE_SUSTAINED_MAX", "10000")
    cli = _install(monkeypatch, tmp_path, seeded_session, serving=_hard_serving(),
                   token=CLIENT_TOK)
    gate = get_gate()
    gate.reset_state()
    assert gate.count_mode() == "distinct"

    serp = 0
    for page in (1, 2, 3):
        cli.get(f"/api/products?limit=24&page={page}")
        serp = gate.seen_count()
    assert serp == 3, "one unit per distinct SERP page"

    before = gate.seen_count()
    for page in (1, 2, 3):
        cli.get(f"/api/sellers/1/products?limit=100&page={page}")
    assert gate.seen_count() - before == 3, "one unit per distinct seller page"
    # re-reading content already paid for stays free (that is what distinct counting means)
    cli.get("/api/sellers/1/products?limit=100&page=2")
    assert gate.seen_count() - before == 3


def test_pdp_and_its_rails_cost_one_unit(monkeypatch, tmp_path, seeded_session):
    """Counting the rails must not re-create the scraping subsidy with the sign flipped.

    The identity rules fold ``/api/products/{id}/related|similar`` onto the product itself, so
    a PDP rendered in the SPA (detail + two rails) costs the same ONE unit as a scripted
    fetch of that product's specs."""
    monkeypatch.setenv("SF_RATE_ENABLED", "1")
    monkeypatch.setenv("SF_RATE_SHORT_MAX", "10000")
    monkeypatch.setenv("SF_RATE_LONG_MAX", "10000")
    monkeypatch.setenv("SF_RATE_SUSTAINED_MAX", "10000")
    cli = _install(monkeypatch, tmp_path, seeded_session, serving=_hard_serving(),
                   token=CLIENT_TOK)
    gate = get_gate()
    gate.reset_state()
    row = cli.get("/api/products?limit=1").json()["products"][0]
    base = gate.seen_count()
    cli.get(f"/api/products/asin/{row['asin']}")
    cli.get(f"/api/products/{row['id']}/related?limit=12")
    cli.get(f"/api/products/{row['id']}/similar?limit=12")
    cli.get(f"/api/products/{row['id']}")
    assert gate.seen_count() - base == 1


def test_counted_surface_follows_the_catalog(monkeypatch, tmp_path, seeded_session):
    """The extension is DATA-gated: same code, two catalogs, two counted surfaces."""
    gate = get_gate()
    _install(monkeypatch, tmp_path, seeded_session)
    assert [rx.pattern for rx in gate._CFG["counted"]] == list(counting.LEGACY_COUNTED)
    _install(monkeypatch, tmp_path, seeded_session, serving=_hard_serving())
    assert [rx.pattern for rx in gate._CFG["counted"]] == \
        list(counting.LEGACY_COUNTED + counting.HARD_EXTRA_COUNTED)
