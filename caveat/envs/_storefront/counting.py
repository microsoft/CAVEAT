"""Auditable clone-storefront surface policy.

Every shopper route that discloses catalog information is classified here.  The
shared app and the certification suite consume the same table, preventing a new
compatibility endpoint from becoming an unmetered enumeration bypass.
"""

from __future__ import annotations

from dataclasses import dataclass

PAGE_SIZE = 24


@dataclass(frozen=True)
class Surface:
    pattern: str
    kind: str                 # card_list | detail | card_rail | transaction
    paged: bool = False


SURFACES = (
    Surface(r"^/api/products$", "card_list", True),
    Surface(r"^/api/storefront$", "card_list", False),       # CAVEAT-Food long menu
    Surface(r"^/api/products/[^/]+$", "detail"),
    Surface(r"^/api/gigs$", "card_list", True),
    Surface(r"^/api/gigs/single/[^/]+$", "detail"),
    Surface(r"^/api/reviews/[^/]+$", "detail"),
    Surface(r"^/caveat_market/products$", "card_list", True),
    Surface(r"^/caveat_market/products/search-by-name/.*$", "card_list", True),
    Surface(r"^/caveat_market/products/get-random$", "card_rail"),
    Surface(r"^/caveat_market/product/[^/]+$", "detail"),
    Surface(r"^/caveat_craft/products$", "card_list", True),
    Surface(r"^/caveat_craft/search_products$", "card_list", True),
    Surface(r"^/caveat_craft/products/\d+$", "detail"),
    Surface(r"^/caveat_craft/products/\d+/reviews$", "detail"),
    Surface(r"^/caveat_kicks/sneakers/?$", "card_list", True),
    Surface(r"^/caveat_kicks/search/.*$", "card_list", True),
    Surface(r"^/caveat_kicks/sneakers/\d+$", "detail"),
    Surface(r"^/caveat_kicks/sneakers/\d+/listingitems$", "card_rail"),
    Surface(r"^/caveat_kicks/listingitems/\d+$", "card_rail"),
    Surface(r"^/caveat_kicks/follows$", "card_list", True),
    # POST/DELETE both return the caller-selected product card.  Middleware only
    # prices GET reads, so caveat_kicks_api explicitly charges this declared mutation via
    # gate.count_identities().
    Surface(r"^/api/follows$", "card_mutation"),
)

COUNTED_PATHS = tuple(s.pattern for s in SURFACES if s.kind != "transaction")
PAGED_PATHS = tuple(s.pattern for s in SURFACES if s.paged)


def inventory() -> list[dict]:
    return [{"pattern": s.pattern, "kind": s.kind, "paged": s.paged} for s in SURFACES]
