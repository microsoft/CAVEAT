"""Seed database with Kaggle Airbnb India data: 150 listings, 15 users, 200 reviews, 150 bookings."""

import argparse
import csv
import hashlib
import os
import random
from datetime import datetime, timedelta, date

from sqlmodel import SQLModel, Session, delete, select

from backend.database import get_engine, init_db, set_db_path
from backend.models import (
    User, Listing, ListingImage, Category, ListingCategory,
    Amenity, ListingAmenity, Booking, Review,
    Wishlist, WishlistItem, Currency, UserSettings,
    HelpArticle, SupportTicket, SearchHistory,
    MessageThread, Message, Notification,
    ReservationShare, BlockedDate
)


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# --- Static Data ---

CATEGORIES_DATA = [
    {"name": "Icons", "icon": "\u2b50", "sort_order": 1},
    {"name": "Amazing views", "icon": "\U0001f3d4\ufe0f", "sort_order": 2},
    {"name": "Rooms", "icon": "\U0001f6cf\ufe0f", "sort_order": 3},
    {"name": "Beachfront", "icon": "\U0001f3d6\ufe0f", "sort_order": 4},
    {"name": "Cabins", "icon": "\U0001f3e1", "sort_order": 5},
    {"name": "OMG!", "icon": "\U0001f62e", "sort_order": 6},
    {"name": "Lakefront", "icon": "\U0001f30a", "sort_order": 7},
    {"name": "Trending", "icon": "\U0001f525", "sort_order": 8},
    {"name": "Countryside", "icon": "\U0001f33e", "sort_order": 9},
    {"name": "Mansions", "icon": "\U0001f3f0", "sort_order": 10},
    {"name": "Treehouses", "icon": "\U0001f333", "sort_order": 11},
    {"name": "Castles", "icon": "\U0001f3ef", "sort_order": 12},
    {"name": "Pools", "icon": "\U0001f3ca", "sort_order": 13},
    {"name": "Farms", "icon": "\U0001f69c", "sort_order": 14},
    {"name": "Tropical", "icon": "\U0001f334", "sort_order": 15},
]

AMENITIES_DATA = [
    {"name": "Hot water", "category": "Bathroom", "icon": "\U0001f6bf", "sort_order": 1},
    {"name": "Shampoo", "category": "Bathroom", "icon": "\U0001f9f4", "sort_order": 2},
    {"name": "Hair dryer", "category": "Bathroom", "icon": "\U0001f487", "sort_order": 3},
    {"name": "Washer", "category": "Bedroom and laundry", "icon": "\U0001f9fa", "sort_order": 4},
    {"name": "Dryer", "category": "Bedroom and laundry", "icon": "\U0001f300", "sort_order": 5},
    {"name": "Iron", "category": "Bedroom and laundry", "icon": "\U0001f454", "sort_order": 6},
    {"name": "Hangers", "category": "Bedroom and laundry", "icon": "\U0001fa9d", "sort_order": 7},
    {"name": "Bed linens", "category": "Bedroom and laundry", "icon": "\U0001f6cf\ufe0f", "sort_order": 8},
    {"name": "TV", "category": "Entertainment", "icon": "\U0001f4fa", "sort_order": 9},
    {"name": "Books", "category": "Entertainment", "icon": "\U0001f4da", "sort_order": 10},
    {"name": "Board games", "category": "Entertainment", "icon": "\U0001f3b2", "sort_order": 11},
    {"name": "Kitchen", "category": "Kitchen and dining", "icon": "\U0001f373", "sort_order": 12},
    {"name": "Refrigerator", "category": "Kitchen and dining", "icon": "\U0001f9ca", "sort_order": 13},
    {"name": "Microwave", "category": "Kitchen and dining", "icon": "\U0001f4e1", "sort_order": 14},
    {"name": "Oven", "category": "Kitchen and dining", "icon": "\U0001f525", "sort_order": 15},
    {"name": "Dishes", "category": "Kitchen and dining", "icon": "\U0001f37d\ufe0f", "sort_order": 16},
    {"name": "Coffee maker", "category": "Kitchen and dining", "icon": "\u2615", "sort_order": 17},
    {"name": "Wifi", "category": "Internet and office", "icon": "\U0001f4f6", "sort_order": 18},
    {"name": "Dedicated workspace", "category": "Internet and office", "icon": "\U0001f4bb", "sort_order": 19},
    {"name": "Pool", "category": "Outdoor", "icon": "\U0001f3ca", "sort_order": 20},
    {"name": "BBQ grill", "category": "Outdoor", "icon": "\U0001f356", "sort_order": 21},
    {"name": "Garden", "category": "Outdoor", "icon": "\U0001f33f", "sort_order": 22},
    {"name": "Patio", "category": "Outdoor", "icon": "\U0001fa91", "sort_order": 23},
    {"name": "Free parking", "category": "Parking", "icon": "\U0001f17f\ufe0f", "sort_order": 24},
    {"name": "Garage", "category": "Parking", "icon": "\U0001f3d7\ufe0f", "sort_order": 25},
    {"name": "Smoke alarm", "category": "Home safety", "icon": "\U0001f6a8", "sort_order": 26},
    {"name": "Fire extinguisher", "category": "Home safety", "icon": "\U0001f9ef", "sort_order": 27},
    {"name": "First aid kit", "category": "Home safety", "icon": "\U0001fa79", "sort_order": 28},
    {"name": "Air conditioning", "category": "Heating and cooling", "icon": "\u2744\ufe0f", "sort_order": 29},
    {"name": "Heating", "category": "Heating and cooling", "icon": "\U0001f525", "sort_order": 30},
    {"name": "Ceiling fan", "category": "Heating and cooling", "icon": "\U0001f300", "sort_order": 31},
    {"name": "Crib", "category": "Family", "icon": "\U0001f476", "sort_order": 32},
    {"name": "High chair", "category": "Family", "icon": "\U0001fa91", "sort_order": 33},
    {"name": "Beach access", "category": "Location features", "icon": "\U0001f3d6\ufe0f", "sort_order": 34},
    {"name": "Waterfront", "category": "Location features", "icon": "\U0001f30a", "sort_order": 35},
    {"name": "Breakfast", "category": "Services", "icon": "\U0001f950", "sort_order": 36},
    {"name": "Long-term stays", "category": "Services", "icon": "\U0001f4c5", "sort_order": 37},
]

CURRENCIES_DATA = [
    {"code": "USD", "symbol": "$", "name": "US Dollar", "exchange_rate": 1.0},
    {"code": "INR", "symbol": "\u20b9", "name": "Indian Rupee", "exchange_rate": 83.0},
    {"code": "EUR", "symbol": "\u20ac", "name": "Euro", "exchange_rate": 0.92},
    {"code": "GBP", "symbol": "\u00a3", "name": "British Pound", "exchange_rate": 0.79},
    {"code": "AUD", "symbol": "A$", "name": "Australian Dollar", "exchange_rate": 1.53},
    {"code": "CAD", "symbol": "C$", "name": "Canadian Dollar", "exchange_rate": 1.36},
    {"code": "JPY", "symbol": "\u00a5", "name": "Japanese Yen", "exchange_rate": 149.0},
    {"code": "SGD", "symbol": "S$", "name": "Singapore Dollar", "exchange_rate": 1.34},
    {"code": "AED", "symbol": "\u062f.\u0625", "name": "UAE Dirham", "exchange_rate": 3.67},
    {"code": "THB", "symbol": "\u0e3f", "name": "Thai Baht", "exchange_rate": 35.0},
]

# --- Users ---

