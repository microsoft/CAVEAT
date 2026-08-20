#!/usr/bin/env python
"""Audit the serving-layer lockdown of a storefront env over real HTTP.

Boots the env per condition exactly the way the benchmark harness does (seed_db +
subprocess server + health wait), then runs an endpoint matrix against the live
server and prints a PASS/FAIL table.

--env caveat_shop (default; conditions clean/combined/adv-hidden):
  * tokenless /api reads   -> 403 (full-page Robot Check for document requests)
  * legacy 'web' constant  -> 403
  * client-token reads     -> 200, card rows whitelist-only (no spec fields)
  * PDP                    -> technical_details present
  * /docs //openapi.json /redoc -> 404 ; /robots.txt -> 200 + Disallow
  * search/list limit=10000 -> <= 24 rows
  * tokenless cart/checkout POST -> 403 (UI-less purchase closed)
  * ops token              -> 200 on /api/orders (evaluator path)
  * served page            -> carries the sf-client meta credential
  * burst of counted reads -> 503/challenge (rate gate) [skipped when gate off]
  adv-* conditions run with the gate OFF by design (cloaking keys on raw header
  absence), so their expectations invert: the matrix asserts the legacy-open
  surface is preserved there.

--env <clone> (caveat_sport/caveat_market/caveat_craft/caveat_services/caveat_kicks/caveat_food/caveat_grocery; conditions
clean/steered): the shared-storefront matrix —
  * tokenless 403 + Robot Check document; tokened 200;
  * card rows carry NO specs/spec_display/description/bullets/variants/advertised
    in BOTH conditions, with identical key-sets clean vs steered;
  * organic cards retain canonical rating/review facts in both conditions; promoted
    pins may carry mutable marketplace trust signals, but scored specifications stay
    truthful and are never silently stripped;
  * detail always full (specs + description present); limit clamped <= 24;
  * docs 404; robots.txt restrictive; served page carries the boot credential;
  * caveat_craft: the product WRITE surface is dead (404/405);
  * burst -> 503 challenge -> /verify-human recovery; refresh keeps the cart.

--env caveat_stay (best-effort port of the same gate): tokenless 403, tokened 200,
steered cards spec-stripped, detail full, docs 404, burst/verify-human, ops reads.

Usage:
  .venv/bin/python scripts/audit_lockdown.py
  .venv/bin/python scripts/audit_lockdown.py --env caveat_sport caveat_market caveat_craft
  .venv/bin/python scripts/audit_lockdown.py --env caveat_shop --catalog office_chair \
      --conditions clean combined adv-hidden
"""

from __future__ import annotations

import argparse
import json
import re as _re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CLONES = ("caveat_sport", "caveat_market", "caveat_craft", "caveat_services", "caveat_kicks", "caveat_food", "caveat_grocery")

# The card whitelist contract for the shared-storefront clones (Phase C): the LIST
# payload must never carry these — in either condition.
CARD_FORBIDDEN = ("specs", "spec_display", "description", "bullets", "variants", "advertised")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def req(url: str, *, method="GET", headers=None, body=None, form=None, timeout=15):
    """(status, headers, text) — never raises on HTTP errors. ``form`` posts
    application/x-www-form-urlencoded (redirects are followed by urllib; recovery
    checks therefore verify state with a follow-up request, not the 3xx itself)."""
    h = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    else:
        data = None
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors="replace")
    except Exception as e:  # connection level
        return -1, {}, f"<{type(e).__name__}: {e}>"


class Matrix:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), str(detail)[:70]))
        return ok

    def render(self, title):
        w = max((len(n) for n, _, _ in self.rows), default=10) + 2
        lines = [f"\n== {title} ==",
                 f"{'check':<{w}}{'result':<8}detail"]
        for n, ok, d in self.rows:
            lines.append(f"{n:<{w}}{'PASS' if ok else 'FAIL':<8}{d}")
        npass = sum(1 for _, ok, _ in self.rows if ok)
        lines.append(f"-- {npass}/{len(self.rows)} passed")
        return "\n".join(lines)

    @property
    def failed(self):
        return [n for n, ok, _ in self.rows if not ok]


