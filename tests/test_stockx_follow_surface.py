"""StockX's shopper-composed Following shelf obeys the catalog serving contract."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentarena.envs._storefront import gate
from agentarena.envs._storefront.app import _BOOT_JS
from agentarena.envs._storefront.counting import COUNTED_PATHS, PAGED_PATHS, PAGE_SIZE
from agentarena.envs.stockx.server.backend import stockx_api as stockx


def _client(monkeypatch):
    rows = {
        f"shoe-{i:03d}": {
            "sku": f"shoe-{i:03d}", "title": f"Shoe {i:03d}", "vendor": "Test",
            "price": 100 + i, "rating": 4.0, "reviews": 10,
            "specs": {}, "spec_display": {},
        }
        for i in range(1, 75)
    }
    id_to_sku = {i: f"shoe-{i:03d}" for i in range(1, 75)}
    monkeypatch.setattr(stockx, "_maps", lambda: (id_to_sku,
                                                   {sku: sid for sid, sku in id_to_sku.items()}))
    monkeypatch.setattr(stockx, "_row", lambda sku: rows[sku])
    monkeypatch.setattr(stockx.steering, "pinned_skus", lambda: [])
    monkeypatch.setattr(stockx.steering, "site", lambda: {})
    monkeypatch.setattr(stockx.steering, "by_sku", lambda sku: rows[sku])
    monkeypatch.setattr(stockx, "_img_url", lambda sku, title="": f"/img/{sku}.jpg")
    stockx._FOLLOWS.clear()

    monkeypatch.setenv("AMAZON_API_GATE", "1")
    monkeypatch.setenv("STOREFRONT_API_GATE", "1")
    monkeypatch.setenv("STOREFRONT_CLIENT_TOKEN", "shopper-token")
    monkeypatch.setenv("STOREFRONT_OPS_TOKEN", "ops-token")
    monkeypatch.setenv("SF_RATE_ENABLED", "1")
    monkeypatch.setenv("SF_COUNT_MODE", "request")
    for name in ("SF_RATE_SHORT_MAX", "SF_RATE_LONG_MAX", "SF_RATE_SUSTAINED_MAX"):
        monkeypatch.setenv(name, "10000")

    app = FastAPI()
    app.include_router(stockx.router)
    app.include_router(stockx.api_router)
    gate.install(app, gated_prefixes=("/api", "/stockx"), counted_paths=COUNTED_PATHS)
    gate.reset_state()
    return TestClient(app), rows


def test_follow_mutation_is_gated_and_rate_accounted(monkeypatch):
    client, _rows = _client(monkeypatch)

    # No shopper credential: middleware rejects the request before it mutates state.
    assert client.post("/api/follows", json={"id": 1}).status_code == 403
    assert not stockx._FOLLOWS
    assert len(gate._SHORT) == 0

    response = client.post(
        "/api/follows", json={"id": 1}, headers={"X-Storefront-Client": "shopper-token"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Shoe 001"
    assert stockx._FOLLOWS == {1}
    assert len(gate._SHORT) == 1, "one caller-selected card disclosure costs one rate unit"

    # The evaluator-only credential bypasses both middleware and the explicit hook.
    before = len(gate._SHORT)
    assert client.post(
        "/api/follows", json={"id": 2}, headers={"X-Storefront-Ops": "ops-token"}
    ).status_code == 200
    assert len(gate._SHORT) == before


def test_follow_container_is_capped_paginated_and_audited(monkeypatch):
    client, _rows = _client(monkeypatch)
    headers = {"X-Storefront-Client": "shopper-token"}
    for sid in range(1, 31):
        assert client.post("/api/follows", json={"id": sid}, headers=headers).status_code == 200

    first = client.get("/stockx/follows?limit=999", headers=headers)
    assert first.status_code == 200
    assert len(first.json()["following_sneakers"]) == PAGE_SIZE
    assert first.headers["X-Storefront-Total"] == "30"
    assert first.headers["X-Storefront-Page-Size"] == str(PAGE_SIZE)
    assert first.headers["X-Storefront-Has-Next"] == "1"

    second = client.get(f"/stockx/follows?limit=999&offset={PAGE_SIZE}", headers=headers)
    assert len(second.json()["following_sneakers"]) == 6
    assert second.headers["X-Storefront-Page"] == "2"
    assert second.headers["X-Storefront-Has-Next"] == "0"

    assert r"^/stockx/follows$" in PAGED_PATHS
    assert r"^/api/follows$" in COUNTED_PATHS
    assert "p==='/stockx/follows'" in _BOOT_JS

