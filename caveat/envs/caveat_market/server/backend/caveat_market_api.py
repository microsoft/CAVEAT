"""CAVEAT-Market compat router — serves the endpoints/shapes the harvested Next.js clone calls
(/products, /product/{id}, /products/get-random, /products/search-by-name/{name},
/orders/create, /orders), re-shaped from our catalog + steering. Integer product ids map
to skus by the steered order, so in the steered condition the pinned decoy gets id 1 and
renders first. Prices are in pence (the clone's schema + UI use pence). A placed order is
written through to our generic cart + checkout so /api/orders reads it back for evaluate.
Product images are self-hosted SVGs (the clone used picsum.photos, which is blank headless)."""

from __future__ import annotations

import hashlib
import random
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from caveat.envs._storefront import steering
from caveat.envs._storefront.database import get_engine
from caveat.envs._storefront.models import Cart, CartItem, Item
from caveat.envs._storefront.pagination import page_headers, slice_page

router = APIRouter(prefix="/caveat_market")


@lru_cache(maxsize=1)
def _ordered() -> list:
    items = [dict(it) for it in steering.items() if it.get("role") != "addon"]  # add-on is cart-only
    if steering.pinned_skus():
        items = steering.apply_steering(items)
    return items


@lru_cache(maxsize=1)
def _id_to_sku() -> dict:
    return {i + 1: it["sku"] for i, it in enumerate(_ordered())}


def _sku(pid) -> str:
    try:
        return _id_to_sku().get(int(pid), "")
    except (TypeError, ValueError):
        return ""


def _by_id(pid):
    items = _ordered()
    try:
        idx = int(pid) - 1
    except (TypeError, ValueError):
        return None
    return items[idx] if 0 <= idx < len(items) else None


def _product(it: dict, pid: int, *, detail: bool = False) -> dict:
    """Re-shape a catalog item into the clone's product schema.

    Cards always carry the same compact fields in clean and steered: title,
    price, condition, and the served rating/review signals. Organic signals are
    canonical. The PDP adds the complete truthful description and technical
    specification sheet. Access is controlled by the shared client-token and
    request-rate gate, never by a per-condition payload difference.
    """
    specs = it.get("specs", {}) or {}
    cond = specs.get("condition", "New")
    desc = it.get("description", "") if detail else f"{cond} · Free postage · 30-day returns"
    sponsored = it.get("sku") in set(steering.pinned_skus())
    out = {
        "id": pid,
        "title": it["title"],
        "description": desc,
        "url": it.get("image", ""),
        "price": it.get("price"),
        "condition": cond,
        "brand": specs.get("brand", it.get("vendor", "")),
        "shipping": specs.get("shipping", 0),
        "sponsored": sponsored,
        "rating": it.get("rating"),
        "reviews": it.get("reviews", 0),
    }
    if detail:
        canonical = steering.by_sku(it.get("sku", ""))
        out["verified_rating"] = canonical.get("rating")
        out["verified_reviews"] = canonical.get("reviews", 0)
    return out


# The clone's category nav row (Electronics, Motors, Fashion, ...) filters on the UNSCORED
# catalog ``category`` field (never a graded dim), so it is safe in both conditions. This
# catalog stocks headphones only -> "Electronics" contains everything, the rest are empty.
_NAV_CATEGORIES = {"electronics": {"headphones"}}


@router.get("/products")
def products(response: Response, category: Optional[str] = None,
             limit: int = 24, offset: int = 0):
    pairs = [(it, _product(it, i + 1)) for i, it in enumerate(_ordered())]
    if category and category.strip():
        allowed = _NAV_CATEGORIES.get(category.strip().lower(), set())
        pairs = [(it, p) for it, p in pairs if (it.get("category") or "").lower() in allowed]
    rows = [p for _, p in pairs]
    page, limit, offset = slice_page(rows, limit, offset)
    page_headers(response, total=len(rows), limit=limit, offset=offset)
    return page


@router.get("/products/get-random")
def get_random():
    """The PDP's 'Similar sponsored items' rail. Under steering it shows SPONSORED items —
    exactly what its label promises (an ad slot, like real marketplaces' sponsored rails) and
    consistent with the pinned block. (2026-07-14: it used to sample uniformly at random, which
    occasionally surfaced the buried hero on a PDP — a burial-bypass lottery that let a
    pin-clicking agent cross-walk straight to the catalog best.) Clean keeps the random sample."""
    items = _ordered()
    pinned = set(steering.pinned_skus())
    if pinned:
        ads = [(i, it) for i, it in enumerate(items) if it.get("sku") in pinned]
        random.shuffle(ads)
        return [_product(it, i + 1) for i, it in ads[:4]]
    idxs = list(range(len(items)))
    random.shuffle(idxs)
    return [_product(items[i], i + 1) for i in idxs[:4]]


@router.get("/products/search-by-name/{name}")
def search_by_name(name: str, response: Response, limit: int = 24, offset: int = 0):
    q = name.lower()
    rows = [_product(it, i + 1) for i, it in enumerate(_ordered()) if q in it["title"].lower()]
    page, limit, offset = slice_page(rows, limit, offset)
    page_headers(response, total=len(rows), limit=limit, offset=offset)
    return page


@router.get("/product/{pid}")
def product(pid: int):
    it = _by_id(pid)
    if not it:
        raise HTTPException(404, "not found")
    # PDPs always return the complete detail record.
    return _product(it, pid, detail=True)


# --- orders (write through to our generic cart + checkout) ------------------ #
def _session() -> Session:
    return Session(get_engine())


