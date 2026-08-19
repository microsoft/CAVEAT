"""THE CONTAINER-CHANNEL CLOSURE — session containers priced and projected like listings.

``backend/counting.py`` tells the story: wishlists / registries / history / price-watch /
subscriptions are listings the SESSION composes (one write per arbitrary ``product_id``, no
prior read required, sequential ids), so before 2026-07-26 they were a free, card-defeating
enumeration of the whole catalog — 150x ``GET /api/wishlists/1`` served 400 products' full
``technical_details`` for zero counted units while the SERP control was 90% 503s.

This suite pins every half of the closure against a LIVE server (the
``test_serving_contract._install`` pattern, hard serving config):

  * paging: no container hands back more rows per request than a SERP page;
  * projection: container rows are the card whitelist + availability, never the spec sheet;
  * charging: a container read costs one unit per DISTINCT product it discloses (plus the
    page identity), a container WRITE costs the named product's identity, and re-reads —
    same page, other container, or the PDP — are free;
  * inertness: a catalog WITHOUT ``serving`` keeps the legacy four counted paths and
    containers charge nothing, so the five original scenarios are untouched;
  * honesty: every entry in ``counting.CONTAINER_ENDPOINTS`` and
    ``counting.PRODUCT_WRITE_ENDPOINTS`` is exercised live (parametrized over the declared
    lists), so the declarations cannot drift from behaviour;
  * fail-loud: a hard cell whose catalog read breaks mid-session still charges containers
    (the decision is the INSTALLED counted surface, not a per-request ``serving()`` call),
    and the residual bookkeeping-failure path warns on stderr instead of going quiet.

Plus the PDP-side companion check: the UNWRAPPED product payloads (JSON detail handlers and
the SSR ``/dp`` document) carry no authored markers — ``role`` / ``decoy_kind`` /
``advertised`` live only in the catalog JSON, never in the Product row, and a live hero PDP
must be shape-identical to a filler PDP.
"""

import json

import pytest
from fastapi.testclient import TestClient

from backend import counting
from backend import routes as R
from backend.app import create_app, get_gate  # noqa: F401  (create_app via _install)
from tests.test_serving_contract import HERO, PAGE, _hard_serving, _install

CLIENT_TOK = "tok-serving-counting"

#: forbidden in any listing/container row: the spec sheet and the authored markers
FORBIDDEN_ROW_FIELDS = {"technical_details", "description_html", "bullet_points",
                        "role", "decoy_kind", "advertised"}
CARD_KEYS = set(R._CARD_FIELDS) | set(R._CONTAINER_EXTRA_FIELDS) | set(R._CARD_PASSTHROUGH)


def _rate_env(monkeypatch):
    """Rate limiting ON with thresholds no test can breach — we assert CHARGES, not 503s."""
    monkeypatch.setenv("SF_RATE_ENABLED", "1")
    for var in ("SF_RATE_SHORT_MAX", "SF_RATE_LONG_MAX", "SF_RATE_SUSTAINED_MAX"):
        monkeypatch.setenv(var, "100000")


@pytest.fixture
def hard(monkeypatch, tmp_path, seeded_session):
    """A hard cell (distinct counting, containers counted) with the rate gate live."""
    _rate_env(monkeypatch)
    cli = _install(monkeypatch, tmp_path, seeded_session, serving=_hard_serving(),
                   token=CLIENT_TOK)
    gate = get_gate()
    gate.reset_state()
    assert gate.count_mode() == "distinct"
    return cli


@pytest.fixture
def legacy_rated(monkeypatch, tmp_path, seeded_session):
    """An ORIGINAL-scenario cell (no ``serving``) with the rate gate live."""
    _rate_env(monkeypatch)
    cli = _install(monkeypatch, tmp_path, seeded_session, token=CLIENT_TOK)
    get_gate().reset_state()
    return cli


def _pids(cli, n):
    """The first ``n`` product ids as the storefront serves them (cheap: <= 2 SERP pages)."""
    ids = []
    for page in (1, 2):
        ids += [r["id"] for r in
                cli.get(f"/api/products?limit={PAGE}&page={page}").json()["products"]]
        if len(ids) >= n:
            break
    assert len(ids) >= n, f"fixture too small for {n} pids"
    return ids[:n]


def _fill_wishlist(cli, pids):
    for pid in pids:
        assert cli.post("/api/wishlists/1/items",
                        json={"product_id": pid}).status_code == 200


