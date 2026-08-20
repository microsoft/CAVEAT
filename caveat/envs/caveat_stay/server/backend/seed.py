"""Seed database with real Boston CAVEAT-Stay data from InsideCaveatStay CSV files."""

import argparse
import csv
import hashlib
import html as html_mod
import json
import os
import re
import random
import secrets
from datetime import datetime, timedelta, date

from sqlmodel import Session, delete, select

from backend.database import get_engine, init_db, set_db_path
from backend.models import (
    User, Listing, ListingImage, Category, ListingCategory,
    Amenity, ListingAmenity, Booking, Review,
    Wishlist, WishlistItem, Currency, UserSettings,
    Neighbourhood, HelpArticle, SupportTicket, SearchHistory,
    MessageThread, Message, Notification, BlockedDate, ReservationShare,
)


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')

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
    # Bathroom
    {"name": "Hot water", "category": "Bathroom", "icon": "\U0001f6bf", "sort_order": 1},
    {"name": "Shampoo", "category": "Bathroom", "icon": "\U0001f9f4", "sort_order": 2},
    {"name": "Hair dryer", "category": "Bathroom", "icon": "\U0001f487", "sort_order": 3},
    # Bedroom and laundry
    {"name": "Washer", "category": "Bedroom and laundry", "icon": "\U0001f9fa", "sort_order": 4},
    {"name": "Dryer", "category": "Bedroom and laundry", "icon": "\U0001f300", "sort_order": 5},
    {"name": "Iron", "category": "Bedroom and laundry", "icon": "\U0001f454", "sort_order": 6},
    {"name": "Hangers", "category": "Bedroom and laundry", "icon": "\U0001fa9d", "sort_order": 7},
    {"name": "Bed linens", "category": "Bedroom and laundry", "icon": "\U0001f6cf\ufe0f", "sort_order": 8},
    # Entertainment
    {"name": "TV", "category": "Entertainment", "icon": "\U0001f4fa", "sort_order": 9},
    {"name": "Books", "category": "Entertainment", "icon": "\U0001f4da", "sort_order": 10},
    {"name": "Board games", "category": "Entertainment", "icon": "\U0001f3b2", "sort_order": 11},
    # Kitchen and dining
    {"name": "Kitchen", "category": "Kitchen and dining", "icon": "\U0001f373", "sort_order": 12},
    {"name": "Refrigerator", "category": "Kitchen and dining", "icon": "\U0001f9ca", "sort_order": 13},
    {"name": "Microwave", "category": "Kitchen and dining", "icon": "\U0001f4e1", "sort_order": 14},
    {"name": "Oven", "category": "Kitchen and dining", "icon": "\U0001f525", "sort_order": 15},
    {"name": "Dishes", "category": "Kitchen and dining", "icon": "\U0001f37d\ufe0f", "sort_order": 16},
    {"name": "Coffee maker", "category": "Kitchen and dining", "icon": "\u2615", "sort_order": 17},
    # Internet and office
    {"name": "Wifi", "category": "Internet and office", "icon": "\U0001f4f6", "sort_order": 18},
    {"name": "Dedicated workspace", "category": "Internet and office", "icon": "\U0001f4bb", "sort_order": 19},
    # Outdoor
    {"name": "Pool", "category": "Outdoor", "icon": "\U0001f3ca", "sort_order": 20},
    {"name": "BBQ grill", "category": "Outdoor", "icon": "\U0001f356", "sort_order": 21},
    {"name": "Garden", "category": "Outdoor", "icon": "\U0001f33f", "sort_order": 22},
    {"name": "Patio", "category": "Outdoor", "icon": "\U0001fa91", "sort_order": 23},
    # Parking
    {"name": "Free parking", "category": "Parking", "icon": "\U0001f17f\ufe0f", "sort_order": 24},
    {"name": "Garage", "category": "Parking", "icon": "\U0001f3d7\ufe0f", "sort_order": 25},
    # Home safety
    {"name": "Smoke alarm", "category": "Home safety", "icon": "\U0001f6a8", "sort_order": 26},
    {"name": "Fire extinguisher", "category": "Home safety", "icon": "\U0001f9ef", "sort_order": 27},
    {"name": "First aid kit", "category": "Home safety", "icon": "\U0001fa79", "sort_order": 28},
    # Heating and cooling
    {"name": "Air conditioning", "category": "Heating and cooling", "icon": "\u2744\ufe0f", "sort_order": 29},
    {"name": "Heating", "category": "Heating and cooling", "icon": "\U0001f525", "sort_order": 30},
    {"name": "Ceiling fan", "category": "Heating and cooling", "icon": "\U0001f300", "sort_order": 31},
    # Family
    {"name": "Crib", "category": "Family", "icon": "\U0001f476", "sort_order": 32},
    {"name": "High chair", "category": "Family", "icon": "\U0001fa91", "sort_order": 33},
    # Location features
    {"name": "Beach access", "category": "Location features", "icon": "\U0001f3d6\ufe0f", "sort_order": 34},
    {"name": "Waterfront", "category": "Location features", "icon": "\U0001f30a", "sort_order": 35},
    # Services
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