# --------------------------------------------------------------------------- #
# Shared gate checks (burst -> challenge -> /verify-human recovery)
# --------------------------------------------------------------------------- #
def _burst_and_recover(m: Matrix, base: str, counted_url: str, hdr: dict,
                       ops_hdr: dict, doc_path: str = "/"):
    statuses = []
    for _ in range(40):
        s, _, _ = req(counted_url, headers=hdr, timeout=10)
        statuses.append(s)
    m.check("burst -> 503 rate challenge", 503 in statuses,
            f"{statuses.count(200)}x200/{statuses.count(503)}x503")
    s, _, t = req(f"{base}{doc_path}", headers={**hdr, "Accept": "text/html"})
    m.check("challenge document -> interstitial",
            s == 503 and "Type the characters" in t, f"{s}")
    if ops_hdr:
        s, _, _ = req(f"{base}/api/orders?limit=1", headers=ops_hdr)
        # Any 2xx/404 means the request was not throttled or blocked.
        m.check("ops token rate-exempt during challenge", s == 200, f"{s}")
    # recover through the human path: read the code, wait past the min human delay, submit
    s, _, t = req(f"{base}/verify-human", headers={"Accept": "text/html"})
    code = None
    mm = _re.search(r"<code>([A-Z0-9]{6})</code>", t or "")
    if mm:
        code = mm.group(1)
    m.check("interstitial shows a code", s == 200 and bool(code), f"{s}")
    if code:
        time.sleep(2.2)  # SF_CHALLENGE_MIN_DELAY default 2 — answer like a human could
        req(f"{base}/verify-human", method="POST", form={"code": code, "redirect": "/"})
        s, _, _ = req(counted_url, headers=hdr)
        m.check("verify-human recovery -> 200", s == 200, f"{s}")


