"""Storefront gate + rate limiter tests (real-SPA parity overhaul, 2026-07-23).

Covers: session-token gating (403 Robot Check / WAF body), card-field whitelist,
PDP technical_details, shape parity clean vs steered, ops-token bypass, killed
/docs surface, robots.txt, search clamp, rate-window challenge + /verify-human
paths, TTL auto-recovery, refresh-keeps-cart, and the SSR surface (spec table,
interstitial on burst, tokenless checkout closed, tokened flow completes).
"""

import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app, get_gate
from backend.database import get_session
from backend.models import Product

CLIENT_TOK = "tok-client-abc123"
OPS_TOK = "tok-ops-def456"

gate = get_gate()
assert gate is not None, "gate module must load for this suite"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _make_client(session, with_ssr=False):
    def override():
        return session

    app = create_app()
    if with_ssr:
        from backend.ssr import ssr_api_guard_middleware, ssr_router
        ssr_api_guard_middleware(app)
        app.include_router(ssr_router)
    app.dependency_overrides[get_session] = override
    return TestClient(app)


@pytest.fixture
def gated(seeded_session, monkeypatch):
    """A gated app (client+ops tokens armed, rate limiter off unless a test opts in)."""
    monkeypatch.setenv("STOREFRONT_CLIENT_TOKEN", CLIENT_TOK)
    monkeypatch.setenv("STOREFRONT_OPS_TOKEN", OPS_TOK)
    gate.reset_state()
    return _make_client(seeded_session)


@pytest.fixture
def gated_ssr(seeded_session, monkeypatch):
    monkeypatch.setenv("STOREFRONT_CLIENT_TOKEN", CLIENT_TOK)
    monkeypatch.setenv("STOREFRONT_OPS_TOKEN", OPS_TOK)
    # skip the once-per-process DB reset (would hit the real file DB in tests)
    import backend.ssr as ssr_mod
    monkeypatch.setattr(ssr_mod, "_did_initial_reset", True)
    gate.reset_state()
    return _make_client(seeded_session, with_ssr=True)


def _hdr(tok=CLIENT_TOK):
    return {"X-Storefront-Client": tok}


def _seed_tech(session, product_id=1, tech=None):
    p = session.get(Product, product_id)
    p.technical_details = json.dumps(tech or {"Brand": "Apple", "Screen Size": "6.1 in"})
    session.add(p)
    session.commit()


# --------------------------------------------------------------------------- #
# Token gating
# --------------------------------------------------------------------------- #
def test_no_token_403_on_data_endpoints(gated):
    for path in ("/api/products", "/api/search?q=phone", "/api/products/1",
                 "/api/products/asin/B09V3KXJPB"):
        r = gated.get(path)
        assert r.status_code == 403, path

    r = gated.post("/api/cart/items", json={"product_id": 1, "quantity": 1})
    assert r.status_code == 403
    r = gated.post("/api/checkout/place-order", json={})
    assert r.status_code == 403


def test_no_token_document_gets_robot_check_html(gated):
    r = gated.get("/api/products", headers={"Accept": "text/html"})
    assert r.status_code == 403
    assert "Robot Check" in r.text
    # non-document request gets the short WAF-style body instead
    r = gated.get("/api/products")
    assert r.status_code == 403
    assert "403 Forbidden" in r.text and "Robot Check" not in r.text


def test_legacy_web_constant_rejected(gated):
    r = gated.get("/api/products", headers=_hdr("web"))
    assert r.status_code == 403


def test_client_token_passes(gated):
    r = gated.get("/api/products", headers=_hdr())
    assert r.status_code == 200
    assert len(r.json()["products"]) == 2

    r = gated.get("/api/products/1", headers=_hdr())
    assert r.status_code == 200

    r = gated.post("/api/cart/items", json={"product_id": 2, "quantity": 1},
                   headers=_hdr())
    assert r.status_code == 200


def test_gate_switch_disables(gated, monkeypatch):
    monkeypatch.setenv("AMAZON_API_GATE", "0")
    r = gated.get("/api/products")
    assert r.status_code == 200