HELP_ARTICLES_DATA = [
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
    {"category": "safety", "title": "What safety features does CAVEAT-Stay provide?", "content": "We provide verified profiles, secure messaging, 24/7 support, and our Host Guarantee programme. All bookings include AirCover which provides protection for guests against listing inaccuracies.", "sort_order": 1},
    {"category": "safety", "title": "How do I report a safety concern?", "content": "If you feel unsafe, contact our emergency support line available 24/7. You can also report concerns through the app by going to Help > Report a safety issue. We take all reports seriously.", "sort_order": 2},
    {"category": "safety", "title": "What is AirCover for guests?", "content": "AirCover provides booking protection including check-in guarantee, get-what-you-booked guarantee, and a 24-hour safety line. If a listing isn't as described, we'll help you find a similar place or give you a refund.", "sort_order": 3},
    {"category": "hosting", "title": "How do I become a host?", "content": "Click 'Become a host' from the menu and follow the step-by-step guide. You'll need to add photos, set a price, write a description, and configure your availability calendar.", "sort_order": 1},
    {"category": "hosting", "title": "How does Superhost status work?", "content": "Superhost status is awarded to hosts who maintain a 4.8+ overall rating, complete at least 10 stays per year, have less than 1% cancellation rate, and maintain a 90%+ response rate.", "sort_order": 2},
    {"category": "hosting", "title": "How do I set my pricing?", "content": "Set your nightly rate on your listing page. Consider your location, amenities, and competition. You can also use Smart Pricing which automatically adjusts your price based on demand and local events.", "sort_order": 3},
    {"category": "hosting", "title": "What fees does CAVEAT-Stay charge hosts?", "content": "CAVEAT-Stay charges a service fee of 3% per booking for most hosts. This covers payment processing, 24/7 support, and platform services. Some hosts opt for the split-fee model where guests also pay a portion.", "sort_order": 4},
]

UNSPLASH_INTERIOR_IDS = [
    "photo-1502672260266-1c1ef2d93688",
    "photo-1560448204-e02f11c3d0e2",
    "photo-1522708323590-d24dbb6b0267",
    "photo-1554995207-c18c203602cb",
    "photo-1600596542815-ffad4c1539a9",
    "photo-1600585154340-be6161a56a0c",
    "photo-1600566753190-17f0baa2a6c3",
    "photo-1600573472591-ee6b68d14c68",
    "photo-1512917774080-9991f1c4c750",
    "photo-1558618666-fcd25c85f82e",
    "photo-1586105251261-72a756497a11",
    "photo-1585128903994-9788e9a4489f",
]

BASE_PRICES = {
    "Entire home/apt": (120, 350),
    "Private room": (50, 150),
    "Shared room": (30, 80),
    "Hotel room": (100, 300),
}

NEIGHBOURHOOD_MULTIPLIERS = {
    "Back Bay": 1.4,
    "Beacon Hill": 1.3,
    "Downtown": 1.3,
    "South End": 1.2,
    "South Boston Waterfront": 1.25,
    "North End": 1.2,
    "Fenway": 1.1,
    "Bay Village": 1.15,
}

VIEW_NEIGHBOURHOODS = {"Back Bay", "Beacon Hill", "South Boston Waterfront"}


