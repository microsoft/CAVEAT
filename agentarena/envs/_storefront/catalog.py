"""Authoring API: a brand ``SiteConfig`` + a ``Catalog`` of ``Item``s. Each clone's
frontend maps this generic data into its own UI shapes; the same objects score what
the agent transacts. ``role`` is ``compliant`` / ``decoy`` (set ``advertised=True``;
must have a real on-page flaw) / ``distractor``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Item:
    sku: str
    title: str
    price: float
    specs: dict[str, Any] = field(default_factory=dict)
    role: str = "distractor"
    advertised: bool = False
    vendor: str = ""
    vendor_slug: str = ""
    category: str = ""
    rating: float = 4.6
    reviews: int = 800
    image: str = ""                     # real photo URL/path (preferred) …
    image_emoji: str = "\U0001F6CD"     # … or an emoji fallback
    image_color: str = "#eef0f3"
    list_price: Optional[float] = None
    bullets: list[str] = field(default_factory=list)
    description: str = ""
    badges: list[str] = field(default_factory=list)
    variants: Optional[dict] = None
    spec_display: dict[str, str] = field(default_factory=dict)
    display_price: Optional[float] = None
    true_price: Optional[float] = None

    def attrs(self) -> dict[str, Any]:
        price = self.true_price if self.true_price is not None else self.price
        return {**self.specs, "price": price}

    def to_seed(self) -> dict[str, Any]:
        return {
            "sku": self.sku, "title": self.title, "vendor": self.vendor,
            "vendor_slug": self.vendor_slug or _slug(self.vendor), "category": self.category,
            "price": self.price,
            "list_price": self.list_price if self.list_price is not None else round(self.price * 1.15, 2),
            "rating": self.rating, "reviews": self.reviews, "image": self.image,
            "image_emoji": self.image_emoji, "image_color": self.image_color,
            "role": self.role, "advertised": self.advertised, "description": self.description,
            "bullets": self.bullets, "badges": list(self.badges), "specs": self.specs,
            "spec_display": self.spec_display, "variants": self.variants,
            "display_price": self.display_price, "true_price": self.true_price,
        }


@dataclass
class SiteConfig:
    brand: str
    vertical: str = "product"           # "product" | "lead"
    labels: dict[str, str] = field(default_factory=dict)
    sponsored_badge: str = "Sponsored"
    value_badge: str = "Best Value"
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"brand": self.brand, "vertical": self.vertical, "labels": self.labels,
                "sponsoredBadge": self.sponsored_badge, "valueBadge": self.value_badge,
                **self.extra}


@dataclass
class Catalog:
    name: str
    site: SiteConfig
    items: list[Item]
    category: str = "all"
    bury_index: int = 6

    def to_seed_json(self) -> dict[str, Any]:
        return {"brand": self.site.brand, "site": self.site.to_json(), "category": self.category,
                "bury_index": self.bury_index, "items": [it.to_seed() for it in self.items]}

    def advertised_skus(self) -> list[str]:
        return [it.sku for it in self.items if it.advertised]

    def by_sku(self, sku: str) -> Optional[Item]:
        return next((it for it in self.items if it.sku == sku), None)


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]
