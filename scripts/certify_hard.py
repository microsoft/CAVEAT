#!/usr/bin/env python3
"""Certify the truthful hard tier statically and through the agent-visible storefront.

The live gate is intentionally exhaustive where truth could drift:

* five scenarios x four conditions;
* all 88 organic pages in every storefront;
* all 2,112 ASIN PDPs in every storefront;
* complete truth on all 2,112 ordinary PDP documents;
* strict closure of every product-data JSON root and representative
  subresources, with no throttling, delay, page cap, or hidden product.

All agent-facing calls carry only ``x-storefront-client`` and the ``sf_client``
cookie.  The certifier never reads the evaluator credential.  It does call
``env.evaluate`` after purchasing the hero; that adapter owns its private
back-channel internally and no credential value or environment mapping is
returned to this script or its report.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import http.cookiejar
import json
import math
import re
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import caveat.envs.caveat_shop  # noqa: E402,F401
from caveat.benchmark import registry, serialize  # noqa: E402
from caveat.benchmark.validate import validate_scenario  # noqa: E402
from caveat.core.environment import ENVIRONMENTS  # noqa: E402
from caveat.scoring.optimal_selection import is_optimal_selection  # noqa: E402


BASES = ("laptop", "office_chair", "mattress", "backpack", "tent")
SIDS = tuple(f"{base}_hard" for base in BASES)
PAGE_SIZE = 24
PAGES = 88
CATALOG_N = 2_112
ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$")
CONDITIONS = ("clean", "format_only", "merchandising", "combined")
EXPECTED_ACCESS = {
    "version": 2,
    "transport": "classic_ssr_v1",
    "product_json": False,
    "detail_representation": "seller_dialect_v2",
}
AD_SLOTS = (0, 4, 8, 12, 16, 20)
FORBIDDEN_PUBLIC_FIELDS = frozenset({
    "role", "advertised", "decoy_kind", "fail_reasons", "campaign_role",
    "objective_profile", "appeal_profile", "agent_note", "adv_hidden",
    "adv_exclude", "true_price", "display_price",
})
OPS_NAME_RE = re.compile(
    r"(?i)(?:STOREFRONT[_-]?OPS|X[_-]?STOREFRONT[_-]?OPS|OPS[_-]?TOKEN)"
)
NUMBER_RE = re.compile(
    r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
FRONTEND_DIST = (
    ROOT / "caveat/envs/caveat_shop/server/frontend/dist"
)
FRONTEND_SHA256 = {
    "assets/index-IwcoZ3da.css":
        "165bdb68f335fa807951d57028a08945b478c988479df1890489a6048f3f1c21",
    "assets/index-JAav4Fab.js":
        "c62007f67c38fda8986b99cefe7e95e65cb38654378f2e482084346bf1344498",
    "index.html":
        "1339458bfa7517c8e85b315194ccafede7805621b277f324fff7fae7b54efd7d",
}


class Audit:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.notes: list[str] = []
        self._lock = threading.Lock()

    def require(self, condition: Any, message: str) -> bool:
        if condition:
            return True
        with self._lock:
            if len(self.errors) < 500:
                self.errors.append(message)
        return False

    def note(self, message: str) -> None:
        with self._lock:
            self.notes.append(message)

    def report(self) -> dict:
        return {
            "verdict": "pass" if not self.errors else "fail",
            "errors": list(self.errors),
            "notes": list(self.notes),
        }


class StatusLedger:
    """One thread-safe response ledger shared by API workers and browsers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.statuses: Counter = Counter()
        self.requests = 0
        self.network_errors: list[dict] = []
        self.unexpected: list[dict] = []
        self.bound_responses: list[dict] = []
        self.credential_findings: list[dict] = []

    def record(
        self,
        label: str,
        *,
        status: int | None,
        expected: set[int],
        network_error: str | None = None,
        surface: Any = None,
    ) -> None:
        with self._lock:
            self.requests += 1
            if network_error is not None:
                if len(self.network_errors) < 100:
                    self.network_errors.append({
                        "label": label, "error": network_error})
                return
            assert status is not None
            self.statuses[status] += 1
            if status in {429, 503} and len(self.bound_responses) < 100:
                self.bound_responses.append({"label": label, "status": status})
            if status not in expected and len(self.unexpected) < 100:
                self.unexpected.append({
                    "label": label,
                    "status": status,
                    "expected": sorted(expected),
                })
            if surface is not None and OPS_NAME_RE.search(_surface_text(surface)):
                if len(self.credential_findings) < 100:
                    self.credential_findings.append({
                        "label": label,
                        "category": "ops_credential_name_on_agent_surface",
                    })

    def report(self) -> dict:
        with self._lock:
            passed = not (
                self.network_errors
                or self.unexpected
                or self.bound_responses
                or self.credential_findings
            )
            return {
                "passed": passed,
                "requests": self.requests,
                "statuses": dict(sorted(self.statuses.items())),
                "network_errors": list(self.network_errors),
                "unexpected": list(self.unexpected),
                "bound_responses": list(self.bound_responses),
                "credential_findings": list(self.credential_findings),
                "no_429_or_503": not self.bound_responses,
            }


def _surface_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


class StorefrontClient:
    """Stateful client-token shopper; callers cannot add credential headers."""

    def __init__(self, base_url: str, client_token: str, ledger: StatusLedger):
        self.base_url = base_url.rstrip("/")
        self.client_token = client_token
        self.ledger = ledger
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Any = None,
        expected: set[int] = frozenset({200}),
    ) -> tuple[int, Any]:
        headers = {
            "Content-Type": "application/json",
            "x-storefront-client": self.client_token,
            "Cookie": f"sf_client={self.client_token}",
            "User-Agent": "truthful-hard-client-certificate/1",
        }
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, method=method, data=data, headers=headers)
        status: int | None = None
        raw = b""
        response_headers: dict[str, str] = {}
        try:
            with self.opener.open(req, timeout=120) as response:
                status = response.status
                raw = response.read()
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            response_headers = dict((exc.headers or {}).items())
            exc.close()
        except Exception as exc:  # noqa: BLE001
            self.ledger.record(
                f"{method}:{path}",
                status=None,
                expected=set(expected),
                network_error=f"{type(exc).__name__}: {exc}",
            )
            raise
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw.decode("utf-8", "replace")}
        self.ledger.record(
            f"{method}:{path}",
            status=status,
            expected=set(expected),
            surface={"headers": response_headers, "body": parsed},
        )
        if status not in expected:
            raise RuntimeError(
                f"{method} {path} -> HTTP {status}, expected {sorted(expected)}")
        return status, parsed

    def get(self, path: str, *, expected: set[int] = frozenset({200})) -> Any:
        return self.request(path, expected=set(expected))[1]

    def post(self, path: str, body: Any = None) -> Any:
        return self.request(
            path, method="POST", body={} if body is None else body)[1]


class HtmlStorefrontClient:
    """Classic-document shopper carrying the same client header and cookie."""

    def __init__(self, base_url: str, client_token: str, ledger: StatusLedger):
        self.base_url = base_url.rstrip("/")
        self.client_token = client_token
        self.ledger = ledger
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        form: dict[str, Any] | None = None,
        expected: set[int] = frozenset({200}),
    ) -> tuple[str, str]:
        data = (
            urllib.parse.urlencode(form).encode()
            if form is not None else None
        )
        headers = {
            "x-storefront-client": self.client_token,
            "Cookie": f"sf_client={self.client_token}",
            "User-Agent": "truthful-hard-classic-storefront-certificate/1",
        }
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(
            self.base_url + path, method=method, data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=120) as response:
                status = response.status
                raw = response.read()
                response_headers = dict(response.headers.items())
                final_url = response.geturl()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            response_headers = dict((exc.headers or {}).items())
            final_url = exc.geturl()
            exc.close()
        except Exception as exc:  # noqa: BLE001
            self.ledger.record(
                f"HTML:{method}:{path}",
                status=None,
                expected=set(expected),
                network_error=f"{type(exc).__name__}: {exc}",
            )
            raise
        text = raw.decode("utf-8", "replace")
        self.ledger.record(
            f"HTML:{method}:{path}",
            status=status,
            expected=set(expected),
            surface={"headers": response_headers, "body": text},
        )
        if status not in expected:
            raise RuntimeError(
                f"HTML {method} {path} -> {status}, expected {sorted(expected)}")
        return text, final_url

    def get(self, path: str) -> str:
        return self.request(path)[0]

    def post(self, path: str, form: dict[str, Any]) -> tuple[str, str]:
        return self.request(path, method="POST", form=form)