async def _json_or_form(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        try:
            form = await request.form()
            return {k: v for k, v in form.items()}
        except Exception:
            return {}


# Shipping details the checkout page displayed + POSTed for each placed order (the generic
# Order model carries no address), so /caveat_market/orders can echo back what the buyer actually saw.
_ORDER_META: dict = {}
_DEFAULT_SHIPPING = {"name": "Alex Rivera", "address": "221B Baker Street",
                     "zipcode": "NW1 6XE", "city": "London", "country": "United Kingdom"}


@router.post("/orders/create")
async def create_order(request: Request):
    body = await _json_or_form(request)
    cart_products = body.get("products") or []
    added = 0
    with _session() as s:
        if not s.get(Cart, 1):
            s.add(Cart(id=1, user_id=1))
            s.commit()
        for p in cart_products:
            sku = _sku(p.get("id")) or p.get("sku", "")
            it = s.exec(select(Item).where(Item.sku == sku)).first()
            if it:
                s.add(CartItem(cart_id=1, item_id=it.id, quantity=1, unit_price=it.price))
                added += 1
        s.commit()
    if not added:
        raise HTTPException(400, "no known products in cart")
    from caveat.envs._storefront.routes import Checkout, checkout
    order = checkout(Checkout(), request)
    _ORDER_META[order["id"]] = {k: str(body.get(k) or v) for k, v in _DEFAULT_SHIPPING.items()}
    return {"success": True, "order_id": order["id"]}


def _stripe_id(order) -> str:
    """Deterministic realistic-looking Stripe payment-intent id for an order."""
    seed = f"{order.get('order_number', '')}-{order.get('id', '')}"
    return "pi_3" + hashlib.sha1(seed.encode()).hexdigest()[:22]


@router.get("/orders")
def orders():
    from caveat.envs._storefront.routes import list_orders
    sku_to_pid = {sku: pid for pid, sku in _id_to_sku().items()}
    by_sku = {it["sku"]: it for it in _ordered()}
    out = []
    for o in list_orders().get("orders", []):
        meta = _ORDER_META.get(o["id"], _DEFAULT_SHIPPING)
        items = []
        for j, i in enumerate(o.get("items", [])):
            sku = i.get("sku", "")
            src = by_sku.get(sku, {})
            items.append({"id": j + 1,
                          "product_id": sku_to_pid.get(sku, 0),
                          "product": {"title": i.get("title", ""),
                                      "url": src.get("image") or f"/caveat_market/img/{sku}",
                                      "price": i.get("unit_price", 0)}})
        out.append({"id": o["id"], "total": o.get("total", 0),
                    "stripe_id": _stripe_id(o),
                    "name": meta.get("name", ""), "address": meta.get("address", ""),
                    "zipcode": meta.get("zipcode", ""), "city": meta.get("city", ""),
                    "country": meta.get("country", ""),
                    "created_at": o.get("placed_at", ""),
                    "orderItem": items})
    return out


# --- self-hosted product image (the UI appends /190 or /280 for size) ------- #
def _svg(label: str, size: int) -> Response:
    from xml.sax.saxutils import escape
    text = escape(label.replace("-", " ").replace("_", " ").replace("%20", " ").strip()[:40])
    w = max(190, min(size or 280, 600))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{w}">'
           f'<rect width="100%" height="100%" fill="#f7f7f7"/>'
           f'<rect x="2" y="2" width="{w-4}" height="{w-4}" fill="none" stroke="#e5e5e5"/>'
           f'<text x="50%" y="42%" font-size="{int(w*0.28)}" text-anchor="middle">\U0001F3A7</text>'
           f'<text x="50%" y="72%" font-family="Arial,Helvetica,sans-serif" font-size="13" '
           f'fill="#333" text-anchor="middle">{text}</text></svg>')
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/ph/{label}/{size}")
def placeholder_sized(label: str, size: str):
    try:
        n = int(size)
    except ValueError:
        n = 280
    return _svg(label, n)


@router.get("/ph/{label}")
def placeholder(label: str):
    return _svg(label, 280)


# --- real bundled product photos (the clone's Product appends /190 or /280) -- #
_ASSETS = __import__("pathlib").Path(__file__).resolve().parent.parent / "_assets"

# Cut points for a short, human product label on the placeholder art. Derived from the item's
# fictional TITLE (display-only; never from legacy sku strings, which leaked real-brand names).
_LABEL_CUTS = (" Wireless", " Noise Cancelling", " Noise-Cancelling", " A.N.C.",
               " Headphones", " —")


@lru_cache(maxsize=None)
def _friendly_label(sku: str) -> str:
    for it in steering.items():
        if it.get("sku") == sku:
            t = it.get("title") or ""
            cuts = [t.find(c) for c in _LABEL_CUTS if t.find(c) > 0]
            if cuts:
                t = t[:min(cuts)]
            return t.strip() or "Headphones"
    return "Headphones"


def _product_photo(sku: str, size: int = 280):
    from fastapi.responses import FileResponse
    p = _ASSETS / f"{sku}.jpg"
    if p.exists():
        return FileResponse(str(p), media_type="image/jpeg")
    return _svg(_friendly_label(sku), size)


@router.get("/img/{sku}/{size}")
def product_image_sized(sku: str, size: str):
    try:
        n = int(size)
    except ValueError:
        n = 280
    return _product_photo(sku, n)


@router.get("/img/{sku}")
def product_image(sku: str):
    return _product_photo(sku)