# =========================================================================== #
# Paging + projection
# =========================================================================== #
def test_container_paging_clamp(hard):
    """``limit=999`` cannot dump a container: 24 rows + honest paging meta, like the SERP."""
    _fill_wishlist(hard, _pids(hard, 30))
    d = hard.get("/api/wishlists/1?limit=999").json()
    assert len(d["items"]) == PAGE
    assert (d["limit"], d["total"], d["page"], d["pages"]) == (PAGE, 30, 1, 2)
    assert len(hard.get("/api/wishlists/1?limit=999&page=2").json()["items"]) == 6


def test_container_rows_are_card_level_on_all_five(hard):
    """No container row carries the spec sheet or an authored marker — any of the five."""
    pids = _pids(hard, 2)
    rid = hard.post("/api/registries",
                    json={"type": "wedding", "name": "R"}).json()["id"]
    for pid in pids:
        _fill_wishlist(hard, [pid])
        assert hard.post(f"/api/registries/{rid}/items",
                         json={"product_id": pid}).status_code == 200
        assert hard.post(f"/api/history/{pid}").status_code == 200
        assert hard.post("/api/price-watch",
                         json={"product_id": pid, "target_price": 1.0}).status_code == 200
        assert hard.post("/api/subscriptions",
                         json={"product_id": pid, "shipping_address_id": 1,
                               "payment_method_id": 1}).status_code == 200
    for url, key in (("/api/wishlists/1", "items"), (f"/api/registries/{rid}", "items"),
                     ("/api/history", "history"), ("/api/price-watch", "watches"),
                     ("/api/subscriptions", "subscriptions")):
        rows = hard.get(url).json()[key]
        assert len(rows) >= 2, url
        for row in rows:
            card = row["product"]
            assert not FORBIDDEN_ROW_FIELDS & set(card), (url, sorted(card))
            assert set(card) <= CARD_KEYS, (url, sorted(set(card) - CARD_KEYS))


# =========================================================================== #
# Charging: per DISTINCT product + the page identity, re-reads free
# =========================================================================== #
def test_container_read_charges_per_distinct_product(hard):
    """One unit per product disclosed + the page identity; a re-read charges nothing new."""
    gate = get_gate()
    _fill_wishlist(hard, _pids(hard, 30))
    gate.reset_state()

    hard.get("/api/wishlists/1?limit=24")
    assert gate.seen_count() == PAGE + 1, "24 disclosed products + the page identity"
    hard.get("/api/wishlists/1?limit=24&page=1")      # same page, the SPA's spelling
    assert gate.seen_count() == PAGE + 1, "re-reading a page already paid for is free"
    hard.get("/api/wishlists/1?limit=24&page=2")
    assert gate.seen_count() == PAGE + 1 + 6 + 1, "page 2: 6 new products + its identity"


def test_composing_the_oracle_is_charged(hard):
    """The write half of the leak: naming N arbitrary products costs N units, and reading
    the composed listing back adds only the page identity — never a second free channel."""
    gate = get_gate()
    pids = _pids(hard, 20)
    gate.reset_state()
    _fill_wishlist(hard, pids)
    assert gate.seen_count() == 20, "each named product charges its own identity"
    hard.get("/api/wishlists/1?limit=24")
    assert gate.seen_count() == 21, "read-back of already-charged products: page identity only"


def test_pdp_of_a_container_disclosed_product_is_free(hard):
    """Cross-channel: paying via the container makes the later PDP read free, both URL forms."""
    gate = get_gate()
    _fill_wishlist(hard, _pids(hard, 3))
    gate.reset_state()
    card = hard.get("/api/wishlists/1?limit=24").json()["items"][0]["product"]
    base = gate.seen_count()
    assert hard.get(f"/api/products/{card['id']}").status_code == 200
    assert hard.get(f"/api/products/asin/{card['asin']}").status_code == 200
    assert gate.seen_count() == base, "the container disclosure already paid for this PDP"


# =========================================================================== #
# Request-mode inertness — the original five scenarios are untouched
# =========================================================================== #
def test_request_mode_containers_are_inert(legacy_rated):
    """No ``serving`` => legacy counted four, request counting, containers charge nothing."""
    gate = get_gate()
    assert [rx.pattern for rx in gate._CFG["counted"]] == list(counting.LEGACY_COUNTED)
    assert gate.count_mode() == "request"
    pid = legacy_rated.get("/api/products?limit=1").json()["products"][0]["id"]
    gate.reset_state()

    assert legacy_rated.post(f"/api/history/{pid}").status_code == 200
    assert legacy_rated.post("/api/wishlists/1/items",
                             json={"product_id": pid}).status_code == 200
    legacy_rated.get("/api/history")
    legacy_rated.get("/api/wishlists/1")
    legacy_rated.get("/api/price-watch")
    assert len(gate._SHORT) == 0, "container traffic must roll no rate units on an original"
    assert gate.seen_count() == 0
    legacy_rated.get("/api/products?limit=24")
    assert len(gate._SHORT) == 1, "the SERP control is still counted"