def _tokenless_probe(
    base_url: str,
    path: str,
    ledger: StatusLedger,
    audit: Audit,
    label: str,
) -> None:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        headers={"User-Agent": "truthful-hard-tokenless-negative/1"},
    )
    status: int | None = None
    raw = b""
    headers = {}
    try:
        with urllib.request.build_opener().open(req, timeout=60) as response:
            status = response.status
            raw = response.read()
            headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
        headers = dict((exc.headers or {}).items())
        exc.close()
    except Exception as exc:  # noqa: BLE001
        ledger.record(
            f"{label}:tokenless:{path}",
            status=None,
            expected={403},
            network_error=f"{type(exc).__name__}: {exc}",
        )
        audit.require(False, f"{label}: tokenless probe failed: {exc}")
        return
    try:
        body = json.loads(raw)
    except Exception:
        body = raw.decode("utf-8", "replace")
    ledger.record(
        f"{label}:tokenless:{path}",
        status=status,
        expected={403},
        surface={"headers": headers, "body": body},
    )
    audit.require(
        status == 403,
        f"{label}: tokenless {path} returned {status}, expected 403")


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _canon(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canon(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canon(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _equal(left: Any, right: Any) -> bool:
    return _canon(left) == _canon(right)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        _canon(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    ).encode()).hexdigest()


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _frontend_contract(audit: Audit) -> dict:
    actual = {}
    for path in sorted(FRONTEND_DIST.rglob("*")):
        if path.is_file():
            actual[str(path.relative_to(FRONTEND_DIST))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    audit.require(
        actual == FRONTEND_SHA256,
        f"shared prebuilt frontend bytes drifted: {actual}")
    return actual


def run_static(sids: tuple[str, ...]) -> dict:
    audit = Audit()
    reports = {}
    _frontend_contract(audit)
    for sid in sids:
        meta_path = serialize.scenario_dir(sid) / "meta.json"
        if not meta_path.exists():
            audit.require(False, f"{sid}: committed artifacts are missing")
            continue
        report = validate_scenario(sid, measured_variants=("graded",))
        reports[sid] = report
        for issue in report.get("issues") or []:
            audit.require(False, f"{sid}: {issue}")
        if not report.get("issues"):
            optimal = report.get("optimal_by_variant", {}).get("graded", [])
            audit.note(f"{sid}: n={report['products']} optimal={optimal}")
    return {
        **audit.report(),
        "kind": "static",
        "scenarios": reports,
        "frontend_sha256": actual if (actual := {
            str(path.relative_to(FRONTEND_DIST)): hashlib.sha256(
                path.read_bytes()).hexdigest()
            for path in sorted(FRONTEND_DIST.rglob("*")) if path.is_file()
        }) else {},
    }


def _card_truth(
    card: dict,
    seed: dict,
    presentation: dict,
    *,
    label: str,
    audit: Audit,
    expected_promo: str | None,
) -> None:
    expected = {
        "asin": seed["asin"],
        "title": seed["title"],
        "price": seed["price"],
        "list_price": seed["list_price"],
        "rating": seed["rating"],
        "rating_count": seed["reviews"],
        "review_count": 0,
        "bought_past_month": seed["bought"],
        "is_prime_eligible": True,
        "delivery_days": presentation["delivery_days"],
        "seller_name": presentation["seller_name"],
        "seller_rating": presentation["seller_rating"],
        "seller_reviews": presentation["seller_reviews"],
    }
    for key, value in expected.items():
        audit.require(
            key in card and _equal(card.get(key), value),
            f"{label}: card {key}={card.get(key)!r}, canonical={value!r}")
    leaked = FORBIDDEN_PUBLIC_FIELDS.intersection(card)
    audit.require(
        not leaked,
        f"{label}: agent-visible card leaks internal fields {sorted(leaked)}")
    if expected_promo is None:
        audit.require(
            "adv_badge" not in card,
            f"{label}: unexpected active/promo copy on a non-anchor surface")
    else:
        audit.require(
            card.get("adv_badge") == expected_promo,
            f"{label}: anchor promo is not the exact factual copy")


def _decode_details(
    technical_details: Any,
    seed_tech: dict,
    profile: dict,
) -> tuple[dict, list[str]]:
    if isinstance(technical_details, str):
        try:
            technical_details = json.loads(technical_details)
        except Exception:
            return {}, ["technical_details is not JSON"]
    if not isinstance(technical_details, dict):
        return {}, ["technical_details is not an object"]
    fields = profile.get("fields") or {}
    expected_labels = {
        str(rule.get("label") or "") for rule in fields.values()
    }
    errors = []
    if set(technical_details) != expected_labels:
        errors.append(
            f"labels={sorted(technical_details)}, expected={sorted(expected_labels)}")
    decoded = {}
    for key, canonical in seed_tech.items():
        rule = fields.get(key) or {}
        label = str(rule.get("label") or "")
        if label not in technical_details:
            errors.append(f"{key}: missing dialect label {label!r}")
            continue
        rendered = str(technical_details[label])
        if "enum" in rule:
            inverse = {str(value): raw for raw, value in (rule.get("enum") or {}).items()}
            raw = inverse.get(rendered)
            expected_raw = (
                "true" if canonical is True
                else "false" if canonical is False
                else str(canonical).strip().lower()
            )
            if raw != expected_raw:
                errors.append(
                    f"{key}: {rendered!r} decodes to {raw!r}, "
                    f"expected {expected_raw!r}")
            else:
                decoded[key] = canonical
            continue
        suffix = str(rule.get("suffix") or "")
        if suffix and not rendered.endswith(suffix):
            errors.append(f"{key}: {rendered!r} lacks suffix {suffix!r}")
            continue
        numeric = rendered[:-len(suffix)] if suffix else rendered
        try:
            encoded = Decimal(numeric.replace(",", "").strip())
            scale = Decimal(str(rule.get("scale", 1)))
            value = encoded / scale
            expected = Decimal(str(canonical))
        except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
            errors.append(f"{key}: cannot reverse {rendered!r}")
            continue
        if value != expected:
            errors.append(
                f"{key}: {rendered!r} decodes to {value}, expected {expected}")
        else:
            decoded[key] = canonical
    return decoded, errors


def _pdp_truth(
    pdp: dict,
    seed: dict,
    presentation: dict,
    truth: dict,
    *,
    label: str,
    audit: Audit,
    expected_promo: str | None,
) -> str | None:
    _card_truth(
        pdp, seed, presentation, label=label, audit=audit,
        expected_promo=expected_promo)
    audit.require(
        pdp.get("stock_quantity") == seed["stock"],
        f"{label}: stock={pdp.get('stock_quantity')}, canonical={seed['stock']}")
    profile_name = (truth.get("format_assignments") or {}).get(seed["asin"])
    profile = (truth.get("format_profiles") or {}).get(profile_name) or {}
    decoded, errors = _decode_details(
        pdp.get("technical_details"), seed.get("tech") or {}, profile)
    audit.require(
        not errors and decoded == (seed.get("tech") or {}),
        f"{label}: seller dialect {profile_name!r} failed canonical decode: {errors[:3]}")
    tech = pdp.get("technical_details")
    if isinstance(tech, str):
        try:
            tech = json.loads(tech)
        except Exception:
            tech = {}
    expected_bullets = [
        f"{rule['label']}: {tech.get(rule['label'])}"
        for rule in (profile.get("fields") or {}).values()
    ]
    audit.require(
        pdp.get("bullet_points") == expected_bullets,
        f"{label}: bullet projection differs from the same reversible dialect")
    return profile_name


def _promo_for(condition: str, asin: str, params: dict) -> str | None:
    if condition != "combined":
        return None
    return (params.get("shopper_promos") or {}).get(asin)


def _sweep_pages(
    client: StorefrontClient,
    *,
    sid: str,
    condition: str,
    seed: dict[str, dict],
    truth: dict,
    params: dict,
    audit: Audit,
) -> dict:
    organic_sequence: list[str] = []
    ads_seen: list[str] = []
    page_digests = []
    presentations = truth["presentations"]
    sponsors = set(params.get("sponsored_skus") or [])
    merchandising = condition in {"merchandising", "combined"}
    for page in range(1, PAGES + 1):
        payload = client.get(
            f"/api/products?limit={PAGE_SIZE}&page={page}"
            f"&offset={(page - 1) * PAGE_SIZE}")
        cards = payload.get("products") or []
        organic = [card for card in cards if card.get("sponsored") is not True]
        ads = [card for card in cards if card.get("sponsored") is True]
        expected_cards = 30 if merchandising else 24
        audit.require(
            len(cards) == expected_cards,
            f"{sid}/{condition}/page{page}: {len(cards)} cards, "
            f"expected {expected_cards}")
        audit.require(
            len(organic) == 24 and len(ads) == (6 if merchandising else 0),
            f"{sid}/{condition}/page{page}: organic/ads="
            f"{len(organic)}/{len(ads)}")
        audit.require(
            payload.get("organic_count") == 24
            and payload.get("sponsored_count") == (6 if merchandising else 0),
            f"{sid}/{condition}/page{page}: explicit organic/ad counters drifted")
        asins = [str(card.get("asin") or "") for card in cards]
        audit.require(
            len(asins) == len(set(asins)),
            f"{sid}/{condition}/page{page}: duplicate ASIN/card key")
        audit.require(
            int(payload.get("total") or 0) == CATALOG_N
            and int(payload.get("limit") or 0) == PAGE_SIZE,
            f"{sid}/{condition}/page{page}: total/limit drifted")
        if merchandising:
            for slot in AD_SLOTS:
                audit.require(
                    slot < len(cards)
                    and cards[slot].get("sponsored") is True
                    and cards[slot].get("ad_label") == "Sponsored",
                    f"{sid}/{condition}/page{page}: slot {slot} is not a "
                    "disclosed additive ad")
        for card in cards:
            asin = str(card.get("asin") or "")
            audit.require(
                asin in seed,
                f"{sid}/{condition}/page{page}: unknown ASIN {asin!r}")
            if asin not in seed:
                continue
            if card.get("sponsored") is True:
                audit.require(
                    asin in sponsors,
                    f"{sid}/{condition}/page{page}: non-cohort ad {asin}")
                ads_seen.append(asin)
            else:
                audit.require(
                    card.get("ad_label") in (None, ""),
                    f"{sid}/{condition}/page{page}: organic copy carries a "
                    "nonempty ad label")
                organic_sequence.append(asin)
            _card_truth(
                card,
                seed[asin],
                presentations[asin],
                label=f"{sid}/{condition}/page{page}/{asin}",
                audit=audit,
                expected_promo=_promo_for(condition, asin, params),
            )
        page_digests.append(_sha([card.get("asin") for card in organic]))
    audit.require(
        len(organic_sequence) == CATALOG_N
        and len(set(organic_sequence)) == CATALOG_N
        and set(organic_sequence) == set(seed),
        f"{sid}/{condition}: organic sweep is not an exact 2,112-product permutation")
    return {
        "organic_sequence": organic_sequence,
        "organic_sha256": _sha(organic_sequence),
        "page_sha256": _sha(page_digests),
        "ads_seen": len(ads_seen),
        "ad_asins_seen": len(set(ads_seen)),
    }


def _review_summary_truth(
    summary: dict,
    seed: dict,
    truth: dict,
) -> list[str]:
    errors = []
    if not _equal(summary.get("average_rating"), seed["rating"]):
        errors.append("average_rating")
    if summary.get("total_ratings") != seed["reviews"]:
        errors.append("total_ratings")
    if summary.get("total_written_reviews") != 0:
        errors.append("total_written_reviews")
    authored = (truth.get("rating_breakdowns") or {}).get(seed["asin"]) or {}
    actual = summary.get("rating_breakdown") or {}
    for star in map(str, range(1, 6)):
        if (actual.get(star) or {}).get("count") != authored.get(star):
            errors.append(f"rating_breakdown.{star}")
    return errors


def _bulk_verify(
    base_url: str,
    token: str,
    ledger: StatusLedger,
    *,
    sid: str,
    condition: str,
    seed: dict[str, dict],
    truth: dict,
    params: dict,
    workers: int,
    max_products: int,
    audit: Audit,
) -> dict:
    ordered_asins = sorted(seed)
    if max_products > 0:
        required = {
            next(asin for asin, row in seed.items() if row.get("decoy_kind") == "hero"),
            params.get("best_seller_sku"),
            params.get("choice_sku"),
        }
        chosen = [asin for asin in ordered_asins if asin in required]
        chosen += [asin for asin in ordered_asins if asin not in required]
        ordered_asins = chosen[:max_products]
    missing_id = 987_654_321
    sentinel_client = StorefrontClient(base_url, token, ledger)
    _, missing_body = sentinel_client.request(
        f"/api/products/{missing_id}", expected={404})
    audit.require(
        missing_body == {"detail": "Product not found"},
        f"{sid}/{condition}: nonexistent numeric detail has unexpected body")

    local = threading.local()

    def client() -> StorefrontClient:
        if not hasattr(local, "client"):
            local.client = StorefrontClient(base_url, token, ledger)
        return local.client

    def verify_one(asin: str) -> dict:
        cli = client()
        pdp = cli.get(f"/api/products/asin/{urllib.parse.quote(asin)}")
        if pdp.get("asin") != asin or not isinstance(pdp.get("id"), int):
            raise AssertionError("ASIN PDP identity/id mismatch")
        product_id = int(pdp["id"])
        _, numeric_body = cli.request(
            f"/api/products/{product_id}", expected={404})
        if numeric_body != missing_body:
            raise AssertionError(
                "valid numeric detail differs from nonexistent numeric detail")
        profile = (truth.get("format_assignments") or {}).get(asin)
        expected_labels = {
            str(rule.get("label") or "")
            for rule in (
                ((truth.get("format_profiles") or {}).get(profile) or {})
                .get("fields") or {}
            ).values()
        }
        technical = pdp.get("technical_details")
        if isinstance(technical, str):
            technical = json.loads(technical)
        decoded, decode_errors = _decode_details(
            technical, seed[asin].get("tech") or {},
            (truth.get("format_profiles") or {}).get(profile) or {},
        )
        if decode_errors or decoded != (seed[asin].get("tech") or {}):
            raise AssertionError(
                f"dialect {profile} decode failed: {decode_errors[:2]}")
        if set(technical) != expected_labels:
            raise AssertionError("dialect label set mismatch")
        leaked = FORBIDDEN_PUBLIC_FIELDS.intersection(pdp)
        if leaked:
            raise AssertionError(f"PDP leaks internal fields {sorted(leaked)}")
        core = {
            "asin": asin,
            "title": seed[asin]["title"],
            "price": seed[asin]["price"],
            "list_price": seed[asin]["list_price"],
            "rating": seed[asin]["rating"],
            "rating_count": seed[asin]["reviews"],
            "bought_past_month": seed[asin]["bought"],
            "stock_quantity": seed[asin]["stock"],
        }
        for key, expected in core.items():
            if not _equal(pdp.get(key), expected):
                raise AssertionError(
                    f"PDP {key}={pdp.get(key)!r}, expected {expected!r}")
        expected_promo = _promo_for(condition, asin, params)
        if (
            pdp.get("adv_badge") if "adv_badge" in pdp else None
        ) != expected_promo:
            raise AssertionError("PDP promo differs from exact combined anchor fact")

        variants = cli.get(f"/api/products/{product_id}/variants")
        reviews = cli.get(
            f"/api/products/{product_id}/reviews?limit=1&page=1")
        review_summary = cli.get(
            f"/api/products/{product_id}/reviews/summary")
        questions = cli.get(
            f"/api/products/{product_id}/questions?limit=1&page=1")
        if variants != {"variants": []}:
            raise AssertionError("unsupported variants are not exactly empty")
        if reviews.get("reviews") != [] or reviews.get("total") != 0:
            raise AssertionError("text reviews were fabricated")
        review_errors = _review_summary_truth(
            review_summary, seed[asin], truth)
        if review_errors:
            raise AssertionError(
                f"aggregate review truth failed: {review_errors}")
        if questions.get("questions") != [] or questions.get("total") != 0:
            raise AssertionError("questions were fabricated")
        return {
            "asin": asin,
            "id": product_id,
            "profile": profile,
            "decoded_sha256": _sha(decoded),
        }

    records = []
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(verify_one, asin): asin for asin in ordered_asins}
        for future in as_completed(futures):
            asin = futures[future]
            try:
                records.append(future.result())
            except Exception as exc:  # noqa: BLE001
                if len(failures) < 100:
                    failures.append(
                        f"{asin}: {type(exc).__name__}: {exc}")
    audit.require(
        not failures,
        f"{sid}/{condition}: exhaustive PDP/subresource failures: {failures[:5]}")
    audit.require(
        len(records) == len(ordered_asins),
        f"{sid}/{condition}: verified {len(records)}/{len(ordered_asins)} PDPs")

    # Related/similar rails materialize and rank the category, so sampling them
    # is deliberate: exhaustive rail calls would add O(N^2) database work without
    # increasing field/dialect coverage.
    records.sort(key=lambda row: row["asin"])
    sample = []
    anchors = {
        params.get("best_seller_sku"),
        params.get("choice_sku"),
        next(asin for asin, row in seed.items() if row.get("decoy_kind") == "hero"),
    }
    for record in records:
        if record["asin"] in anchors or int(hashlib.sha256(
                record["asin"].encode()).hexdigest(), 16) % 257 == 0:
            sample.append(record)
        if len(sample) >= 12:
            break
    for record in sample:
        cli = sentinel_client
        product_id = record["id"]
        for endpoint in ("related", "similar", "frequently-bought"):
            payload = cli.get(
                f"/api/products/{product_id}/{endpoint}?limit=24")
            products = payload.get("products")
            audit.require(
                isinstance(products, list),
                f"{sid}/{condition}/{record['asin']}/{endpoint}: "
                "response is not a product list")
            for card in products or []:
                rail_asin = str(card.get("asin") or "")
                audit.require(
                    rail_asin in seed,
                    f"{sid}/{condition}/{endpoint}: unknown rail ASIN {rail_asin}")
                if rail_asin in seed:
                    _card_truth(
                        card,
                        seed[rail_asin],
                        truth["presentations"][rail_asin],
                        label=f"{sid}/{condition}/{endpoint}/{rail_asin}",
                        audit=audit,
                        expected_promo=_promo_for(
                            condition, rail_asin, params),
                    )
        frequent = cli.get(
            f"/api/products/{product_id}/frequently-bought")
        audit.require(
            frequent == {"products": []},
            f"{sid}/{condition}: unsupported frequently-bought rail is nonempty")

    profile_counts = Counter(record["profile"] for record in records)
    return {
        "verified": len(records),
        "expected": len(ordered_asins),
        "all_products": max_products == 0,
        "numeric_roots_404": len(records) + 1,
        "lightweight_subresource_products": len(records),
        "rail_sample_products": len(sample),
        "profile_counts": dict(sorted(profile_counts.items())),
        "decoded_sha256": _sha({
            row["asin"]: row["decoded_sha256"] for row in records}),
        "id_by_asin": {
            row["asin"]: row["id"] for row in records},
    }


def _star_glyphs(rating: Any) -> str:
    value = float(rating or 0)
    full = int(value)
    half = 1 if value - full >= 0.5 else 0
    return "★" * full + ("½" if half else "") + "☆" * (5 - full - half)


def _number(text: str) -> Decimal:
    match = NUMBER_RE.search(str(text))
    if not match or not match.group(0):
        raise ValueError(f"no numeric value in {text!r}")
    return Decimal(match.group(0).replace(",", ""))


def _discount_percent(price: Any, list_price: Any) -> int | None:
    try:
        price_value = float(price)
        list_value = float(list_price)
    except (TypeError, ValueError):
        return None
    if list_value <= price_value or list_value <= 0:
        return None
    return int(math.floor(
        ((list_value - price_value) / list_value) * 100.0 + 0.5))


def _ssr_cards(document: str) -> tuple[list[dict], BeautifulSoup]:
    soup = BeautifulSoup(document, "html.parser")
    cards = []
    for node in soup.select("div.card"):
        link = node.select_one("a[href^='/dp/']")
        if link is None:
            continue
        href = str(link.get("href") or "")
        asin = urllib.parse.unquote(
            href.split("/dp/", 1)[1].split("?", 1)[0])
        hidden = node.select_one("input[name='product_id']")
        count_node = node.select_one(".rating-count")
        count_match = re.search(
            r"([0-9][0-9,]*)",
            count_node.get_text(" ", strip=True) if count_node else "")
        price_node = node.select_one(".price")
        list_price_node = node.select_one(".list-price")
        bought_node = node.select_one(".bought-count")
        delivery_node = node.select_one(".delivery-days")
        discount_node = node.select_one(".discount-percent")
        cards.append({
            "asin": asin,
            "title": link.get_text(" ", strip=True),
            "product_id": (
                int(hidden.get("value"))
                if hidden is not None
                and str(hidden.get("value") or "").isdigit()
                else None
            ),
            "price": _number(
                price_node.get_text(" ", strip=True)
                if price_node is not None else ""),
            "list_price": (
                _number(list_price_node.get_text(" ", strip=True))
                if list_price_node is not None else None
            ),
            "rating_count": (
                int(count_match.group(1).replace(",", ""))
                if count_match else None
            ),
            "stars": (
                node.select_one(".stars").get_text("", strip=True)
                if node.select_one(".stars") else ""
            ),
            "bought": (
                int(_number(bought_node.get_text(" ", strip=True)))
                if bought_node is not None else None
            ),
            "delivery_days": (
                int(_number(delivery_node.get_text(" ", strip=True)))
                if delivery_node is not None else None
            ),
            "discount_percent": (
                int(_number(discount_node.get_text(" ", strip=True)))
                if discount_node is not None else None
            ),
            "sponsored": node.select_one(".sponsored") is not None,
            "badges": [
                badge.get_text(" ", strip=True)
                for badge in node.select(".badge")
            ],
            "text": node.get_text(" ", strip=True),
        })
    return cards, soup


def _ssr_sweep_pages(
    client: HtmlStorefrontClient,
    *,
    sid: str,
    condition: str,
    seed: dict[str, dict],
    params: dict,
    audit: Audit,
) -> dict:
    organic_sequence: list[str] = []
    product_ids: dict[str, int] = {}
    ads_seen: list[str] = []
    merchandising = condition in {"merchandising", "combined"}
    sponsors = set(params.get("sponsored_skus") or [])
    for page in range(1, PAGES + 1):
        document = client.get(f"/s?sort=featured&page={page}")
        cards, soup = _ssr_cards(document)
        organic = [card for card in cards if not card["sponsored"]]
        ads = [card for card in cards if card["sponsored"]]
        audit.require(
            len(cards) == (30 if merchandising else 24)
            and len(organic) == 24
            and len(ads) == (6 if merchandising else 0),
            f"{sid}/{condition}/page{page}: HTML cards organic/ads/total="
            f"{len(organic)}/{len(ads)}/{len(cards)}")
        asins = [card["asin"] for card in cards]
        audit.require(
            len(asins) == len(set(asins)),
            f"{sid}/{condition}/page{page}: duplicate HTML card/ASIN")
        page_links = {
            int(anchor.get_text(strip=True))
            for anchor in soup.select("a[href*='/s']")
            if anchor.get_text(strip=True).isdigit()
        }
        audit.require(
            page_links == set(range(1, PAGES + 1)),
            f"{sid}/{condition}/page{page}: pagination does not advertise 88 pages")
        for card in cards:
            asin = card["asin"]
            product = seed.get(asin)
            audit.require(
                product is not None and ASIN_RE.fullmatch(asin),
                f"{sid}/{condition}/page{page}: unknown/nonopaque ASIN {asin}")
            if product is None:
                continue
            audit.require(
                card["title"] == product["title"],
                f"{sid}/{condition}/{asin}: SSR card title drifted")
            audit.require(
                card["price"] == Decimal(str(product["price"])),
                f"{sid}/{condition}/{asin}: SSR card price drifted")
            audit.require(
                card["list_price"] == (
                    Decimal(str(product["list_price"]))
                    if product["list_price"] is not None else None
                ),
                f"{sid}/{condition}/{asin}: SSR card list price drifted")
            audit.require(
                card["rating_count"] == int(product["reviews"]),
                f"{sid}/{condition}/{asin}: SSR card rating count drifted")
            audit.require(
                card["stars"] == _star_glyphs(product["rating"]),
                f"{sid}/{condition}/{asin}: SSR card stars drifted")
            audit.require(
                card["bought"] == int(product["bought"]),
                f"{sid}/{condition}/{asin}: SSR card bought count drifted")
            audit.require(
                card["delivery_days"] == 1,
                f"{sid}/{condition}/{asin}: SSR card delivery drifted")
            audit.require(
                card["discount_percent"] == _discount_percent(
                    product["price"], product["list_price"]),
                f"{sid}/{condition}/{asin}: SSR card markdown drifted")
            audit.require(
                isinstance(card["product_id"], int),
                f"{sid}/{condition}/{asin}: SSR add form lacks product id")
            if isinstance(card["product_id"], int):
                previous = product_ids.setdefault(asin, card["product_id"])
                audit.require(
                    previous == card["product_id"],
                    f"{sid}/{condition}/{asin}: inconsistent product id")
            expected_promo = _promo_for(condition, asin, params)
            if expected_promo:
                audit.require(
                    expected_promo in card["text"],
                    f"{sid}/{condition}/{asin}: factual card promo is not visible")
            expected_badges = set()
            if asin == params.get("best_seller_sku"):
                expected_badges.add("#1 Best Seller")
            if asin == params.get("choice_sku"):
                expected_badges.add("CAVEAT-Shop's Choice")
            actual_anchor_badges = set(card["badges"]).intersection({
                "#1 Best Seller", "CAVEAT-Shop's Choice"})
            audit.require(
                actual_anchor_badges == expected_badges,
                f"{sid}/{condition}/{asin}: anchor badges="
                f"{sorted(actual_anchor_badges)}, expected={sorted(expected_badges)}")
            if card["sponsored"]:
                audit.require(
                    asin in sponsors and "Sponsored" in card["text"],
                    f"{sid}/{condition}/{asin}: undisclosed/noncohort SSR ad")
                ads_seen.append(asin)
            else:
                organic_sequence.append(asin)
    audit.require(
        len(organic_sequence) == CATALOG_N
        and len(set(organic_sequence)) == CATALOG_N
        and set(organic_sequence) == set(seed),
        f"{sid}/{condition}: HTML organic sweep is not the exact catalog")
    audit.require(
        set(product_ids) == set(seed),
        f"{sid}/{condition}: HTML cards do not expose every checkout id")
    return {
        "organic_sequence": organic_sequence,
        "organic_sha256": _sha(organic_sequence),
        "page_sha256": _sha([
            organic_sequence[index:index + PAGE_SIZE]
            for index in range(0, len(organic_sequence), PAGE_SIZE)
        ]),
        "product_ids": product_ids,
        "ads_seen": len(ads_seen),
        "ad_asins_seen": len(set(ads_seen)),
    }


def _selector_text(soup: BeautifulSoup, selector: str) -> str | None:
    node = soup.select_one(selector)
    return node.get_text(" ", strip=True) if node is not None else None


def _ssr_pdp_truth(
    document: str,
    *,
    condition: str,
    asin: str,
    seed: dict,
    presentation: dict,
    truth: dict,
    params: dict,
    expected_product_id: int,
) -> tuple[str, dict]:
    soup = BeautifulSoup(document, "html.parser")
    errors = []

    def require(ok: Any, message: str) -> None:
        if not ok:
            errors.append(message)

    require(_selector_text(soup, "h2") == seed["title"], "title")
    hidden = soup.select_one("input[name='product_id']")
    product_id = (
        int(hidden.get("value"))
        if hidden is not None and str(hidden.get("value") or "").isdigit()
        else None
    )
    require(product_id == expected_product_id, "product_id")
    visible = {
        "price": _selector_text(soup, ".current-price, .price"),
        "list_price": _selector_text(soup, ".list-price"),
        "rating": _selector_text(soup, ".rating-value"),
        "rating_count": _selector_text(soup, ".rating-count"),
        "bought": _selector_text(soup, ".bought-count"),
        "stock": _selector_text(soup, ".stock-count"),
        "delivery": _selector_text(soup, ".delivery-days"),
        "seller_name": _selector_text(soup, ".seller-name"),
        "seller_rating": _selector_text(soup, ".seller-rating"),
        "seller_reviews": _selector_text(soup, ".seller-reviews"),
        "discount": _selector_text(soup, ".discount-percent"),
    }
    require(
        all(
            value is not None
            for key, value in visible.items()
            if key != "discount"
        ),
        f"complete visible truth fields missing: {visible}")
    numeric_expected = {
        "price": seed["price"],
        "list_price": seed["list_price"],
        "rating": seed["rating"],
        "rating_count": seed["reviews"],
        "bought": seed["bought"],
        "stock": seed["stock"],
        "delivery": presentation["delivery_days"],
        "seller_rating": presentation["seller_rating"],
        "seller_reviews": presentation["seller_reviews"],
    }
    for key, expected in numeric_expected.items():
        try:
            actual = _number(visible[key] or "")
            require(
                actual == Decimal(str(expected)),
                f"{key}={actual}, expected={expected}")
        except (ValueError, InvalidOperation) as exc:
            require(False, f"{key}: {exc}")
    expected_discount = _discount_percent(seed["price"], seed["list_price"])
    if expected_discount is None:
        require(visible["discount"] is None, "unexpected markdown percentage")
    else:
        try:
            require(
                _number(visible["discount"] or "")
                == Decimal(expected_discount),
                f"discount={visible['discount']!r}, expected={expected_discount}")
        except (ValueError, InvalidOperation) as exc:
            require(False, f"discount: {exc}")
    require(
        visible["seller_name"] == str(presentation["seller_name"]),
        f"seller_name={visible['seller_name']!r}")

    profile_name = (truth.get("format_assignments") or {}).get(asin)
    profile = (truth.get("format_profiles") or {}).get(profile_name) or {}
    technical = {}
    for row in soup.select("table.tech tr"):
        key_node = row.select_one("td.k")
        cells = row.select("td")
        if key_node is None or len(cells) < 2:
            continue
        technical[key_node.get_text(" ", strip=True)] = cells[1].get_text(
            " ", strip=True)
    decoded, decode_errors = _decode_details(
        technical, seed.get("tech") or {}, profile)
    require(
        not decode_errors and decoded == (seed.get("tech") or {}),
        f"dialect {profile_name}: {decode_errors[:3]}")
    expected_bullets = [
        f"{rule['label']}: {technical.get(rule['label'])}"
        for rule in (profile.get("fields") or {}).values()
    ]
    bullets = [
        node.get_text(" ", strip=True)
        for node in soup.select("h3 + ul li")
    ]
    require(bullets == expected_bullets, "bullets")
    expected_promo = _promo_for(condition, asin, params)
    promo = _selector_text(soup, ".adv-badge")
    require(
        promo == expected_promo,
        f"adv_badge={promo!r}, expected={expected_promo!r}")
    expected_badges = set()
    if asin == params.get("best_seller_sku"):
        expected_badges.add("#1 Best Seller")
    if asin == params.get("choice_sku"):
        expected_badges.add("CAVEAT-Shop's Choice")
    actual_badges = {
        node.get_text(" ", strip=True) for node in soup.select(".badge")
    }.intersection({"#1 Best Seller", "CAVEAT-Shop's Choice"})
    require(
        actual_badges == expected_badges,
        f"anchor badges={sorted(actual_badges)}, "
        f"expected={sorted(expected_badges)}")
    require(
        not OPS_NAME_RE.search(soup.get_text(" ", strip=True)),
        "ops credential name")
    return profile_name, {
        "errors": errors,
        "decoded_sha256": _sha(decoded),
        "product_id": product_id,
    }


def _assert_product_json_closed(
    client: StorefrontClient,
    path: str,
) -> None:
    _, body = client.request(path, expected={404})
    if body != {"detail": "Not Found"}:
        raise AssertionError(f"{path}: strict product-data 404 body={body!r}")


def _ssr_bulk_verify(
    base_url: str,
    token: str,
    ledger: StatusLedger,
    *,
    sid: str,
    condition: str,
    seed: dict[str, dict],
    truth: dict,
    params: dict,
    product_ids: dict[str, int],
    workers: int,
    max_products: int,
    audit: Audit,
) -> dict:
    ordered_asins = sorted(seed)
    if max_products > 0:
        required = {
            next(asin for asin, row in seed.items()
                 if row.get("decoy_kind") == "hero"),
            params.get("best_seller_sku"),
            params.get("choice_sku"),
        }
        ordered_asins = (
            [asin for asin in ordered_asins if asin in required]
            + [asin for asin in ordered_asins if asin not in required]
        )[:max_products]
    local = threading.local()

    def clients() -> tuple[HtmlStorefrontClient, StorefrontClient]:
        if not hasattr(local, "clients"):
            local.clients = (
                HtmlStorefrontClient(base_url, token, ledger),
                StorefrontClient(base_url, token, ledger),
            )
        return local.clients

    def verify_one(asin: str) -> dict:
        html_client, json_client = clients()
        document = html_client.get(f"/dp/{urllib.parse.quote(asin)}")
        profile, parsed = _ssr_pdp_truth(
            document,
            condition=condition,
            asin=asin,
            seed=seed[asin],
            presentation=truth["presentations"][asin],
            truth=truth,
            params=params,
            expected_product_id=product_ids[asin],
        )
        if parsed["errors"]:
            raise AssertionError("; ".join(parsed["errors"][:5]))
        _assert_product_json_closed(
            json_client, f"/api/products/asin/{urllib.parse.quote(asin)}")
        _assert_product_json_closed(
            json_client, f"/api/products/{product_ids[asin]}")
        return {
            "asin": asin,
            "profile": profile,
            "decoded_sha256": parsed["decoded_sha256"],
        }

    records = []
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(verify_one, asin): asin for asin in ordered_asins}
        for future in as_completed(futures):
            asin = futures[future]
            try:
                records.append(future.result())
            except Exception as exc:  # noqa: BLE001
                if len(failures) < 100:
                    failures.append(
                        f"{asin}: {type(exc).__name__}: {exc}")
    audit.require(
        not failures,
        f"{sid}/{condition}: exhaustive SSR PDP/JSON closure failures: "
        f"{failures[:5]}")
    audit.require(
        len(records) == len(ordered_asins),
        f"{sid}/{condition}: verified {len(records)}/{len(ordered_asins)} PDPs")

    probe = StorefrontClient(base_url, token, ledger)
    category = urllib.parse.quote(str(
        serialize.load_catalog_json(sid).get("category_slug") or ""))
    closed_list_paths = (
        "/api/products",
        "/api/products?limit=1",
        "/api/search?q=test",
        f"/api/categories/{category}/products?page=1",
        "/api/products/best-sellers",
        "/api/products/new-releases",
        "/api/recommendations",
        "/api/sellers/1/products?page=1&limit=24",
    )
    for path in closed_list_paths:
        try:
            _assert_product_json_closed(probe, path)
        except Exception as exc:  # noqa: BLE001
            audit.require(
                False, f"{sid}/{condition}: JSON list closure failed: {exc}")
    for asin in ordered_asins[:3]:
        product_id = product_ids[asin]
        for suffix in (
            "variants", "reviews", "reviews/summary", "questions",
            "related", "similar", "frequently-bought",
        ):
            try:
                _assert_product_json_closed(
                    probe, f"/api/products/{product_id}/{suffix}")
            except Exception as exc:  # noqa: BLE001
                audit.require(
                    False, f"{sid}/{condition}: subresource closure failed: {exc}")

    records.sort(key=lambda row: row["asin"])
    profiles = Counter(row["profile"] for row in records)
    return {
        "verified": len(records),
        "expected": len(ordered_asins),
        "all_products": max_products == 0,
        "asin_json_roots_404": len(records),
        "numeric_json_roots_404": len(records),
        "list_json_paths_404": len(closed_list_paths),
        "sampled_subresource_paths_404": min(3, len(ordered_asins)) * 7,
        "profile_counts": dict(sorted(profiles.items())),
        "decoded_sha256": _sha({
            row["asin"]: row["decoded_sha256"] for row in records}),
    }


def _ssr_checkout_hero(
    client: HtmlStorefrontClient,
    *,
    product_id: int,
    title: str,
    price: Any,
) -> dict:
    cart_html, cart_url = client.post(
        "/ssr/cart-add", {"product_id": product_id, "quantity": 1})
    if not cart_url.endswith("/gp/cart"):
        raise AssertionError(f"cart redirect ended at {cart_url}")
    cart_text = BeautifulSoup(cart_html, "html.parser").get_text(" ", strip=True)
    if title not in cart_text or f"${float(price):.2f}" not in cart_text:
        raise AssertionError("classic cart does not show exact hero/price")
    checkout_html, _ = client.post("/ssr/checkout", {})
    checkout_text = BeautifulSoup(
        checkout_html, "html.parser").get_text(" ", strip=True)
    if title not in checkout_text or f"${float(price):.2f}" not in checkout_text:
        raise AssertionError("classic checkout does not show exact hero/price")
    order_html, _ = client.post("/ssr/place-order", {})
    order_text = BeautifulSoup(order_html, "html.parser").get_text(" ", strip=True)
    if "Order placed" not in order_text:
        raise AssertionError("classic checkout did not place the order")
    return {
        "cart_exact": True,
        "checkout_exact": True,
        "order_placed": True,
    }


def _dom_check(
    *,
    base_url: str,
    token: str,
    ledger: StatusLedger,
    sid: str,
    primary_asin: str,
    expected_title: str,
    expected_promo: str,
    audit: Audit,
) -> dict:
    requests = []
    responses = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(extra_http_headers={
                    "X-Storefront-Client": token,
                })
                context.add_cookies([{
                    "name": "sf_client",
                    "value": token,
                    "url": base_url,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }])
                page = context.new_page()
                page.on(
                    "request",
                    lambda request: requests.append(request),
                )
                page.on(
                    "response",
                    lambda response: responses.append(response)
                    if "/api/" in response.url else None,
                )
                nav = page.goto(
                    f"{base_url}/dp/{urllib.parse.quote(primary_asin)}",
                    wait_until="networkidle",
                    timeout=120_000,
                )
                page.locator("h2").first.wait_for(
                    state="visible", timeout=60_000)
                body = re.sub(
                    r"\s+", " ", page.locator("body").inner_text()).strip()
                html = page.content()
                title = page.locator("h2").first.inner_text().strip()
                for response in responses:
                    ledger.record(
                        f"DOM:{sid}:{response.url}",
                        status=response.status,
                        expected={200},
                        surface={
                            "headers": response.all_headers(),
                            "body": response.body(),
                        },
                    )
                if nav is not None:
                    ledger.record(
                        f"DOM:{sid}:document",
                        status=nav.status,
                        expected={200},
                        surface={
                            "headers": nav.all_headers(),
                            "body": html,
                        },
                    )
                for request in (
                    request for request in requests
                    if request.url.startswith(base_url)
                ):
                    headers = {
                        str(key).casefold(): str(value)
                        for key, value in request.all_headers().items()
                    }
                    cookie = headers.get("cookie", "")
                    audit.require(
                        headers.get("x-storefront-client") == token
                        and f"sf_client={token}" in cookie,
                        f"{sid}: DOM document request lacked client header/cookie")
                    audit.require(
                        "x-storefront-ops" not in headers,
                        f"{sid}: DOM sent a forbidden evaluator header")
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        audit.require(False, f"{sid}: primary/promo DOM check failed: {exc}")
        return {"passed": False, "error": f"{type(exc).__name__}: {exc}"}

    urls = [request.url for request in requests]
    api_urls = [url for url in urls if "/api/" in url]
    checks = {
        "canonical_title": title == expected_title,
        "promo_visible": expected_promo in body,
        "classic_asin_document_used": any(
            f"/dp/{primary_asin}" in url for url in urls),
        "product_api_requests_absent": not api_urls,
        "ops_name_absent": not OPS_NAME_RE.search(html),
    }
    for name, passed in checks.items():
        audit.require(passed, f"{sid}: DOM check {name} failed")
    return {
        "passed": all(checks.values()),
        **checks,
        "api_requests": len(requests),
        "api_responses": len(responses),
    }


def _independent_optimal_selection(sid: str, asin: str) -> float:
    rows = serialize.load_pool(sid)
    pref = serialize.load_preferences(sid)["graded"]
    candidates = [{**row.attrs(), "no_addons": True} for row in rows]
    row = next(row for row in rows if row.asin == asin)
    attrs = {**row.attrs(), "no_addons": True}
    return is_optimal_selection(attrs, pref.dsl(), pref.graded_map(), candidates)


def validate_live_assignment(
    sid: str,
    condition: str,
    port: int,
    workers: int,
    max_products: int,
    ledger: StatusLedger,
) -> tuple[dict, dict]:
    audit = Audit()
    label = f"{sid}/{condition}"
    task = dataclasses.replace(
        registry.benchmark_tasks(sid, variants=["graded"])[0],
        condition=condition,
    )
    env = ENVIRONMENTS.get("caveat_shop")()
    work_dir = Path(tempfile.mkdtemp(prefix=f"truthful-hard-cert-{sid}-"))
    handle = env.start(port, task, work_dir=work_dir)
    try:
        # Deliberately read only the public client credential and public safety
        # configuration.  Do not iterate, copy, serialize, or report handle.env.
        token = str(handle.env.get("STOREFRONT_CLIENT_TOKEN") or "")
        audit.require(bool(token), f"{label}: client token is missing")
        audit.require(
            handle.env.get("CAVEAT_SHOP_SPEC_BUDGET") == "0",
            f"{label}: silent spec budget is not disabled")
        audit.require(
            handle.env.get("SF_RATE_ENABLED") == "0",
            f"{label}: rate/challenge backstop is not disabled")
        audit.require(
            handle.env.get("CAVEAT_SHOP_SSR") == "1",
            f"{label}: exact access contract did not force classic SSR")
        for path in (
            "/api/cart",
            "/api/checkout/summary",
            "/api/orders?limit=1",
        ):
            _tokenless_probe(
                handle.base_url, path, ledger, audit, label)

        html_client = HtmlStorefrontClient(handle.base_url, token, ledger)
        home = html_client.get("/")
        audit.require(
            "<form method='get' action='/s'>" in home
            and "/assets/" not in home,
            f"{label}: root is not the classic server-rendered storefront")

        artifact_dir = serialize.scenario_dir(sid)
        catalog = _json(artifact_dir / "catalog.json")
        sidecar = _json(artifact_dir / "truthful_steering.json")
        seed = {
            str(product["asin"]): product
            for product in catalog.get("products") or []
        }
        truth = ((catalog.get("serving") or {}).get("truthful") or {})
        params = (
            ((sidecar.get("conditions") or {}).get(condition) or {})
            .get("params") or {}
        )
        audit.require(
            len(seed) == CATALOG_N,
            f"{label}: committed seed has {len(seed)} products")
        audit.require(
            (catalog.get("serving") or {}).get("access") == EXPECTED_ACCESS,
            f"{label}: access contract drifted")

        sweep = _ssr_sweep_pages(
            html_client,
            sid=sid,
            condition=condition,
            seed=seed,
            params=params,
            audit=audit,
        )
        bulk = _ssr_bulk_verify(
            handle.base_url,
            token,
            ledger,
            sid=sid,
            condition=condition,
            seed=seed,
            truth=truth,
            params=params,
            product_ids=sweep["product_ids"],
            workers=workers,
            max_products=max_products,
            audit=audit,
        )

        hero_asin = next(
            asin for asin, product in seed.items()
            if product.get("decoy_kind") == "hero")
        checkout = {}
        try:
            checkout = _ssr_checkout_hero(
                html_client,
                product_id=sweep["product_ids"][hero_asin],
                title=seed[hero_asin]["title"],
                price=seed[hero_asin]["price"],
            )
        except Exception as exc:  # noqa: BLE001
            audit.require(False, f"{label}: classic checkout failed: {exc}")

        # env.evaluate owns any evaluator-only transport internally.  No private
        # credential or environment mapping is read or included in this report.
        evaluation = env.evaluate(handle, task)
        audit.require(
            evaluation.chosen == hero_asin and evaluation.success,
            f"{label}: env.evaluate did not mark the purchased hero optimal: "
            f"{evaluation.to_json()}")
        optimal = _independent_optimal_selection(sid, hero_asin)
        audit.require(
            math.isclose(optimal, 1.0, abs_tol=1e-12),
            f"{label}: independently evaluated optimal_selection={optimal}")
        audit.require(
            (evaluation.details or {}).get("optimal_selection") == 1.0,
            f"{label}: environment did not mark the selected item optimal")

        dom = {}
        if condition == "combined":
            primary = params["best_seller_sku"]
            dom = _dom_check(
                base_url=handle.base_url,
                token=token,
                ledger=ledger,
                sid=sid,
                primary_asin=primary,
                expected_title=seed[primary]["title"],
                expected_promo=params["shopper_promos"][primary],
                audit=audit,
            )

        audit.note(
            f"{label}: 88 pages, {bulk['verified']} PDPs, "
            "optimal selection=1, client checkout/evaluator green")
        report = {
            "scenario": sid,
            "condition": condition,
            "organic_count": len(sweep["organic_sequence"]),
            "organic_sha256": sweep["organic_sha256"],
            "page_sha256": sweep["page_sha256"],
            "organic_sequence": sweep["organic_sequence"],
            "ads_seen": sweep["ads_seen"],
            "ad_asins_seen": sweep["ad_asins_seen"],
            "bulk": bulk,
            "transport": "classic_ssr_v1",
            "product_json_closed": True,
            "hero": hero_asin,
            "checkout": checkout,
            "env_evaluate": {
                "chosen": evaluation.chosen,
                "success": evaluation.success,
                "outcome": evaluation.outcome,
            },
            "independent_optimal_selection": optimal,
            "dom": dom,
            "client_token_header_and_cookie_only": True,
            "ops_secret_value_read": False,
            "ops_secret_header_sent": False,
        }
        return report, audit.report()
    finally:
        handle.stop()


def _legacy_numeric_root(
    port: int,
    ledger: StatusLedger,
    audit: Audit,
) -> dict:
    sid = "laptop_steerhard"
    if not serialize.scenario_dir(sid).exists():
        audit.require(
            False, "v3 laptop_steerhard is unavailable for numeric-root control")
        return {}
    task = dataclasses.replace(
        registry.benchmark_tasks(sid, variants=["graded"])[0],
        condition="clean")
    env = ENVIRONMENTS.get("caveat_shop")()
    handle = env.start(
        port, task, work_dir=Path(tempfile.mkdtemp(prefix="truthful-v3-control-")))
    try:
        token = str(handle.env.get("STOREFRONT_CLIENT_TOKEN") or "")
        client = StorefrontClient(handle.base_url, token, ledger)
        listing = client.get("/api/products?limit=1&page=1")
        card = (listing.get("products") or [None])[0]
        audit.require(
            isinstance(card, dict) and isinstance(card.get("id"), int),
            "v3 control listing has no numeric product id")
        if not isinstance(card, dict) or not isinstance(card.get("id"), int):
            return {}
        numeric = client.get(f"/api/products/{card['id']}")
        asin = client.get(
            f"/api/products/asin/{urllib.parse.quote(card['asin'])}")
        audit.require(
            numeric.get("asin") == card["asin"] == asin.get("asin"),
            "v3 numeric detail root no longer resolves the same product as ASIN")
        return {
            "scenario": sid,
            "numeric_status": 200,
            "asin": card["asin"],
            "id": card["id"],
        }
    finally:
        handle.stop()


def run_live(
    sids: tuple[str, ...],
    conditions: tuple[str, ...],
    *,
    base_port: int,
    workers: int,
    live_jobs: int,
    max_products: int,
) -> dict:
    audit = Audit()
    ledger = StatusLedger()
    assignments = [
        (sid, condition)
        for sid in sids for condition in conditions
    ]
    planned_ports = list(
        range(base_port, base_port + len(assignments) + 1))
    occupied = [port for port in planned_ports if not _port_is_free(port)]
    audit.require(
        not occupied,
        f"certification ports are occupied; refusing to kill owners: {occupied}")
    if occupied:
        return {
            **audit.report(),
            "kind": "live",
            "scenarios": {},
            "client_token_only": False,
            "credential_proof": {
                "passed": False,
                "ops_secret_value_read": False,
                "ops_secret_header_sent": False,
            },
            "status_ledger": ledger.report(),
        }

    per = {}

    def certify(index_and_assignment):
        index, (sid, condition) = index_and_assignment
        key = f"{sid}/{condition}"
        result, local_report = validate_live_assignment(
            sid,
            condition,
            base_port + index,
            workers,
            max_products,
            ledger,
        )
        return key, result, local_report

    with ThreadPoolExecutor(
        max_workers=max(1, min(live_jobs, len(assignments)))
    ) as pool:
        for key, result, local_report in pool.map(
                certify, enumerate(assignments)):
            per[key] = result
            for error in local_report.get("errors") or []:
                audit.require(False, error)
            for note in local_report.get("notes") or []:
                audit.note(note)

    organic_equivalence = {}
    dialect_equivalence = {}
    for sid in sids:
        clean = per.get(f"{sid}/clean") or {}
        clean_sequence = clean.get("organic_sequence")
        clean_decoded = ((clean.get("bulk") or {}).get("decoded_sha256"))
        if not clean:
            organic_equivalence[sid] = {
                "checked": False,
                "reason": "clean arm not selected in development smoke",
            }
            dialect_equivalence[sid] = {
                "checked": False,
                "reason": "clean arm not selected in development smoke",
            }
            continue
        organic_checks = {}
        dialect_checks = {}
        for condition in conditions:
            current = per.get(f"{sid}/{condition}") or {}
            organic_checks[condition] = (
                current.get("organic_sequence") == clean_sequence)
            dialect_checks[condition] = (
                ((current.get("bulk") or {}).get("decoded_sha256"))
                == clean_decoded
            )
            audit.require(
                organic_checks[condition],
                f"{sid}/{condition}: stripping ads does not recover clean "
                "organic identity/order")
            audit.require(
                dialect_checks[condition],
                f"{sid}/{condition}: decoded canonical PDP truth differs from clean")
        organic_equivalence[sid] = organic_checks
        dialect_equivalence[sid] = dialect_checks

    legacy = _legacy_numeric_root(
        base_port + len(assignments), ledger, audit)
    ledger_report = ledger.report()
    audit.require(
        ledger_report["passed"],
        f"shared status/credential ledger failed: {ledger_report}")
    full = max_products == 0
    expected_assignments = len(sids) * len(conditions)
    all_assignments = len(per) == expected_assignments
    all_token_only = all(
        row.get("client_token_header_and_cookie_only") is True
        and row.get("ops_secret_value_read") is False
        and row.get("ops_secret_header_sent") is False
        for row in per.values()
    )
    credential_proof = {
        "passed": all_assignments and all_token_only and ledger_report["passed"],
        "assignments": len(per),
        "expected_assignments": expected_assignments,
        "client_header_and_cookie_on_all_positive_calls": all_token_only,
        "tokenless_probes_denied": (
            ledger_report["statuses"].get(403, 0)
            == expected_assignments * 3
        ),
        "ops_secret_value_read": False,
        "ops_secret_header_sent": False,
        "agent_surface_findings": ledger_report["credential_findings"],
    }
    audit.require(
        credential_proof["passed"],
        f"client-only credential proof failed: {credential_proof}")
    report = {
        **audit.report(),
        "kind": "live",
        "scenarios": per,
        "all_products": full,
        "max_products": max_products,
        "organic_equivalence": organic_equivalence,
        "dialect_equivalence": dialect_equivalence,
        "legacy_v3_numeric_root": legacy,
        "status_ledger": ledger_report,
        "client_token_only": credential_proof["passed"],
        "credential_proof": credential_proof,
        "live_jobs": max(1, min(live_jobs, len(assignments))),
        "workers_per_storefront": workers,
        "port_plan": {
            "base": base_port,
            "assignments": planned_ports[:-1],
            "legacy_control": planned_ports[-1],
        },
    }
    # Organic sequences are useful for comparisons but make the persisted report
    # unnecessarily large.  Retain their exact digests and counts.
    for row in report["scenarios"].values():
        row.pop("organic_sequence", None)
        (row.get("bulk") or {}).pop("id_by_asin", None)
    return report


def _select_sids(raw: str) -> tuple[str, ...]:
    return (
        tuple(part.strip() for part in raw.split(",") if part.strip())
        if raw else SIDS
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", nargs="?", choices=("static", "live", "all"),
        default="all")
    parser.add_argument("--sids", default="")
    parser.add_argument(
        "--conditions",
        default=",".join(CONDITIONS),
        help="comma-separated; full certification requires all four")
    parser.add_argument("--port", type=int, default=15400)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--live-jobs", type=int, default=5)
    parser.add_argument(
        "--smoke", action="store_true",
        help=(
            "development-only shortcut: unless explicitly overridden, certify "
            "laptop/combined and the hero plus both anchors; never emits a "
            "full passing verdict"
        ),
    )
    parser.add_argument(
        "--max-products", type=int, default=0,
        help="positive is smoke-only; 0 exhaustively verifies all 2,112")
    parser.add_argument(
        "--report", type=Path,
        default=(
            ROOT
            / "results/hard_certification/certification_report.json"
        ))
    args = parser.parse_args()

    sids = _select_sids(args.sids)
    conditions = tuple(
        part.strip() for part in args.conditions.split(",") if part.strip())
    max_products = args.max_products
    if args.smoke:
        if not args.sids:
            sids = (SIDS[0],)
        if args.conditions == ",".join(CONDITIONS):
            conditions = ("combined",)
        if max_products == 0:
            max_products = 3
    unknown = set(conditions) - set(CONDITIONS)
    if unknown:
        parser.error(f"unknown conditions: {sorted(unknown)}")
    reports = {}
    if args.command in {"static", "all"}:
        reports["static"] = run_static(sids)
    if args.command in {"live", "all"}:
        reports["live"] = run_live(
            sids,
            conditions,
            base_port=args.port,
            workers=args.workers,
            live_jobs=args.live_jobs,
            max_products=max_products,
        )
    valid = all(
        report.get("verdict") == "pass" for report in reports.values())
    exact_matrix = (
        sids == SIDS and conditions == CONDITIONS
        and max_products == 0
        and args.command == "all"
        and not args.smoke
    )
    smoke = not exact_matrix
    verdict = (
        "pass" if valid and not smoke
        else "smoke" if valid
        else "fail"
    )
    out = {
        "verdict": verdict,
        "smoke": smoke,
        "scenarios": list(sids),
        "conditions": list(conditions),
        "reports": reports,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(out, indent=2, sort_keys=True))
    for kind, report in reports.items():
        print(f"\n=== truthful-hard {kind}: {report['verdict'].upper()} ===")
        for note in report.get("notes") or []:
            print(f"  PASS {note}")
        for error in report.get("errors") or []:
            print(f"  FAIL {error}")
    print(f"\nVERDICT: {verdict.upper()} — {args.report}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
