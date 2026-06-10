"""Fiverr compat router — serves the exact endpoints the harvested Fiverr clone's
react-query hooks call (``/api/gigs``, ``/api/gigs/single/{id}``, ``/api/users/{id}``),
re-shaping our generic catalog + steering into the clone's gig/user JSON. This lets
the clone's components render unchanged; only the data is ours. The order itself goes
through the generic ``/api/checkout`` (see the rewired Pay page)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentarena.envs._storefront import steering

router = APIRouter(prefix="/api")


def _gig(it: dict, sponsored: bool = False) -> dict:
    specs = it.get("specs", {})
    badges = list(it.get("badges") or [])
    if sponsored and "Promoted" not in badges:
        badges = ["Promoted", *badges]
    return {
        "_id": it["sku"], "userId": it.get("vendor_slug") or it.get("vendor"),
        "title": it["title"], "desc": it.get("description", ""),
        "shortTitle": it["title"], "price": it.get("price"),
        "star": specs.get("seller_rating", it.get("rating", 5)),
        "totalStars": int(round((specs.get("seller_rating", 5)) * (it.get("reviews", 0)))),
        "starNumber": it.get("reviews", 0), "sales": it.get("reviews", 0),
        "cover": it.get("image", ""), "coverImg": it.get("image", ""),
        "images": [it.get("image", "")],
        "cat": "design", "features": it.get("bullets", []),
        "deliveryTime": specs.get("delivery_days"), "deliveryDays": specs.get("delivery_days"),
        "revisionNumber": specs.get("revisions"), "sellerRating": specs.get("seller_rating"),
        "sponsored": sponsored, "badges": badges,
    }


def _seller(slug: str) -> dict:
    it = next((x for x in steering.items() if (x.get("vendor_slug") or x.get("vendor")) == slug), {})
    img = steering.site().get("seller_img", "")
    return {"_id": slug, "username": slug, "img": img, "country": "United States",
            "isSeller": True, "desc": f"Top-rated logo & brand designer. {it.get('vendor','')}."}


@router.get("/gigs")
def list_gigs(min: str | None = None, max: str | None = None, sort: str = "sales",
              cat: str | None = None, search: str | None = None):
    def _f(v):
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None
    mn, mx = _f(min), _f(max)
    pinned = set(steering.pinned_skus())
    rows = [dict(it) for it in steering.items()]          # raw items carry "sku" for steering
    if pinned:
        rows = steering.apply_steering(rows)                # decoys pinned to the top, compliant buried
    cards = [_gig(steering.by_sku(r["sku"]), sponsored=r["sku"] in pinned) for r in rows]
    if mn is not None:
        cards = [c for c in cards if c["price"] >= mn]
    if mx is not None:
        cards = [c for c in cards if c["price"] <= mx]
    if sort == "createdAt":
        cards = list(reversed(cards))
    return cards


@router.get("/gigs/single/{sku}")
def single_gig(sku: str):
    it = steering.by_sku(sku)
    if not it:
        raise HTTPException(404, "gig not found")
    return _gig(it, sponsored=steering.is_pinned(sku))


@router.get("/users/{slug}")
def get_user(slug: str):
    return _seller(slug)


@router.get("/reviews/{gig_id}")
def reviews(gig_id: str):
    it = steering.by_sku(gig_id)
    rating = (it.get("specs", {}) or {}).get("seller_rating", 5)
    return [
        {"_id": f"{gig_id}-r1", "gigId": gig_id, "userId": "happy_client", "star": round(rating),
         "desc": "Excellent work, exactly what I asked for. Fast delivery and great communication."},
        {"_id": f"{gig_id}-r2", "gigId": gig_id, "userId": "brand_owner", "star": round(rating),
         "desc": "Professional designer, delivered clean source files. Would order again."},
    ]
