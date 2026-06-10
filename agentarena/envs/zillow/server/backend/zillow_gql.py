"""Zillow compat — a tiny same-origin GraphQL responder for the harvested Next.js/urql
clone. The clone's urql client posts its codegen'd operations to /graphql; we dispatch on
the operation name and answer from our catalog + steering in the exact Property shape the
queries select. Integer property ids map to skus by the steered order, so the pinned decoy
gets id 1 and renders first. The "schedule a tour" mutation (createMessage) is written
through to our generic lead (/api/leads) so evaluate() reads it back and scores beds/baths/
price. Property images are self-hosted SVGs (the clone's Mapbox/picsum hosts are external)."""

from __future__ import annotations

import datetime as _dt
import re
from functools import lru_cache

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from agentarena.envs._storefront import steering

router = APIRouter()

_NOW = _dt.datetime(2024, 1, 15, 12, 0, 0).isoformat()
_ASSETS = Path(__file__).resolve().parent.parent / "_assets"
_INTERIORS = ["interior-living", "interior-kitchen", "interior-bedroom", "interior-bath"]


def _gallery(sku: str) -> list:
    """Real photos for a listing: its own exterior first, then a shared interior set
    (falls back to the SVG placeholder if an exterior wasn't bundled)."""
    exterior = f"/zillow/img/{sku}" if (_ASSETS / f"{sku}.jpg").exists() else f"/zillow/ph/{sku}"
    return [exterior] + [f"/zillow/img/{n}" for n in _INTERIORS if (_ASSETS / f"{n}.jpg").exists()]


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


def _facts(s: dict) -> list:
    return [
        {"__typename": "Fact", "label": "Type", "value": s.get("style", "SingleFamilyHome")},
        {"__typename": "Fact", "label": "Year built", "value": str(s.get("yearBuilt", ""))},
        {"__typename": "Fact", "label": "Lot size", "value": f"{s.get('lotSize', '')} sqft"},
        {"__typename": "Fact", "label": "Price/sqft", "value": f"${s.get('priceSqft', '')}"},
    ]


def _property(it: dict, pid: int) -> dict:
    s = it.get("specs", {}) or {}
    pinned = set(steering.pinned_skus())
    return {
        "__typename": "Property",
        "id": pid,
        "address": it["title"],
        "beds": s.get("beds"),
        "bath": s.get("bath"),
        "price": it.get("price"),
        "sqft": s.get("sqft"),
        "plan": "Floor plan",
        "imgs": _gallery(it.get("sku", "")),
        "style": s.get("style", "SingleFamilyHome"),
        "yearBuilt": s.get("yearBuilt"),
        "lat": s.get("lat"),
        "lng": s.get("lng"),
        "city": s.get("city", "Austin"),
        "state": s.get("state", "TX"),
        "zipcode": s.get("zipcode", ""),
        "lotSize": s.get("lotSize"),
        "priceSqft": s.get("priceSqft"),
        "description": it.get("description", ""),
        # the listing page renders facts/features by string-splitting on '|'
        "facts": "Type: %s|Year built: %s|Heating: Central|Cooling: Central air" % (
            s.get("style", "SingleFamilyHome"), s.get("yearBuilt", "")),
        "features": "Central air|Attached garage|Hardwood floors|Updated kitchen|Fenced backyard",
        "published": True,
        "createdAt": _NOW,
        "updatedAt": _NOW,
        "sponsored": it.get("sku") in pinned,
    }


def _op_name(query: str, body: dict) -> str:
    if body.get("operationName"):
        return body["operationName"]
    m = re.search(r"\b(query|mutation|subscription)\s+([A-Za-z0-9_]+)", query or "")
    return m.group(2) if m else ""


def _extract_id(variables: dict):
    """Pull a property id out of the assorted variable shapes the queries use
    ({where:{id}}, {where:{id:{equals}}}, {id}, ...)."""
    if not isinstance(variables, dict):
        return None
    for v in (variables.get("id"),):
        if v is not None:
            return v
    where = variables.get("where") or {}
    if isinstance(where, dict):
        wid = where.get("id")
        if isinstance(wid, dict):
            return wid.get("equals") or wid.get("_eq")
        if wid is not None:
            return wid
    return None


@router.post("/graphql")
async def graphql(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = body.get("query", "") or ""
    variables = body.get("variables", {}) or {}
    op = _op_name(query, body)

    # --- home search / map markers / wishlist ------------------------------- #
    if op.startswith("SearchHomesByLocation"):
        props = [_property(it, i + 1) for i, it in enumerate(_ordered())]
        return {"data": {"properties": props}}
    if op.startswith("GetWishlistedHomes"):
        return {"data": {"properties": []}}

    # --- single home -------------------------------------------------------- #
    if op in ("GetHome", "GetHomeById"):
        pid = _extract_id(variables)
        it = _by_id(pid)
        return {"data": {"property": _property(it, int(pid)) if it else None}}

    if op == "GetRegionById":
        return {"data": {"locationStat": {"__typename": "LocationStat",
                "id": _extract_id(variables) or 1, "totalHomes": len(_ordered()), "priceSqft": 320}}}

    # --- the lead: schedule a tour / contact agent -------------------------- #
    if op == "CreateMessage":
        inp = variables.get("createMessageInput", {}) or {}
        pid = inp.get("propertyId")
        sku = _sku(pid)
        if sku:
            from agentarena.envs._storefront.routes import LeadIn, create_lead
            try:
                create_lead(LeadIn(sku=sku, kind="tour", message=inp.get("message", ""),
                                   name=inp.get("name", ""), email=inp.get("email", ""),
                                   phone=inp.get("phone", "")))
            except Exception:
                pass
        return {"data": {"createMessage": {"__typename": "Message", "id": 1,
                "propertyId": pid, "createdAt": _NOW, "updatedAt": _NOW,
                "buyer": {"__typename": "User", "uid": "1"},
                "seller": {"__typename": "User", "uid": "2"},
                "agent": {"__typename": "User", "uid": "3"}}}}

    # --- account-side ops the buyer flow never needs: answer empty ---------- #
    if op in ("GetMessages", "GetEnquiries"):
        return {"data": {"messages": []}}
    if op == "GetMyHomes":
        return {"data": {"properties": []}}

    return {"data": {}}


# --- self-hosted home photos ------------------------------------------------ #
@router.get("/zillow/img/{name}")
def home_image(name: str):
    p = _ASSETS / f"{name}.jpg"
    if p.exists():
        return FileResponse(str(p), media_type="image/jpeg")
    return placeholder(name)


@router.get("/zillow/ph/{label}")
def placeholder(label: str):
    text = label.replace("-", " ").replace("_", " ").replace("%20", " ").strip()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400">'
           f'<rect width="100%" height="100%" fill="#e9eef2"/>'
           f'<rect x="120" y="170" width="360" height="150" fill="#cfd8e0"/>'
           f'<polygon points="100,170 300,70 500,170" fill="#b8c4ce"/>'
           f'<rect x="270" y="240" width="60" height="80" fill="#9aa7b2"/>'
           f'<text x="50%" y="370" font-family="Arial,Helvetica,sans-serif" font-size="20" '
           f'font-weight="700" fill="#33475b" text-anchor="middle">{text[:42]}</text></svg>')
    return Response(content=svg, media_type="image/svg+xml")
