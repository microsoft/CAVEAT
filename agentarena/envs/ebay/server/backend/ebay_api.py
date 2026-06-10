"""eBay compat router — serves the endpoints/shapes the harvested Next.js clone calls
(/products, /product/{id}, /products/get-random, /products/search-by-name/{name},
/orders/create, /orders), re-shaped from our catalog + steering. Integer product ids map
to skus by the steered order, so in the steered condition the pinned decoy gets id 1 and
renders first. Prices are in pence (the clone's schema + UI use pence). A placed order is
written through to our generic cart + checkout so /api/orders reads it back for evaluate.
Product images are self-hosted SVGs (the clone used picsum.photos, which is blank headless)."""

from __future__ import annotations

import random
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from agentarena.envs._storefront import steering
from agentarena.envs._storefront.database import get_engine
from agentarena.envs._storefront.models import Cart, CartItem, Item

router = APIRouter(prefix="/ebay")


@lru_cache(maxsize=1)
def _ordered() -> list:
    items = [dict(it) for it in steering.items()]
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


def _product(it: dict, pid: int) -> dict:
    specs = it.get("specs", {}) or {}
    return {
        "id": pid,
        "title": it["title"],
        "description": it.get("description", ""),
        "url": it.get("image", ""),
        "price": it.get("price"),
        "condition": specs.get("condition", "New"),
        "brand": specs.get("brand", it.get("vendor", "")),
        "shipping": specs.get("shipping", 0),
        "sponsored": it.get("sku") in set(steering.pinned_skus()),
    }


@router.get("/products")
def products():
    return [_product(it, i + 1) for i, it in enumerate(_ordered())]


@router.get("/products/get-random")
def get_random():
    items = _ordered()
    idxs = list(range(len(items)))
    random.shuffle(idxs)
    return [_product(items[i], i + 1) for i in idxs[:4]]


@router.get("/products/search-by-name/{name}")
def search_by_name(name: str):
    q = name.lower()
    return [_product(it, i + 1) for i, it in enumerate(_ordered()) if q in it["title"].lower()]


@router.get("/product/{pid}")
def product(pid: int):
    it = _by_id(pid)
    if not it:
        raise HTTPException(404, "not found")
    return _product(it, pid)


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
    from agentarena.envs._storefront.routes import Checkout, checkout
    order = checkout(Checkout())
    return {"success": True, "order_id": order["id"]}


@router.get("/orders")
def orders():
    from agentarena.envs._storefront.routes import list_orders
    out = []
    for o in list_orders().get("orders", []):
        out.append({"id": o["id"], "total": sum(i.get("unit_price", 0) for i in o.get("items", [])),
                    "created_at": o.get("created_at", ""),
                    "orderItem": [{"product_id": 0, "product": {"title": i.get("title", ""),
                                   "url": "", "price": i.get("unit_price", 0)}}
                                  for i in o.get("items", [])]})
    return out


# --- self-hosted product image (the UI appends /190 or /280 for size) ------- #
def _svg(label: str, size: int) -> Response:
    text = label.replace("-", " ").replace("_", " ").replace("%20", " ").strip()
    w = max(190, min(size or 280, 600))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{w}">'
           f'<rect width="100%" height="100%" fill="#f7f7f7"/>'
           f'<rect x="2" y="2" width="{w-4}" height="{w-4}" fill="none" stroke="#e5e5e5"/>'
           f'<text x="50%" y="42%" font-size="{int(w*0.28)}" text-anchor="middle">\U0001F3A7</text>'
           f'<text x="50%" y="72%" font-family="Arial,Helvetica,sans-serif" font-size="13" '
           f'fill="#333" text-anchor="middle">{text[:40]}</text></svg>')
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


def _product_photo(sku: str):
    from fastapi.responses import FileResponse
    p = _ASSETS / f"{sku}.jpg"
    if p.exists():
        return FileResponse(str(p), media_type="image/jpeg")
    return _svg(sku, 280)


@router.get("/img/{sku}/{size}")
def product_image_sized(sku: str, size: str):
    return _product_photo(sku)


@router.get("/img/{sku}")
def product_image(sku: str):
    return _product_photo(sku)
