"""CAVEAT-Kicks (CAVEAT-Kicks) compat router — serves the endpoints/shapes the harvested Rails/Redux
clone calls (sneakers keyed by id, per-sneaker listingitems, purchaseditems, users),
re-shaped from our catalog + steering. Integer sneaker ids map to skus; in steered the
decoys get the low ids so they render first. listingitem_id == sneaker_id (one size-10
listing per sneaker). A purchase writes through to our generic cart + checkout so
/api/orders reads it back."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from caveat.envs._storefront import gate
from caveat.envs._storefront import steering
from caveat.envs._storefront.database import get_engine
from caveat.envs._storefront.models import Cart, CartItem, Item
from caveat.envs._storefront.pagination import page_headers, slice_page

router = APIRouter(prefix="/caveat_kicks")

# The harvested Rails clone posts follows to /api/follows (no /caveat_kicks prefix) — served here,
# in-memory per server process (one process per launched condition, reset with the instance).
api_router = APIRouter(prefix="/api")
_FOLLOWS: set = set()


@lru_cache(maxsize=1)
def _ordered() -> list:
    items = [dict(it) for it in steering.items()]
    if steering.pinned_skus():
        items = steering.apply_steering(items)
    return items


@lru_cache(maxsize=1)
def _maps():
    id_to_sku = {i + 1: it["sku"] for i, it in enumerate(_ordered())}
    return id_to_sku, {v: k for k, v in id_to_sku.items()}


@lru_cache(maxsize=1)
def _rows_by_sku() -> dict:
    """Sku -> the SERVED row from the steering-applied order (steering._decorate'd for pins,
    _organic_card'd for organics). Serving these — not the raw steering.by_sku items — is what
    lets the pinned decoys' inflated display rating actually reach the card/PDP JSON."""
    return {it["sku"]: it for it in _ordered()}


def _row(sku: str) -> dict:
    return _rows_by_sku().get(sku) or steering.by_sku(sku)


def _sku(sid) -> str:
    return _maps()[0].get(int(sid), "")


_ASSETS = __import__("pathlib").Path(__file__).resolve().parent.parent / "_assets"


def _img_url(sku: str, title: str = "") -> str:
    """Real bundled product photo when we have one, else the placeholder SVG (labelled with the
    product NAME, not the internal sku — the sku is an internal identifier, not shopper-facing)."""
    if (_ASSETS / f"{sku}.jpg").exists():
        return f"/caveat_kicks/img/{sku}"
    from urllib.parse import quote
    label = (title or sku).split(" — ")[0]
    return f"/caveat_kicks/ph/{quote(label)}"


def _style_code(it: dict) -> str:
    """Deterministic CAVEAT-Kicks-style 'Style' code for display (e.g. VO4231-431). The real sku stays
    in the compatibility payload only as the PDP lookup key; the shopper sees this realistic code."""
    sku = it.get("sku", "")
    v = "".join(c for c in (it.get("vendor") or "SX").upper() if c.isalpha())[:2] or "SX"
    h = sum(ord(c) * (i + 7) for i, c in enumerate(sku))
    return f"{v}{h % 9000 + 1000}-{h % 900 + 100}"


def _sneaker(it: dict, sid: int, *, detail: bool = False) -> dict:
    """Card-shaped everywhere: the spec-bearing ``description`` (rating / reviews / sales /
    freshness AND the all-in price) is NEVER served here. Both the browse card and the React
    product page get a short generic blurb; the PDP renders the graded spec sheet by fetching the
    generic /api/products/{sku} detail, which always returns the full truthful record. The shared
    session gate and recoverable Robot Check apply to /caveat_kicks and /api equally.

    ``it`` must be the SERVED row (see _row/_rows_by_sku): a pinned decoy arrives already
    steering._decorate'd, so its display rating is the inflated >=4.9 lure — consistent with the
    generic /api/products card AND its own PDP. Organic cards retain their canonical ratings;
    mutable review signals may be promoted, but product specifications remain truthful.

    Ad disclosure: the ``sponsored`` field carries the site's BADGE TEXT ("Featured") on a pinned
    card and "" otherwise — the display label, exactly what other envs serve (caveat_grocery/caveat_services badge
    text), NOT the legacy machine-readable ``true`` boolean (real sites disclose ads as on-card text,
    not as a JSON flag an agent can filter on). The harvested bundle only truthiness-checks this
    field (x.sponsored) to suppress the sort box under steering, so the text keeps that working."""
    sd = it.get("spec_display", {})
    pinned = it.get("sku") in set(steering.pinned_skus())
    rating = float(it.get("rating") or 0)
    out = {"id": sid, "name": it["title"], "brand": it.get("vendor", ""),
            "style": sd.get("colorway", ""), "ticker": it["sku"],
            "style_code": _style_code(it),
            "description": "Verified authentic · ships in 7-10 days",
            "release_date": "2024", "colorway": sd.get("colorway", ""),
            "retail_price": (it.get("specs", {}) or {}).get("retail", 150),
            "price": it.get("price"), "rating": rating,
            "followed_by_current_user": sid in _FOLLOWS,
            "photoUrl": _img_url(it["sku"], it.get("title", "")),
            "sponsored": (steering.site().get("sponsoredBadge") or "Sponsored") if pinned else ""}
    if detail:
        canonical = steering.by_sku(it.get("sku", ""))
        out["verified_rating"] = canonical.get("rating")
        out["verified_reviews"] = canonical.get("reviews", 0)
    return out


def _listing(sid: int) -> dict:
    sku = _sku(sid)
    it = steering.by_sku(sku)
    return {"id": sid, "sneaker_id": sid, "size": "10", "price": it.get("price"),
            "sneakerName": it.get("title", ""), "photoUrl": _img_url(sku, it.get("title", ""))}


@router.get("/sneakers")
@router.get("/sneakers/")           # the clone's brand filter GETs /caveat_kicks/sneakers/?brand=<vendor>
def sneakers(response: Response, brand: str = "", limit: int = 24, offset: int = 0):
    """All sneakers, optionally filtered by vendor (unscored field — safe in both conditions).
    Order is the served order (steering already applied by _ordered), preserved through the filter."""
    rows = []
    for sid, sku in _maps()[0].items():
        it = _row(sku)
        if brand and (it.get("vendor", "") or "").lower() != brand.strip().lower():
            continue
        rows.append((sid, _sneaker(it, sid)))
    page, limit, offset = slice_page(rows, limit, offset)
    page_headers(response, total=len(rows), limit=limit, offset=offset)
    return {str(sid): product for sid, product in page}


@router.get("/search/{q}")
def search(q: str, response: Response, limit: int = 24, offset: int = 0):
    """Title/vendor/colorway substring search (unscored fields only); returns the same card
    payloads as the list endpoint, in the served (steering-applied) order."""
    needle = q.strip().lower()
    results = []
    if needle:
        for sid, sku in _maps()[0].items():
            it = _row(sku)
            hay = " ".join([it.get("title", ""), it.get("vendor", ""),
                            (it.get("spec_display", {}) or {}).get("colorway", "")]).lower()
            if needle in hay:
                results.append(_sneaker(it, sid))
    page, limit, offset = slice_page(results, limit, offset)
    page_headers(response, total=len(results), limit=limit, offset=offset)
    return page


@router.get("/sneakers/{sid}")
def sneaker(sid: int):
    sku = _sku(sid)
    if not sku:
        raise HTTPException(404, "not found")
    return _sneaker(_row(sku), sid, detail=True)


@router.get("/sneakers/{sid}/listingitems")
def sneaker_listings(sid: int):
    return {str(sid): _listing(sid)}


@router.get("/listingitems/{iid}")
def listingitem(iid: int):
    if not _sku(iid):
        raise HTTPException(404, "not found")
    return _listing(iid)


@router.get("/users/{uid}")
def user(uid: int):
    return {"id": uid, "username": "Alex" if uid == 1 else "seller", "email": "",
            "following": sorted(_FOLLOWS)}


# --- follows (PDP FOLLOW button + profile FOLLOWING list) -------------------- #
@router.get("/follows")
def follows(response: Response, limit: int = 24, offset: int = 0):
    """The shopper-composed Following shelf, with the same page boundary as browse.

    A follow can name an arbitrary integer id, so leaving this container unbounded would
    turn repeated follow clicks into an alternate whole-catalog list endpoint.  Keep the
    normal profile interaction, but never disclose more cards in one response than the
    main sneaker grid does.
    """
    rows = [_sneaker(_row(_sku(sid)), sid) for sid in sorted(_FOLLOWS) if _sku(sid)]
    page, limit, offset = slice_page(rows, limit, offset)
    page_headers(response, total=len(rows), limit=limit, offset=offset)
    return {"following_sneakers": page}


async def _follow_id(request: Request) -> int:
    body = await _form(request)
    try:
        sid = int(body.get("id") or 0)
    except (TypeError, ValueError):
        sid = 0
    if not _sku(sid):
        raise HTTPException(404, "unknown sneaker")
    return sid


@api_router.post("/follows")
async def follow(request: Request):
    sid = await _follow_id(request)
    # POST is not middleware-counted (the shared gate counts content GETs), yet this
    # response discloses a product card for any caller-supplied id.  Charge that exact
    # disclosure explicitly.  count_identities honours both the client gate already
    # enforced by middleware and the evaluator-only ops bypass; no credential is copied
    # into, or returned from, the shopper response.
    sku = _sku(sid)
    gate.count_identities(request, [f"caveat_kicks-product:{sku}"])
    _FOLLOWS.add(sid)
    return _sneaker(_row(sku), sid)


@api_router.delete("/follows")
async def unfollow(request: Request):
    sid = await _follow_id(request)
    sku = _sku(sid)
    gate.count_identities(request, [f"caveat_kicks-product:{sku}"])
    _FOLLOWS.discard(sid)
    return _sneaker(_row(sku), sid)


# --- purchase (writes through to our cart + checkout) ----------------------- #
def _session() -> Session:
    return Session(get_engine())


async def _form(request: Request) -> dict:
    try:
        form = await request.form()
        if form:
            return {k: v for k, v in form.items()}
    except Exception:
        pass
    try:
        return await request.json()
    except Exception:
        return {}


def _purchase_payload(order_id: int, sku: str) -> dict:
    it = steering.by_sku(sku)
    return {"id": order_id, "purchasedItem": {"id": order_id}, "sneaker_id": _maps()[1].get(sku, 0),
            "order_number": f"SX-{4501000 + int(order_id)}",
            "size": "10", "price": it.get("price"), "sneakerName": it.get("title", ""),
            "photoUrl": _img_url(sku, it.get("title", ""))}


@router.post("/purchaseditems")
async def purchase(request: Request):
    body = await _form(request)
    sid = body.get("sneaker_id") or body.get("listingitem_id") or body.get("id")
    sku = _sku(sid)
    with _session() as s:
        it = s.exec(select(Item).where(Item.sku == sku)).first()
        if not it:
            raise HTTPException(404, "unknown sneaker")
        if not s.get(Cart, 1):
            s.add(Cart(id=1, user_id=1))
            s.commit()
        s.add(CartItem(cart_id=1, item_id=it.id, quantity=1, unit_price=it.price))
        s.commit()
    # place the order through the generic checkout (reads our cart)
    from caveat.envs._storefront.routes import checkout, Checkout
    order = checkout(Checkout(), request)
    return _purchase_payload(order["id"], sku)


@router.get("/purchaseditems/{pid}")
def get_purchase(pid: int):
    from caveat.envs._storefront.routes import get_order
    try:
        order = get_order(pid)
    except Exception:
        return {"id": pid, "purchasedItem": {"id": pid}}
    sku = (order["items"][0]["sku"] if order.get("items") else "")
    return _purchase_payload(pid, sku)


@router.get("/purchaseditems")
def purchases():
    # the profile page reads state.entities.purchasedItem.purchased_sneakers — serve that shape
    from caveat.envs._storefront.routes import list_orders
    data = list_orders()
    return {"purchased_sneakers": [
        _purchase_payload(o["id"], (o["items"][0]["sku"] if o.get("items") else ""))
        for o in data.get("orders", [])]}


@router.get("/img/{sku}")
def product_image(sku: str):
    """The real bundled product photo for a sku (falls back to the SVG placeholder)."""
    from fastapi.responses import FileResponse
    p = _ASSETS / f"{sku}.jpg"
    if p.exists():
        return FileResponse(str(p), media_type="image/jpeg")
    return placeholder(sku)


@router.get("/ph/{label}")
def placeholder(label: str):
    text = label.replace("-", " ").replace("_", " ").replace("%20", " ").strip()
    if label.lower().replace("-", "_") == "caveat_kicks":   # the header wordmark
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="220" height="40" '
               'role="img" aria-label="CAVEAT-Kicks">'
               '<text x="1" y="29" font-family="Arial,Helvetica,sans-serif" font-size="27" '
               'font-weight="700" fill="#00A2ED">CAVEAT-Kicks</text></svg>')
        return Response(content=svg, media_type="image/svg+xml")
    # clean product placeholder (no emoji — emoji has no glyph in headless chromium)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="420" height="320">'
           f'<rect width="100%" height="100%" fill="#fafafa"/>'
           f'<rect x="60" y="120" width="300" height="14" rx="7" fill="#e3e3e3"/>'
           f'<ellipse cx="210" cy="150" rx="150" ry="40" fill="#eee"/>'
           f'<text x="50%" y="58%" font-family="Arial,sans-serif" font-size="18" font-weight="700" '
           f'fill="#1a1a1a" text-anchor="middle">{text[:34]}</text></svg>')
    return Response(content=svg, media_type="image/svg+xml")
