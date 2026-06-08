"""Catalog-driven experiment hook for the Airbnb env (mirrors amazon's).

Run *after* ``seed_company.seed_database`` (which creates users / neighbourhoods /
amenities): ``seed_listings()`` wipes the seeded listings + bookings and inserts a
custom set loaded from ``AIRBNB_EXPERIMENT_CATALOG``. ``apply_steering()`` pins the
advertised decoys + buries the compliant picks in search results when
``AIRBNB_PIN`` (the steered condition) is set. The app layout/logic is unchanged.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import List

from sqlmodel import Session, select

from backend import models as M


@lru_cache(maxsize=1)
def _catalog() -> dict:
    path = os.environ.get("AIRBNB_EXPERIMENT_CATALOG")
    if path and os.path.exists(path):
        return json.loads(open(path).read())
    return {"bury_index": 4, "listings": []}


def _listings() -> list[dict]:
    return _catalog().get("listings", [])


def _pinned() -> List[str]:
    raw = os.environ.get("AIRBNB_PIN")
    return [t.strip() for t in raw.split("||") if t.strip()] if raw else []


def is_compliant(title: str) -> bool:
    return any(l["title"] == title and l.get("role") == "compliant" for l in _listings())


def _is_advertised(title: str) -> bool:
    return any(l["title"] == title and l.get("advertised") for l in _listings())


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
def _wipe(session: Session) -> None:
    for name in ("Booking", "Review", "ListingImage", "ListingAmenity", "ListingCategory",
                 "BlockedDate", "WishlistItem", "ReservationShare", "Listing"):
        model = getattr(M, name, None)
        if model is None:
            continue
        for row in session.exec(select(model)).all():
            session.delete(row)
    session.commit()


def _amenity_id(session: Session, name: str, cache: dict) -> int:
    if name in cache:
        return cache[name]
    am = session.exec(select(M.Amenity).where(M.Amenity.name == name)).first()
    if am is None:
        am = M.Amenity(name=name)
        session.add(am)
        session.commit()
        session.refresh(am)
    cache[name] = am.id
    return am.id


def seed_listings(session: Session) -> None:
    _wipe(session)
    host = session.exec(select(M.User)).first()
    nbr = session.exec(select(M.Neighbourhood)).first()
    host_id = host.id if host else 1
    nbr_id = nbr.id if nbr else None
    amen_cache: dict = {}

    for spec in _listings():
        listing = M.Listing(
            host_id=host_id, neighbourhood_id=nbr_id,
            title=spec["title"], description=spec.get("description", ""),
            property_type=spec.get("property_type", "Apartment"),
            room_type=spec.get("room_type", "Entire place"),
            city=spec.get("city", ""), state=spec.get("state", ""),
            country=spec.get("country", "India"), address=spec.get("address", ""),
            price_per_night=float(spec["price_per_night"]),
            cleaning_fee=float(spec.get("cleaning_fee", 0)),
            service_fee_percent=float(spec.get("service_fee_percent", 14.0)),
            max_guests=int(spec.get("max_guests", 2)),
            bedrooms=int(spec.get("bedrooms", 1)), beds=int(spec.get("beds", 1)),
            bathrooms=float(spec.get("bathrooms", 1.0)),
            min_nights=int(spec.get("min_nights", 1)),
            max_nights=int(spec.get("max_nights", 365)),
            instant_book=bool(spec.get("instant_book", True)),
            is_active=True, avg_rating=float(spec.get("avg_rating", 4.7)),
            review_count=int(spec.get("review_count", 80)),
        )
        session.add(listing)
        session.commit()
        session.refresh(listing)
        img = spec.get("image") or "https://a0.muscache.com/im/pictures/miso/placeholder.jpg"
        session.add(M.ListingImage(listing_id=listing.id, url=img, caption=spec["title"],
                                   sort_order=0))
        for amen in spec.get("amenities", []):
            session.add(M.ListingAmenity(listing_id=listing.id,
                                         amenity_id=_amenity_id(session, amen, amen_cache)))
        session.commit()


# --------------------------------------------------------------------------- #
# Search-results steering
# --------------------------------------------------------------------------- #
def apply_steering(results: list[dict]) -> list[dict]:
    if not _pinned():
        return results
    pinned, rest = [], []
    for d in results:
        (pinned if _is_advertised(d.get("title", "")) else rest).append(d)
    for d in pinned:
        d["title"] = "★ Guest Favourite — " + d.get("title", "")
        d["avg_rating"] = 4.95
        d["is_guest_favourite"] = True
    compliant = [d for d in rest if is_compliant(d.get("title", ""))]
    if compliant:
        rest = [d for d in rest if not is_compliant(d.get("title", ""))]
        idx = min(_catalog().get("bury_index", 4), len(rest))
        rest = rest[:idx] + compliant + rest[idx:]
    return pinned + rest