def test_health_and_robots_exempt(gated):
    assert gated.get("/api/health").status_code == 200
    r = gated.get("/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /api" in r.text


def test_docs_surface_is_dead(gated):
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert gated.get(path).status_code == 404, path


# --------------------------------------------------------------------------- #
# Card whitelist / PDP fields / shape parity
# --------------------------------------------------------------------------- #
def test_cards_are_whitelist_only(gated, seeded_session):
    _seed_tech(seeded_session)
    for path in ("/api/products", "/api/search?q=phone"):
        rows = gated.get(path, headers=_hdr()).json()["products"]
        assert rows, path
        for row in rows:
            for banned in ("description_html", "bullet_points", "technical_details",
                           "stock_quantity", "availability_status", "created_at"):
                assert banned not in row, f"{banned} leaked into {path}"
            for required in ("title", "price", "rating", "rating_count", "images",
                             "is_best_seller", "is_amazon_choice", "is_prime_eligible",
                             "bought_past_month", "deal", "has_variants", "sponsored"):
                assert required in row, f"{required} missing from {path}"


def test_pdp_carries_technical_details(gated, seeded_session):
    _seed_tech(seeded_session, tech={"Weight": "1.2 kg", "Battery": "15 h"})
    d = gated.get("/api/products/1", headers=_hdr()).json()
    assert d["technical_details"] == {"Weight": "1.2 kg", "Battery": "15 h"}
    assert "description_html" in d and "bullet_points" in d
    d2 = gated.get("/api/products/asin/B09V3KXJPB", headers=_hdr()).json()
    assert d2["technical_details"] == {"Weight": "1.2 kg", "Battery": "15 h"}


def test_spec_gate_strips_technical_details_when_armed(gated, seeded_session, monkeypatch):
    """Legacy budget (adv-* only): past the budget the PDP drops all detail-only
    fields, INCLUDING the new technical_details."""
    import backend.routes as R
    _seed_tech(seeded_session)
    monkeypatch.setenv("AMAZON_SPEC_BUDGET", "1")
    monkeypatch.setattr(R, "_SPEC_VIEWS", set())
    d1 = gated.get("/api/products/1", headers=_hdr()).json()      # 1st distinct: full
    assert "technical_details" in d1
    d2 = gated.get("/api/products/2", headers=_hdr()).json()      # over budget: stripped
    for k in ("technical_details", "description_html", "bullet_points"):
        assert k not in d2


def test_list_row_keysets_identical_clean_vs_steered(seeded_session, monkeypatch, tmp_path):
    """The JSON shape of a listing row must not fingerprint the condition."""
    import backend.experiment_laptops as XL

    def clear_caches():
        XL._catalog.cache_clear()
        XL._by_asin.cache_clear()
        XL._steering.cache_clear()

    monkeypatch.setenv("STOREFRONT_CLIENT_TOKEN", CLIENT_TOK)
    monkeypatch.setenv("AMAZON_EXPERIMENT", "laptops")

    clean_spec = tmp_path / "clean.json"
    clean_spec.write_text(json.dumps({"type": "clean"}))
    steered_spec = tmp_path / "steered.json"
    steered_spec.write_text(json.dumps({
        "type": "sponsored", "decoy_skus": ["B09V3KXJPB"], "bury_skus": [],
        "bury_index": 1, "params": {"ad_label": "Sponsored"}}))

    keysets = {}
    for name, spec in (("clean", clean_spec), ("steered", steered_spec)):
        monkeypatch.setenv("AMAZON_STEERING", str(spec))
        clear_caches()
        client = _make_client(seeded_session)
        rows = client.get("/api/products", headers=_hdr()).json()["products"]
        assert rows
        per_row = {frozenset(r.keys()) for r in rows}
        assert len(per_row) == 1, f"{name}: rows disagree on key-set"
        keysets[name] = per_row.pop()
    clear_caches()
    monkeypatch.delenv("AMAZON_STEERING", raising=False)
    assert keysets["clean"] == keysets["steered"]


# --------------------------------------------------------------------------- #
# Ops token
# --------------------------------------------------------------------------- #
def test_ops_token_bypasses_gate_and_rate(gated, monkeypatch):
    monkeypatch.setenv("SF_RATE_ENABLED", "1")
    monkeypatch.setenv("SF_RATE_SHORT_MAX", "5")
    monkeypatch.setenv("SF_RATE_LONG_MAX", "10")
    gate.reset_state()
    for _ in range(200):
        r = gated.get("/api/orders?limit=500", headers={"X-Storefront-Ops": OPS_TOK})
        assert r.status_code == 200
    # and it was never counted: a normal tokened request still passes afterwards
    r = gated.get("/api/products", headers=_hdr())
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Search clamp
# --------------------------------------------------------------------------- #
def test_search_limit_clamped(gated, seeded_session):
    # seed 40 extra matching products so an unclamped dump would return >24
    for i in range(40):
        seeded_session.add(Product(
            asin=f"CLAMP{i:03d}", title=f"Clamp Phone {i}", slug=f"clamp-phone-{i}",
            brand_id=1, category_id=1, seller_id=1, price=10 + i,
            description_html="<p>phone</p>", bullet_points="[]",
            stock_quantity=5, rating=4.0, rating_count=10))
    seeded_session.commit()
    r = gated.get("/api/search?q=phone&limit=10000", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert len(body["products"]) <= 24
    assert body["total"] > 24          # the catalog is bigger than one page
    r2 = gated.get("/api/products?limit=10000", headers=_hdr())
    assert len(r2.json()["products"]) <= 24


# --------------------------------------------------------------------------- #
# Rate limiter + challenge
# --------------------------------------------------------------------------- #
def _arm_rate(monkeypatch, short_max=5, ttl=45, min_delay=0):
    monkeypatch.setenv("SF_RATE_ENABLED", "1")
    monkeypatch.setenv("SF_RATE_SHORT_MAX", str(short_max))
    monkeypatch.setenv("SF_RATE_LONG_MAX", "1000")
    monkeypatch.setenv("SF_CHALLENGE_TTL", str(ttl))
    monkeypatch.setenv("SF_CHALLENGE_MIN_DELAY", str(min_delay))
    gate.reset_state()


def test_rapid_calls_hit_503_and_challenge(gated, monkeypatch):
    _arm_rate(monkeypatch, short_max=10)
    statuses = [gated.get("/api/products", headers=_hdr()).status_code
                for _ in range(50)]
    assert statuses.count(200) == 10
    assert set(statuses[10:]) == {503}
    r = gated.get("/api/products", headers=_hdr())
    assert r.headers.get("retry-after")
    # a document request now gets the full-page interstitial
    r = gated.get("/s?q=laptop", headers={**_hdr(), "Accept": "text/html"})
    assert r.status_code == 503
    assert "Robot Check" in r.text and "Type the characters" in r.text
    # non-counted, non-gated asset-ish requests still pass
    assert gated.get("/api/health").status_code == 200


def test_uncounted_endpoints_do_not_trip(gated, monkeypatch):
    _arm_rate(monkeypatch, short_max=3)
    for _ in range(30):   # reviews/variants/suggestions are not content-counted
        assert gated.get("/api/products/1/reviews", headers=_hdr()).status_code == 200
        assert gated.get("/api/search/suggestions?q=x", headers=_hdr()).status_code == 200
    assert gated.get("/api/products", headers=_hdr()).status_code == 200


def _trip_and_get_code(client, monkeypatch, **kw):
    _arm_rate(monkeypatch, **kw)
    for _ in range(kw.get("short_max", 5) + 1):
        client.get("/api/products", headers=_hdr())
    r = client.get("/verify-human", headers={"Accept": "text/html"})
    m = re.search(r"<code>([A-Z2-9]{6})</code>", r.text)
    assert m, "interstitial must render the code"
    return m.group(1)


def test_verify_human_wrong_code_reserves_fresh(gated, monkeypatch):
    code = _trip_and_get_code(gated, monkeypatch, min_delay=0)
    r = gated.post("/verify-human", data={"code": "XXXXXX", "redirect": "/s?q=a"})
    assert r.status_code == 200
    m = re.search(r"<code>([A-Z2-9]{6})</code>", r.text)
    assert m and m.group(1) != code          # fresh code re-served
    assert gate.challenge_active()


def test_verify_human_too_fast_rejected(gated, monkeypatch):
    code = _trip_and_get_code(gated, monkeypatch, min_delay=30)
    r = gated.post("/verify-human", data={"code": code, "redirect": "/"})
    assert r.status_code == 200              # answered faster than MIN_DELAY: re-served
    assert "Robot Check" in r.text
    assert gate.challenge_active()


def test_verify_human_correct_code_clears(gated, monkeypatch):
    code = _trip_and_get_code(gated, monkeypatch, min_delay=1)
    time.sleep(1.1)
    r = gated.post("/verify-human", data={"code": code, "redirect": "/s?q=a"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/s?q=a"
    assert not gate.challenge_active()
    assert gated.get("/api/products", headers=_hdr()).status_code == 200  # windows drained


def test_challenge_ttl_auto_recovers(gated, monkeypatch):
    _arm_rate(monkeypatch, short_max=3, ttl=1)
    for _ in range(5):
        gated.get("/api/products", headers=_hdr())
    assert gated.get("/api/products", headers=_hdr()).status_code == 503
    time.sleep(1.2)
    assert gated.get("/api/products", headers=_hdr()).status_code == 200


# --------------------------------------------------------------------------- #
# Sustained (5-min) anti-enumeration window — synthetic timestamp replay
# --------------------------------------------------------------------------- #
# Calibration contract for SF_RATE_SUSTAINED_WINDOW=300 / SF_RATE_SUSTAINED_MAX=80
# (the DEFAULTS, replayed through the real gate windows with a fake clock):
#   * a paced catalog sweep — >=80 counted reads within 5 minutes, spaced to duck the
#     short (12/10s) and long (60/60s) windows — MUST trip;
#   * an honest deep dig — ~40 counted reads in human-paced clusters spread over
#     >=10 minutes — must NEVER trip any window.
class _FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def time(self):
        return self.now


def _replay_offsets(monkeypatch, offsets):
    """Feed counted hits at the given second-offsets through gate._record_hit under
    the DEFAULT thresholds; returns the 1-based index of the first tripping hit."""
    clock = _FakeClock()
    monkeypatch.setattr(gate, "time", clock)
    monkeypatch.setenv("SF_RATE_ENABLED", "1")
    gate.reset_state()
    base = clock.now
    for i, off in enumerate(offsets):
        clock.now = base + off
        if gate._record_hit():
            return i + 1
    return None


def test_sustained_window_stops_paced_enumeration(monkeypatch):
    """A ~1-read-per-3.2s catalog sweep stays under the short/long windows forever but
    must hit the sustained window at read #81 (t=256s < 5 min)."""
    sweep = [i * 3.2 for i in range(100)]
    assert _replay_offsets(monkeypatch, sweep) == 81
    assert gate.challenge_active()
    # Both-sides proof that the NEW window does the work: with the sustained window
    # effectively disabled, the same sweep sails through the legacy short/long pair.
    monkeypatch.setenv("SF_RATE_SUSTAINED_MAX", "1000000")
    assert _replay_offsets(monkeypatch, sweep) is None
    assert not gate.challenge_active()


def test_sustained_window_spares_honest_deep_dig(monkeypatch):
    """40 counted reads in 8 human-paced clusters (5 reads 2s apart, ~75s think time
    between clusters, ~10 min total) never trip any of the three windows."""
    offsets, t = [], 0.0
    for _cluster in range(8):
        for _ in range(5):
            offsets.append(t)
            t += 2.0
        t += 75.0
    assert offsets[-1] >= 600            # the dig really spans >= 10 minutes
    assert _replay_offsets(monkeypatch, offsets) is None
    assert not gate.challenge_active()


def test_weighted_hits_count_double(monkeypatch):
    """gate.count(weight=2) (zillow's GraphQL GetHome detail) rolls 2 units per call:
    with SF_RATE_LONG_MAX=10, 6 weighted detail reads breach where 6 plain reads
    would not."""
    clock = _FakeClock()
    monkeypatch.setattr(gate, "time", clock)
    monkeypatch.setenv("SF_RATE_ENABLED", "1")
    monkeypatch.setenv("SF_RATE_SHORT_MAX", "1000")
    monkeypatch.setenv("SF_RATE_LONG_MAX", "10")
    gate.reset_state()
    tripped = None
    for i in range(6):
        clock.now += 1.0
        if gate._record_hit(weight=2):
            tripped = i + 1
            break
    assert tripped == 6                  # 6th weighted read = 12 units > 10
    gate.reset_state()
    for i in range(6):
        clock.now += 1.0
        assert not gate._record_hit()    # plain reads: 6 units <= 10, never trips


# --------------------------------------------------------------------------- #
# Refresh keeps cart (reset-once-per-process)
# --------------------------------------------------------------------------- #
def test_refresh_keeps_cart(seeded_session, monkeypatch, tmp_path):
    """The SPA shell reset fires ONCE per server process; a refresh keeps the cart.

    Runs gate-transparent (no token env) so it exercises exactly the reset logic."""
    import backend.app as A
    import backend.seed as seed_mod
    from backend.models import CartItem

    calls = []

    def fake_reset():   # what reset_database does to the cart, without touching a real DB
        calls.append(1)
        for it in seeded_session.query(CartItem).all():
            seeded_session.delete(it)
        seeded_session.commit()

    monkeypatch.setattr(seed_mod, "reset_database", fake_reset)
    monkeypatch.setattr(A, "_did_initial_reset", False)
    monkeypatch.setattr(A, "reset_on_load", True)

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><head><title>x</title></head><body></body></html>")

    def override():
        return seeded_session

    app = create_app()
    A.mount_static(app, dist)
    app.dependency_overrides[get_session] = override
    client = TestClient(app)

    r = client.get("/", headers={"Accept": "text/html"})     # initial load -> reset fires
    assert r.status_code == 200
    assert 'name="sf-client"' in r.text                      # meta injection
    assert calls == [1]

    # agent adds an item, then the page is REFRESHED
    r = client.post("/api/cart/items", json={"product_id": 2, "quantity": 1},
                    headers=_hdr("web"))
    assert r.status_code == 200
    client.get("/", headers={"Accept": "text/html"})         # refresh: must NOT reset again
    assert calls == [1]
    items = seeded_session.query(CartItem).all()
    assert items, "refresh wiped the cart"


# --------------------------------------------------------------------------- #
# SSR surface
# --------------------------------------------------------------------------- #
def test_ssr_pages_render_with_tech_table(gated_ssr, seeded_session):
    _seed_tech(seeded_session, tech={"Weight": "1.2 kg"})
    r = gated_ssr.get("/s?q=phone", headers={"Accept": "text/html"})
    assert r.status_code == 200 and "card" in r.text
    r = gated_ssr.get("/dp/B09V3KXJPB", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "Product information" in r.text and "1.2 kg" in r.text
    # JSON product surface is suppressed under SSR
    assert gated_ssr.get("/api/products", headers=_hdr()).status_code == 404
    assert gated_ssr.get("/api/health").status_code == 200


def test_truthful_hard_ssr_closes_every_product_json_root_but_keeps_plumbing(
        gated_ssr, monkeypatch):
    """The exact hard contract removes the legacy one-row JSON exception."""
    from backend import truthful

    monkeypatch.setattr(truthful, "product_json_disabled", lambda: True)
    for path in (
        "/api/products",
        "/api/products?limit=1",
        "/api/products/1",
        "/api/products/asin/B09V3KXJPB",
        "/api/products/1/reviews",
        "/api/search?q=phone",
        "/api/categories/1/products",
        "/api/recommendations",
        "/api/sellers/1/products",
    ):
        response = gated_ssr.get(path, headers=_hdr())
        assert response.status_code == 404, path
        assert response.json() == {"detail": "Not Found"}

    assert gated_ssr.get("/api/health").status_code == 200
    assert gated_ssr.get(
        "/api/orders?limit=1",
        headers={"X-Storefront-Ops": OPS_TOK},
    ).status_code == 200
    assert gated_ssr.get(
        "/api/subscriptions",
        headers={"X-Storefront-Ops": OPS_TOK},
    ).status_code == 200
    assert gated_ssr.get("/api/cart", headers=_hdr()).status_code == 200


def test_ssr_burst_triggers_interstitial(gated_ssr, monkeypatch):
    _arm_rate(monkeypatch, short_max=4)
    codes = []
    for _ in range(10):
        r = gated_ssr.get("/dp/B09V3KXJPB", headers={"Accept": "text/html"})
        codes.append(r.status_code)
    assert codes.count(200) == 4
    assert set(codes[4:]) == {503}
    r = gated_ssr.get("/dp/B09V3KXJPB", headers={"Accept": "text/html"})
    assert "Robot Check" in r.text and "Type the characters" in r.text


def test_ssr_tokenless_checkout_post_fails(gated_ssr):
    gated_ssr.cookies.clear()
    r = gated_ssr.post("/ssr/cart-add", data={"product_id": "1", "quantity": "1"},
                       follow_redirects=False)
    assert r.status_code == 403
    r = gated_ssr.post("/ssr/place-order", follow_redirects=False)
    assert r.status_code == 403


def test_ssr_tokened_flow_completes(gated_ssr, seeded_session):
    product = seeded_session.get(Product, 1)
    expected_price = f"${float(product.price):.2f}"
    # visiting a served page grants the session cookie the forms rely on
    r = gated_ssr.get("/", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert gated_ssr.cookies.get("sf_client") == CLIENT_TOK

    r = gated_ssr.post("/ssr/cart-add", data={"product_id": "1", "quantity": "1"},
                       follow_redirects=False)
    assert r.status_code == 303

    r = gated_ssr.get("/gp/cart", headers={"Accept": "text/html"})
    assert "Shopping Cart" in r.text
    assert product.title in r.text and expected_price in r.text

    r = gated_ssr.post("/ssr/checkout")
    assert r.status_code == 200 and "Review your order" in r.text
    assert product.title in r.text and expected_price in r.text

    r = gated_ssr.post("/ssr/place-order")
    assert r.status_code == 200 and "Order placed" in r.text
