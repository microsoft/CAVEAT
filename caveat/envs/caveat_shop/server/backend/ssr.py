# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Classic server-rendered storefront.

The historical explicit ``-ssr`` transport control retains its old behavior.  The
truthful-hard tier additionally selects this transport from its exact data contract:
all 2,112 products remain discoverable through ordinary pagination and each PDP
renders the complete truthful product, seller, delivery, and seller-dialect facts.
Product-data JSON is unavailable, as on an ordinary document storefront; there is
no rate limit, artificial delay, omission, or pagination cap in truthful-hard.

Cart/checkout forms call the same route functions as the SPA, so transaction and
scoring semantics do not fork with transport.
"""

from __future__ import annotations

import html as _html
import math
import os
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from backend import routes as R
from backend.database import get_session

ssr_router = APIRouter()
_did_initial_reset = False


def _count(request: Request) -> None:
    """Rate-count a content document GET (/s, /dp/*) against the shared gate windows.

    Under SSR "the API is the HTML", so the SERP/PDP document loads are the content
    reads. Raises the gate's RateChallenged (rendered as the Robot Check interstitial
    by the gate's exception handler) on breach; silent no-op when the gate module is
    unavailable (standalone server) or rate limiting is off."""
    try:
        from backend.app import get_gate
        gate = get_gate()
    except Exception:
        return
    if gate is not None:
        gate.count(request)


def _reset_once() -> None:
    """Mirror the SPA's initial-load DB reset exactly once per server process —
    SSR page loads are all real document loads, so the SPA's per-refresh reset
    would wipe the cart on every navigation."""
    global _did_initial_reset
    if _did_initial_reset:
        return
    _did_initial_reset = True
    try:
        from backend.seed import reset_database
        reset_database()
    except Exception as e:  # never 500 a page over a reset
        print(f"[ssr] initial reset failed: {e}")


def _e(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _stars(rating) -> str:
    try:
        r = float(rating or 0)
    except Exception:
        r = 0.0
    full = int(r)
    half = 1 if (r - full) >= 0.5 else 0
    return "★" * full + ("½" if half else "") + "☆" * (5 - full - half)


def _money(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return _e(value)


def _rating_text(product: dict) -> str:
    try:
        from backend import truthful
        exact = truthful.product_json_disabled()
    except Exception:
        exact = False
    try:
        value = float(product.get("rating") or 0)
    except (TypeError, ValueError):
        return ""
    if exact:
        return f"{product.get('rating')} out of 5"
    return f"{value:.1f} out of 5"


def _discount_percent(product: dict) -> int | None:
    """Match the shared storefront's ``Math.round`` markdown arithmetic."""
    try:
        price = float(product.get("price"))
        list_price = float(product.get("list_price"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or not math.isfinite(list_price):
        return None
    if list_price <= price or list_price <= 0:
        return None
    return int(math.floor(((list_price - price) / list_price) * 100.0 + 0.5))


def _badges(product: dict) -> str:
    labels = list(product.get("badges") or [])
    if product.get("is_best_seller") and "#1 Best Seller" not in labels:
        labels.append("#1 Best Seller")
    if product.get("is_caveat_shop_choice") and "CAVEAT-Shop's Choice" not in labels:
        labels.append("CAVEAT-Shop's Choice")
    return "".join(
        f"<span class='badge'>{_e(label)}</span>" for label in labels
    )


def _delivery(product: dict) -> str:
    try:
        days = int(product.get("delivery_days"))
    except (TypeError, ValueError):
        return ""
    if days <= 0:
        return ""
    when = "Tomorrow" if days == 1 else f"in {days} days"
    return (
        f"<span class='delivery-days'>{days} day"
        f"{'' if days == 1 else 's'}</span> · FREE delivery {_e(when)}<br>"
    )


def _page(title: str, body: str) -> HTMLResponse:
    resp = HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_e(title)}</title>"
        "<style>body{font-family:Arial,sans-serif;max-width:980px;margin:12px auto;}"
        ".card{border:1px solid #ddd;padding:10px;margin:8px 0;}"
        ".sponsored{color:#767676;font-size:12px;} .badge{background:#c45500;color:#fff;"
        "padding:1px 6px;font-size:12px;margin-right:4px;} .price{font-size:18px;font-weight:bold;}"
        ".stars{color:#de7921;} nav a{margin-right:10px;}"
        "table.tech{border-collapse:collapse;} table.tech td{border:1px solid #ddd;"
        "padding:4px 10px;font-size:13px;} table.tech td.k{background:#f7f7f7;font-weight:bold;}"
        "</style></head><body>"
        "<nav><a href='/'>CAVEAT-Shop</a><a href='/gp/cart'>Cart</a></nav>"
        f"{body}</body></html>")
    # Session-scope the SSR surface: document responses carry the client credential as a
    # cookie, so the plain-HTML forms (cart-add/checkout/place-order) pass the gate only
    # when the flow starts from a served page — a UI-less POST from outside has no token.
    tok = os.environ.get("STOREFRONT_CLIENT_TOKEN")
    if tok:
        resp.set_cookie("sf_client", tok, httponly=True, samesite="lax")
    return resp


def _card_html(p: dict) -> str:
    asin = p.get("asin", "")
    badges = _badges(p)
    sponsored = "<div class='sponsored'>Sponsored</div>" if p.get("sponsored") else ""
    deal = f" <span class='badge'>{_e(p['deal_label'])}</span>" if p.get("deal_label") else ""
    discount = _discount_percent(p)
    discount_html = (
        f" <span class='discount-percent'>{discount}% off</span>"
        if discount is not None else ""
    )
    list_price = (
        f" <span class='list-price'>List price ${_money(p.get('list_price'))}</span>"
        if p.get("list_price") is not None else ""
    )
    promo = (
        f"<div class='adv-badge'>{_e(p.get('adv_badge'))}</div>"
        if p.get("adv_badge") else ""
    )
    return (
        f"<div class='card'>{sponsored}"
        f"<a href='/dp/{_e(asin)}'><b>{_e(p.get('title'))}</b></a><br>"
        f"<span class='stars'>{_stars(p.get('rating'))}</span> "
        f"<span class='rating-count'>({_e(p.get('rating_count'))})</span><br>"
        f"{badges}{deal} <span class='price'>${_money(p.get('price'))}</span>"
        f"{discount_html}{list_price}<br>"
        f"<span class='bought-count'>{_e(p.get('bought_past_month'))}+ bought "
        f"in past month</span><br>"
        f"{_delivery(p)}{promo}"
        f"<form method='post' action='/ssr/cart-add'>"
        f"<input type='hidden' name='product_id' value='{_e(p.get('id'))}'>"
        f"<button type='submit'>Add to Cart</button></form></div>")


@ssr_router.get("/", response_class=HTMLResponse)
def ssr_home(session: Session = Depends(R.get_session)):
    _reset_once()
    body = ("<h1>CAVEAT-Shop</h1>"
            "<form method='get' action='/s'>"
            "<input name='q' placeholder='Search CAVEAT-Shop' size='40'>"
            "<button type='submit'>Search</button></form>")
    return _page("CAVEAT-Shop", body)


@ssr_router.get("/s", response_class=HTMLResponse)
def ssr_search(request: Request, q: Optional[str] = None, page: int = 1,
               sort: str = "featured", session: Session = Depends(R.get_session)):
    _reset_once()
    _count(request)   # SERP document load = one counted content read
    data = R.list_products(q=q, sort=sort, page=page, session=session)
    cards = "".join(_card_html(p) for p in data.get("products", []))
    # Page links must advertise exactly the servable window (the SSR mirror of the SPA's
    # `total`-derived page buttons): 7 legacy pages, or `serving.pages` on the hard tier.
    # `_max_pages()` returns None for every original catalog -> range(1, 8), byte-identical.
    from backend.experiment_laptops import max_pages as _max_pages
    _npages = _max_pages() or R._LEGACY_MAX_PAGES
    pages = "".join(
        f"<a href='/s?q={_e(q or '')}&sort={_e(sort)}&page={n}'>{n}</a> "
        for n in range(1, _npages + 1))
    sorts = "".join(
        f"<a href='/s?q={_e(q or '')}&sort={s}&page=1'>{lbl}</a> "
        for s, lbl in (("featured", "Featured"), ("price_asc", "Price: Low to High"),
                       ("price_desc", "Price: High to Low"), ("rating", "Avg. Customer Review"),
                       ("newest", "Newest"), ("best_selling", "Best Selling")))
    body = (f"<h2>Results for '{_e(q or '')}'</h2><div>Sort: {sorts}</div>{cards}"
            f"<div>Page: {pages}</div>")
    return _page(f"{q or 'Search'} - CAVEAT-Shop", body)


def _tech_table(p: dict) -> str:
    """PDP 'Product information' table — mirrors the SPA's technical_details table."""
    tech = p.get("technical_details") or {}
    if not isinstance(tech, dict) or not tech:
        return ""
    try:
        from backend import truthful
        exact_labels = truthful.product_json_disabled()
    except Exception:
        exact_labels = False
    rows = "".join(
        f"<tr><td class='k'>{_e(str(k) if exact_labels else str(k).replace('_', ' ').title())}"
        f"</td><td>{_e(v)}</td></tr>"
        for k, v in tech.items()
    )
    return f"<h3>Product information</h3><table class='tech'>{rows}</table>"


@ssr_router.get("/dp/{asin}", response_class=HTMLResponse)
def ssr_pdp(request: Request, asin: str, session: Session = Depends(R.get_session)):
    _reset_once()
    _count(request)   # PDP document load = one counted content read
    p = R.get_product_by_asin(asin, session)   # spec_gate applies exactly as on the JSON path
    badges = _badges(p)
    bullets = "".join(f"<li>{_e(b)}</li>" for b in (p.get("bullet_points") or ["High-quality product"]))
    rnum = _rating_text(p)
    list_price = (
        f"<div class='list-price'>List price ${_money(p.get('list_price'))}</div>"
        if p.get("list_price") is not None else ""
    )
    promo = (
        f"<div class='adv-badge'>{_e(p.get('adv_badge'))}</div>"
        if p.get("adv_badge") else ""
    )
    discount = _discount_percent(p)
    discount_html = (
        f"<span class='discount-percent'>{discount}% off</span>"
        if discount is not None else ""
    )
    seller = (
        f"<div>Sold by <span class='seller-name'>{_e(p.get('seller_name'))}</span>"
        f" · <span class='seller-rating'>{_e(p.get('seller_rating'))} stars</span>"
        f" · <span class='seller-reviews'>{_e(p.get('seller_reviews'))} seller "
        f"reviews</span></div>"
        if p.get("seller_name") else ""
    )
    body = (
        f"<h2>{_e(p.get('title'))}</h2>"
        f"<span class='stars'>{_stars(p.get('rating'))}</span> "
        f"<span class='rating-value'>{_e(rnum)}</span> "
        f"<span class='rating-count'>{_e(p.get('rating_count'))} ratings</span>"
        f"<br>{badges}<br>"
        f"<span class='price current-price'>${_money(p.get('price'))}</span>"
        f"{discount_html}{list_price}"
        f"<div class='bought-count'>{_e(p.get('bought_past_month'))} bought "
        f"in the past month</div>"
        f"<div class='stock-count'>{_e(p.get('stock_quantity'))} in stock</div>"
        f"<div>Delivery: {_delivery(p)}</div>"
        f"{seller}"
        f"{promo}"
        f"<h3>About this item</h3><ul>{bullets}</ul>"
        f"{_tech_table(p)}"
        f"<form method='post' action='/ssr/cart-add'>"
        f"<input type='hidden' name='product_id' value='{_e(p.get('id'))}'>"
        f"Qty: <input name='quantity' value='1' size='2'>"
        f"<button type='submit'>Add to Cart</button></form>")
    return _page(p.get("title", "Product"), body)


@ssr_router.post("/ssr/cart-add")
def ssr_cart_add(product_id: int = Form(...), quantity: int = Form(1),
                 session: Session = Depends(R.get_session),
                 session_token: Optional[str] = Cookie(None)):
    R.add_to_cart(R.CartItemCreate(product_id=product_id, quantity=quantity),
                  session, session_token)
    return RedirectResponse("/gp/cart", status_code=303)


@ssr_router.get("/gp/cart", response_class=HTMLResponse)
def ssr_cart(session: Session = Depends(R.get_session),
             session_token: Optional[str] = Cookie(None)):
    _reset_once()
    cart = R.get_cart(session, session_token)
    rows = "".join(
        f"<div class='card'>{_e(i.get('product_title'))} — "
        f"${_money(i.get('product_price'))} x {_e(i.get('quantity'))}"
        f" = ${_money(i.get('subtotal'))}</div>"
        for i in (cart.get("items") or []))
    fee = (f"<div>+ {_e(cart.get('fee_label'))}: ${_money(cart.get('service_fee'))}</div>"
           if cart.get("service_fee") else "")
    body = (f"<h2>Shopping Cart</h2>{rows or '<p>Your cart is empty.</p>'}"
            f"<div>Subtotal: ${_money(cart.get('subtotal'))}</div>{fee}"
            f"<div><b>Total: ${_money(cart.get('total'))}</b></div>"
            "<form method='post' action='/ssr/checkout'>"
            "<button type='submit'>Proceed to checkout</button></form>")
    return _page("Shopping Cart", body)


@ssr_router.post("/ssr/checkout", response_class=HTMLResponse)
def ssr_checkout(session: Session = Depends(R.get_session),
                 session_token: Optional[str] = Cookie(None)):
    R.start_checkout(session, session_token)
    cart = R.get_cart(session, session_token)
    summary = R.get_checkout_summary(session, session_token)
    items = "".join(
        f"<div class='card'>{_e(i.get('product_title'))} — "
        f"${_money(i.get('product_price'))} x {_e(i.get('quantity'))}</div>"
        for i in (cart.get("items") or [])
    )
    totals = "".join(
        f"<div>{_e(k)}: ${_money(v)}</div>"
        for k, v in summary.items()
        if k in ("subtotal", "service_fee", "shipping_cost", "tax", "total")
        and v is not None
    )
    body = (f"<h2>Review your order</h2>{items}{totals}"
            "<form method='post' action='/ssr/place-order'>"
            "<button type='submit'>Place your order</button></form>")
    return _page("Checkout", body)


@ssr_router.post("/ssr/place-order", response_class=HTMLResponse)
def ssr_place_order(session: Session = Depends(R.get_session),
                    session_token: Optional[str] = Cookie(None)):
    order = R.place_order(R.PlaceOrder(), session, session_token)
    oid = order.get("order_number") or order.get("id") or ""
    body = (f"<h2>Order placed, thank you!</h2><div>Confirmation: {_e(oid)}</div>"
            f"<div>Total: ${_money(order.get('total'))}</div>")
    return _page("Order confirmation", body)


# JSON endpoints that must stay reachable under SSR: the health probe,
# transaction plumbing, and the evaluator's order read. These all sit under the
# storefront gate's "/api" prefix, so they additionally require the client token
# (SSR pages carry it as the sf_client cookie) or the evaluator's ops token —
# reachable-under-SSR no longer means an open UI-less purchase/read channel.
_SSR_API_ALLOWED_PREFIXES = ("/api/health",
                             "/api/cart", "/api/checkout", "/api/orders", "/api/auth",
                             "/api/addresses", "/api/payment", "/api/users",
                             # evaluator plumbing — 404ing this leaves a retry-loop socket
                             # on the worker, which free_port() then kill -9s at teardown
                             "/api/subscriptions")


def ssr_api_guard_middleware(app):
    """404 the product-data JSON surface under SSR: the HTML pages ARE the data channel
    (transport control). Transaction/write APIs and the health probe stay live."""
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api"):
            try:
                from backend import truthful
                strict_hard = truthful.product_json_disabled()
            except Exception:
                strict_hard = False
            allowed = (
                path.startswith(_SSR_API_ALLOWED_PREFIXES)
                or (
                    not strict_hard
                    and path == "/api/products"
                    and request.url.query == "limit=1"
                )
            )
            if not allowed:
                return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)