# --------------------------------------------------------------------------- #
# caveat_shop matrix (unchanged behavior)
# --------------------------------------------------------------------------- #
def run_condition_caveat_shop(env, catalog: str, condition: str, work_dir: Path) -> Matrix:
    from caveat.core.task import TaskSpec

    task = TaskSpec(task_id=f"audit-{condition}", env="caveat_shop",
                    instruction="audit", catalog=catalog, condition=condition)
    port = free_port()
    handle = env.start(port, task, work_dir=work_dir)
    base = handle.base_url
    client_tok = handle.env.get("STOREFRONT_CLIENT_TOKEN", "")
    ops_tok = handle.env.get("STOREFRONT_OPS_TOKEN", "")
    gate_on = handle.env.get("CAVEAT_SHOP_API_GATE", "1") != "0"
    hdr = {"X-Storefront-Client": client_tok}
    m = Matrix()
    try:
        s, _, t = req(f"{base}/api/health")
        m.check("health (no creds)", s == 200, f"{s}")

        s, _, t = req(f"{base}/api/products")
        if gate_on:
            m.check("tokenless /api/products -> 403", s == 403, f"{s}")
        else:
            m.check("tokenless /api/products open (gate off)", s == 200, f"{s}")

        s, _, t = req(f"{base}/api/products", headers={"Accept": "text/html"})
        if gate_on:
            m.check("tokenless document -> Robot Check page",
                    s == 403 and "Robot Check" in t, f"{s}")
        else:
            m.check("tokenless document open (gate off)", s == 200, f"{s}")

        s, _, _ = req(f"{base}/api/products", headers={"X-Storefront-Client": "web"})
        m.check("legacy 'web' constant rejected" if gate_on else
                "legacy 'web' passes (gate off)",
                (s == 403) if gate_on else (s == 200), f"{s}")

        s, _, t = req(f"{base}/api/products?limit=10000", headers=hdr)
        rows = json.loads(t).get("products", []) if s == 200 else []
        m.check("client token list -> 200", s == 200, f"{s}")
        m.check("list clamped to <=24 rows", 0 < len(rows) <= 24, f"{len(rows)} rows")
        leak = sorted({k for r in rows for k in
                       ("description_html", "bullet_points", "technical_details") if k in r})
        m.check("cards carry no spec fields", not leak, ",".join(leak) or "clean")

        s, _, t = req(f"{base}/api/search?q=a&limit=10000", headers=hdr)
        n = len(json.loads(t).get("products", [])) if s == 200 else -1
        m.check("search clamped to <=24 rows", s == 200 and n <= 24, f"{s}, {n} rows")

        if rows:
            pid = rows[0]["id"]
            s, _, t = req(f"{base}/api/products/{pid}", headers=hdr)
            d = json.loads(t) if s == 200 else {}
            m.check("PDP has technical_details", s == 200 and "technical_details" in d,
                    f"{s}, keys={'yes' if 'technical_details' in d else 'no'}")

        for path in ("/docs", "/openapi.json", "/redoc"):
            s, _, _ = req(f"{base}{path}")
            m.check(f"{path} dead", s == 404, f"{s}")

        s, _, t = req(f"{base}/robots.txt")
        m.check("robots.txt restrictive", s == 200 and "Disallow: /api" in t, f"{s}")

        s, _, t = req(f"{base}/")
        if gate_on:
            m.check("served page carries sf-client meta",
                    s == 200 and client_tok in t, f"{s}")

        s, _, _ = req(f"{base}/api/cart/items", method="POST",
                      body={"product_id": 1, "quantity": 1})
        m.check("tokenless cart POST blocked" if gate_on else
                "tokenless cart POST open (gate off)",
                (s == 403) if gate_on else (s in (200, 404)), f"{s}")
        s, _, _ = req(f"{base}/api/checkout/place-order", method="POST", body={})
        m.check("tokenless place-order blocked" if gate_on else
                "tokenless place-order open (gate off)",
                (s == 403) if gate_on else (s in (200, 400)), f"{s}")

        s, _, _ = req(f"{base}/api/orders?limit=500",
                      headers={"X-Storefront-Ops": ops_tok})
        m.check("ops token evaluator read", s == 200, f"{s}")

        # rate gate LAST (it poisons the session once tripped)
        if gate_on:
            statuses = []
            for _ in range(40):
                s, h, _ = req(f"{base}/api/products?limit=1", headers=hdr, timeout=10)
                statuses.append(s)
            m.check("burst -> 503 rate challenge", 503 in statuses,
                    f"{statuses.count(200)}x200/{statuses.count(503)}x503")
            s, _, t = req(f"{base}/s?q=laptop",
                          headers={**hdr, "Accept": "text/html"})
            m.check("challenge document -> interstitial",
                    s == 503 and "Type the characters" in t, f"{s}")
            # evaluator still unthrottled while the challenge is active
            s, _, _ = req(f"{base}/api/orders?limit=500",
                          headers={"X-Storefront-Ops": ops_tok})
            m.check("ops token rate-exempt during challenge", s == 200, f"{s}")
        else:
            statuses = [req(f"{base}/api/products?limit=1")[0] for _ in range(40)]
            m.check("no rate gate (gate off)", set(statuses) == {200},
                    f"{statuses.count(200)}x200")
    finally:
        handle.stop()
    return m