USERS_DATA = [
    {"name": "Priya Sharma", "email": "abc@gmail.com", "phone": "+91-98100-12345", "bio": "Travel enthusiast and food blogger. Love discovering hidden gems across India.", "is_host": True, "is_superhost": True},
    {"name": "Arjun Mehta", "email": "arjun.mehta@gmail.com", "phone": "+91-98200-23456", "bio": "Architect turned hospitality entrepreneur. My properties reflect my love for design.", "is_host": True, "is_superhost": True},
    {"name": "Ananya Reddy", "email": "ananya.reddy@gmail.com", "phone": "+91-98300-34567", "bio": "Software engineer who invests in vacation rentals. Remote work friendly stays!", "is_host": True, "is_superhost": False},
    {"name": "Vikram Singh", "email": "vikram.singh@gmail.com", "phone": "+91-98400-45678", "bio": "Retired army officer managing heritage properties in Rajasthan and Himachal.", "is_host": True, "is_superhost": True},
    {"name": "Deepa Nair", "email": "deepa.nair@gmail.com", "phone": "+91-98500-56789", "bio": "Kerala native with a passion for sustainable tourism and backwater living.", "is_host": True, "is_superhost": False},
    {"name": "Rohan Kapoor", "email": "rohan.kapoor@gmail.com", "phone": "+91-98600-67890", "bio": "Boutique hotel owner expanding to Airbnb. Luxury stays at affordable prices.", "is_host": True, "is_superhost": True},
    {"name": "Kavita Joshi", "email": "kavita.joshi@gmail.com", "phone": "+91-98700-78901", "bio": "Mountain lover and trekking guide. My cabins are your base camp for adventure.", "is_host": True, "is_superhost": False},
    {"name": "Sanjay Gupta", "email": "sanjay.gupta@gmail.com", "phone": "+91-98800-89012", "bio": "Weekend traveler exploring India one city at a time.", "is_host": False, "is_superhost": False},
    {"name": "Meera Iyer", "email": "meera.iyer@gmail.com", "phone": "+91-98900-90123", "bio": "Digital nomad and yoga practitioner. Always looking for peaceful retreats.", "is_host": False, "is_superhost": False},
    {"name": "Aditya Verma", "email": "aditya.verma@gmail.com", "phone": "+91-99000-01234", "bio": "Corporate professional who plans epic group trips with friends.", "is_host": False, "is_superhost": False},
    {"name": "Sneha Patil", "email": "sneha.patil@gmail.com", "phone": "+91-99100-12345", "bio": "Solo female traveler documenting budget-friendly stays across India.", "is_host": False, "is_superhost": False},
    {"name": "Rahul Deshmukh", "email": "rahul.deshmukh@gmail.com", "phone": "+91-99200-23456", "bio": "Photographer seeking Instagrammable locations and unique accommodations.", "is_host": False, "is_superhost": False},
    {"name": "Nisha Krishnan", "email": "nisha.krishnan@gmail.com", "phone": "+91-99300-34567", "bio": "Family travel planner. Kid-friendly stays are my specialty.", "is_host": False, "is_superhost": False},
    {"name": "Amit Choudhury", "email": "amit.choudhury@gmail.com", "phone": "+91-99400-45678", "bio": "Foodie traveler on a mission to taste every regional cuisine.", "is_host": False, "is_superhost": False},
    {"name": "Ritu Bhatia", "email": "ritu.bhatia@gmail.com", "phone": "+91-99500-56789", "bio": "Wellness retreat seeker. Love spas, meditation, and nature stays.", "is_host": False, "is_superhost": False},
]

DESCRIPTION_TEMPLATES = [
    "Welcome to this beautiful {prop_type} in {city}. Enjoy {feature1} and {feature2}. Perfect for {audience}.",
    "Experience the charm of {city} from this stunning {prop_type}. Featuring {feature1}, {feature2}, and all modern amenities. Ideal for {audience}.",
    "Nestled in the heart of {city}, this {prop_type} offers {feature1} and {feature2}. A perfect base to explore the local culture and cuisine.",
    "This thoughtfully designed {prop_type} in {city} combines {feature1} with {feature2}. Wake up to beautiful mornings and end your day in complete comfort.",
    "Escape to this serene {prop_type} in {city}. With {feature1} and {feature2}, you will have everything you need for an unforgettable stay.",
]

FEATURES = [
    "panoramic views", "a private pool", "lush gardens", "traditional architecture",
    "modern interiors", "a fully equipped kitchen", "a spacious balcony", "high-speed wifi",
    "air-conditioned rooms", "a rooftop terrace", "beach proximity", "mountain views",
    "a cozy fireplace", "handpicked furnishings", "local artwork", "a peaceful courtyard",
    "a spa bathroom", "natural lighting", "eco-friendly amenities", "a barbecue area",
]

AUDIENCES = [
    "couples and honeymooners", "families with children", "solo travelers",
    "digital nomads and remote workers", "groups of friends", "weekend getaways",
    "nature lovers", "adventure seekers", "culture enthusiasts", "foodies and explorers",
]

REVIEW_COMMENTS = [
    "Absolutely stunning property! The views were breathtaking and we didn't want to leave. Already planning our next trip back!",
    "Amazing stay! The host was incredibly welcoming and the place was spotless.",
    "Loved every minute of our stay. The location is perfect and the views are breathtaking.",
    "The photos don't do it justice - even more beautiful in person! We were blown away.",
    "This place is truly magical. Woke up to the most stunning sunrise every single morning.",
    "A hidden gem! Already planning our next visit. This was the best Airbnb experience ever!",
    "We celebrated our anniversary here and it was absolutely perfect. Could not have asked for more!",
    "Five stars all the way! The host went above and beyond to make our stay comfortable.",
    "The villa was exactly as described. The pool was clean, kitchen fully equipped with all utensils, and the bedroom linens were hotel-quality. The host left us a welcome basket with local snacks and a handwritten note with restaurant recommendations.",
    "Very clean and well-maintained property. The bathroom had premium toiletries, the kitchen had a coffee maker and spices, and the wifi was fast enough for video calls. Would highly recommend to anyone visiting.",
    "Beautiful property with attention to detail. Every room was thoughtfully decorated with local artwork. The balcony had comfortable seating and the garden was well-maintained. The local tips from the host were invaluable.",
    "The traditional architecture combined with modern comforts is a rare find. Loved the hand-carved wooden doors, the antique furniture, and yet the bathroom was completely modern with a rain shower.",
    "Spacious rooms and comfortable beds. We slept like babies every night. The kitchen was well-stocked and we enjoyed cooking local recipes with the spice collection the host left for guests.",
    "Ideal for remote work - fast wifi with backup connection, a dedicated workspace with ergonomic chair, and a quiet neighborhood. The coffee maker was a lifesaver for early morning meetings.",
    "Great stay, would definitely come back!",
    "Perfect getaway. Loved it!",
    "Excellent location, amazing host. Highly recommend!",
    "Clean, comfortable, and in a fantastic location. Everything a traveler needs.",
    "Great value for money. The amenities were exactly as described.",
    "Comfortable stay with all the basics covered. Would book again.",
    "Super cozy! Felt like home from the moment we walked in.",
    "Beautiful place overall. The only minor issue was the water pressure in the upstairs bathroom, but everything else was perfect. The host was quick to address our concern.",
    "Good stay overall but the road to the property was a bit rough. Once you arrive though, it's absolutely worth it. The property itself is gorgeous.",
    "Nice property but the wifi was unreliable during peak hours. Host was very responsive about it and offered a mobile hotspot as backup. The rest of the stay was fantastic.",
    "Beautiful location but a bit noisy on weekends due to nearby restaurants. Bring earplugs if you're a light sleeper! Otherwise the property itself is wonderful.",
    "The place was clean but slightly smaller than expected from the photos. That said, it was cozy and had everything we needed for a comfortable stay.",
    "Decent place for the price. The furniture could use some updates but the location makes up for it. The host was genuinely caring and helpful.",
    "Perfect base for exploring the local culture. The host recommended the best street food spots and hidden temples that aren't in any guidebook. An authentic experience!",
    "Host was friendly and helpful. The breakfast spread was delicious - fresh parathas, local jam, and chai made with spices from the garden. A truly Indian hospitality experience.",
    "The neighborhood is vibrant with great cafes and restaurants nearby. We loved walking to the local market every morning for fresh fruits. The host's recommendations were spot on.",
    "Thoughtful touches everywhere - from the welcome basket with local sweets to the curated guidebook with hand-drawn maps. You can tell the host genuinely loves sharing their home.",
    "We had a wonderful family vacation here. Kids loved the pool and garden. The host arranged a local cooking class for us which was the highlight of our trip.",
    "Great base for exploring the area. The host provided excellent recommendations for hiking trails, waterfalls, and local eateries. We discovered places we never would have found on our own.",
    "The pool was the highlight of our stay. Crystal clear, well-maintained, and surrounded by tropical plants. Felt like our own private resort.",
    "Excellent communication from the host. Check-in was seamless with detailed instructions. They even arranged an early check-in for us without any extra charge.",
    "Highly recommend for couples. Romantic setting with beautiful surroundings. The sunset from the terrace was unforgettable.",
    "The kitchen was well-stocked and we enjoyed cooking local recipes. The host left a recipe book of regional dishes which was such a thoughtful touch.",
]