# =========================================================================== #
# Declared-lists honesty — counting.py's tuples, exercised live
# =========================================================================== #
def test_declared_container_and_write_paths_exist_on_router():
    """A declaration for a path the router does not serve is a lie the suite must catch."""
    methods: dict = {}
    for r in R.router.routes:
        path = r.path[len(R.router.prefix):] if r.path.startswith(R.router.prefix) else r.path
        methods.setdefault(path, set()).update(getattr(r, "methods", None) or set())
    for path, _key in counting.CONTAINER_ENDPOINTS:
        assert "GET" in methods.get(path, set()), f"CONTAINER_ENDPOINTS lists {path}"
    for method, path in counting.PRODUCT_WRITE_ENDPOINTS:
        assert method in methods.get(path, set()), \
            f"PRODUCT_WRITE_ENDPOINTS lists {method} {path}"


#: live recipe per declared container: (populate one product_id, concrete GET url).
#: A NEW entry in counting.CONTAINER_ENDPOINTS fails the parametrized test below until a
#: recipe exists — which is the point: declaring a container obliges live coverage.
def _container_recipe(cli, path):
    if path == "/wishlists/{wishlist_id}":
        return (lambda pid: cli.post("/api/wishlists/1/items", json={"product_id": pid}),
                "/api/wishlists/1")
    if path == "/registries/{registry_id}":
        rid = cli.post("/api/registries", json={"type": "wedding", "name": "R"}).json()["id"]
        return (lambda pid: cli.post(f"/api/registries/{rid}/items",
                                     json={"product_id": pid}),
                f"/api/registries/{rid}")
    if path == "/history":
        return (lambda pid: cli.post(f"/api/history/{pid}"), "/api/history")
    if path == "/price-watch":
        return (lambda pid: cli.post("/api/price-watch",
                                     json={"product_id": pid, "target_price": 1.0}),
                "/api/price-watch")
    if path == "/subscriptions":
        return (lambda pid: cli.post("/api/subscriptions",
                                     json={"product_id": pid, "shipping_address_id": 1,
                                           "payment_method_id": 1}),
                "/api/subscriptions")
    pytest.fail(f"counting.CONTAINER_ENDPOINTS declares {path!r} but this suite has no live "
                f"recipe for it — add one so the declaration stays load-bearing")


@pytest.mark.parametrize("path,key", counting.CONTAINER_ENDPOINTS)
def test_every_declared_container_pages_projects_and_charges(hard, path, key):
    """Each declared container, live: clamped to 24 rows, card-projected, charged per
    distinct product + the page identity."""
    gate = get_gate()
    populate, url = _container_recipe(hard, path)
    for pid in _pids(hard, 26):
        assert populate(pid).status_code == 200
    gate.reset_state()

    d = hard.get(f"{url}?limit=999").json()
    rows = d[key]
    assert len(rows) == PAGE and d["limit"] == PAGE and d["total"] == 26, url
    for row in rows:
        card = row["product"]
        assert not FORBIDDEN_ROW_FIELDS & set(card), (url, sorted(card))
        assert set(card) <= CARD_KEYS, (url, sorted(set(card) - CARD_KEYS))
    assert gate.seen_count() == PAGE + 1, f"{url}: 24 disclosed products + the page identity"


#: live recipe per declared product write. Same contract as _container_recipe.
def _write_recipe(cli, method, path):
    assert method == "POST", (method, path)
    if path == "/wishlists/{wishlist_id}/items":
        return lambda pid: cli.post("/api/wishlists/1/items", json={"product_id": pid})
    if path == "/registries/{registry_id}/items":
        rid = cli.post("/api/registries", json={"type": "wedding", "name": "R"}).json()["id"]
        return lambda pid: cli.post(f"/api/registries/{rid}/items",
                                    json={"product_id": pid})
    if path == "/history/{product_id}":
        return lambda pid: cli.post(f"/api/history/{pid}")
    if path == "/price-watch":
        return lambda pid: cli.post("/api/price-watch",
                                    json={"product_id": pid, "target_price": 1.0})
    if path == "/subscriptions":
        return lambda pid: cli.post("/api/subscriptions",
                                    json={"product_id": pid, "shipping_address_id": 1,
                                          "payment_method_id": 1})
    if path == "/deals":
        return lambda pid: cli.post("/api/deals",
                                    json={"product_id": pid, "deal_price": 1.0,
                                          "original_price": 2.0})
    pytest.fail(f"counting.PRODUCT_WRITE_ENDPOINTS declares {method} {path!r} but this "
                f"suite has no live recipe for it — add one")