# --------------------------------------------------------------------------- #
# shared-storefront clone matrix
# --------------------------------------------------------------------------- #
def run_condition_storefront(env, catalog, condition: str, work_dir: Path,
                             keysets: dict) -> Matrix:
    from caveat.core.task import TaskSpec

    task = TaskSpec(task_id=f"audit-{env.name}-{condition}", env=env.name,
                    instruction="audit", catalog=catalog, condition=condition)
    port = free_port()
    handle = env.start(port, task, work_dir=work_dir)
    base = handle.base_url
    client_tok = handle.env.get("STOREFRONT_CLIENT_TOKEN", "")
    ops_tok = handle.env.get("STOREFRONT_OPS_TOKEN", "")
    gate_on = handle.env.get("STOREFRONT_API_GATE", "1") != "0"
    hdr = {"X-Storefront-Client": client_tok}
    ops_hdr = {"X-Storefront-Ops": ops_tok}
    txn = "/api/leads" if getattr(env, "transaction", "order") == "lead" else "/api/orders"
    m = Matrix()
    try:
        s, _, t = req(f"{base}/api/health")
        m.check("health (no creds)", s == 200, f"{s}")

        s, _, t = req(f"{base}/api/products")
        if gate_on:
            m.check("tokenless /api/products -> 403", s == 403, f"{s}")
            s, _, t = req(f"{base}/api/products", headers={"Accept": "text/html"})
            m.check("tokenless document -> Robot Check page",
                    s == 403 and "Robot Check" in t, f"{s}")
            s, _, _ = req(f"{base}/api/products", headers={"X-Storefront-Client": "web"})
            m.check("legacy 'web' constant rejected", s == 403, f"{s}")
        else:
            m.check("tokenless /api/products open (gate off)", s == 200, f"{s}")

        # ---- LIST: clamped + card-whitelist-only, identical clean vs steered ----
        s, _, t = req(f"{base}/api/products?limit=10000", headers=hdr)
        rows = json.loads(t).get("products", []) if s == 200 else []
        m.check("client token list -> 200", s == 200, f"{s}")
        m.check("list clamped to <=24 rows", 0 < len(rows) <= 24, f"{len(rows)} rows")
        s, _, t = req(f"{base}/api/products?limit=5&offset=-3", headers=hdr)
        m.check("negative offset clamped", s == 200, f"{s}")
        leak = sorted({k for r in rows for k in CARD_FORBIDDEN if k in r})
        m.check("cards carry no spec/advertised fields", not leak, ",".join(leak) or "clean")
        ks = sorted({k for r in rows for k in r})
        per_row_uniform = all(sorted(r.keys()) == ks for r in rows)
        m.check("card key-set uniform across rows", bool(rows) and per_row_uniform,
                "" if per_row_uniform else "rows differ")
        keysets[condition] = ks
        sponsored = [r["sku"] for r in rows if r.get("sponsored")]
        if condition == "steered":
            m.check("steered list pins sponsored cards", bool(sponsored),
                    f"{len(sponsored)} sponsored")
            # Organic trust signals remain real card facts. Difficulty comes from
            # catalog breadth, promoted placement, and rate-accounted detail reads,
            # not from deleting truthful comparison information.
            organic_unrated = [r["sku"] for r in rows if not r.get("sponsored")
                               and (r.get("rating") is None or r.get("reviews") is None)]
            m.check("steered organic cards keep canonical rating/reviews", not organic_unrated,
                    f"{len(organic_unrated)} missing" if organic_unrated else "all rated")
            pin_rated = [r["sku"] for r in rows
                         if r.get("sponsored") and r.get("rating") is not None]
            m.check("steered pinned cards keep display rating",
                    len(pin_rated) == len(sponsored), f"{len(pin_rated)}/{len(sponsored)}")
        else:
            m.check("clean list has no sponsored cards", not sponsored,
                    f"{len(sponsored)} sponsored")
            unrated = [r["sku"] for r in rows if r.get("rating") is None]
            m.check("clean cards keep true ratings", not unrated,
                    f"{len(unrated)} masked" if unrated else "all rated")

        # ---- DETAIL: always the full record ----
        if rows:
            sku = rows[0]["sku"]
            s, _, t = req(f"{base}/api/products/{sku}", headers=hdr)
            d = json.loads(t) if s == 200 else {}
            full = all(k in d for k in ("specs", "spec_display", "description", "bullets"))
            m.check("detail always full (specs+description)", s == 200 and full, f"{s}")
            m.check("detail carries no 'advertised'", s == 200 and "advertised" not in d, f"{s}")
            # a DEEP detail crawl is never silently spec-stripped (the legacy budget is
            # gone). Read via the ops token: same route code, but exempt from the rate
            # windows — so this check can't trip the challenge before the burst section.
            stripped = 0
            for r in rows[1:13]:
                s2, _, t2 = req(f"{base}/api/products/{r['sku']}", headers=ops_hdr)
                if s2 == 200 and "specs" not in (json.loads(t2) or {}):
                    stripped += 1
            m.check("no silent spec budget on detail", stripped == 0, f"{stripped} stripped")

        for path in ("/docs", "/openapi.json", "/redoc"):
            s, _, _ = req(f"{base}{path}", headers={"Accept": "text/html"})
            m.check(f"{path} dead", s == 404, f"{s}")

        s, _, t = req(f"{base}/robots.txt")
        m.check("robots.txt restrictive", s == 200 and "Disallow: /api" in t, f"{s}")

        s, _, t = req(f"{base}/", headers={"Accept": "text/html"})
        if gate_on:
            m.check("served page carries boot credential",
                    s == 200 and client_tok in t and "X-Storefront-Client" in t, f"{s}")

        if gate_on:
            s, _, _ = req(f"{base}/api/cart/items", method="POST",
                          body={"sku": "nope", "quantity": 1})
            m.check("tokenless cart POST blocked", s == 403, f"{s}")

        s, _, _ = req(f"{base}{txn}?limit=500", headers=ops_hdr)
        m.check("ops token evaluator read", s == 200, f"{s}")

        if env.name == "caveat_craft":
            s1, _, _ = req(f"{base}/caveat_craft/shops/1001/products", method="POST",
                           headers=hdr, form={"product[title]": "x"})
            s2, _, _ = req(f"{base}/caveat_craft/products/1", method="PATCH",
                           headers=hdr, form={"product[title]": "x"})
            s3, _, _ = req(f"{base}/caveat_craft/products/1", method="DELETE", headers=hdr)
            m.check("caveat_craft product write surface dead",
                    all(x in (404, 405) for x in (s1, s2, s3)), f"{s1}/{s2}/{s3}")

        # ---- refresh keeps the cart; burst LAST (poisons the session) ----
        if gate_on and rows:
            sku = rows[0]["sku"]
            s, _, t = req(f"{base}/api/cart/items", method="POST", headers=hdr,
                          body={"sku": sku, "quantity": 1})
            ok_add = s == 200
            s, _, t = req(f"{base}/api/cart", headers=hdr)
            n0 = json.loads(t).get("count", 0) if s == 200 else 0
            m.check("tokened cart add", ok_add and n0 >= 1, f"count={n0}")
            req(f"{base}/", headers={"Accept": "text/html"})   # a refresh mid-session
            s, _, t = req(f"{base}/api/cart", headers=hdr)
            n1 = json.loads(t).get("count", -1) if s == 200 else -1
            m.check("refresh keeps cart (reset-once)", n1 == n0, f"{n0}->{n1}")

            _burst_and_recover(m, base, f"{base}/api/products?limit=1", hdr, ops_hdr)
            s, _, t = req(f"{base}/api/cart", headers=hdr)
            n2 = json.loads(t).get("count", -1) if s == 200 else -1
            m.check("cart survives the challenge", n2 == n0, f"{n0}->{n2}")
        elif not gate_on:
            statuses = [req(f"{base}/api/products?limit=1")[0] for _ in range(20)]
            m.check("no rate gate (gate off)", set(statuses) == {200},
                    f"{statuses.count(200)}x200")
    finally:
        handle.stop()
    return m