# --- Unsplash Image IDs by property type ---

PROPERTY_IMAGES = {
    "Villa": [
        "1512917774080-9991f1c4c750",
        "1600585154340-be6161a56a0c",
        "1613977257363-707ba9348227",
        "1582268611958-ebfd161ef9cf",
        "1580587771525-78b9dba3b914",
    ],
    "Apartment": [
        "1560184897-ae75f418493e",
        "1522708323590-d24dbb6b0267",
        "1502672260266-1c1ef2d93688",
        "1560185127-6ed189bf02f4",
        "1484154218962-a197022b5858",
    ],
    "Condo": [
        "1560184897-ae75f418493e",
        "1522708323590-d24dbb6b0267",
        "1502672260266-1c1ef2d93688",
        "1560185127-6ed189bf02f4",
        "1484154218962-a197022b5858",
    ],
    "Home": [
        "1600210492493-0946911123ea",
        "1583608205776-bfd35f0d9f83",
        "1600585154526-990dced4db0d",
        "1600607687939-ce8a6c25118c",
        "1574362848149-11496d93a7c7",
    ],
    "Cottage": [
        "1600210492493-0946911123ea",
        "1583608205776-bfd35f0d9f83",
        "1600585154526-990dced4db0d",
        "1600607687939-ce8a6c25118c",
        "1574362848149-11496d93a7c7",
    ],
    "Bungalow": [
        "1600210492493-0946911123ea",
        "1583608205776-bfd35f0d9f83",
        "1600585154526-990dced4db0d",
        "1600607687939-ce8a6c25118c",
        "1574362848149-11496d93a7c7",
    ],
    "Hotel": [
        "1542314831-068cd1dbfeeb",
        "1571896349842-33c89424de2d",
        "1571896349842-33c89424de2d",
        "1520250497591-112f2f40a3f4",
        "1445019980597-93fa8acb246c",
    ],
    "Farmhouse": [
        "1449158743715-0a90ebb6d2d8",
        "1505691938895-1758d7feb511",
        "1510798831971-661eb04b3739",
        "1416331108676-a22ccb276e35",
        "1416331108676-a22ccb276e35",
    ],
    "Houseboat": [
        "1449158743715-0a90ebb6d2d8",
        "1505691938895-1758d7feb511",
        "1510798831971-661eb04b3739",
        "1416331108676-a22ccb276e35",
        "1416331108676-a22ccb276e35",
    ],
    "Treehouse": [
        "1449158743715-0a90ebb6d2d8",
        "1505691938895-1758d7feb511",
        "1510798831971-661eb04b3739",
        "1416331108676-a22ccb276e35",
        "1416331108676-a22ccb276e35",
    ],
    "Castle": [
        "1542314831-068cd1dbfeeb",
        "1571896349842-33c89424de2d",
        "1571896349842-33c89424de2d",
        "1520250497591-112f2f40a3f4",
        "1445019980597-93fa8acb246c",
    ],
    "Tent": [
        "1449158743715-0a90ebb6d2d8",
        "1505691938895-1758d7feb511",
        "1510798831971-661eb04b3739",
        "1416331108676-a22ccb276e35",
        "1416331108676-a22ccb276e35",
    ],
}

INTERIOR_IMAGES = [
    "1616486338812-3dadae4b4ace",
    "1560448204-e02f11c3d0e2",
    "1552321554-5fefe8c9ef14",
    "1600566753086-00f18fb6b3ea",
    "1618221195710-dd6b41faaea6",
]

DEFAULT_EXTERIOR_IMAGES = [
    "1600210492493-0946911123ea",
    "1583608205776-bfd35f0d9f83",
    "1542314831-068cd1dbfeeb",
    "1449158743715-0a90ebb6d2d8",
    "1512917774080-9991f1c4c750",
]


def _unsplash_url(photo_id: str) -> str:
    return f"https://images.unsplash.com/photo-{photo_id}?w=800&h=600&fit=crop"


def _get_listing_images(prop_type: str, listing_idx: int) -> list[str]:
    """Return 5 Unsplash URLs: 1 exterior + 4 interior, cycling through arrays."""
    exteriors = PROPERTY_IMAGES.get(prop_type, DEFAULT_EXTERIOR_IMAGES)
    ext_id = exteriors[listing_idx % len(exteriors)]
    urls = [_unsplash_url(ext_id)]
    for i in range(4):
        int_id = INTERIOR_IMAGES[(listing_idx + i) % len(INTERIOR_IMAGES)]
        urls.append(_unsplash_url(int_id))
    return urls


# --- CSV Parsing Helpers ---

def _parse_address(address: str):
    """Parse address like 'Candolim, Goa, India' into (city, state, country)."""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    elif len(parts) == 2:
        # e.g. "Goa, India" → city=Goa, state=Goa
        return parts[0], parts[0], parts[1]
    else:
        return parts[0], parts[0], "India"


def _parse_room_type(csv_room_type: str):
    """Map CSV roomType to (property_type, room_type)."""
    rt = csv_room_type.strip()
    rt_lower = rt.lower()

    # Entire place variants
    if rt_lower.startswith("entire villa"):
        return "Villa", "Entire place"
    if rt_lower.startswith("entire home") or rt_lower == "entire home/apt":
        return "Home", "Entire place"
    if rt_lower.startswith("entire cottage"):
        return "Cottage", "Entire place"
    if rt_lower.startswith("entire bungalow"):
        return "Bungalow", "Entire place"
    if rt_lower.startswith("entire condo"):
        return "Condo", "Entire place"
    if rt_lower.startswith("entire cabin"):
        return "Home", "Entire place"
    if rt_lower.startswith("entire chalet"):
        return "Home", "Entire place"
    if rt_lower.startswith("entire rental unit"):
        return "Apartment", "Entire place"
    if rt_lower.startswith("entire serviced apartment"):
        return "Apartment", "Entire place"
    if rt_lower.startswith("entire vacation home"):
        return "Home", "Entire place"
    if rt_lower == "entire place":
        return "Home", "Entire place"

    # Farm stay
    if rt_lower == "farm stay":
        return "Farmhouse", "Entire place"

    # Houseboat / Boat
    if rt_lower in ("houseboat", "boat"):
        return "Houseboat", "Entire place"

    # Treehouse
    if rt_lower == "treehouse":
        return "Treehouse", "Entire place"

    # Castle
    if rt_lower == "castle":
        return "Castle", "Entire place"

    # Tent / Campsite / Hut / Camper
    if rt_lower in ("tent", "campsite", "hut", "camper/rv"):
        return "Tent", "Entire place"

    # Room in hotel / boutique hotel / heritage hotel / resort / serviced apartment
    if rt_lower.startswith("room in"):
        return "Hotel", "Private room"

    # Private room in ...
    if rt_lower.startswith("private room in"):
        suffix = rt[len("Private room in "):].strip().lower()
        if "villa" in suffix:
            return "Villa", "Private room"
        if "resort" in suffix:
            return "Hotel", "Private room"
        if "bungalow" in suffix:
            return "Bungalow", "Private room"
        if "cottage" in suffix:
            return "Cottage", "Private room"
        if "castle" in suffix:
            return "Castle", "Private room"
        if "farm" in suffix:
            return "Farmhouse", "Private room"
        if "chalet" in suffix:
            return "Home", "Private room"
        if "condo" in suffix:
            return "Condo", "Private room"
        if "nature lodge" in suffix or "earthen" in suffix:
            return "Farmhouse", "Private room"
        if "bed and breakfast" in suffix:
            return "Hotel", "Private room"
        # default private room
        return "Home", "Private room"

    # Shared room
    if rt_lower.startswith("shared room"):
        return "Home", "Shared room"

    # Fallback
    return "Home", "Entire place"


