"""Code-generate the clone's ``components/datav2.tsx`` from our Python catalog, so the
real DoorDash frontend renders OUR dishes (with skus) and the backend agrees on them.

    python -m agentarena.envs.doordash.server.gen_datav2   # writes frontend/components/datav2.tsx
"""

from __future__ import annotations

import json
from pathlib import Path

from agentarena.envs.doordash.catalog import RESTAURANTS

OUT = Path(__file__).resolve().parent / "frontend" / "components" / "datav2.tsx"


def _restaurant_entry(r: dict) -> str:
    items = []
    for dish in r["dishes"]:
        # tuple is (sku, name, price, image, veg, role, adv, desc[, true_price]). The card shows the
        # sticker `price`; any drip true_price is a backend checkout surcharge, not shown on the card.
        sku, name, price, image, veg, role, adv, desc = dish[:8]
        items.append({
            "sku": sku, "itemName": name, "image": {"src": image, "alt": name},
            "price": price, "description": desc, "vegetarian": veg,
            "ratingPercentage": int(round(r["rating"] * 19)), "ratingCount": 80,
        })
    data = {
        "restaurantData": {
            "restaurantName": r["name"],
            "restaurantImage": {"src": r["image"], "alt": f"Image from {r['name']}"},
            "distance": r["distance"], "deliveryTime": r["eta"], "pickupTime": r["eta"],
            "isDashPass": True, "deliveryFee": r["fee"],
        },
        "storefrontData": {
            "shortDescription": r["desc"], "averageRating": r["rating"],
            "ratingCount": r["ratings"], "priceRating": 2,
            "operationHours": [{"openHour": 8, "openMinute": 0, "closeHour": 22, "closeMinute": 30}],
            "items": items,
        },
    }
    return f"    {r['store_id']}: {json.dumps(data, indent=8)[:-1]}    }}"


def generate() -> str:
    entries = ",\n".join(_restaurant_entry(r) for r in RESTAURANTS)
    ids = [r["store_id"] for r in RESTAURANTS]
    carousels = [
        {"carouselName": "Now on DoorDash", "selectedRestaurantIDs": ids},
        {"carouselName": "Most Popular Local Restaurants", "selectedRestaurantIDs": ids[1:] + ids[:1]},
    ]
    return ("// AUTO-GENERATED from agentarena/envs/doordash/catalog.py — do not edit by hand.\n"
            "export const restaurantList = {\n" + entries + "\n};\n\n"
            "export const restaurantCarousels = " + json.dumps(carousels, indent=4) + ";\n")


if __name__ == "__main__":
    OUT.write_text(generate())
    print(f"wrote {OUT}")