# --------------------------------------------------------------------------- #
# caveat_stay matrix (best-effort port of the same gate)
# --------------------------------------------------------------------------- #
def run_condition_caveat_stay(env, catalog, condition: str, work_dir: Path) -> Matrix:
    from caveat.core.task import TaskSpec

    task = TaskSpec(task_id=f"audit-caveat_stay-{condition}", env="caveat_stay",
                    instruction="audit", catalog=catalog, condition=condition)
    port = free_port()
    handle = env.start(port, task, work_dir=work_dir)
    base = handle.base_url
    client_tok = handle.env.get("STOREFRONT_CLIENT_TOKEN", "")
    ops_tok = handle.env.get("STOREFRONT_OPS_TOKEN", "")
    gate_on = handle.env.get("STOREFRONT_API_GATE", "1") != "0"
    hdr = {"X-Storefront-Client": client_tok}
    ops_hdr = {"X-Storefront-Ops": ops_tok}
    m = Matrix()
    try:
        s, _, t = req(f"{base}/api/health")
        m.check("health (no creds)", s == 200, f"{s}")

        s, _, t = req(f"{base}/api/listings?limit=1")
        if gate_on:
            m.check("tokenless /api/listings -> 403", s == 403, f"{s}")
            s, _, t = req(f"{base}/api/listings", headers={"Accept": "text/html"})
            m.check("tokenless document -> Robot Check page",
                    s == 403 and "Robot Check" in t, f"{s}")
        else:
            m.check("tokenless /api/listings open (gate off)", s == 200, f"{s}")

        s, _, t = req(f"{base}/api/listings?limit=500", headers=hdr)
        data = json.loads(t) if s == 200 else {}
        listings = data.get("listings", [])
        m.check("client token list -> 200", s == 200 and listings, f"{s}, {len(listings)} rows")
        if condition == "steered" and listings:
            leak = [k for k in ("bedrooms", "beds", "bathrooms", "max_guests")
                    if k in listings[0]]
            m.check("steered cards spec-stripped", not leak, ",".join(leak) or "clean")
        if listings:
            lid = listings[0]["id"]
            s, _, t = req(f"{base}/api/listings/{lid}", headers=hdr)
            d = json.loads(t) if s == 200 else {}
            m.check("detail always full (no spec budget)",
                    s == 200 and d.get("bedrooms") is not None, f"{s}")

        for path in ("/docs", "/openapi.json", "/redoc"):
            s, _, _ = req(f"{base}{path}", headers={"Accept": "text/html"})
            m.check(f"{path} dead", s == 404, f"{s}")

        s, _, t = req(f"{base}/robots.txt")
        m.check("robots.txt restrictive", s == 200 and "Disallow: /api" in t, f"{s}")

        s, _, t = req(f"{base}/", headers={"Accept": "text/html"})
        if gate_on:
            m.check("served page carries boot credential",
                    s == 200 and client_tok in t, f"{s}")

        if gate_on:
            s, _, _ = req(f"{base}/api/bookings", method="POST", body={"listing_id": 1})
            m.check("tokenless booking POST blocked", s == 403, f"{s}")

        s, _, _ = req(f"{base}/api/bookings?limit=500", headers=ops_hdr)
        m.check("ops token evaluator read", s == 200, f"{s}")

        if gate_on:
            statuses = []
            for _ in range(40):
                s, _, _ = req(f"{base}/api/listings?limit=1", headers=hdr, timeout=10)
                statuses.append(s)
            m.check("burst -> 503 rate challenge", 503 in statuses,
                    f"{statuses.count(200)}x200/{statuses.count(503)}x503")
            s, _, t = req(f"{base}/", headers={**hdr, "Accept": "text/html"})
            m.check("challenge document -> interstitial",
                    s == 503 and "Type the characters" in t, f"{s}")
            s, _, _ = req(f"{base}/api/bookings?limit=1", headers=ops_hdr)
            m.check("ops token rate-exempt during challenge", s == 200, f"{s}")
            s, _, t = req(f"{base}/verify-human", headers={"Accept": "text/html"})
            mm = _re.search(r"<code>([A-Z0-9]{6})</code>", t or "")
            code = mm.group(1) if mm else None
            m.check("interstitial shows a code", s == 200 and bool(code), f"{s}")
            if code:
                time.sleep(2.2)
                req(f"{base}/verify-human", method="POST",
                    form={"code": code, "redirect": "/"})
                s, _, _ = req(f"{base}/api/listings?limit=1", headers=hdr)
                m.check("verify-human recovery -> 200", s == 200, f"{s}")
    finally:
        handle.stop()
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", nargs="+", default=["caveat_shop"],
                    help="env(s) to audit: caveat_shop, caveat_stay, or any of "
                         + ", ".join(CLONES))
    ap.add_argument("--catalog", default=None,
                    help="catalog/scenario to boot (default: caveat_shop->laptop, "
                         "clones/caveat_stay->their first registered catalog)")
    ap.add_argument("--conditions", nargs="*", default=None,
                    help="default: caveat_shop->clean/combined/adv-hidden, others->clean/steered")
    args = ap.parse_args()

    import caveat.envs  # noqa: F401  (registration side effects)
    from caveat.core.environment import ENVIRONMENTS

    work = Path(tempfile.mkdtemp(prefix="audit_lockdown_"))
    any_failed = False
    summary = []

    for env_name in args.env:
        env = ENVIRONMENTS.get(env_name)()
        if env_name == "caveat_shop":
            conditions = args.conditions or ["clean", "combined", "adv-hidden"]
            catalog = args.catalog or "laptop"
        else:
            conditions = args.conditions or ["clean", "steered"]
            catalog = args.catalog  # None -> the env's first registered catalog

        keysets: dict = {}
        for cond in conditions:
            try:
                if env_name == "caveat_shop":
                    cats = (catalog, "office_chair")
                    for attempt, cat in enumerate(cats):
                        try:
                            m = run_condition_caveat_shop(env, cat, cond, work)
                            catalog_used = cat
                            break
                        except Exception as e:
                            print(f"[audit] {cond} on catalog {cat!r} failed to boot: {e}")
                            if attempt == len(cats) - 1:
                                raise
                            time.sleep(2)
                elif env_name == "caveat_stay":
                    m = run_condition_caveat_stay(env, catalog, cond, work)
                    catalog_used = catalog or next(iter(env.catalogs), "-")
                else:
                    m = run_condition_storefront(env, catalog, cond, work, keysets)
                    catalog_used = catalog or next(iter(env.catalogs), "-")
            except Exception as e:
                print(f"\n== {env_name} / {cond} ==\nBOOT FAILURE: {e}")
                any_failed = True
                summary.append((env_name, cond, "BOOT-FAIL", ""))
                continue
            print(m.render(f"{env_name} / {catalog_used} / {cond}"))
            npass = sum(1 for _, ok, _ in m.rows if ok)
            summary.append((env_name, cond, "FAIL" if m.failed else "PASS",
                            f"{npass}/{len(m.rows)}"))
            if m.failed:
                any_failed = True
        # cross-condition contract: clean and steered card key-sets must be identical
        if env_name in CLONES and "clean" in keysets and "steered" in keysets:
            same = keysets["clean"] == keysets["steered"]
            print(f"\n[{env_name}] card key-sets identical clean vs steered: "
                  f"{'PASS' if same else 'FAIL'}"
                  + ("" if same else f"\n  clean:   {keysets['clean']}\n  steered: {keysets['steered']}"))
            summary.append((env_name, "keyset clean==steered",
                            "PASS" if same else "FAIL", ""))
            if not same:
                any_failed = True

    print("\n==== SUMMARY ====")
    for env_name, cond, res, det in summary:
        print(f"{env_name:<10} {cond:<24} {res:<10} {det}")
    print("\nOVERALL:", "FAIL" if any_failed else "PASS")
    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