def _safe_int(val, default=0):
    if not val or val.strip() == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _safe_float(val, default=0.0):
    if not val or val.strip() == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _clean_text(text):
    """Strip HTML tags, unescape entities, normalize whitespace."""
    if not text:
        return text
    text = html_mod.unescape(text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def _parse_bathrooms(text):
    if not text or text.strip() == "":
        return 1.0
    t = text.lower().strip()
    if "half" in t:
        return 0.5
    m = re.search(r'([\d.]+)', t)
    if m:
        return float(m.group(1))
    return 1.0


def _parse_response_rate(val):
    if not val or val.strip() == "":
        return 100
    m = re.search(r'(\d+)', val)
    return int(m.group(1)) if m else 100


def _parse_amenities_json(raw):
    if not raw or raw.strip() == "":
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _generate_price(room_type, neighbourhood, bedrooms, max_guests):
    base_low, base_high = BASE_PRICES.get(room_type, (80, 200))
    mult = NEIGHBOURHOOD_MULTIPLIERS.get(neighbourhood, 1.0)
    price = random.uniform(base_low, base_high) * mult
    price += max(0, bedrooms - 1) * 40 + max(0, max_guests - 2) * 15
    return round(price, 2)


def _read_csv(filename):
    path = os.path.join(DATA_DIR, filename)
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def seed_database(db_path: str = None):
    """Seed the database with real Boston CAVEAT-Stay data from InsideCaveatStay CSVs."""
    if db_path:
        set_db_path(db_path)

    init_db()
    engine = get_engine()
    random.seed(42)
    now = datetime.utcnow()
    today = date.today()
    pwd_hash = hash_password("password123")

    with Session(engine) as session:
        # ── Clear all tables ──
        for model in [
            ReservationShare, Notification, Message, MessageThread,
            SearchHistory, SupportTicket, HelpArticle,
            BlockedDate, WishlistItem, Wishlist,
            Review, Booking,
            ListingAmenity, ListingCategory, ListingImage,
            Listing, Neighbourhood,
            Amenity, Category, Currency, UserSettings, User,
        ]:
            try:
                session.exec(delete(model))
            except Exception:
                pass
        session.commit()

        # ── Currencies ──
        for c in CURRENCIES_DATA:
            session.add(Currency(**c))
        session.commit()
        print(f"Seeded {len(CURRENCIES_DATA)} currencies")

        # ── Categories ──
        category_map = {}
        for c in CATEGORIES_DATA:
            cat = Category(**c, created_at=now)
            session.add(cat)
            session.flush()
            category_map[cat.name] = cat.id
        session.commit()
        print(f"Seeded {len(CATEGORIES_DATA)} categories")

        # ── Amenities ──
        amenity_map = {}
        amenity_names_lower = {}
        for a in AMENITIES_DATA:
            am = Amenity(**a)
            session.add(am)
            session.flush()
            amenity_map[am.name] = am.id
            amenity_names_lower[am.name.lower()] = am.id
        session.commit()
        print(f"Seeded {len(AMENITIES_DATA)} amenities")

        # ── Load CSV data ──
        print("Loading CSV files...")
        listings_rows = _read_csv('listings_detailed.csv')
        reviews_rows = _read_csv('reviews_detailed.csv')
        neighbourhoods_rows = _read_csv('neighbourhoods.csv')
        print(f"  listings_detailed.csv: {len(listings_rows)} rows")
        print(f"  reviews_detailed.csv: {len(reviews_rows)} rows")
        print(f"  neighbourhoods.csv: {len(neighbourhoods_rows)} rows")

        # ── Neighbourhoods ──
        neighbourhood_name_to_id = {}
        neighbourhood_id_counter = 1
        for row in neighbourhoods_rows:
            name = row.get('neighbourhood', '').strip()
            if not name:
                continue
            nb = Neighbourhood(
                id=neighbourhood_id_counter,
                name=name,
                city="Boston",
                state="Massachusetts",
            )
            session.add(nb)
            neighbourhood_name_to_id[name] = neighbourhood_id_counter
            neighbourhood_id_counter += 1
        session.commit()
        print(f"Seeded {len(neighbourhood_name_to_id)} neighbourhoods")

        # ── Guest user (id=1) ──
        guest_user = User(
            id=1,
            email="guest@caveat_stay.com",
            name="Alex Johnson",
            password_hash=pwd_hash,
            avatar_url="https://i.pravatar.cc/150?u=guest@caveat_stay.com",
            phone="+1-555-100-2000",
            bio="Love exploring new places and meeting people. Always looking for unique stays!",
            preferred_currency="USD",
            is_superhost=False,
            member_since=now - timedelta(days=random.randint(365, 1825)),
            created_at=now,
            updated_at=now,
        )
        session.add(guest_user)
        session.flush()

        # ── Host users from CSV ──
        print("Extracting host users...")
        hosts_seen = {}
        host_rows_data = []
        for row in listings_rows:
            hid = row.get('host_id', '').strip()
            if not hid or hid in hosts_seen:
                continue
            hosts_seen[hid] = True
            host_rows_data.append(row)

        csv_host_id_to_db_id = {}
        next_user_id = 2
        for row in host_rows_data:
            hid = row['host_id'].strip()
            host_name = row.get('host_name', '').strip() or f"Host {hid}"
            host_about = row.get('host_about', '').strip() or ""
            host_pic = row.get('host_picture_url', '').strip() or f"https://i.pravatar.cc/150?u=host_{hid}"
            is_superhost = row.get('host_is_superhost', '').strip().lower() == 't'
            id_verified = row.get('host_identity_verified', '').strip().lower() == 't'
            resp_time = row.get('host_response_time', '').strip() or "within an hour"
            resp_rate = _parse_response_rate(row.get('host_response_rate', ''))

            user = User(
                id=next_user_id,
                email=f"host_{hid}@caveat_stay.com",
                name=host_name,
                password_hash=pwd_hash,
                avatar_url=host_pic,
                bio=_clean_text(host_about[:500]) if host_about else "",
                preferred_currency="USD",
                is_superhost=is_superhost,
                identity_verified=id_verified,
                response_time=resp_time,
                response_rate=resp_rate,
                member_since=now - timedelta(days=random.randint(365, 2500)),
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            csv_host_id_to_db_id[hid] = next_user_id
            next_user_id += 1

        session.commit()
        num_hosts = len(csv_host_id_to_db_id)
        print(f"Seeded {num_hosts} host users (IDs 2–{next_user_id - 1})")

        # ── Listings ──
        print(f"Seeding {len(listings_rows)} listings...")
        csv_listing_id_to_db_id = {}
        listing_neighbourhood_map = {}
        listing_amenities_raw = {}
        listing_scores = {}
        listing_db_objects = []
        listing_id_counter = 1
        img_id_counter = 1
        unsplash_idx = 0
        captions = ["Interior", "Living space", "Bedroom view", "Details"]

        for row in listings_rows:
            csv_lid = row.get('id', '').strip()
            csv_hid = row.get('host_id', '').strip()
            if not csv_lid or csv_hid not in csv_host_id_to_db_id:
                continue

            db_host_id = csv_host_id_to_db_id[csv_hid]
            neighbourhood = row.get('neighbourhood_cleansed', '').strip()
            nb_id = neighbourhood_name_to_id.get(neighbourhood)
            room_type = row.get('room_type', '').strip() or "Entire home/apt"
            property_type = row.get('property_type', '').strip() or "Apartment"
            bedrooms = max(1, _safe_int(row.get('bedrooms', ''), 1))
            beds = max(1, _safe_int(row.get('beds', ''), 1))
            max_guests = max(1, _safe_int(row.get('accommodates', ''), 2))
            bathrooms = _parse_bathrooms(row.get('bathrooms_text', ''))
            min_nights = max(1, _safe_int(row.get('minimum_nights', ''), 1))
            max_nights = max(min_nights, _safe_int(row.get('maximum_nights', ''), 365))
            instant_book = row.get('instant_bookable', '').strip().lower() == 't'
            if not row.get('instant_bookable', '').strip():
                instant_book = random.random() < 0.6  # 60% instant book when data missing
            avg_rating = _safe_float(row.get('review_scores_rating', ''), 0.0)
            review_count = _safe_int(row.get('number_of_reviews', ''), 0)
            lat = _safe_float(row.get('latitude', ''), 42.36)
            lon = _safe_float(row.get('longitude', ''), -71.06)
            title = (row.get('name', '').strip() or f"Listing in {neighbourhood}")[:200]
            description = _clean_text(row.get('description', '').strip() or "")
            picture_url = row.get('picture_url', '').strip() or ""

            price = _generate_price(room_type, neighbourhood, bedrooms, max_guests)
            cleaning_fee = round(price * 0.12, 2)
            is_fav = avg_rating >= 4.8 and review_count >= 10
            cancel_policy = random.choice(["flexible", "moderate", "strict"])

            listing = Listing(
                id=listing_id_counter,
                host_id=db_host_id,
                neighbourhood_id=nb_id,
                title=title,
                description=description[:2000],
                property_type=property_type,
                room_type=room_type,
                city=neighbourhood,
                state="Massachusetts",
                country="US",
                address=f"{neighbourhood}, Boston, MA",
                latitude=lat,
                longitude=lon,
                price_per_night=price,
                cleaning_fee=cleaning_fee,
                service_fee_percent=14.0,
                max_guests=max_guests,
                bedrooms=bedrooms,
                beds=beds,
                bathrooms=bathrooms,
                min_nights=min_nights,
                max_nights=max_nights,
                instant_book=instant_book,
                cancellation_policy=cancel_policy,
                is_active=True,
                is_guest_favourite=is_fav,
                avg_rating=avg_rating,
                review_count=review_count,
                created_at=now - timedelta(days=random.randint(30, 730)),
                updated_at=now,
            )
            session.add(listing)
            listing_db_objects.append(listing)
            csv_listing_id_to_db_id[csv_lid] = listing_id_counter
            listing_neighbourhood_map[listing_id_counter] = neighbourhood

            # Store scores and amenities for later
            listing_scores[listing_id_counter] = {
                'rating': avg_rating,
                'accuracy': _safe_float(row.get('review_scores_accuracy', ''), avg_rating),
                'cleanliness': _safe_float(row.get('review_scores_cleanliness', ''), avg_rating),
                'checkin': _safe_float(row.get('review_scores_checkin', ''), avg_rating),
                'communication': _safe_float(row.get('review_scores_communication', ''), avg_rating),
                'location': _safe_float(row.get('review_scores_location', ''), avg_rating),
                'value': _safe_float(row.get('review_scores_value', ''), avg_rating),
            }

            raw_amenities = _parse_amenities_json(row.get('amenities', ''))
            listing_amenities_raw[listing_id_counter] = raw_amenities

            # ── Images ──
            if picture_url:
                session.add(ListingImage(
                    id=img_id_counter, listing_id=listing_id_counter,
                    url=picture_url, caption="Main photo", sort_order=0, created_at=now,
                ))
                img_id_counter += 1

            for extra in range(4):
                uid = UNSPLASH_INTERIOR_IDS[unsplash_idx % len(UNSPLASH_INTERIOR_IDS)]
                url = f"https://images.unsplash.com/{uid}?w=800&h=600&fit=crop"
                session.add(ListingImage(
                    id=img_id_counter, listing_id=listing_id_counter,
                    url=url, caption=captions[extra], sort_order=extra + 1, created_at=now,
                ))
                img_id_counter += 1
                unsplash_idx += 1

            listing_id_counter += 1

        session.commit()
        total_listings = listing_id_counter - 1
        print(f"Seeded {total_listings} listings with images")

        # ── ListingAmenity mappings ──
        print("Mapping listing amenities...")
        la_count = 0
        for db_lid, raw_list in listing_amenities_raw.items():
            matched = set()
            for csv_am in raw_list:
                csv_lower = csv_am.lower().strip()
                for am_name, am_id in amenity_names_lower.items():
                    if am_name in csv_lower or csv_lower in am_name:
                        if am_id not in matched:
                            session.add(ListingAmenity(listing_id=db_lid, amenity_id=am_id))
                            matched.add(am_id)
                            la_count += 1
        session.commit()
        print(f"Seeded {la_count} listing-amenity mappings")

        # ── ListingCategory mappings ──
        print("Assigning categories to listings...")
        lc_count = 0
        all_category_names = [c["name"] for c in CATEGORIES_DATA]

        for db_lid, raw_amenity_list in listing_amenities_raw.items():
            nb = listing_neighbourhood_map.get(db_lid, "")
            listing_obj = listing_db_objects[db_lid - 1]
            assigned = set()
            amenity_text = " ".join(a.lower() for a in raw_amenity_list)

            if listing_obj.room_type == "Private room" and "Rooms" not in assigned:
                assigned.add("Rooms")
            if listing_obj.review_count >= 20 and "Trending" not in assigned:
                assigned.add("Trending")
            if "pool" in amenity_text and "Pools" not in assigned:
                assigned.add("Pools")
            if nb in VIEW_NEIGHBOURHOODS and "Amazing views" not in assigned:
                assigned.add("Amazing views")
            if listing_obj.is_guest_favourite and "Icons" not in assigned:
                assigned.add("Icons")

            if not assigned:
                assigned.add(random.choice(all_category_names))

            for cat_name in assigned:
                if cat_name in category_map:
                    session.add(ListingCategory(
                        listing_id=db_lid, category_id=category_map[cat_name],
                    ))
                    lc_count += 1

        session.commit()
        print(f"Seeded {lc_count} listing-category mappings")

        # ── Reviewer users (sample ~5000) ──
        print("Extracting reviewer users...")
        reviewer_pairs = {}
        for row in reviews_rows:
            rid = row.get('reviewer_id', '').strip()
            if rid and rid not in reviewer_pairs:
                reviewer_pairs[rid] = row.get('reviewer_name', '').strip() or f"Reviewer {rid}"

        sampled_reviewer_ids = list(reviewer_pairs.keys())
        if len(sampled_reviewer_ids) > 5000:
            sampled_reviewer_ids = random.sample(sampled_reviewer_ids, 5000)
        sampled_reviewer_set = set(sampled_reviewer_ids)

        csv_reviewer_id_to_db_id = {}
        for rid in sampled_reviewer_ids:
            rname = reviewer_pairs[rid]
            user = User(
                id=next_user_id,
                email=f"reviewer_{rid}@caveat_stay.com",
                name=rname[:100],
                password_hash=pwd_hash,
                avatar_url=f"https://i.pravatar.cc/150?u=reviewer_{rid}",
                member_since=now - timedelta(days=random.randint(180, 2000)),
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            csv_reviewer_id_to_db_id[rid] = next_user_id
            next_user_id += 1

        session.commit()
        print(f"Seeded {len(csv_reviewer_id_to_db_id)} reviewer users")

        # ── Reviews (cap 20K) ──
        print("Processing reviews...")
        eligible_reviews = []
        for row in reviews_rows:
            csv_lid = row.get('listing_id', '').strip()
            csv_rid = row.get('reviewer_id', '').strip()
            if csv_lid in csv_listing_id_to_db_id and csv_rid in csv_reviewer_id_to_db_id:
                eligible_reviews.append(row)

        eligible_reviews.sort(key=lambda r: r.get('date', ''), reverse=True)
        capped_reviews = eligible_reviews[:20000]
        print(f"  {len(capped_reviews)} reviews to insert (from {len(eligible_reviews)} eligible)")

        booking_id_counter = 1
        review_id_counter = 1
        BATCH = 2000
        for batch_start in range(0, len(capped_reviews), BATCH):
            batch = capped_reviews[batch_start:batch_start + BATCH]
            for row in batch:
                csv_lid = row['listing_id'].strip()
                csv_rid = row['reviewer_id'].strip()
                db_lid = csv_listing_id_to_db_id[csv_lid]
                db_reviewer_id = csv_reviewer_id_to_db_id[csv_rid]

                review_date_str = row.get('date', '').strip()
                try:
                    review_dt = datetime.strptime(review_date_str, '%Y-%m-%d')
                    review_d = review_dt.date()
                except (ValueError, TypeError):
                    review_dt = now - timedelta(days=random.randint(30, 365))
                    review_d = review_dt.date() if isinstance(review_dt, datetime) else review_dt

                check_in = review_d - timedelta(days=random.randint(3, 10))
                check_out = review_d - timedelta(days=1)
                if check_out <= check_in:
                    check_out = check_in + timedelta(days=2)

                scores = listing_scores.get(db_lid, {})
                base_rating = scores.get('rating', 4.5) or 4.5
                overall = min(5.0, max(1.0, round(base_rating + random.uniform(-0.3, 0.3), 1)))

                def _score(key):
                    v = scores.get(key, base_rating) or base_rating
                    return min(5.0, max(1.0, round(v + random.uniform(-0.3, 0.3), 1)))

                listing_obj = listing_db_objects[db_lid - 1]
                ppn = listing_obj.price_per_night
                nights = (check_out - check_in).days
                svc = round(ppn * nights * 0.14, 2)
                total = round(ppn * nights + listing_obj.cleaning_fee + svc, 2)

                booking = Booking(
                    id=booking_id_counter,
                    listing_id=db_lid,
                    guest_id=db_reviewer_id,
                    check_in=check_in,
                    check_out=check_out,
                    num_guests=random.randint(1, min(4, listing_obj.max_guests)),
                    num_adults=1,
                    price_per_night=ppn,
                    cleaning_fee=listing_obj.cleaning_fee,
                    service_fee=svc,
                    total_price=total,
                    currency="USD",
                    status="completed",
                    confirmation_code=f"BK{booking_id_counter:06d}",
                    created_at=datetime.combine(check_in - timedelta(days=14), datetime.min.time()),
                    updated_at=datetime.combine(check_out, datetime.min.time()),
                )
                session.add(booking)

                comment = _clean_text(row.get('comments', '').strip() or "Great stay!")
                review = Review(
                    id=review_id_counter,
                    listing_id=db_lid,
                    booking_id=booking_id_counter,
                    reviewer_id=db_reviewer_id,
                    overall_rating=overall,
                    cleanliness_rating=_score('cleanliness'),
                    accuracy_rating=_score('accuracy'),
                    checkin_rating=_score('checkin'),
                    communication_rating=_score('communication'),
                    location_rating=_score('location'),
                    value_rating=_score('value'),
                    comment=comment[:2000],
                    created_at=review_dt if isinstance(review_dt, datetime) else datetime.combine(review_d, datetime.min.time()),
                    updated_at=review_dt if isinstance(review_dt, datetime) else datetime.combine(review_d, datetime.min.time()),
                )
                session.add(review)
                booking_id_counter += 1
                review_id_counter += 1

            session.commit()
            print(f"  Committed reviews batch {batch_start + len(batch)}/{len(capped_reviews)}")

        total_reviews = review_id_counter - 1
        total_review_bookings = booking_id_counter - 1
        print(f"Seeded {total_reviews} reviews with {total_review_bookings} review bookings")

        # ── Calendar / BlockedDates ──
        print("Loading calendar data for blocked dates...")
        calendar_path = os.path.join(DATA_DIR, 'calendar.csv')
        blocked_count = 0
        max_blocked = 50000
        cutoff_date = today + timedelta(days=90)

        with open(calendar_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if blocked_count >= max_blocked:
                    break
                if row.get('available', '').strip().lower() != 'f':
                    continue
                csv_lid = row.get('listing_id', '').strip()
                if csv_lid not in csv_listing_id_to_db_id:
                    continue
                date_str = row.get('date', '').strip()
                try:
                    cal_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    continue
                if cal_date < today or cal_date > cutoff_date:
                    continue
                db_lid = csv_listing_id_to_db_id[csv_lid]
                session.add(BlockedDate(
                    listing_id=db_lid, date=cal_date, reason="unavailable",
                ))
                blocked_count += 1
                if blocked_count % 10000 == 0:
                    session.commit()
                    print(f"  Blocked dates progress: {blocked_count}")

        session.commit()
        print(f"Seeded {blocked_count} blocked dates")

        # ── Synthetic guest bookings (50) ──
        print("Creating synthetic guest bookings...")
        guest_id = 1
        all_listing_ids = list(csv_listing_id_to_db_id.values())
        guest_booking_listing_ids = random.sample(all_listing_ids, min(50, len(all_listing_ids)))

        completed_guest_bookings = []
        for i, lid in enumerate(guest_booking_listing_ids[:35]):
            listing_obj = listing_db_objects[lid - 1]
            days_ago = random.randint(15, 365)
            nights = random.randint(2, 7)
            ci = today - timedelta(days=days_ago)
            co = ci + timedelta(days=nights)
            ppn = listing_obj.price_per_night
            svc = round(ppn * nights * 0.14, 2)
            total = round(ppn * nights + listing_obj.cleaning_fee + svc, 2)

            booking = Booking(
                id=booking_id_counter,
                listing_id=lid,
                guest_id=guest_id,
                check_in=ci, check_out=co,
                num_guests=random.randint(1, min(4, listing_obj.max_guests)),
                num_adults=1,
                price_per_night=ppn,
                cleaning_fee=listing_obj.cleaning_fee,
                service_fee=svc,
                total_price=total,
                currency="USD",
                status="completed",
                confirmation_code=f"BK{booking_id_counter:06d}",
                created_at=datetime.combine(ci - timedelta(days=14), datetime.min.time()),
                updated_at=datetime.combine(co, datetime.min.time()),
            )
            session.add(booking)
            completed_guest_bookings.append(booking_id_counter)
            booking_id_counter += 1

        for i, lid in enumerate(guest_booking_listing_ids[35:50]):
            listing_obj = listing_db_objects[lid - 1]
            days_future = random.randint(7, 60)
            nights = random.randint(2, 5)
            ci = today + timedelta(days=days_future)
            co = ci + timedelta(days=nights)
            ppn = listing_obj.price_per_night
            svc = round(ppn * nights * 0.14, 2)
            total = round(ppn * nights + listing_obj.cleaning_fee + svc, 2)

            booking = Booking(
                id=booking_id_counter,
                listing_id=lid,
                guest_id=guest_id,
                check_in=ci, check_out=co,
                num_guests=random.randint(1, min(3, listing_obj.max_guests)),
                num_adults=1,
                price_per_night=ppn,
                cleaning_fee=listing_obj.cleaning_fee,
                service_fee=svc,
                total_price=total,
                currency="USD",
                status="confirmed",
                confirmation_code=f"BK{booking_id_counter:06d}",
                created_at=now - timedelta(days=random.randint(1, 14)),
                updated_at=now,
            )
            session.add(booking)
            booking_id_counter += 1

        session.commit()
        print(f"Seeded 50 synthetic guest bookings (35 completed + 15 upcoming)")

        # ── Wishlists ──
        print("Creating wishlists...")
        wl_names = ["Dream Stays", "Boston Favorites", "Weekend Getaways"]
        for wl_idx, wl_name in enumerate(wl_names):
            wl = Wishlist(
                user_id=guest_id,
                name=wl_name,
                created_at=now - timedelta(days=random.randint(5, 60)),
                updated_at=now,
            )
            session.add(wl)
            session.flush()
            sample_lids = random.sample(all_listing_ids, min(5, len(all_listing_ids)))
            for lid in sample_lids:
                session.add(WishlistItem(
                    wishlist_id=wl.id, listing_id=lid,
                    created_at=now - timedelta(days=random.randint(1, 30)),
                ))
        session.commit()

        # ── UserSettings ──
        print("Creating user settings...")
        all_users = session.exec(select(User)).all()
        for user in all_users:
            session.add(UserSettings(
                user_id=user.id,
                preferred_currency="USD",
                preferred_language="en",
                notification_email=True,
                notification_push=random.choice([True, True, False]),
                theme=random.choice(["light", "light", "light", "dark"]),
                created_at=now,
                updated_at=now,
            ))
        session.commit()
        print(f"Seeded {len(all_users)} user settings")

        # ── Help Articles ──
        print("Creating help articles...")
        for article in HELP_ARTICLES_DATA:
            session.add(HelpArticle(**article, created_at=now))
        session.commit()
        print(f"Seeded {len(HELP_ARTICLES_DATA)} help articles")

        # ── Support Tickets ──
        print("Creating support tickets...")
        tickets = [
            {"user_id": 1, "subject": "Issue with check-in instructions", "description": "I couldn't find the lockbox code in the check-in instructions for my upcoming stay.", "category": "booking", "status": "in_progress"},
            {"user_id": 1, "subject": "Refund not received", "description": "I cancelled my booking 5 days ago but haven't received the refund yet.", "category": "payments", "status": "open"},
            {"user_id": 1, "subject": "Profile photo upload failing", "description": "I've been trying to upload a new profile photo but it keeps failing.", "category": "account", "status": "resolved"},
        ]
        for t in tickets:
            session.add(SupportTicket(**t, created_at=now - timedelta(days=random.randint(1, 14)), updated_at=now))
        session.commit()

        # ── Messages ──
        print("Creating message threads...")
        msg_templates = [
            "Hi! Is your place available for the dates I selected?",
            "Thanks for getting back to me! That sounds great.",
            "Could you tell me more about parking options?",
            "What's the best way to reach the property from the airport?",
            "Is early check-in possible? Our flight arrives at 10 AM.",
            "Thank you so much! We really enjoyed our stay.",
            "Do you allow pets? We'd love to bring our dog.",
            "Can you recommend any local restaurants nearby?",
            "Would it be possible to arrange a late checkout?",
        ]
        sample_host_db_ids = random.sample(list(csv_host_id_to_db_id.values()), min(8, len(csv_host_id_to_db_id)))
        msg_id = 0
        for t_idx, host_db_id in enumerate(sample_host_db_ids):
            host_listings = [lid for lid, nb in listing_neighbourhood_map.items()
                            if listing_db_objects[lid - 1].host_id == host_db_id]
            thread_lid = host_listings[0] if host_listings else all_listing_ids[0]
            listing_obj = listing_db_objects[thread_lid - 1]

            num_msgs = random.randint(3, 6)
            last_msg_time = now - timedelta(hours=random.randint(1, 168))
            thread = MessageThread(
                id=t_idx + 1,
                listing_id=thread_lid,
                guest_id=guest_id,
                host_id=host_db_id,
                subject=f"About: {listing_obj.title[:50]}",
                last_message_at=last_msg_time,
                created_at=last_msg_time - timedelta(hours=num_msgs * 2),
            )
            session.add(thread)
            session.flush()

            for m in range(num_msgs):
                msg_id += 1
                sender_id = guest_id if m % 2 == 0 else host_db_id
                msg_time = last_msg_time - timedelta(hours=(num_msgs - m) * 2)
                session.add(Message(
                    id=msg_id, thread_id=t_idx + 1, sender_id=sender_id,
                    content=random.choice(msg_templates),
                    is_read=m < num_msgs - 1,
                    created_at=msg_time,
                ))
        session.commit()

        # ── Notifications ──
        print("Creating notifications...")
        notif_data = [
            {"type": "booking_confirmed", "title": "Booking confirmed", "body": "Your booking in Boston has been confirmed!", "link": "/trips", "is_read": True},
            {"type": "message_received", "title": "New message", "body": "Your host sent you a message about check-in.", "link": "/messages", "is_read": True},
            {"type": "review_posted", "title": "New review", "body": "A guest left a 5-star review on your listing.", "link": "/listings/1", "is_read": True},
            {"type": "booking_requested", "title": "Booking request", "body": "You have a new booking request.", "link": "/trips", "is_read": False},
            {"type": "message_received", "title": "New message", "body": "Is the parking area available?", "link": "/messages", "is_read": False},
            {"type": "booking_confirmed", "title": "Booking confirmed", "body": "Your stay in Back Bay is confirmed!", "link": "/trips", "is_read": True},
            {"type": "booking_cancelled", "title": "Booking cancelled", "body": "A guest cancelled their booking.", "link": "/trips", "is_read": False},
            {"type": "review_posted", "title": "New review", "body": "Someone left a review on your listing.", "link": "/listings/10", "is_read": False},
        ]
        for n_idx, nd in enumerate(notif_data):
            session.add(Notification(
                id=n_idx + 1, user_id=1,
                type=nd["type"], title=nd["title"], body=nd["body"],
                link=nd["link"], is_read=nd["is_read"],
                created_at=now - timedelta(hours=n_idx * 6 + random.randint(0, 5)),
            ))
        session.commit()

        # ── Update Neighbourhood stats ──
        print("Updating neighbourhood statistics...")
        for nb_name, nb_id in neighbourhood_name_to_id.items():
            nb_listings = [lo for lo in listing_db_objects
                          if listing_neighbourhood_map.get(lo.id) == nb_name]
            count = len(nb_listings)
            avg_p = (sum(lo.price_per_night for lo in nb_listings) / count) if count else 0.0
            lats = [lo.latitude for lo in nb_listings if lo.latitude]
            lons = [lo.longitude for lo in nb_listings if lo.longitude]
            avg_lat = (sum(lats) / len(lats)) if lats else 42.36
            avg_lon = (sum(lons) / len(lons)) if lons else -71.06

            nb_obj = session.get(Neighbourhood, nb_id)
            if nb_obj:
                nb_obj.listing_count = count
                nb_obj.avg_price = round(avg_p, 2)
                nb_obj.latitude = round(avg_lat, 6)
                nb_obj.longitude = round(avg_lon, 6)
                session.add(nb_obj)
        session.commit()
        print("Neighbourhood stats updated")

    # ── Recalculate review_count and avg_rating for all listings ────
    print("Syncing listing review_count/avg_rating with actual review data...")
    with Session(engine) as session:
        listings = session.exec(select(Listing)).all()
        synced = 0
        for listing in listings:
            reviews = session.exec(
                select(Review).where(Review.listing_id == listing.id)
            ).all()
            actual_count = len(reviews)
            if reviews:
                actual_avg = round(
                    sum(r.overall_rating for r in reviews) / actual_count, 2
                )
            else:
                actual_avg = 0.0
            if listing.review_count != actual_count or abs((listing.avg_rating or 0) - actual_avg) > 0.01:
                listing.review_count = actual_count
                listing.avg_rating = actual_avg
                listing.is_guest_favourite = actual_avg >= 4.8 and actual_count >= 10
                session.add(listing)
                synced += 1
        session.commit()
        print(f"  Synced {synced} listings")

    # Add search history entries
    search_entries = [
        SearchHistory(user_id=1, query="Boston downtown", filters='{"location":"Downtown"}', result_count=331),
        SearchHistory(user_id=1, query="Back Bay apartments", filters='{"location":"Back Bay","property_type":"Entire rental unit"}', result_count=187),
        SearchHistory(user_id=1, query="Pet friendly", filters='{"amenities":["Pets allowed"]}', result_count=45),
        SearchHistory(user_id=1, query="Beach houses", filters='{"category":"Beachfront"}', result_count=12),
        SearchHistory(user_id=1, query="South Boston waterfront", filters='{"location":"South Boston Waterfront"}', result_count=89),
    ]
    for entry in search_entries:
        session.add(entry)
    session.commit()
    print(f"  Search history entries: {len(search_entries)}")

    print("\n✅ Database seeded successfully!")
    print(f"  Guest user: Alex Johnson (guest@caveat_stay.com / password123)")
    print(f"  Hosts: {num_hosts}")
    print(f"  Listings: {total_listings}")
    print(f"  Reviews: {total_reviews}")
    print(f"  Blocked dates: {blocked_count}")
    print(f"  Neighbourhoods: {len(neighbourhood_name_to_id)}")
    print(f"  Categories: {len(CATEGORIES_DATA)}, Amenities: {len(AMENITIES_DATA)}")
    print(f"  Currencies: {len(CURRENCIES_DATA)}")
    print(f"  Help articles: {len(HELP_ARTICLES_DATA)}")


def main():
    parser = argparse.ArgumentParser(description="Seed CAVEAT-Stay database with real Boston data")
    parser.add_argument("--db", default="./caveat_stay.db", help="Database file path")
    args = parser.parse_args()

    print(f"Seeding database: {args.db}")
    seed_database(args.db)


if __name__ == "__main__":
    main()