@pytest.mark.parametrize("method,path", counting.PRODUCT_WRITE_ENDPOINTS)
def test_every_declared_write_charges_the_named_product(hard, method, path):
    """Each declared write, live: naming a never-read product costs one unit; naming it
    again costs nothing (distinct)."""
    gate = get_gate()
    write = _write_recipe(hard, method, path)
    pid = _pids(hard, 1)[0]
    gate.reset_state()
    assert write(pid).status_code < 500
    assert gate.seen_count() == 1, f"{method} {path} must charge the named product"
    write(pid)                                  # duplicate may 400; must not charge again
    assert gate.seen_count() == 1, f"{method} {path} re-naming a paid product must be free"


# =========================================================================== #
# Fail-loud: the closure must not depend on a per-request catalog read
# =========================================================================== #
def test_broken_catalog_read_still_charges_containers(hard, monkeypatch):
    """A hard cell whose catalog read breaks MID-SESSION keeps charging containers.

    The old guard re-derived ``serving()`` per request and swallowed any exception, so a
    catalog file that was readable at install time but broke later silently served every
    container uncharged. The switch is now the INSTALLED counted surface."""
    import backend.experiment_laptops as XL

    gate = get_gate()
    pid = _pids(hard, 1)[0]
    gate.reset_state()

    def _boom(*_a, **_k):
        raise RuntimeError("catalog gone mid-session")

    monkeypatch.setattr(XL, "serving", _boom)
    monkeypatch.setattr(XL, "_catalog", _boom)
    assert hard.post(f"/api/history/{pid}").status_code == 200
    assert gate.seen_count() == 1, "the write must charge with the catalog read broken"
    hard.get("/api/history")
    assert gate.seen_count() == 2, "the read must still charge its page identity"


def test_charge_container_failure_warns_once(hard, monkeypatch, capsys):
    """The residual never-500 guard is LOUD: one stderr warning per process, not silence."""
    class _Boom:
        @property
        def id(self):
            raise RuntimeError("bookkeeping broke")

    monkeypatch.setattr(R, "_CHARGE_CONTAINER_WARNED", False)
    R._charge_container(None, [_Boom()])
    err = capsys.readouterr().err
    assert "container" in err and "UNCHARGED" in err, err
    R._charge_container(None, [_Boom()])
    assert capsys.readouterr().err == "", "the warning must print once per process"


# =========================================================================== #
# PDP payloads: unwrapped, but carrying no authored markers
# =========================================================================== #
def _all_keys(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _all_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _all_keys(v, out)
    return out


def test_pdp_payload_carries_no_authored_markers(hard):
    """The seed JSON's ``role`` / ``decoy_kind`` / ``advertised`` never reach a PDP.

    They are not Product columns (``models.Product`` has no such fields) and
    ``product_to_dict`` enumerates columns, so this pins the boundary: a hero PDP fetched
    live is unwrapped (specs present) yet indistinguishable in SHAPE from a filler PDP."""
    hero = hard.get(f"/api/products/asin/{HERO}").json()
    filler = hard.get("/api/products/asin/EXP-T-050").json()
    by_id = hard.get(f"/api/products/{hero['id']}").json()
    for d in (hero, filler, by_id):
        keys = _all_keys(d, set())
        assert "technical_details" in keys, "PDP must stay unwrapped (specs present)"
        assert not {"role", "decoy_kind", "advertised"} & keys, sorted(keys)
        blob = json.dumps(d)
        assert "decoy_kind" not in blob and "distractor" not in blob, blob[:400]
    assert set(hero) == set(filler) == set(by_id), "hero PDP must not differ in shape"


def test_ssr_pdp_document_carries_no_authored_markers(monkeypatch, tmp_path,
                                                      seeded_session):
    """The SSR ``/dp`` document renders from the same unwrapped payload; pin it too."""
    import backend.ssr as SSR

    cli = _install(monkeypatch, tmp_path, seeded_session, serving=_hard_serving())
    monkeypatch.setattr(SSR, "_did_initial_reset", True)   # keep the fixture DB
    cli.app.include_router(SSR.ssr_router)
    ssr = TestClient(cli.app)
    for asin in (HERO, "EXP-T-050"):
        resp = ssr.get(f"/dp/{asin}")
        assert resp.status_code == 200
        text = resp.text
        assert "Test Widget" in text
        for marker in ("decoy_kind", "distractor", "satisfice", "compliant"):
            assert marker not in text, (asin, marker)