def _load_csv_listings(csv_path: str, limit: int = 150) -> list[dict]:
    """Load and parse CSV, returning up to `limit` valid listings."""
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    valid = []
    for row in rows:
        # Skip rows with bad data
        name = (row.get("name") or "").strip()
        lat_str = (row.get("location/lat") or "").strip()
        lng_str = (row.get("location/lng") or "").strip()
        price_str = (row.get("pricing/rate/amount") or "").strip()
        guests_str = (row.get("numberOfGuests") or "").strip()
        address = (row.get("address") or "").strip()

        if not name or not lat_str or not lng_str or not price_str or not address:
            continue
        try:
            lat = float(lat_str)
            lng = float(lng_str)
            price_inr = float(price_str)
            num_guests = int(guests_str) if guests_str else 2
        except (ValueError, TypeError):
            continue

        if price_inr <= 0 or num_guests <= 0:
            continue

        city, state, country = _parse_address(address)
        property_type, room_type = _parse_room_type(row.get("roomType", ""))
        price_usd = round(price_inr / 83.0, 0)
        if price_usd < 1:
            price_usd = 1.0

        stars_str = (row.get("stars") or "").strip()
        stars = None
        if stars_str:
            try:
                stars = float(stars_str)
            except ValueError:
                pass

        is_superhost = (row.get("isHostedBySuperhost") or "").strip().lower() == "true"

        bedrooms = max(1, min(6, num_guests // 2))
        beds = max(1, bedrooms + random.randint(0, 1))
        bathrooms = max(1.0, round(bedrooms * 0.75))

        valid.append({
            "name": name,
            "lat": lat,
            "lng": lng,
            "price_usd": price_usd,
            "num_guests": min(num_guests, 16),
            "city": city,
            "state": state,
            "country": country,
            "property_type": property_type,
            "room_type": room_type,
            "stars": stars,
            "is_superhost": is_superhost,
            "bedrooms": bedrooms,
            "beds": beds,
            "bathrooms": bathrooms,
            "address": address,
        })

        if len(valid) >= limit:
            break

    return valid


def seed_database(db_path: str = None):
    """Seed the database with rich Airbnb data."""
    random.seed(42)

    if db_path:
        set_db_path(db_path)

    init_db()
    engine = get_engine()

    # Drop and recreate all tables to handle schema changes
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)

    now = datetime.utcnow()
    default_password_hash = hash_password("password")

    with Session(engine) as session:
        # --- Users ---
        print("Creating users...")
        users = []
        host_user_ids = []
        guest_user_ids = []

        for i, u_data in enumerate(USERS_DATA):
            user_id = i + 1
            # User 1 (default user) gets password "abc123", others get "password"
            pw_hash = hash_password("abc123") if user_id == 1 else default_password_hash
            identity_verified = random.random() < 0.6
            response_rates = [85, 88, 90, 92, 95, 97, 98, 99, 100]
            response_times = ["within an hour", "within an hour", "within a few hours", "within a day"]
            user = User(
                id=user_id,
                email=u_data["email"],
                name=u_data["name"],
                password_hash=pw_hash,
                avatar_url=f"https://i.pravatar.cc/150?u={u_data['email']}",
                phone=u_data["phone"],
                bio=u_data["bio"],
                preferred_currency="INR",
                preferred_language="en",
                is_superhost=u_data["is_superhost"],
                identity_verified=identity_verified,
                response_rate=random.choice(response_rates),
                response_time=random.choice(response_times),
                total_reviews_received=random.randint(0, 50) if u_data["is_host"] else 0,
                member_since=now - timedelta(days=random.randint(180, 1825)),
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            users.append(user)
            if u_data["is_host"]:
                host_user_ids.append(user_id)
            else:
                guest_user_ids.append(user_id)
        session.commit()

        # --- Currencies ---
        for c_data in CURRENCIES_DATA:
            session.add(Currency(**c_data))
        session.commit()

        # --- Categories ---
        category_map = {}
        for c_data in CATEGORIES_DATA:
            cat = Category(**c_data, created_at=now)
            session.add(cat)
            session.flush()
            category_map[cat.name] = cat.id
        session.commit()

        # --- Amenities ---
        amenity_map = {}
        amenity_ids = []
        for a_data in AMENITIES_DATA:
            amenity = Amenity(**a_data)
            session.add(amenity)
            session.flush()
            amenity_map[amenity.name] = amenity.id
            amenity_ids.append(amenity.id)
        session.commit()

        # --- Listings from Kaggle CSV ---
        csv_path = os.path.join(os.path.dirname(__file__), "data", "Airbnb_India_Top_500.csv")
        csv_listings = _load_csv_listings(csv_path, limit=150)
        num_listings = len(csv_listings)
        print(f"Creating {num_listings} listings from Kaggle CSV...")
        category_names = [c["name"] for c in CATEGORIES_DATA]
        all_amenity_names = [a["name"] for a in AMENITIES_DATA]
        listing_ids = []
        listing_city_map = {}  # listing_id -> city

        for idx, csv_row in enumerate(csv_listings):
            listing_id = idx + 1
            host_id = host_user_ids[(listing_id - 1) % len(host_user_ids)]
            prop_type = csv_row["property_type"]

            title = csv_row["name"]

            # Generate description
            desc_tpl = DESCRIPTION_TEMPLATES[(listing_id - 1) % len(DESCRIPTION_TEMPLATES)]
            features = random.sample(FEATURES, 2)
            audience = random.choice(AUDIENCES)
            description = desc_tpl.format(
                prop_type=prop_type.lower(), city=csv_row["city"],
                feature1=features[0], feature2=features[1], audience=audience,
            )

            price = csv_row["price_usd"]
            cleaning_fee = round(price * random.uniform(0.10, 0.15), 0)

            # Ratings from CSV stars
            stars = csv_row["stars"]
            if stars is not None:
                avg_rating = round(min(5.0, max(1.0, stars)), 2)
                review_count = random.randint(1, 50)
                is_guest_favourite = avg_rating >= 4.8
            else:
                avg_rating = round(random.uniform(3.5, 4.8), 2)
                review_count = 0
                is_guest_favourite = False

            listing = Listing(
                id=listing_id,
                host_id=host_id,
                title=title,
                description=description,
                property_type=prop_type,
                room_type=csv_row["room_type"],
                city=csv_row["city"],
                state=csv_row["state"],
                country=csv_row["country"],
                address=csv_row["address"],
                latitude=round(csv_row["lat"], 4),
                longitude=round(csv_row["lng"], 4),
                price_per_night=price,
                cleaning_fee=cleaning_fee,
                service_fee_percent=14.0,
                max_guests=csv_row["num_guests"],
                bedrooms=csv_row["bedrooms"],
                beds=csv_row["beds"],
                bathrooms=csv_row["bathrooms"],
                check_in_time=random.choice(["2:00 PM", "3:00 PM", "4:00 PM"]),
                check_out_time=random.choice(["10:00 AM", "11:00 AM", "12:00 PM"]),
                min_nights=random.choice([1, 1, 1, 2, 2, 3]),
                max_nights=random.choice([30, 90, 180, 365]),
                instant_book=random.random() > 0.3,
                cancellation_policy=random.choice(["flexible", "moderate", "strict"]),
                is_active=True,
                is_guest_favourite=is_guest_favourite,
                avg_rating=avg_rating,
                review_count=review_count,
                created_at=now - timedelta(days=random.randint(30, 365)),
                updated_at=now,
            )
            session.add(listing)
            listing_ids.append(listing_id)
            listing_city_map[listing_id] = csv_row["city"]

            # Images (5 per listing) — 1 exterior + 4 interior
            captions = ["Living space", "Bedroom", "Kitchen & dining", "Bathroom", "Outdoor view"]
            image_urls = _get_listing_images(prop_type, idx)
            for n in range(5):
                session.add(ListingImage(
                    listing_id=listing_id,
                    url=image_urls[n],
                    caption=captions[n],
                    sort_order=n,
                    created_at=now,
                ))

            # Random categories (1-3)
            num_cats = random.randint(1, 3)
            chosen_cats = random.sample(category_names, num_cats)
            for cat_name in chosen_cats:
                session.add(ListingCategory(
                    listing_id=listing_id,
                    category_id=category_map[cat_name],
                ))

            # Random amenities (12-20) — always include Wifi and Hot water
            num_amenities = random.randint(12, 20)
            chosen_amenities = ["Wifi", "Hot water"]
            remaining = [a for a in all_amenity_names if a not in chosen_amenities]
            chosen_amenities += random.sample(remaining, min(num_amenities - 2, len(remaining)))
            for am_name in chosen_amenities:
                session.add(ListingAmenity(
                    listing_id=listing_id,
                    amenity_id=amenity_map[am_name],
                ))

        session.commit()

        # --- Bookings ---
        # We need: 150 general bookings + 10 explicit bookings for user 1 as guest
        print("Creating bookings...")
        today = date.today()
        all_guest_ids = guest_user_ids + host_user_ids  # hosts can also be guests
        booking_id = 0
        booking_records = []  # track completed bookings for reviews
        user1_booking_records = []  # track user 1's completed bookings for reviews

        # --- User 1 explicit bookings (8-10) ---
        # User 1 (Priya Sharma, host_id=1) books listings by OTHER hosts
        user1_eligible_listings = [lid for lid in listing_ids
                                   if session.get(Listing, lid).host_id != 1]

        # Pick 10 distinct listings for user 1
        user1_listing_picks = random.sample(user1_eligible_listings, 10)

        user1_booking_specs = [
            # 5 completed (past)
            {"status": "completed", "days_ago": 150, "nights": 4},
            {"status": "completed", "days_ago": 120, "nights": 3},
            {"status": "completed", "days_ago": 90, "nights": 5},
            {"status": "completed", "days_ago": 60, "nights": 2},
            {"status": "completed", "days_ago": 30, "nights": 7},
            # 3 confirmed (upcoming)
            {"status": "confirmed", "days_ahead": 14, "nights": 3},
            {"status": "confirmed", "days_ahead": 30, "nights": 5},
            {"status": "confirmed", "days_ahead": 45, "nights": 4},
            # 2 cancelled
            {"status": "cancelled", "days_ago": 45, "nights": 3},
            {"status": "cancelled", "days_ago": 20, "nights": 2},
        ]

        for idx, spec in enumerate(user1_booking_specs):
            booking_id += 1
            lid = user1_listing_picks[idx]
            listing_obj = session.get(Listing, lid)

            if spec["status"] in ("completed", "cancelled"):
                check_in_date = today - timedelta(days=spec["days_ago"])
            else:
                check_in_date = today + timedelta(days=spec["days_ahead"])

            nights = spec["nights"]
            check_out_date = check_in_date + timedelta(days=nights)
            num_adults = random.randint(1, min(3, listing_obj.max_guests))
            num_children = random.randint(0, max(0, listing_obj.max_guests - num_adults - 1))
            num_guests = num_adults + num_children

            price = listing_obj.price_per_night
            cleaning = listing_obj.cleaning_fee
            service = round(price * nights * listing_obj.service_fee_percent / 100, 2)
            total = round(price * nights + cleaning + service, 2)

            booking = Booking(
                id=booking_id,
                listing_id=lid,
                guest_id=1,
                check_in=check_in_date,
                check_out=check_out_date,
                num_guests=num_guests,
                num_adults=num_adults,
                num_children=num_children,
                num_infants=random.choice([0, 0, 0, 1]),
                num_pets=0,
                price_per_night=price,
                cleaning_fee=cleaning,
                service_fee=service,
                total_price=total,
                currency="USD",
                status=spec["status"],
                created_at=datetime.combine(check_in_date - timedelta(days=random.randint(7, 30)), datetime.min.time()),
                updated_at=now,
            )
            session.add(booking)

            if spec["status"] == "completed":
                rec = {
                    "booking_id": booking_id,
                    "listing_id": lid,
                    "guest_id": 1,
                    "check_out": check_out_date,
                }
                booking_records.append(rec)
                user1_booking_records.append(rec)

        session.commit()

        # --- General bookings (140 more, total ~150) ---
        for _ in range(140):
            booking_id += 1
            lid = random.choice(listing_ids)
            listing_obj = session.get(Listing, lid)

            # Don't let host book their own listing
            possible_guests = [g for g in all_guest_ids if g != listing_obj.host_id]
            guest_id = random.choice(possible_guests)

            # Mix of past and future bookings
            roll = random.random()
            if roll < 0.60:
                check_in_date = today - timedelta(days=random.randint(7, 180))
                status = "completed"
            elif roll < 0.80:
                check_in_date = today + timedelta(days=random.randint(1, 60))
                status = "confirmed"
            elif roll < 0.90:
                check_in_date = today - timedelta(days=random.randint(0, 90))
                status = "cancelled"
            else:
                check_in_date = today + timedelta(days=random.randint(1, 30))
                status = "pending"

            nights = random.randint(1, 14)
            check_out_date = check_in_date + timedelta(days=nights)
            num_adults = random.randint(1, min(4, listing_obj.max_guests))
            num_children = random.randint(0, max(0, listing_obj.max_guests - num_adults))
            num_guests = num_adults + num_children

            price = listing_obj.price_per_night
            cleaning = listing_obj.cleaning_fee
            service = round(price * nights * listing_obj.service_fee_percent / 100, 2)
            total = round(price * nights + cleaning + service, 2)

            booking = Booking(
                id=booking_id,
                listing_id=lid,
                guest_id=guest_id,
                check_in=check_in_date,
                check_out=check_out_date,
                num_guests=num_guests,
                num_adults=num_adults,
                num_children=num_children,
                num_infants=random.choice([0, 0, 0, 0, 1]),
                num_pets=random.choice([0, 0, 0, 0, 0, 1]),
                price_per_night=price,
                cleaning_fee=cleaning,
                service_fee=service,
                total_price=total,
                currency="USD",
                status=status,
                created_at=datetime.combine(check_in_date - timedelta(days=random.randint(7, 30)), datetime.min.time()),
                updated_at=now,
            )
            session.add(booking)

            if status == "completed":
                booking_records.append({
                    "booking_id": booking_id,
                    "listing_id": lid,
                    "guest_id": guest_id,
                    "check_out": check_out_date,
                })

        session.commit()

        # --- Reviews (~200) ---
        print("Creating reviews...")
        review_id = 0
        listing_ratings = {}  # listing_id -> list of ratings

        # First: 6 reviews from User 1 for their completed bookings
        user1_review_comments = [
            "Absolutely stunning property! The views were breathtaking and we didn't want to leave. Already planning our next trip back!",
            "The villa was exactly as described. The pool was clean, kitchen fully equipped with all utensils, and the bedroom linens were hotel-quality. The host left us a welcome basket with local snacks and a handwritten note with restaurant recommendations.",
            "Perfect base for exploring the local culture. The host recommended the best street food spots and hidden temples that aren't in any guidebook. An authentic experience!",
            "Great stay, would definitely come back! The location was unbeatable.",
            "Beautiful place overall. The only minor issue was the water pressure in the upstairs bathroom, but everything else was perfect. The host was quick to address our concern.",
            "Thoughtful touches everywhere - from the welcome basket with local sweets to the curated guidebook with hand-drawn maps. You can tell the host genuinely loves sharing their home.",
        ]

        # Use the first 5 completed bookings (we have exactly 5) + pick one more if available
        user1_reviews_to_write = user1_booking_records[:6]
        for i, rec in enumerate(user1_reviews_to_write):
            review_id += 1
            overall = round(random.uniform(4.0, 5.0), 1)
            comment = user1_review_comments[i % len(user1_review_comments)]

            review = Review(
                id=review_id,
                listing_id=rec["listing_id"],
                booking_id=rec["booking_id"],
                reviewer_id=1,
                overall_rating=overall,
                cleanliness_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.3, 0.3), 1))),
                accuracy_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.2, 0.2), 1))),
                checkin_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.2, 0.3), 1))),
                communication_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.1, 0.2), 1))),
                location_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.1, 0.3), 1))),
                value_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.3, 0.2), 1))),
                comment=comment,
                created_at=datetime.combine(rec["check_out"] + timedelta(days=random.randint(1, 3)), datetime.min.time()),
                updated_at=now,
            )
            session.add(review)
            listing_ratings.setdefault(rec["listing_id"], []).append(overall)

        # Remaining reviews from other users (~194 more to reach 200)
        # Filter out user1 booking records already used for reviews
        user1_booking_ids_reviewed = {rec["booking_id"] for rec in user1_reviews_to_write}
        other_booking_records = [r for r in booking_records if r["booking_id"] not in user1_booking_ids_reviewed]

        review_sources = other_booking_records.copy()
        target_remaining = 200 - review_id
        while len(review_sources) < target_remaining:
            review_sources.append(random.choice(other_booking_records))

        random.shuffle(review_sources)
        for rec in review_sources[:target_remaining]:
            review_id += 1
            overall = round(random.uniform(3.5, 5.0), 1)
            comment = random.choice(REVIEW_COMMENTS)

            review = Review(
                id=review_id,
                listing_id=rec["listing_id"],
                booking_id=rec["booking_id"],
                reviewer_id=rec["guest_id"],
                overall_rating=overall,
                cleanliness_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.5, 0.5), 1))),
                accuracy_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.3, 0.3), 1))),
                checkin_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.3, 0.3), 1))),
                communication_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.2, 0.3), 1))),
                location_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.2, 0.4), 1))),
                value_rating=min(5.0, max(1.0, round(overall + random.uniform(-0.4, 0.3), 1))),
                comment=comment,
                created_at=datetime.combine(rec["check_out"] + timedelta(days=random.randint(1, 5)), datetime.min.time()),
                updated_at=now,
            )
            session.add(review)

            listing_ratings.setdefault(rec["listing_id"], []).append(overall)

        session.commit()

        # Update listing avg_rating, review_count, and is_guest_favourite
        print("Updating listing ratings...")
        for lid, ratings in listing_ratings.items():
            listing_obj = session.get(Listing, lid)
            listing_obj.avg_rating = round(sum(ratings) / len(ratings), 2)
            listing_obj.review_count = len(ratings)
            listing_obj.is_guest_favourite = listing_obj.avg_rating >= 4.8
            session.add(listing_obj)
        session.commit()

        # --- Wishlists ---
        print("Creating wishlists...")

        # Helper: find listings in cities/states matching a keyword, not hosted by user 1
        def _listings_in_cities(keyword: str, exclude_host: int = 1) -> list[int]:
            result = []
            kw = keyword.lower()
            for lid, city in listing_city_map.items():
                listing_obj = session.get(Listing, lid)
                if listing_obj.host_id == exclude_host:
                    continue
                if kw in city.lower() or kw in (listing_obj.state or "").lower() or kw in (listing_obj.address or "").lower():
                    result.append(lid)
            return result

        # --- User 1 wishlists (3 curated) ---
        goa_listings = _listings_in_cities("Goa")
        mountain_listings = (_listings_in_cities("Shimla") + _listings_in_cities("Manali")
                           + _listings_in_cities("Himachal") + _listings_in_cities("Ooty"))
        # Deduplicate
        mountain_listings = list(dict.fromkeys(mountain_listings))
        city_listings = (_listings_in_cities("Mumbai") + _listings_in_cities("Bangalore")
                        + _listings_in_cities("Delhi") + _listings_in_cities("Chennai")
                        + _listings_in_cities("Jaipur"))
        city_listings = list(dict.fromkeys(city_listings))

        user1_wishlists = [
            ("Goa Getaway", goa_listings, 5),
            ("Mountain Retreats", mountain_listings, 4),
            ("City Escapes", city_listings, 4),
        ]

        for wl_name, candidates, target_count in user1_wishlists:
            wl = Wishlist(
                user_id=1,
                name=wl_name,
                created_at=now - timedelta(days=random.randint(10, 90)),
                updated_at=now,
            )
            session.add(wl)
            session.flush()

            pick_count = min(target_count, len(candidates))
            chosen = random.sample(candidates, pick_count)
            for lid in chosen:
                session.add(WishlistItem(
                    wishlist_id=wl.id,
                    listing_id=lid,
                    created_at=now - timedelta(days=random.randint(1, 30)),
                ))

        # --- Other users' wishlists ---
        other_wishlist_config = [
            ("Dream Vacations", 8),
            ("Weekend Getaways", 9),
            ("Beach Favorites", 10),
            ("Bucket List", 11),
            ("Romantic Escapes", 12),
            ("Family Fun", 13),
        ]

        for wl_name, uid in other_wishlist_config:
            wl = Wishlist(
                user_id=uid,
                name=wl_name,
                created_at=now - timedelta(days=random.randint(5, 60)),
                updated_at=now,
            )
            session.add(wl)
            session.flush()

            num_items = random.randint(3, 6)
            chosen_listings = random.sample(listing_ids, num_items)
            for lid in chosen_listings:
                session.add(WishlistItem(
                    wishlist_id=wl.id,
                    listing_id=lid,
                    created_at=now - timedelta(days=random.randint(1, 30)),
                ))
        session.commit()

        # --- UserSettings ---
        print("Creating user settings...")
        currency_choices = ["INR", "USD", "EUR", "GBP", "AUD", "SGD"]
        for user in users:
            settings = UserSettings(
                user_id=user.id,
                preferred_currency=random.choice(currency_choices),
                preferred_language="en",
                notification_email=random.choice([True, True, True, False]),
                notification_push=random.choice([True, True, False]),
                theme=random.choice(["light", "light", "light", "dark"]),
                created_at=now,
                updated_at=now,
            )
            session.add(settings)
        session.commit()

        # --- Help Articles ---
        print("Creating help articles...")
        help_articles = [
            {"category": "booking", "title": "How do I book a listing?", "content": "Browse listings, select your dates and number of guests, then click 'Reserve'. You'll be asked to confirm your booking details and payment method before the reservation is finalised.", "sort_order": 1},
            {"category": "booking", "title": "Can I cancel my booking?", "content": "Yes, you can cancel a booking from the Trips page. The refund amount depends on the host's cancellation policy. Flexible policies allow free cancellation up to 24 hours before check-in, while strict policies may charge a fee.", "sort_order": 2},
            {"category": "booking", "title": "How do I modify my reservation dates?", "content": "Go to your Trips page, find the booking you want to modify, and click 'Change reservation'. You can adjust dates and guest count subject to availability. Price differences will be calculated automatically.", "sort_order": 3},
            {"category": "booking", "title": "What is Instant Book?", "content": "Instant Book allows you to book a listing immediately without waiting for host approval. Listings with the lightning bolt icon support Instant Book. This is great for last-minute travel plans.", "sort_order": 4},
            {"category": "account", "title": "How do I update my profile?", "content": "Go to Settings from the main menu. You can update your name, email, phone number, and profile photo. A complete profile helps hosts know who you are and builds trust in the community.", "sort_order": 1},
            {"category": "account", "title": "How do I verify my identity?", "content": "Identity verification can be completed by uploading a government-issued ID through your account settings. This helps build trust and may be required by some hosts before accepting your booking.", "sort_order": 2},
            {"category": "account", "title": "How do I reset my password?", "content": "Click 'Forgot password' on the login page. Enter your registered email address and we'll send you a password reset link. The link expires after 24 hours for security.", "sort_order": 3},
            {"category": "payments", "title": "What payment methods are accepted?", "content": "We accept major credit and debit cards (Visa, Mastercard, American Express), UPI, net banking, and wallet payments. Payment methods may vary by region.", "sort_order": 1},
            {"category": "payments", "title": "When am I charged for my booking?", "content": "For most bookings, you're charged at the time of booking confirmation. For long-term stays (28+ nights), you may be eligible for monthly payments where the first month is charged upfront.", "sort_order": 2},
            {"category": "payments", "title": "How do refunds work?", "content": "Refunds are processed according to the cancellation policy. Once initiated, refunds typically take 5-10 business days to appear in your account. The exact timing depends on your payment provider.", "sort_order": 3},
            {"category": "payments", "title": "Can I pay in a different currency?", "content": "Yes, you can change your preferred currency in Settings. Prices will be displayed in your chosen currency using current exchange rates. The actual charge may vary slightly due to exchange rate fluctuations.", "sort_order": 4},
            {"category": "safety", "title": "What safety features does Airbnb provide?", "content": "We provide verified profiles, secure messaging, 24/7 support, and our Host Guarantee programme. All bookings include AirCover which provides protection for guests against listing inaccuracies.", "sort_order": 1},
            {"category": "safety", "title": "How do I report a safety concern?", "content": "If you feel unsafe, contact our emergency support line available 24/7. You can also report concerns through the app by going to Help > Report a safety issue. We take all reports seriously.", "sort_order": 2},
            {"category": "safety", "title": "What is AirCover for guests?", "content": "AirCover provides booking protection including check-in guarantee, get-what-you-booked guarantee, and a 24-hour safety line. If a listing isn't as described, we'll help you find a similar place or give you a refund.", "sort_order": 3},
            {"category": "hosting", "title": "How do I become a host?", "content": "Click 'Become a host' from the menu and follow the step-by-step guide. You'll need to add photos, set a price, write a description, and configure your availability calendar.", "sort_order": 1},
            {"category": "hosting", "title": "How does Superhost status work?", "content": "Superhost status is awarded to hosts who maintain a 4.8+ overall rating, complete at least 10 stays per year, have less than 1% cancellation rate, and maintain a 90%+ response rate.", "sort_order": 2},
            {"category": "hosting", "title": "How do I set my pricing?", "content": "Set your nightly rate on your listing page. Consider your location, amenities, and competition. You can also use Smart Pricing which automatically adjusts your price based on demand and local events.", "sort_order": 3},
            {"category": "hosting", "title": "What fees does Airbnb charge hosts?", "content": "Airbnb charges a service fee of 3% per booking for most hosts. This covers payment processing, 24/7 support, and platform services. Some hosts opt for the split-fee model where guests also pay a portion.", "sort_order": 4},
        ]
        for article_data in help_articles:
            session.add(HelpArticle(**article_data, created_at=now))
        session.commit()

        # --- Sample Support Tickets for User 1 ---
        print("Creating sample support tickets...")
        sample_tickets = [
            {"user_id": 1, "subject": "Issue with check-in instructions", "description": "I couldn't find the lockbox code in the check-in instructions for my upcoming stay. The host hasn't responded to my messages.", "category": "booking", "status": "in_progress"},
            {"user_id": 1, "subject": "Refund not received", "description": "I cancelled my booking 5 days ago but haven't received the refund yet. Order #12345.", "category": "payments", "status": "open"},
            {"user_id": 1, "subject": "Profile photo upload failing", "description": "I've been trying to upload a new profile photo but it keeps failing with an error message. I've tried different image formats.", "category": "account", "status": "resolved"},
        ]
        for ticket_data in sample_tickets:
            session.add(SupportTicket(**ticket_data, created_at=now - timedelta(days=random.randint(1, 14)), updated_at=now))
        session.commit()

        # --- Search History for User 1 ---
        print("Creating search history...")
        search_entries = [
            {"user_id": 1, "query": "Goa", "filters": '{"check_in":"2025-12-20","check_out":"2025-12-27","guests":"4"}', "result_count": 24},
            {"user_id": 1, "query": "Manali", "filters": '{"check_in":"2025-11-15","check_out":"2025-11-20"}', "result_count": 18},
            {"user_id": 1, "query": "Mumbai", "filters": '{"property_type":"Apartment","guests":"2"}', "result_count": 32},
            {"user_id": 1, "query": "Jaipur", "filters": '{"check_in":"2026-01-10","check_out":"2026-01-14","guests":"6"}', "result_count": 15},
            {"user_id": 1, "query": "Udaipur", "filters": '{"property_type":"Villa"}', "result_count": 9},
            {"user_id": 1, "query": "Shimla", "filters": '{"check_in":"2025-12-25","check_out":"2025-12-31"}', "result_count": 21},
            {"user_id": 1, "query": "Kochi", "filters": '{"guests":"3"}', "result_count": 12},
            {"user_id": 1, "query": "Ooty", "filters": '{}', "result_count": 7},
        ]
        for j, entry_data in enumerate(search_entries):
            session.add(SearchHistory(**entry_data, created_at=now - timedelta(hours=j * 12 + random.randint(1, 10))))
        session.commit()

        # --- Message Threads and Messages ---
        print("Creating message threads and messages...")
        session.exec(delete(Message))
        session.exec(delete(MessageThread))
        session.commit()

        msg_templates = [
            "Hi! Is your place available for the dates I selected?",
            "Thanks for getting back to me! That sounds great.",
            "Could you tell me more about parking options?",
            "We're a group of friends looking for a relaxing getaway.",
            "What's the best way to reach the property from the airport?",
            "Is early check-in possible? Our flight arrives at 10 AM.",
            "Thank you so much! We really enjoyed our stay.",
            "The place was exactly as described, wonderful experience!",
            "Do you allow pets? We'd love to bring our dog.",
            "Is the pool heated? We're visiting in December.",
            "Can you recommend any local restaurants nearby?",
            "We had an amazing time, the views were stunning!",
            "Is there a grocery store within walking distance?",
            "The WiFi was great, perfect for working remotely.",
            "Would it be possible to arrange a late checkout?",
        ]

        thread_configs = [
            {"guest_id": 1, "host_id": 2, "listing_id": 1},
            {"guest_id": 1, "host_id": 3, "listing_id": 5},
            {"guest_id": 1, "host_id": 4, "listing_id": 10},
            {"guest_id": 8, "host_id": 2, "listing_id": 2},
            {"guest_id": 9, "host_id": 3, "listing_id": 6},
            {"guest_id": 10, "host_id": 4, "listing_id": 11},
            {"guest_id": 1, "host_id": 5, "listing_id": 15},
            {"guest_id": 11, "host_id": 2, "listing_id": 3},
            {"guest_id": 1, "host_id": 6, "listing_id": 20},
            {"guest_id": 12, "host_id": 3, "listing_id": 7},
            {"guest_id": 1, "host_id": 2, "listing_id": 4},
            {"guest_id": 13, "host_id": 5, "listing_id": 16},
        ]

        msg_id = 0
        for t_idx, tc in enumerate(thread_configs):
            listing_obj = session.get(Listing, tc["listing_id"])
            subject = f"About: {listing_obj.title}" if listing_obj else ""
            num_msgs = random.randint(3, 8)
            last_msg_time = now - timedelta(hours=random.randint(1, 168))

            thread = MessageThread(
                id=t_idx + 1,
                listing_id=tc["listing_id"],
                guest_id=tc["guest_id"],
                host_id=tc["host_id"],
                subject=subject,
                last_message_at=last_msg_time,
                created_at=last_msg_time - timedelta(hours=num_msgs * 2),
            )
            session.add(thread)
            session.commit()

            for m in range(num_msgs):
                msg_id += 1
                sender_id = tc["guest_id"] if m % 2 == 0 else tc["host_id"]
                msg_time = last_msg_time - timedelta(hours=(num_msgs - m) * 2)
                is_read = m < num_msgs - 1 or sender_id == 1
                session.add(Message(
                    id=msg_id,
                    thread_id=t_idx + 1,
                    sender_id=sender_id,
                    content=random.choice(msg_templates),
                    is_read=is_read,
                    created_at=msg_time,
                ))
            session.commit()

        # --- Notifications ---
        print("Creating notifications...")
        session.exec(delete(Notification))
        session.commit()

        notif_data = [
            {"type": "booking_confirmed", "title": "Booking confirmed", "body": "Your booking at Beach Villa in Goa has been confirmed!", "link": "/trips", "is_read": True},
            {"type": "message_received", "title": "New message", "body": "Priya Sharma: Thanks for your booking! Looking forward to hosting you.", "link": "/messages", "is_read": True},
            {"type": "review_posted", "title": "New review", "body": "A guest left a 5-star review on your Beach Villa listing", "link": "/listings/1", "is_read": True},
            {"type": "booking_requested", "title": "Booking request", "body": "You have a new booking request for Cozy Apartment in Mumbai", "link": "/trips", "is_read": False},
            {"type": "message_received", "title": "New message", "body": "Raj Patel: Is the pool area available for private events?", "link": "/messages", "is_read": False},
            {"type": "booking_confirmed", "title": "Booking confirmed", "body": "Your stay at Mountain Retreat in Manali is confirmed for next month!", "link": "/trips", "is_read": True},
            {"type": "booking_cancelled", "title": "Booking cancelled", "body": "A guest cancelled their booking for your property in Jaipur", "link": "/trips", "is_read": False},
            {"type": "review_posted", "title": "New review", "body": "Someone left a review on your Penthouse listing", "link": "/listings/10", "is_read": False},
            {"type": "message_received", "title": "New message", "body": "Sophie: Do you provide airport pickup service?", "link": "/messages", "is_read": False},
            {"type": "booking_confirmed", "title": "Booking confirmed", "body": "Your weekend getaway in Kerala is all set!", "link": "/trips", "is_read": True},
            {"type": "message_received", "title": "New message", "body": "Amit: Thank you! We had a wonderful time at your place.", "link": "/messages", "is_read": True},
            {"type": "booking_requested", "title": "Booking request", "body": "New request for your Treehouse in Wayanad", "link": "/trips", "is_read": False},
            {"type": "review_posted", "title": "New review", "body": "4.8 star review on your Farmhouse listing", "link": "/listings/20", "is_read": False},
            {"type": "booking_cancelled", "title": "Booking cancelled", "body": "Your booking in Shimla was cancelled by the guest", "link": "/trips", "is_read": True},
            {"type": "message_received", "title": "New message", "body": "Anjali: Can we check in an hour early?", "link": "/messages", "is_read": False},
            {"type": "booking_confirmed", "title": "Booking confirmed", "body": "Your Goa trip is booked and confirmed!", "link": "/trips", "is_read": True},
            {"type": "message_received", "title": "New message", "body": "Vikram: The caretaker will meet you at the gate.", "link": "/messages", "is_read": False},
            {"type": "review_posted", "title": "New review", "body": "A guest loved your Cottage in Ooty - 5 stars!", "link": "/listings/15", "is_read": False},
        ]

        for n_idx, nd in enumerate(notif_data):
            session.add(Notification(
                id=n_idx + 1,
                user_id=1,
                type=nd["type"],
                title=nd["title"],
                body=nd["body"],
                link=nd["link"],
                is_read=nd["is_read"],
                created_at=now - timedelta(hours=n_idx * 6 + random.randint(0, 5)),
            ))
        session.commit()

        # --- Guest eligibility requirements on ~20% / ~10% of listings ---
        all_listings = session.exec(select(Listing)).all()
        for i, lst in enumerate(all_listings):
            if i % 5 == 0:  # ~20%
                lst.require_profile_photo = True
            if i % 10 == 0:  # ~10%
                lst.require_identity_verified = True
            session.add(lst)
        session.commit()

        # --- Sample reservation shares for user 1 ---
        import secrets as _secrets
        user1_bookings = session.exec(
            select(Booking).where(Booking.guest_id == 1)
        ).all()
        for sb_idx, sb in enumerate(user1_bookings[:4]):
            share = ReservationShare(
                booking_id=sb.id,
                shared_by_user_id=1,
                shared_with_email=f"friend{sb_idx + 1}@example.com",
                share_token=_secrets.token_urlsafe(16),
            )
            session.add(share)
        session.commit()

        # --- Generate confirmation codes for all bookings ---
        import string as _string
        all_bookings = session.exec(select(Booking)).all()
        for bk in all_bookings:
            if not bk.confirmation_code:
                bk.confirmation_code = "AIRBNB-" + ''.join(
                    _secrets.choice(_string.ascii_uppercase + _string.digits) for _ in range(6)
                )
                session.add(bk)
        session.commit()

        # --- Blocked dates across various listings ---
        blocked_date_data = [
            (1, date.today() + timedelta(days=20), "maintenance"),
            (1, date.today() + timedelta(days=21), "maintenance"),
            (1, date.today() + timedelta(days=22), "maintenance"),
            (2, date.today() + timedelta(days=15), "personal"),
            (2, date.today() + timedelta(days=16), "personal"),
            (3, date.today() + timedelta(days=25), "unavailable"),
            (3, date.today() + timedelta(days=26), "unavailable"),
            (4, date.today() + timedelta(days=30), "maintenance"),
            (4, date.today() + timedelta(days=31), "maintenance"),
            (5, date.today() + timedelta(days=10), "personal"),
            (5, date.today() + timedelta(days=11), "personal"),
            (6, date.today() + timedelta(days=18), "unavailable"),
            (7, date.today() + timedelta(days=35), "maintenance"),
            (7, date.today() + timedelta(days=36), "maintenance"),
            (8, date.today() + timedelta(days=12), "personal"),
            (10, date.today() + timedelta(days=40), "unavailable"),
            (10, date.today() + timedelta(days=41), "unavailable"),
            (12, date.today() + timedelta(days=50), "maintenance"),
            (15, date.today() + timedelta(days=28), "personal"),
            (15, date.today() + timedelta(days=29), "personal"),
            (20, date.today() + timedelta(days=14), "unavailable"),
            (20, date.today() + timedelta(days=15), "unavailable"),
            (25, date.today() + timedelta(days=45), "maintenance"),
            (30, date.today() + timedelta(days=33), "personal"),
            (35, date.today() + timedelta(days=22), "unavailable"),
            (40, date.today() + timedelta(days=27), "maintenance"),
            (45, date.today() + timedelta(days=19), "personal"),
            (50, date.today() + timedelta(days=38), "unavailable"),
            (55, date.today() + timedelta(days=42), "maintenance"),
            (60, date.today() + timedelta(days=55), "personal"),
        ]
        for lid, bd_date, reason in blocked_date_data:
            session.add(BlockedDate(listing_id=lid, date=bd_date, reason=reason))
        session.commit()

    # Summary
    total_wishlists = len(user1_wishlists) + len(other_wishlist_config)
    print(f"  Users: {len(USERS_DATA)} ({len(host_user_ids)} hosts, {len(guest_user_ids)} guests)")
    print(f"  Listings: {num_listings}")
    print(f"  Bookings: {booking_id}")
    print(f"  Reviews: {review_id}")
    print(f"  Wishlists: {total_wishlists} (3 for User 1, {len(other_wishlist_config)} for others)")
    print(f"  Categories: {len(CATEGORIES_DATA)}, Amenities: {len(AMENITIES_DATA)}")
    print(f"  Currencies: {len(CURRENCIES_DATA)}")
    print(f"\nUser 1 (Priya Sharma) data:")
    print(f"  Bookings as guest: 10 (5 completed, 3 confirmed, 2 cancelled)")
    print(f"  Reviews written: {len(user1_reviews_to_write)}")
    print(f"  Wishlists: Goa Getaway, Mountain Retreats, City Escapes")
    print(f"\nUser accounts:")
    print(f"  Default user: abc@gmail.com / abc123")
    print(f"  {'ID':<4} {'Email':35} {'Name':20} {'Type'}")
    print("-" * 75)
    for i, u in enumerate(USERS_DATA):
        utype = "Host (Superhost)" if u["is_superhost"] else ("Host" if u["is_host"] else "Guest")
        print(f"  {i+1:<4} {u['email']:35} {u['name']:20} {utype}")


def main():
    parser = argparse.ArgumentParser(description="Seed Airbnb database with rich company data")
    parser.add_argument("--db", default="./airbnb_company.db", help="Database file path")
    args = parser.parse_args()

    print(f"Seeding database: {args.db}")
    seed_database(args.db)


if __name__ == "__main__":
    main()
