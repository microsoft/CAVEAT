# CAVEAT-Shop Replica Specification

A 100% faithful mockup of CAVEAT-Shop's web interface using React + Tailwind frontend and FastAPI + SQLite backend.

---

## Quick Reference

| Category | Details |
|----------|---------|
| **Data Models** | 38 models: User, UserSession, Product, ProductVariant, Category, Department, Brand, Seller, Cart, CartItem, Order, OrderItem, Review, Wishlist, WishlistItem, Address, PaymentMethod, SearchHistory, BrowsingHistory, Deal, Coupon, Subscription, Notification, PriceWatch, AlexaShoppingList, GiftCard, Registry, Message, ProductRecall, ShoppingPreference, CaveatShopCreditCard, MusicLibrary, BusinessAccount |
| **API Endpoints** | 120+ endpoints covering products, search, cart, checkout, orders, reviews, wishlists, account, recommendations, deals, sellers, gift cards, messages, registries, business accounts |
| **Frontend Routes** | 45+ routes including home, product pages, search, cart, checkout, orders, account, wishlists, deals, buy again, messages, registries |
| **Key Features** | Product search with filters, product detail pages, reviews and ratings, cart management, checkout flow, order tracking, wishlists, Prime benefits, deals, recommendations, account management, buy again, gift cards, registries, business accounts |
| **UI Components** | Header with Account dropdown, Navigation, Mega Menu, Product Grid, Product Cards, Product Detail, Buy Box, Cart, Checkout, Reviews, Filters, Account Hub (9 cards), Account link sections |

---

## Table of Contents

1. [UI Layout & Components](#1-ui-layout--components)
2. [Data Models](#2-data-models-backend)
3. [API Endpoints](#3-api-endpoints)
4. [Frontend Routes](#4-frontend-routes)
5. [Color Palette](#5-color-palette-caveat_shop-accurate)
6. [Typography](#6-typography)
7. [Icons](#7-icons)
8. [Key Interactions & Behaviors](#8-key-interactions--behaviors)
9. [Responsive Breakpoints](#9-responsive-breakpoints)
10. [Account Page Structure](#10-account-page-structure)
11. [Empty States](#11-empty-states)
12. [Loading States](#12-loading-states)
13. [Error States](#13-error-states)
14. [Notifications](#14-notifications)
15. [Accessibility](#15-accessibility)
16. [Database Seed Data](#16-database-seed-data)
17. [File Structure](#17-file-structure-target)
18. [Implementation Priority](#18-implementation-priority)

---

## 1. UI Layout & Components

### 1.1 Header Bar (Fixed Top)

#### 1.1.1 Top Navigation Bar (Primary)
- **Left Section:**
  - CAVEAT-Shop logo (white text on dark blue, click goes to home)
  - Delivery location: Pin icon + "Deliver to [Name]" + "[City ZIP]" (click opens location modal)
- **Center Section (Search):**
  - Department dropdown ("All" default, expandable list of departments)
  - Search input field (placeholder: "Search CAVEAT-Shop")
  - Search button (orange with magnifying glass icon)
  - Voice search microphone icon (inside search bar, right)
- **Right Section:**
  - Language selector: Flag icon + "EN" dropdown (EN, ES, etc.)
  - "Hello, [Name]" + "Account & Lists" (dropdown on hover with account menu)
  - "Returns & Orders" link
  - Cart icon with item count badge + "Cart" text

#### 1.1.3 Account & Lists Dropdown (Hover Menu)
Three-column layout with header bar:

**Header Bar:**
- "Sign Out" link

**Left Column - Buy it again:**
- "Buy it again" header with "View All & Manage" link
- List of previously purchased products (4-6 items)
- Each item shows:
  - Product thumbnail image
  - Product title (truncated, 2 lines)
  - Price (e.g., "$35.99")
  - Unit price in parentheses (e.g., "($27.42/lb)")
  - Prime badge (blue checkmark + "prime")
  - "Add to cart" button (yellow, rounded)

**Center Column - Your Lists:**
- "Your Lists" header
- User's wishlists (dynamically populated):
  - Wish List (default)
  - Shopping List
  - Custom lists (e.g., "Christmas", "Birthday Ideas")
- Divider line
- "Create a List" link
- "Find a List or Registry" link
- "Alexa Shopping List" with item count (e.g., "0 items")

**Right Column - Your Account:**
- "Your Account" header
- Account links (full list):
  - Account
  - Orders
  - Keep Shopping For
  - Recommendations
  - Browsing History
  - Your Shopping preferences
  - CAVEAT-Shop Credit Cards
  - Recalls and Product Safety Alerts
  - Subscribe & Save Items
  - Memberships & Subscriptions
  - Prime Membership
  - Music Library
  - Start a Selling Account
  - Create Your Free Business Account
  - Customer Service

#### 1.1.4 Sub-Navigation Bar (Secondary)
- **Left:** Hamburger menu icon + "All" (opens mega menu sidebar)
- **Quick Links (scrollable on mobile):**
  - 30-Minute Delivery (for eligible areas)
  - 3 Hour Delivery (for eligible areas)
  - Medical Care
  - Buy Again
  - Pet Supplies
  - Household, Health & Baby Care
  - Browsing History
  - Sell
  - Gift Cards
  - Subscribe & Save
  - Today's Deals
  - Customer Service
  - Registry
  - Prime (with Prime logo)
- **Right (promotional):** Rotating promo text (e.g., "Ring 6 on DAZN—Jan 31 6PM ET")

### 1.2 Mega Menu Sidebar (Slide-in from Left)

#### 1.2.1 Header
- "Hello, [Name]"
- Close X button (top right)

#### 1.2.2 Menu Sections
- **Trending:**
  - Best Sellers
  - New Releases
  - Movers & Shakers
- **Digital Content:**
  - CAVEAT-Shop Music
  - CAVEAT-Shop Photos
- **Shop by Department:** (Expandable sub-menus)
  - Electronics
  - Computers
  - Smart Home
  - Arts & Crafts
  - Automotive
  - Baby
  - Beauty & Personal Care
  - Women's Fashion
  - Men's Fashion
  - Girls' Fashion
  - Boys' Fashion
  - Health & Household
  - Home & Kitchen
  - Industrial & Scientific
  - Luggage
  - Movies & Television
  - Pet Supplies
  - Software
  - Sports & Outdoors
  - Tools & Home Improvement
  - Toys & Games
  - Video Games
  - See All
- **Programs & Features:**
  - Gift Cards
  - Shop By Interest
  - CAVEAT-Shop Live
  - International Shopping
  - CAVEAT-Shop Second Chance
- **Help & Settings:**
  - Your Account
  - Customer Service
  - Sign Out

### 1.3 Homepage Layout

#### 1.3.1 Hero Carousel
- Full-width image slider
- Navigation arrows (left/right)
- Dot indicators at bottom
- Auto-rotate (5 second intervals)
- Content: Promotional banners, seasonal campaigns, Prime Day, etc.
- Fade gradient at bottom blending into content

#### 1.3.2 Category Cards Grid (Below Hero)
- 4-column grid of category cards
- Each card: Image + Category name + "Shop now" link
- Categories: Electronics, Fashion, Home, Beauty, etc.
- Cards overlap hero section slightly

#### 1.3.3 Product Carousels (Multiple)
- **Keep shopping for [category]:** Recently viewed related items
- **Inspired by your browsing history:** Personalized recommendations
- **Best Sellers in [Department]:** Top selling items
- **Deals for you:** Personalized deal recommendations
- **Popular products in [Category]:** Trending items
- **Frequently repurchased:** Subscribe & Save suggestions
- **Customers who bought X also bought:** Collaborative filtering

Each carousel:
- Horizontal scrolling with arrows
- 5-6 visible items (desktop)
- Product card shows: Image, Title (2 lines), Rating stars, Review count, Price, Prime badge

#### 1.3.4 Sign-in Prompt Card
- "Sign in for the best experience"
- Sign in button (yellow)
- Appears for logged-out users

#### 1.3.5 Recently Viewed Section
- "Your browsing history" header with "View or edit" link
- Horizontal carousel of recently viewed products

#### 1.3.6 Footer

**Top Footer (Back to top):**
- "Back to top" link (dark blue bar)

**Middle Footer (4-column links):**
- **Get to Know Us:** Careers, Blog, About CAVEAT-Shop, Investor Relations, CAVEAT-Shop Devices, CAVEAT-Shop Science
- **Make Money with Us:** Sell products on CAVEAT-Shop, Sell on CAVEAT-Shop Business, Sell apps on CAVEAT-Shop, Become an Affiliate, Advertise Your Products, Self-Publish with Us, Host an CAVEAT-Shop Hub, See More
- **CAVEAT-Shop Payment Products:** CAVEAT-Shop Business Card, Shop with Points, Reload Your Balance, CAVEAT-Shop Currency Converter
- **Let Us Help You:** Your Account, Your Orders, Shipping Rates & Policies, Returns & Replacements, Manage Your Content and Devices, CAVEAT-Shop Assistant, Help

**Bottom Footer:**
- CAVEAT-Shop logo (center)
- Language selector
- Currency selector
- Country selector
- Copyright and legal links

### 1.4 Search Results Page

#### 1.4.1 Search Header
- "Results" header
- Result count: "1-48 of over 10,000 results for '[search term]'"
- Sort dropdown: "Sort by: Featured" (Featured, Price: Low to High, Price: High to Low, Avg. Customer Review, Newest Arrivals)

#### 1.4.2 Left Sidebar Filters
- **Department:** Hierarchical category tree
- **Customer Reviews:** 4 stars & up, 3 stars & up, etc.
- **Brand:** Checkbox list with brand names
- **Price:** Min-Max input fields + Go button
- **Deals & Discounts:** Today's Deals, All Discounts
- **Condition:** New, Renewed, Used
- **Availability:** Include Out of Stock
- **Seller:** CAVEAT-Shop.com, Third-party sellers
- **Climate Pledge Friendly:** Filter for sustainable products
- **Additional filters based on category:**
  - Electronics: Screen Size, Storage Capacity, Connectivity
  - Clothing: Size, Color, Material
  - Books: Format (Kindle, Hardcover, Paperback), Language

#### 1.4.3 Product Grid
- **View toggle:** Grid view / List view
- **Sponsored products:** "Sponsored" label, mixed into results
- **Product cards in grid:**
  - Product image (hover shows secondary images)
  - Sponsored badge (if applicable)
  - Title (3 lines max, truncated)
  - Rating: Star display (1-5, with decimals shown) + review count
  - "X bought in past month" social proof
  - Price: Dollar amount (whole number large, cents superscript)
  - List price strikethrough (if on sale)
  - Discount percentage badge (if applicable)
  - Prime badge with delivery date
  - "FREE delivery [date]" text
  - "Or fastest delivery [date]" for Prime
  - Color/variant swatches (if applicable)
  - "Add to cart" button
  - "Save for later" heart icon

#### 1.4.4 Pagination
- Page numbers (1, 2, 3, ... 20)
- Previous/Next arrows
- "Page X of Y" indicator

### 1.5 Product Detail Page

#### 1.5.1 Left Column - Product Images
- Main product image (large, zoomable on hover)
- Thumbnail gallery (6-8 images)
- 360° view button (if available)
- Video thumbnail (if product video exists)
- "Click image to open expanded view" tooltip
- Image zoom: Magnifier lens on desktop, pinch-zoom on mobile

#### 1.5.2 Center Column - Product Information
- **Product Title:** Full title (can be long), typically includes brand, name, key specs
- **Brand link:** "Visit the [Brand] Store"
- **Rating:** Star rating (4.5 stars) + rating number (4.5 out of 5 stars)
- **Review count:** "12,345 ratings" (clickable, jumps to reviews)
- **Best Seller badge:** "#1 Best Seller in [Category]" (orange ribbon)
- **CAVEAT-Shop's Choice badge:** "CAVEAT-Shop's Choice for '[keyword]'" (dark blue)
- **Climate Pledge Friendly badge** (if applicable)
- **Price section:**
  - "Price:" label
  - Dollar amount (large)
  - List price (strikethrough)
  - Savings: "You save: $X.XX (XX%)"
  - Coupon: "Apply $X coupon" checkbox
  - Prime exclusive pricing (if member)
- **Availability:** "In Stock" (green) or "Only X left in stock - order soon" (orange) or "Currently unavailable"
- **Delivery info:**
  - FREE delivery date for Prime
  - Standard delivery date
  - "Or fastest delivery" option
  - "Deliver to [Location]" with change link
- **Quantity selector:** Dropdown (1-30)
- **Buy buttons:**
  - "Add to Cart" (yellow)
  - "Buy Now" (orange)
- **Secure transaction:** Lock icon + text
- **Ships from:** CAVEAT-Shop.com or [Seller Name]
- **Sold by:** CAVEAT-Shop.com or [Seller Name] (link to seller page)
- **Return policy:** "Eligible for Return, Refund or Replacement within 30 days"
- **Gift options:** "Add a gift receipt for easy returns" checkbox
- **Add to List** dropdown (Wishlist, other lists)

#### 1.5.3 Right Column - Buy Box (Sticky)
- Condensed version of purchase options
- Price
- Delivery options
- Add to Cart button
- Buy Now button
- "Other sellers on CAVEAT-Shop" link (if multiple sellers)
- "New & Used (X) from $XX.XX" link

#### 1.5.4 Product Variations
- **Color selection:** Swatches with color names, selected state highlighted
- **Size selection:** Size buttons (S, M, L, XL) or dropdown
- **Style selection:** Text or image buttons
- **Configuration selection:** For electronics (capacity, color, etc.)
- Unavailable combinations grayed out
- Price may change per variation

#### 1.5.5 Product Details Section
- **About this item:** Bullet point list of features
- **Technical Details table:** Key specs (Dimensions, Weight, Material, etc.)
- **Additional Information table:** ASIN, Best Sellers Rank, Date First Available
- **Product description:** Long-form description with formatting
- **From the manufacturer:** Brand content module with images/videos
- **Compare with similar items:** Comparison table

#### 1.5.6 Frequently Bought Together
- "Frequently bought together" header
- 3 products displayed horizontally with + signs between
- Combined price shown
- "Add all three to Cart" button
- Individual checkboxes to include/exclude items

#### 1.5.7 Customers Who Viewed This Also Viewed
- Horizontal carousel of related products
- Product cards with image, title, rating, price

#### 1.5.8 Product Bundles (if available)
- "Special offers and product promotions" section
- Bundle deals and promotions listed

#### 1.5.9 Customer Reviews Section
- **Summary header:**
  - Overall rating (large stars + number)
  - "X global ratings"
  - Rating breakdown bar chart (5 star, 4 star, etc. with percentages)
- **Review with images:** Carousel of customer-uploaded images
- **Top positive review** and **Top critical review** side by side
- **Review filters:**
  - "All reviewers" dropdown (Verified purchase only)
  - "All formats" dropdown (All formats, specific format)
  - "All stars" dropdown (filter by rating)
  - Text search within reviews
- **Individual reviews:**
  - Reviewer name + avatar
  - Rating stars + review title
  - "Reviewed in [Country] on [Date]"
  - "Verified Purchase" badge
  - Review body text
  - "X people found this helpful" count
  - "Helpful" button
  - "Report" link
  - Images/videos attached to review
  - Seller response (if applicable)
- **Pagination** for reviews
- **Write a review button** (for verified purchasers)

#### 1.5.10 Questions & Answers Section
- "Customer questions & answers" header
- Search questions input
- List of Q&As:
  - Question text
  - Answer text with answerer info
  - Vote buttons (helpful/not helpful)
- "Ask a question" button
- "See all questions" link

### 1.6 Shopping Cart Page

#### 1.6.1 Cart Header
- "Shopping Cart" title
- "Deselect all items" link
- Price header column

#### 1.6.2 Cart Items List
- **Each item:**
  - Checkbox (for selection)
  - Product image (clickable to detail page)
  - Product title (link)
  - "In Stock" status
  - Gift option: "This is a gift" checkbox
  - Seller: "Sold by: [Seller]"
  - Eligible for FREE Shipping
  - Quantity dropdown
  - Unit price
  - Delete link
  - "Save for later" link
  - "Compare with similar items" link
- **Group by seller** (CAVEAT-Shop vs third-party)

#### 1.6.3 Save for Later Section
- Items removed from cart but saved
- "Move to Cart" button per item
- Delete option

#### 1.6.4 Cart Summary Sidebar (Right)
- Subtotal: "Subtotal (X items): $XXX.XX"
- "This order contains a gift" checkbox
- "Proceed to checkout" button (yellow)
- EMI options (if applicable)

#### 1.6.5 Product Recommendations
- "Customers who bought items in your cart also bought" carousel
- "Frequently bought together" suggestions
- "Your recently viewed items" carousel

### 1.7 Checkout Flow

#### 1.7.1 Checkout Header
- CAVEAT-Shop logo (left)
- Checkout progress indicator (1. Shipping, 2. Payment, 3. Review)
- Lock icon + "Checkout"

#### 1.7.2 Step 1: Shipping Address
- **Select existing address:**
  - Radio buttons for saved addresses
  - Each address shows: Name, Street, City, State, ZIP, Phone
  - "Deliver to this address" button
- **Add new address:**
  - Form fields: Full name, Phone, Address Line 1, Address Line 2, City, State, ZIP
  - "Add address" button
- **Gift options:**
  - Gift message option
  - Hide prices checkbox

#### 1.7.3 Step 2: Payment Method
- **Saved payment methods:**
  - Credit/debit cards with last 4 digits
  - CAVEAT-Shop Store Card
  - Gift card balance
- **Add payment method:**
  - Card number, expiration, CVV
  - Billing address
- **Apply gift cards/promo codes:**
  - Input field
  - "Apply" button
- **Choose installment plans** (if eligible)

#### 1.7.4 Step 3: Review & Place Order
- **Shipping address summary**
- **Payment method summary**
- **Items summary:**
  - Each item with image, title, quantity, price
  - Delivery date for each
- **Order summary:**
  - Items total
  - Shipping & handling
  - Total before tax
  - Estimated tax
  - **Order total** (large, bold)
- **Place your order button** (yellow)
- Terms agreement text

### 1.8 Order Confirmation Page

- "Order placed, thanks!" header with checkmark
- Order number
- "We'll send a confirmation email"
- Estimated delivery date
- Order summary
- "Continue shopping" button
- Recommended products

### 1.9 Orders Page (Account)

#### 1.9.1 Order Filters
- Time filter dropdown: "Past 3 months", "2024", "2023", etc.
- Search orders input

#### 1.9.2 Order List
- **Each order card:**
  - Order date
  - Order total
  - Ship to: [Name]
  - Order # (link to details)
  - "View order details" | "Invoice" links
  - **Per item in order:**
    - Product image
    - Product title
    - "Buy it again" button
    - "View your item" button
    - "Write a product review" link
    - Delivery status: "Delivered [Date]" or "Arriving [Date]"
    - "Track package" button
    - "Return or replace items" link
    - "Archive order" link

#### 1.9.3 Order Details Page
- Full order information
- Shipping address
- Payment method
- Item details
- Tracking information
- Order status timeline
- Return/refund options

### 1.10 Account Page Hub

- **Your Account** header
- **Grid of account sections (card-based layout, 3 columns x 4 rows):**

**Row 1:**
  - Your Orders (box icon) - Track, return, cancel an order, download invoice or buy again
  - Login & security (shield icon) - Edit login, name, and mobile number
  - Prime (Prime logo) - Manage your membership, view benefits, and payment settings

**Row 2:**
  - Your Addresses (house icon) - Edit, remove or set default address
  - Your business account (CAVEAT-Shop Business logo) - Create your free CAVEAT-Shop Business account and get 50% off up to $50 on your orders. T&Cs apply.
  - Gift cards (gift card icon) - View balance or redeem a card, and purchase a new Gift Card

**Row 3:**
  - Your Payments (wallet icon) - View all transactions, manage payment methods and settings
  - Your Lists (list icon) - View, modify, and share your lists, or create new ones
  - Customer Service (headset icon) - Browse self service options, help articles or contact us

**Row 4:**
  - Your Messages (message icon) - View or respond to messages from CAVEAT-Shop, Sellers and Buyers

- **Bottom Section - Two Column Links:**

**Ordering and shopping preferences:**
  - Your Addresses
  - CAVEAT-Shop credit cards
  - Your transactions
  - Archived orders
  - Download order reports

**Memberships and subscriptions:**
  - Subscribe & Save
  - Other subscriptions

### 1.10.1 Buy Again Page
- "Buy Again" header
- Filter tabs: "All", "Subscribe & Save eligible"
- Sort by: "Purchase date" dropdown
- Product grid of previously purchased items:
  - Product image
  - Product title
  - Price with unit pricing (e.g., "$11.49 ($36.77/lb)")
  - Prime badge
  - "Add to cart" button
  - Star rating
  - "Subscribe & Save" option if eligible

### 1.10.2 Keep Shopping For Page
- Personalized recommendations based on browsing
- "Keep shopping for" header
- Category tabs for different product types viewed
- Product carousel for each category

### 1.10.3 Recommendations Page
- "Recommended for you" header
- Based on: Browsing history, purchase history, wishlists
- "Improve your recommendations" link
- Product grid with recommendation reason shown

### 1.10.4 Shopping Preferences Page
- Language preferences
- Currency preferences
- Country/region settings
- Personalization settings (opt-in/out)
- Manage browsing history
- Manage recommendations

### 1.10.5 CAVEAT-Shop Credit Cards Page
- CAVEAT-Shop Store Card
- CAVEAT-Shop Prime Rewards Visa
- CAVEAT-Shop Secured Card
- Apply for card options
- Manage existing cards
- View rewards balance

### 1.10.6 Recalls and Product Safety Page
- Product recall notifications
- Safety alerts for purchased items
- Instructions for affected products

### 1.10.7 Subscribe & Save Items Page
- Active subscriptions list
- Next delivery date per item
- Delivery frequency (every 1-6 months)
- "Skip next delivery" option
- "Cancel subscription" option
- Discount percentage shown
- Upcoming delivery calendar

### 1.10.8 Memberships & Subscriptions Page
- Prime membership status and renewal
- Subscribe & Save summary
- Manage all subscription settings

### 1.10.9 Medical Care & Pharmacy Page
- CAVEAT-Shop Pharmacy
- Prescription management
- Insurance information
- Order prescription history
- CAVEAT-Shop Clinic access

### 1.10.10 Music Library Page
- Purchased music
- CAVEAT-Shop Music playlists
- Uploaded music
- Play/download options

### 1.10.11 Selling Account Page
- "Start a Selling Account" for new sellers
- Seller Central link for existing sellers
- Seller registration flow
- Selling fees information

### 1.10.12 Business Account Page
- CAVEAT-Shop Business registration
- Business Prime benefits
- Multi-user account management
- Business analytics

### 1.11 Wishlist Page

#### 1.11.1 List Selector
- Dropdown to select list
- "Create a List" button

#### 1.11.2 List Header
- List name (editable)
- Privacy setting: Public/Private toggle
- Share list link
- "More" dropdown (Rename, Delete)

#### 1.11.3 List Items
- **Each item:**
  - Product image
  - Product title
  - Price (current)
  - Price change indicator (if price changed since added)
  - "Added [date]"
  - Priority selector: Highest, High, Medium, Low, Lowest
  - Quantity needed
  - "Add to Cart" button
  - "Delete" link
  - "Move to another list" option
  - Comment field
- Sort options: Date added, Price, Priority

### 1.12 Deals Page

#### 1.12.1 Deal Types
- **Lightning Deals:** Time-limited, quantity-limited
- **Deal of the Day:** Featured daily deals
- **Coupons:** Clippable discount coupons
- **Prime Early Access:** Prime-exclusive deals
- **Subscribe & Save:** Recurring delivery discounts

#### 1.12.2 Deal Card
- Product image
- "Lightning Deal" badge
- Progress bar showing claim percentage
- Time remaining countdown
- Original price (strikethrough)
- Deal price (bold)
- Discount percentage
- "Add to Cart" or "Add to Watchlist" button

#### 1.12.3 Deal Filters
- Department
- Discount percentage
- Deal type
- Prime eligible

### 1.13 Prime Page

- Prime benefits overview
- Prime Video content
- Prime Music
- Prime Reading
- Prime Gaming
- Prime Try Before You Buy
- Whole Foods benefits
- Grubhub+
- "Try Prime" or "Manage membership" buttons

---

## 2. Data Models (Backend)

### 2.1 User
```python
class User(SQLModel, table=True):
    id: int (primary key)
    email: str (unique)
    password_hash: str
    name: str
    phone: str | None
    avatar_url: str | None
    is_prime: bool = False
    prime_since: datetime | None
    default_address_id: int | None
    default_payment_id: int | None
    created_at: datetime
    updated_at: datetime
```

### 2.2 UserSession
```python
class UserSession(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    session_token: str (unique)
    device_type: str  # "desktop" | "mobile" | "tablet" | "app"
    device_name: str  # "Chrome on Windows", "CAVEAT-Shop App on iPhone"
    browser: str | None
    os: str | None
    ip_address: str
    location: str | None  # "Seattle, WA"
    is_current: bool = False
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
```

### 2.2.2 LoginWithCaveatShopApp
```python
class LoginWithCaveatShopApp(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    app_name: str
    app_id: str
    permissions: str  # JSON array of granted scopes
    authorized_at: datetime
    last_used_at: datetime | None
```

### 2.3 Address
```python
class Address(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    full_name: str
    phone: str
    address_line1: str
    address_line2: str | None
    city: str
    state: str
    zip_code: str
    country: str = "United States"
    is_default: bool = False
    delivery_instructions: str | None
    address_type: str  # "residential" | "commercial"
    created_at: datetime
```

### 2.3 PaymentMethod
```python
class PaymentMethod(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    type: str  # "credit_card" | "debit_card" | "gift_card" | "caveat_shop_store_card"
    card_number_last4: str
    card_brand: str  # "visa" | "mastercard" | "amex" | "discover"
    expiry_month: int
    expiry_year: int
    cardholder_name: str
    billing_address_id: int (foreign key -> Address)
    is_default: bool = False
    created_at: datetime
```

### 2.4 Department
```python
class Department(SQLModel, table=True):
    id: int (primary key)
    name: str  # "Electronics", "Clothing", etc.
    slug: str (unique)
    description: str | None
    image_url: str | None
    parent_id: int | None (foreign key -> Department, for hierarchy)
    display_order: int
    is_active: bool = True
```

### 2.5 Category
```python
class Category(SQLModel, table=True):
    id: int (primary key)
    department_id: int (foreign key -> Department)
    name: str
    slug: str
    description: str | None
    image_url: str | None
    parent_id: int | None (foreign key -> Category, for hierarchy)
    display_order: int
    is_active: bool = True
```

### 2.6 Brand
```python
class Brand(SQLModel, table=True):
    id: int (primary key)
    name: str (unique)
    slug: str (unique)
    logo_url: str | None
    description: str | None
    store_url: str | None
    is_verified: bool = False
```

### 2.7 Seller
```python
class Seller(SQLModel, table=True):
    id: int (primary key)
    name: str
    slug: str (unique)
    description: str | None
    logo_url: str | None
    rating: float  # 0.0 - 5.0
    rating_count: int
    is_caveat_shop: bool = False  # True for CAVEAT-Shop's own listings
    feedback_percentage: float  # Positive feedback %
    ships_from: str
    return_policy: str
    created_at: datetime
```

### 2.8 Product
```python
class Product(SQLModel, table=True):
    id: int (primary key)
    asin: str (unique)  # CAVEAT-Shop Standard Identification Number
    title: str
    slug: str
    brand_id: int (foreign key -> Brand)
    category_id: int (foreign key -> Category)
    seller_id: int (foreign key -> Seller)

    # Pricing
    price: float
    list_price: float | None  # Original price if on sale
    currency: str = "USD"

    # Description
    description_html: str
    bullet_points: str  # JSON array of feature bullets

    # Inventory
    stock_quantity: int
    availability_status: str  # "in_stock" | "low_stock" | "out_of_stock" | "preorder"
    max_order_quantity: int = 30

    # Media
    images: str  # JSON array of image URLs
    videos: str | None  # JSON array of video URLs

    # Ratings
    rating: float  # 0.0 - 5.0
    rating_count: int
    review_count: int

    # Badges
    is_best_seller: bool = False
    best_seller_rank: int | None
    best_seller_category: str | None
    is_caveat_shop_choice: bool = False
    caveat_shop_choice_keyword: str | None
    is_prime_eligible: bool = True
    is_climate_pledge: bool = False

    # Shipping
    weight_pounds: float | None
    dimensions: str | None  # JSON: {length, width, height}
    shipping_weight: float | None
    ships_from: str = "CAVEAT-Shop"

    # Technical Details
    technical_details: str  # JSON object of specs

    # Stats
    bought_past_month: int = 0

    created_at: datetime
    updated_at: datetime
```

### 2.9 ProductVariant
```python
class ProductVariant(SQLModel, table=True):
    id: int (primary key)
    product_id: int (foreign key -> Product)
    variant_type: str  # "color" | "size" | "style" | "configuration"
    variant_value: str  # "Blue" | "Large" | "64GB"
    sku: str (unique)
    price: float
    stock_quantity: int
    images: str  # JSON array of variant-specific images
    is_available: bool = True
```

### 2.10 ProductImage
```python
class ProductImage(SQLModel, table=True):
    id: int (primary key)
    product_id: int (foreign key -> Product)
    url: str
    alt_text: str
    is_primary: bool = False
    display_order: int
    type: str  # "main" | "variant" | "lifestyle" | "size_chart"
```

### 2.11 Cart
```python
class Cart(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User, nullable for guest)
    session_id: str | None  # For guest carts
    created_at: datetime
    updated_at: datetime
```

### 2.12 CartItem
```python
class CartItem(SQLModel, table=True):
    id: int (primary key)
    cart_id: int (foreign key -> Cart)
    product_id: int (foreign key -> Product)
    variant_id: int | None (foreign key -> ProductVariant)
    quantity: int
    is_gift: bool = False
    gift_message: str | None
    saved_for_later: bool = False
    added_at: datetime
```

### 2.13 Order
```python
class Order(SQLModel, table=True):
    id: int (primary key)
    order_number: str (unique)  # Format: 111-1234567-1234567
    user_id: int (foreign key -> User)

    # Addresses
    shipping_address_id: int (foreign key -> Address)
    billing_address_id: int (foreign key -> Address)

    # Payment
    payment_method_id: int (foreign key -> PaymentMethod)

    # Totals
    subtotal: float
    shipping_cost: float
    tax: float
    discount: float
    total: float
    currency: str = "USD"

    # Status
    status: str  # "pending" | "processing" | "shipped" | "delivered" | "cancelled" | "returned"

    # Gift
    is_gift: bool = False
    gift_message: str | None

    # Timestamps
    placed_at: datetime
    shipped_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None

    # Shipping
    shipping_method: str  # "standard" | "expedited" | "priority" | "same_day"
    estimated_delivery_start: date
    estimated_delivery_end: date

    # Promo
    promo_code: str | None
    promo_discount: float = 0
```

### 2.14 OrderItem
```python
class OrderItem(SQLModel, table=True):
    id: int (primary key)
    order_id: int (foreign key -> Order)
    product_id: int (foreign key -> Product)
    variant_id: int | None (foreign key -> ProductVariant)
    seller_id: int (foreign key -> Seller)

    quantity: int
    unit_price: float
    total_price: float

    # Status per item (can differ from order)
    status: str  # "pending" | "shipped" | "delivered" | "returned"

    # Tracking
    tracking_number: str | None
    carrier: str | None  # "UPS" | "USPS" | "FedEx" | "CAVEAT-Shop Logistics"

    # Return
    is_returnable: bool = True
    return_deadline: date | None
    return_status: str | None  # "requested" | "approved" | "shipped" | "received" | "refunded"
```

### 2.15 Review
```python
class Review(SQLModel, table=True):
    id: int (primary key)
    product_id: int (foreign key -> Product)
    user_id: int (foreign key -> User)
    order_item_id: int | None (foreign key -> OrderItem)  # For verified purchase

    rating: int  # 1-5
    title: str
    body: str

    # Verification
    is_verified_purchase: bool = False

    # Media
    images: str | None  # JSON array of image URLs
    videos: str | None  # JSON array of video URLs

    # Helpfulness
    helpful_votes: int = 0
    total_votes: int = 0

    # Location
    review_country: str = "United States"

    # Status
    status: str = "published"  # "pending" | "published" | "rejected"

    created_at: datetime
    updated_at: datetime
```

### 2.16 ReviewVote
```python
class ReviewVote(SQLModel, table=True):
    id: int (primary key)
    review_id: int (foreign key -> Review)
    user_id: int (foreign key -> User)
    is_helpful: bool
    created_at: datetime
```

### 2.17 Question
```python
class Question(SQLModel, table=True):
    id: int (primary key)
    product_id: int (foreign key -> Product)
    user_id: int (foreign key -> User)
    question_text: str
    answer_count: int = 0
    created_at: datetime
```

### 2.18 Answer
```python
class Answer(SQLModel, table=True):
    id: int (primary key)
    question_id: int (foreign key -> Question)
    user_id: int (foreign key -> User)
    answer_text: str
    is_seller_answer: bool = False
    helpful_votes: int = 0
    created_at: datetime
```

### 2.19 Wishlist
```python
class Wishlist(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    name: str  # "Wish List", "Shopping List", custom names
    is_default: bool = False
    is_public: bool = False
    description: str | None
    created_at: datetime
    updated_at: datetime
```

### 2.20 WishlistItem
```python
class WishlistItem(SQLModel, table=True):
    id: int (primary key)
    wishlist_id: int (foreign key -> Wishlist)
    product_id: int (foreign key -> Product)
    variant_id: int | None (foreign key -> ProductVariant)
    quantity_desired: int = 1
    quantity_received: int = 0
    priority: str = "medium"  # "highest" | "high" | "medium" | "low" | "lowest"
    comment: str | None
    price_when_added: float
    added_at: datetime
```

### 2.21 BrowsingHistory
```python
class BrowsingHistory(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    product_id: int (foreign key -> Product)
    viewed_at: datetime
```

### 2.22 SearchHistory
```python
class SearchHistory(SQLModel, table=True):
    id: int (primary key)
    user_id: int | None (foreign key -> User)
    session_id: str | None
    query: str
    department_filter: str | None
    results_count: int
    searched_at: datetime
```

### 2.23 Deal
```python
class Deal(SQLModel, table=True):
    id: int (primary key)
    product_id: int (foreign key -> Product)
    deal_type: str  # "lightning" | "deal_of_day" | "coupon" | "prime_early" | "subscribe_save"
    discount_percentage: float
    deal_price: float
    original_price: float

    # For lightning deals
    start_time: datetime
    end_time: datetime
    claimed_percentage: float = 0
    max_claims: int | None
    current_claims: int = 0

    # For coupons
    coupon_code: str | None
    coupon_terms: str | None

    is_active: bool = True
    is_prime_exclusive: bool = False
```

### 2.24 Coupon
```python
class Coupon(SQLModel, table=True):
    id: int (primary key)
    code: str (unique)
    description: str
    discount_type: str  # "percentage" | "fixed"
    discount_value: float
    min_order_amount: float | None
    max_discount: float | None
    valid_from: datetime
    valid_until: datetime
    usage_limit: int | None
    usage_count: int = 0
    is_active: bool = True
```

### 2.25 Subscription (Subscribe & Save)
```python
class Subscription(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    product_id: int (foreign key -> Product)
    variant_id: int | None (foreign key -> ProductVariant)
    quantity: int
    frequency_months: int  # 1, 2, 3, 4, 5, 6
    discount_percentage: float  # Usually 5-15%
    next_delivery_date: date
    shipping_address_id: int (foreign key -> Address)
    payment_method_id: int (foreign key -> PaymentMethod)
    status: str  # "active" | "paused" | "cancelled"
    created_at: datetime
```

### 2.26 Notification
```python
class Notification(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    type: str  # "order_update" | "deal_alert" | "price_drop" | "back_in_stock" | "delivery"
    title: str
    message: str
    link: str | None
    is_read: bool = False
    created_at: datetime
```

### 2.27 PriceWatch
```python
class PriceWatch(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    product_id: int (foreign key -> Product)
    target_price: float | None  # Alert when price drops below this
    created_at: datetime
```

### 2.28 RecentlyViewed
```python
class RecentlyViewed(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    product_id: int (foreign key -> Product)
    viewed_at: datetime
```

### 2.29 AlexaShoppingList
```python
class AlexaShoppingList(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    item_name: str
    quantity: int = 1
    is_completed: bool = False
    added_via: str = "alexa"  # "alexa" | "web" | "app"
    created_at: datetime
    completed_at: datetime | None
```

### 2.30 GiftCard
```python
class GiftCard(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    code: str (unique)
    original_amount: float
    current_balance: float
    currency: str = "USD"
    redeemed_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
```

### 2.31 GiftCardTransaction
```python
class GiftCardTransaction(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    amount: float
    type: str  # "redemption" | "reload" | "purchase" | "refund"
    order_id: int | None (foreign key -> Order)
    balance_after: float
    created_at: datetime
```

### 2.32 Registry
```python
class Registry(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    type: str  # "wedding" | "baby" | "birthday" | "custom"
    name: str
    event_date: date | None
    is_public: bool = True
    shipping_address_id: int (foreign key -> Address)
    created_at: datetime
```

### 2.33 RegistryItem
```python
class RegistryItem(SQLModel, table=True):
    id: int (primary key)
    registry_id: int (foreign key -> Registry)
    product_id: int (foreign key -> Product)
    quantity_desired: int = 1
    quantity_purchased: int = 0
    priority: str = "medium"
    added_at: datetime
```

### 2.34 Message
```python
class Message(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    sender_type: str  # "caveat_shop" | "seller" | "system"
    sender_id: int | None  # Seller ID if from seller
    subject: str
    body: str
    related_order_id: int | None (foreign key -> Order)
    is_read: bool = False
    created_at: datetime
```

### 2.35 ProductRecall
```python
class ProductRecall(SQLModel, table=True):
    id: int (primary key)
    product_id: int (foreign key -> Product)
    title: str
    description: str
    severity: str  # "high" | "medium" | "low"
    action_required: str
    recall_date: date
    is_active: bool = True
```

### 2.36 UserRecallAlert
```python
class UserRecallAlert(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    recall_id: int (foreign key -> ProductRecall)
    order_item_id: int (foreign key -> OrderItem)
    is_acknowledged: bool = False
    notified_at: datetime
```

### 2.37 ShoppingPreference
```python
class ShoppingPreference(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User, unique)
    language: str = "en_US"
    currency: str = "USD"
    country: str = "US"
    personalized_ads: bool = True
    browsing_history_enabled: bool = True
    recommendations_enabled: bool = True
    email_preferences: str  # JSON object of email opt-ins
    updated_at: datetime
```

### 2.38 CaveatShopCreditCard
```python
class CaveatShopCreditCard(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    card_type: str  # "store_card" | "prime_visa" | "secured"
    card_number_last4: str
    rewards_balance: float = 0
    cashback_rate: float  # 5% for Prime Visa on CAVEAT-Shop
    is_primary: bool = False
    opened_at: datetime
```

### 2.39 MusicLibrary
```python
class MusicLibrary(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)
    track_id: str
    title: str
    artist: str
    album: str | None
    album_art_url: str | None
    duration_seconds: int
    is_purchased: bool = False
    is_uploaded: bool = False  # User-uploaded music
    added_at: datetime
```

### 2.40 BusinessAccount
```python
class BusinessAccount(SQLModel, table=True):
    id: int (primary key)
    user_id: int (foreign key -> User)  # Admin user
    business_name: str
    business_type: str  # "sole_proprietor" | "llc" | "corporation" | "nonprofit"
    tax_id: str | None
    is_business_prime: bool = False
    created_at: datetime
```

### 2.41 BusinessAccountUser
```python
class BusinessAccountUser(SQLModel, table=True):
    id: int (primary key)
    business_account_id: int (foreign key -> BusinessAccount)
    user_id: int (foreign key -> User)
    role: str  # "admin" | "buyer" | "viewer"
    spending_limit: float | None
    added_at: datetime
```

---

## 3. API Endpoints

### 3.1 Products
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/products` | List products with filters |
| GET | `/api/products/:id` | Get product details |
| GET | `/api/products/:id/variants` | Get product variants |
| GET | `/api/products/:id/reviews` | Get product reviews |
| GET | `/api/products/:id/questions` | Get product Q&A |
| GET | `/api/products/:id/related` | Get related products |
| GET | `/api/products/:id/frequently-bought` | Get frequently bought together |
| GET | `/api/products/:id/similar` | Get similar products |
| GET | `/api/products/best-sellers` | Get best sellers |
| GET | `/api/products/new-releases` | Get new releases |
| GET | `/api/products/movers-shakers` | Get movers and shakers |

**GET /api/products Query Parameters:**
- `q`: Search query
- `department`: Department slug
- `category`: Category slug
- `brand`: Brand slug(s), comma-separated
- `min_price`: Minimum price
- `max_price`: Maximum price
- `min_rating`: Minimum rating (1-5)
- `prime`: Prime eligible only (boolean)
- `deals`: Deals only (boolean)
- `condition`: new | renewed | used
- `sort`: featured | price_asc | price_desc | rating | newest | best_selling
- `page`: Page number
- `limit`: Items per page (default 48)

### 3.2 Search
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/search` | Full-text product search |
| GET | `/api/search/suggestions` | Search autocomplete |
| GET | `/api/search/filters` | Get available filters for query |
| POST | `/api/search/history` | Save search to history |
| GET | `/api/search/history` | Get user's search history |
| DELETE | `/api/search/history` | Clear search history |

### 3.3 Categories & Departments
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/departments` | List all departments |
| GET | `/api/departments/:slug` | Get department details |
| GET | `/api/departments/:slug/categories` | Get department categories |
| GET | `/api/categories` | List all categories |
| GET | `/api/categories/:slug` | Get category details |
| GET | `/api/categories/:slug/products` | Get category products |
| GET | `/api/categories/:slug/subcategories` | Get subcategories |

### 3.4 Cart
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/cart` | Get current cart |
| POST | `/api/cart/items` | Add item to cart |
| PUT | `/api/cart/items/:id` | Update cart item quantity |
| DELETE | `/api/cart/items/:id` | Remove item from cart |
| POST | `/api/cart/items/:id/save-for-later` | Move to saved items |
| POST | `/api/cart/items/:id/move-to-cart` | Move saved item to cart |
| DELETE | `/api/cart` | Clear entire cart |
| POST | `/api/cart/apply-coupon` | Apply coupon code |
| DELETE | `/api/cart/coupon` | Remove coupon |

### 3.5 Checkout
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/checkout/start` | Initialize checkout session |
| PUT | `/api/checkout/shipping` | Set shipping address |
| PUT | `/api/checkout/payment` | Set payment method |
| GET | `/api/checkout/summary` | Get order summary |
| POST | `/api/checkout/place-order` | Place the order |
| GET | `/api/checkout/shipping-options` | Get shipping options |
| POST | `/api/checkout/validate` | Validate checkout data |

### 3.6 Orders
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orders` | List user's orders |
| GET | `/api/orders/:id` | Get order details |
| GET | `/api/orders/:id/tracking` | Get tracking info |
| POST | `/api/orders/:id/cancel` | Cancel order |
| POST | `/api/orders/:order_id/items/:item_id/return` | Initiate return |
| GET | `/api/orders/:id/invoice` | Get invoice PDF |
| POST | `/api/orders/:id/archive` | Archive order |
| GET | `/api/orders/archived` | Get archived orders |

**GET /api/orders Query Parameters:**
- `status`: Filter by status
- `period`: 3months | 6months | year | all
- `q`: Search orders

### 3.7 Reviews
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/reviews` | Get user's reviews |
| POST | `/api/products/:id/reviews` | Create review |
| PUT | `/api/reviews/:id` | Update review |
| DELETE | `/api/reviews/:id` | Delete review |
| POST | `/api/reviews/:id/vote` | Vote helpful/not helpful |
| POST | `/api/reviews/:id/report` | Report review |
| GET | `/api/products/:id/reviews/summary` | Get review summary stats |

**GET /api/products/:id/reviews Query Parameters:**
- `rating`: Filter by stars (1-5)
- `verified`: Verified purchase only
- `sort`: recent | helpful | rating_high | rating_low
- `search`: Search within reviews

### 3.8 Questions & Answers
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/products/:id/questions` | Get product questions |
| POST | `/api/products/:id/questions` | Ask question |
| POST | `/api/questions/:id/answers` | Answer question |
| POST | `/api/answers/:id/vote` | Vote on answer |

### 3.9 Wishlists
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/wishlists` | Get user's wishlists |
| POST | `/api/wishlists` | Create wishlist |
| GET | `/api/wishlists/:id` | Get wishlist |
| PUT | `/api/wishlists/:id` | Update wishlist |
| DELETE | `/api/wishlists/:id` | Delete wishlist |
| POST | `/api/wishlists/:id/items` | Add item to wishlist |
| PUT | `/api/wishlists/:id/items/:item_id` | Update wishlist item |
| DELETE | `/api/wishlists/:id/items/:item_id` | Remove from wishlist |
| POST | `/api/wishlists/:id/items/:item_id/move` | Move to another list |
| GET | `/api/wishlists/:id/share` | Get shareable link |

### 3.10 User Account
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/user` | Get current user |
| PUT | `/api/user` | Update user profile |
| PUT | `/api/user/password` | Change password |
| GET | `/api/user/addresses` | Get addresses |
| POST | `/api/user/addresses` | Add address |
| PUT | `/api/user/addresses/:id` | Update address |
| DELETE | `/api/user/addresses/:id` | Delete address |
| GET | `/api/user/payment-methods` | Get payment methods |
| POST | `/api/user/payment-methods` | Add payment method |
| DELETE | `/api/user/payment-methods/:id` | Remove payment method |
| GET | `/api/user/prime` | Get Prime status |
| GET | `/api/user/notifications` | Get notifications |
| PUT | `/api/user/notifications/:id/read` | Mark notification read |

### 3.11 Browsing History
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/history` | Get browsing history |
| DELETE | `/api/history` | Clear all history |
| DELETE | `/api/history/:product_id` | Remove item from history |
| POST | `/api/history/:product_id` | Add to browsing history |

### 3.12 Recommendations
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/recommendations` | Get personalized recommendations |
| GET | `/api/recommendations/deals` | Get deal recommendations |
| GET | `/api/recommendations/buy-again` | Get buy again suggestions |
| GET | `/api/recommendations/inspired-by` | Get inspired by history |

### 3.13 Deals
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/deals` | List all deals |
| GET | `/api/deals/lightning` | Get lightning deals |
| GET | `/api/deals/today` | Get deal of the day |
| GET | `/api/deals/coupons` | Get clippable coupons |
| POST | `/api/deals/:id/claim` | Claim a deal |
| POST | `/api/coupons/:id/clip` | Clip a coupon |

**GET /api/deals Query Parameters:**
- `department`: Filter by department
- `discount_min`: Minimum discount %
- `type`: lightning | deal_of_day | coupon
- `prime`: Prime exclusive only

### 3.14 Sellers
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/sellers/:id` | Get seller profile |
| GET | `/api/sellers/:id/products` | Get seller's products |
| GET | `/api/sellers/:id/reviews` | Get seller feedback |

### 3.15 Subscriptions
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/subscriptions` | Get user's subscriptions |
| POST | `/api/subscriptions` | Create subscription |
| PUT | `/api/subscriptions/:id` | Update subscription |
| POST | `/api/subscriptions/:id/skip` | Skip next delivery |
| DELETE | `/api/subscriptions/:id` | Cancel subscription |

### 3.16 Price Watch
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/price-watch` | Get watched products |
| POST | `/api/price-watch` | Add product to watch |
| DELETE | `/api/price-watch/:id` | Remove from watch |

### 3.17 Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Create account |
| POST | `/api/auth/login` | Sign in with email/password |
| POST | `/api/auth/logout` | Sign out current session |
| POST | `/api/auth/logout-all` | Sign out all devices |
| GET | `/api/auth/me` | Get current authenticated user |
| GET | `/api/auth/sessions` | Get active sessions |
| DELETE | `/api/auth/sessions/:id` | Revoke specific session |

### 3.18 Buy Again
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/buy-again` | Get previously purchased products |
| GET | `/api/buy-again/subscribe-eligible` | Get Subscribe & Save eligible repurchases |

### 3.19 Gift Cards
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/gift-cards` | Get user's gift cards |
| GET | `/api/gift-cards/balance` | Get total gift card balance |
| POST | `/api/gift-cards/redeem` | Redeem gift card code |
| POST | `/api/gift-cards/reload` | Reload gift card balance |
| GET | `/api/gift-cards/transactions` | Get gift card transaction history |

### 3.20 Alexa Shopping List
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/alexa-list` | Get Alexa shopping list |
| POST | `/api/alexa-list` | Add item to list |
| PUT | `/api/alexa-list/:id` | Update list item |
| DELETE | `/api/alexa-list/:id` | Remove from list |
| POST | `/api/alexa-list/:id/complete` | Mark item as complete |
| POST | `/api/alexa-list/:id/add-to-cart` | Add item to cart |

### 3.21 Messages
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/messages` | Get all messages |
| GET | `/api/messages/:id` | Get message details |
| PUT | `/api/messages/:id/read` | Mark message as read |
| DELETE | `/api/messages/:id` | Delete message |
| POST | `/api/messages/reply` | Reply to seller message |

### 3.22 Recalls & Safety
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/recalls` | Get user's recall alerts |
| GET | `/api/recalls/:id` | Get recall details |
| POST | `/api/recalls/:id/acknowledge` | Acknowledge recall alert |

### 3.23 Shopping Preferences
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/preferences` | Get shopping preferences |
| PUT | `/api/preferences` | Update preferences |
| PUT | `/api/preferences/personalization` | Toggle personalization settings |

### 3.24 CAVEAT-Shop Credit Cards
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/credit-cards` | Get CAVEAT-Shop credit cards |
| GET | `/api/credit-cards/:id/rewards` | Get rewards balance |
| GET | `/api/credit-cards/:id/transactions` | Get card transactions |

### 3.25 Music Library
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/music/library` | Get music library |
| GET | `/api/music/purchases` | Get purchased music |
| GET | `/api/music/uploads` | Get uploaded music |
| POST | `/api/music/uploads` | Upload music |
| DELETE | `/api/music/uploads/:id` | Delete uploaded track |

### 3.26 Registries
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/registries` | Get user's registries |
| POST | `/api/registries` | Create registry |
| GET | `/api/registries/:id` | Get registry details |
| PUT | `/api/registries/:id` | Update registry |
| DELETE | `/api/registries/:id` | Delete registry |
| POST | `/api/registries/:id/items` | Add item to registry |
| DELETE | `/api/registries/:id/items/:item_id` | Remove from registry |
| GET | `/api/registries/search` | Find a registry (public) |

### 3.27 Business Account
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/business` | Get business account |
| POST | `/api/business` | Create business account |
| PUT | `/api/business` | Update business account |
| GET | `/api/business/users` | Get business account users |
| POST | `/api/business/users` | Add user to business account |
| PUT | `/api/business/users/:id` | Update user role/limits |
| DELETE | `/api/business/users/:id` | Remove user from account |

---

## 4. Frontend Routes

| Route | Component | Description |
|-------|-----------|-------------|
| `/` | Home | Homepage with carousels |
| `/s` | SearchResults | Search results page |
| `/dp/:asin` | ProductDetail | Product detail page |
| `/gp/cart` | Cart | Shopping cart |
| `/gp/buy/spc` | Checkout | Checkout flow |
| `/gp/css/order-history` | Orders | Order history |
| `/gp/your-account/order-details/:id` | OrderDetail | Order details |
| `/hz/wishlist` | WishlistsOverview | All wishlists |
| `/hz/wishlist/:id` | WishlistDetail | Single wishlist |
| `/gp/yourstore` | YourCaveatShop | Personalized page |
| `/gp/history` | BrowsingHistory | Browsing history |
| `/gp/goldbox` | Deals | Today's deals |
| `/deals` | DealsPage | All deals |
| `/gp/prime` | Prime | Prime benefits |
| `/gp/css/account` | AccountHub | Account home |
| `/gp/css/account/address` | Addresses | Manage addresses |
| `/gp/css/account/payment` | PaymentMethods | Manage payments |
| `/gp/css/account/orders` | Orders | Order history |
| `/gp/css/account/security` | Security | Login & security |
| `/gp/css/account/subscriptions` | Subscriptions | Subscribe & Save |
| `/b/:department` | DepartmentPage | Department browse |
| `/b/:department/:category` | CategoryPage | Category browse |
| `/sp/:seller_id` | SellerProfile | Seller storefront |
| `/review/create/:asin` | CreateReview | Write a review |
| `/ap/signin` | SignIn | Sign in page |
| `/ap/register` | Register | Create account |
| `/gp/help/customer` | Help | Customer service |
| `/gp/returns` | Returns | Return center |
| `/gp/buyagain` | BuyAgain | Previously purchased products |
| `/gp/yourstore/iyr` | KeepShopping | Keep shopping for recommendations |
| `/gp/yourstore/ref` | Recommendations | Personalized recommendations |
| `/gp/css/account/preferences` | ShoppingPreferences | Shopping preferences |
| `/gp/cobrandcard` | CaveatShopCreditCards | CAVEAT-Shop credit card options |
| `/gp/css/account/recalls` | RecallsPage | Product safety recalls |
| `/gp/css/account/messages` | Messages | Message center |
| `/gp/music/library` | MusicLibrary | Music library |
| `/gp/gc/balance` | GiftCardBalance | Gift card balance |
| `/gift-cards` | GiftCards | Gift cards hub |
| `/registries` | Registries | Your registries |
| `/registries/:id` | RegistryDetail | Single registry view |
| `/registries/search` | RegistrySearch | Find a registry |
| `/gp/css/account/business` | BusinessAccount | Business account |
| `/business/register` | BusinessRegister | Create business account |
| `/alexa/lists` | AlexaLists | Alexa shopping lists |
| `/gp/pharmacy` | Pharmacy | CAVEAT-Shop Pharmacy |
| `/gp/seller-account` | SellerAccount | Seller central |
| `/gp/css/account/archived` | ArchivedOrders | Archived orders |
| `/alexa` | AllThingsAlexa | All things Alexa hub |

---

## 5. Color Palette (CAVEAT-Shop Accurate)

### 5.1 Light Mode (Default)
```css
/* Primary Brand Colors */
--caveat_shop-orange: #FF9900
--caveat_shop-orange-hover: #FA8900
--caveat_shop-blue: #232F3E
--caveat_shop-blue-light: #37475A
--caveat_shop-teal: #007185
--caveat_shop-teal-hover: #C7511F

/* UI Colors */
--background: #FFFFFF
--background-secondary: #F7F8F8
--background-tertiary: #EAEDED
--surface: #FFFFFF
--border: #D5D9D9
--border-strong: #888C8C
--text-primary: #0F1111
--text-secondary: #565959
--text-muted: #767676

/* Interactive */
--link-color: #007185
--link-hover: #C7511F
--button-yellow: #FFD814
--button-yellow-hover: #F7CA00
--button-orange: #FFA41C
--button-orange-hover: #FA8900
--button-gray: #F0F2F2
--button-gray-hover: #E3E6E6

/* Status */
--success-color: #067D62
--success-bg: #F0FCF9
--error-color: #B12704
--error-bg: #FCF4F2
--warning-color: #C7511F
--warning-bg: #FFF8E6
--info-color: #007185

/* Price */
--price-red: #B12704
--price-whole: #0F1111
--price-savings: #CC0C39
--deal-red: #CC0C39

/* Ratings */
--star-filled: #FFA41C
--star-empty: #F0F0F0

/* Prime */
--prime-blue: #1A98FF
--prime-check: #067D62

/* Badges */
--bestseller-orange: #FF9900
--caveat_shop-choice-blue: #002F36
--climate-green: #067D62
```

### 5.2 Dark Mode
```css
/* Brand Colors (adjusted) */
--caveat_shop-orange: #FF9900
--caveat_shop-blue: #131921
--caveat_shop-blue-light: #232F3E
--caveat_shop-teal: #36C2CE

/* UI Colors */
--background: #131921
--background-secondary: #191F28
--background-tertiary: #232F3E
--surface: #232F3E
--border: #3D4F61
--border-strong: #566B7F
--text-primary: #E3E6E6
--text-secondary: #8D9096
--text-muted: #6B7280

/* Interactive */
--link-color: #36C2CE
--link-hover: #FF9900
--button-yellow: #FFD814
--button-orange: #FFA41C
```

---

## 6. Typography

```css
/* Font Family */
font-family: "CAVEAT-Shop Ember", Arial, sans-serif

/* Sizes */
--font-xs: 11px    /* Fine print, badges */
--font-sm: 12px    /* Captions, metadata */
--font-base: 13px  /* Body text */
--font-md: 14px    /* Standard UI text */
--font-lg: 16px    /* Headings */
--font-xl: 18px    /* Section headers */
--font-2xl: 21px   /* Page titles */
--font-3xl: 24px   /* Large titles */
--font-4xl: 28px   /* Hero text */

/* Price Typography */
--price-symbol: 13px
--price-whole: 28px
--price-fraction: 13px (superscript)

/* Weights */
--font-normal: 400
--font-medium: 500
--font-bold: 700
```

---

## 7. Icons

Use a combination of custom CAVEAT-Shop-style icons and Material Icons:

### Navigation
- `menu` - hamburger/all menu
- `search` - search
- `location_on` - delivery location pin
- `person` - account
- `shopping_cart` - cart
- `keyboard_arrow_down` - dropdown
- `chevron_left` / `chevron_right` - carousel navigation

### Product
- `star` / `star_half` / `star_border` - ratings
- `check_circle` - in stock / Prime check
- `local_shipping` - delivery
- `verified` - verified purchase
- `thumb_up` / `thumb_down` - review helpfulness
- `image` - product images
- `play_circle` - product video
- `zoom_in` - image zoom

### Actions
- `add_shopping_cart` - add to cart
- `favorite` / `favorite_border` - wishlist
- `share` - share product
- `compare_arrows` - compare
- `loop` - buy again
- `history` - browsing history
- `notifications` - alerts

### Orders
- `inventory_2` - package
- `local_shipping` - shipping
- `check_circle` - delivered
- `autorenew` - return
- `receipt` - invoice

### Account
- `lock` - security
- `credit_card` - payment
- `home` - address
- `card_membership` - Prime
- `settings` - settings

---

## 8. Key Interactions & Behaviors

### 8.1 Search
- **Autocomplete:** Suggestions appear after 2+ characters
- **Department filtering:** Narrow search to specific department
- **Recent searches:** Show recent queries (logged-in users)
- **Trending searches:** Popular searches
- **Submit:** Enter key or click search button

### 8.2 Product Images
- **Thumbnail hover:** Main image changes
- **Main image hover:** Zoom lens appears
- **Click:** Opens lightbox/expanded view
- **360 view:** Click and drag to rotate
- **Video:** Click to play inline

### 8.3 Add to Cart
- **Single click:** Adds with default quantity (1)
- **Quantity selector:** Update before adding
- **Variant required:** Prompt to select size/color
- **Out of stock:** Show "Sign up for availability alert"
- **Success feedback:** Side panel slides in showing cart

### 8.4 Cart Interactions
- **Quantity change:** Dropdown (1-10, 10+)
- **Delete:** Remove with confirmation
- **Save for later:** Move to saved section
- **Gift option:** Checkbox + message field
- **Proceed to checkout:** Yellow button

### 8.5 Checkout Flow
- **Address selection:** Radio buttons, default pre-selected
- **Payment selection:** Radio buttons with last 4 digits shown
- **Gift cards:** Input field with apply button
- **Place order:** Single-click purchase
- **Error handling:** Inline validation messages

### 8.6 Reviews
- **Star filter:** Click rating bar to filter
- **Helpful vote:** Single click, can change vote
- **Image preview:** Lightbox gallery
- **Verified badge:** Display for verified purchases
- **Sort:** Dropdown (most recent, most helpful)

### 8.7 Wishlist
- **Add:** Heart icon on product cards/detail
- **List selection:** Dropdown if multiple lists
- **Move to cart:** Button per item
- **Share:** Generate shareable link
- **Priority:** Dropdown per item

### 8.8 Real-time Updates
- **Cart count:** Updates immediately
- **Stock changes:** Warning if low/out while browsing
- **Price changes:** Alert if price changed in cart
- **Deal countdown:** Timer updates live

### 8.9 Keyboard Shortcuts
- `/` - Focus search
- `Esc` - Close modal/popover
- `Enter` - Submit forms
- Arrow keys - Navigate carousels

---

## 9. Responsive Breakpoints

```css
/* Desktop Large */
@media (min-width: 1500px) {
  /* Full layout, 6 products per row */
}

/* Desktop */
@media (min-width: 1200px) and (max-width: 1499px) {
  /* Standard layout, 5 products per row */
}

/* Tablet Landscape */
@media (min-width: 992px) and (max-width: 1199px) {
  /* Condensed sidebar, 4 products per row */
}

/* Tablet Portrait */
@media (min-width: 768px) and (max-width: 991px) {
  /* No sidebar filters (modal), 3 products per row */
}

/* Mobile */
@media (max-width: 767px) {
  /* Bottom nav, 2 products per row, hamburger menu */
}
```

---

## 10. Account Page Structure

### 10.1 Your Orders
- Order history with filters
- Track packages
- Return items
- Leave seller feedback
- Write product reviews
- Download invoices
- Archive orders

### 10.2 Login & Security
- **Name:** Edit your name (first and last)
- **Email:**
  - Current email displayed (partially masked)
  - "Edit" button to change email
  - Verification required for changes
- **Primary mobile number:**
  - Current number displayed (partially masked)
  - "Edit" button to add/change
  - Used for verification codes
- **Password:**
  - "Edit" button to change password
  - Requires current password
  - Password strength indicator
- **Compromised account?:**
  - "Start" button to secure account
  - Guided security checkup
- **Manage devices:**
  - View all signed-in devices
  - Device name, type, location, last activity
  - "Sign out" button per device
  - "Sign out of all devices" option
- **Login with CAVEAT-Shop:**
  - Manage third-party app permissions
  - Revoke app access
- **Where you're signed in:**
  - Active sessions list
  - Browser/app name and location
  - "Sign out" individual sessions

### 10.2.1 Sign-In Page (`/ap/signin`)
- **CAVEAT-Shop logo** (top center)
- **"Sign in" heading**
- **Email or mobile phone number field:**
  - Text input
  - "Continue" button (yellow)
- **After email entry - Password step:**
  - "[email]" shown with "Change" link
  - Password field with show/hide toggle
  - "Keep me signed in" checkbox with info tooltip
  - "Sign in" button (yellow)
  - "Forgot your password?" link
- **Alternative sign-in options:**
  - OTP (One-Time Password) option
- **CAPTCHA:** Shown after failed attempts
- **New to CAVEAT-Shop section:**
  - "Create your CAVEAT-Shop account" button (gray)
- **Footer links:**
  - Conditions of Use
  - Privacy Notice
  - Help

### 10.2.2 Create Account Page (`/ap/register`)
- **"Create account" heading**
- **Form fields:**
  - Your name (text input)
  - Mobile number or email (text input)
  - Password (with show/hide toggle, strength meter)
  - Re-enter password
- **"Continue" button** (yellow)
- **Legal text:** "By creating an account, you agree to CAVEAT-Shop's Conditions of Use and Privacy Notice"
- **Verification step:**
  - OTP sent to email/phone
  - 6-digit code input
  - "Verify" button
  - "Resend OTP" link
- **Already have an account?**
  - "Sign in" link

### 10.2.3 Forgot Password Flow
- **Step 1 - Enter email/phone:**
  - "Password assistance" heading
  - "Enter the email address or mobile phone number associated with your CAVEAT-Shop account"
  - Input field
  - "Continue" button
- **Step 2 - Verification:**
  - OTP sent to email/phone
  - "Enter OTP" input
  - "Continue" button
- **Step 3 - Reset password:**
  - "Create new password" heading
  - New password field
  - Re-enter password field
  - Password requirements displayed
  - "Save changes and sign in" button

### 10.2.4 Sign-Out
- **Sign out link locations:**
  - Account dropdown menu header
  - Account & Lists dropdown
  - Mobile hamburger menu bottom
  - Account settings page
- **Sign-out behavior:**
  - Clears session cookies
  - Redirects to homepage
  - Cart persists (merged on next sign-in)
  - Browsing history retained for recommendations
- **Sign out of all devices:**
  - Available in Login & Security
  - Requires password confirmation
  - Signs out all active sessions

### 10.3 Prime Membership
- Membership status
- Benefits overview
- Manage membership
- Share benefits
- Prime Video settings

### 10.4 Your Addresses
- Add/edit/delete addresses
- Set default shipping address
- Set default billing address
- 1-Click settings

### 10.5 Your Payments
- Manage payment methods
- Add credit/debit card
- CAVEAT-Shop Store Card
- Gift card balance
- Shop with Points
- Transactions

### 10.6 Your Messages
- Message center
- Buyer/seller messages
- Gift card notifications
- Delivery notifications

### 10.7 Digital Content
- Manage content and devices
- Kindle library
- Audible library
- Apps and games

### 10.8 Memberships & Subscriptions
- Prime membership
- Subscribe & Save
- Kindle Unlimited
- Audible membership

---

## 11. Empty States

### 11.1 Cart Empty
- **Icon:** Shopping cart illustration
- **Heading:** "Your CAVEAT-Shop Cart is empty"
- **Subtext:** "Shop today's deals" or "Sign in to see your items"
- **Action:** "Sign in" button, "Shop deals" link

### 11.2 Wishlist Empty
- **Icon:** Heart illustration
- **Heading:** "Your list is empty"
- **Subtext:** "Add items you'd like to shop for"
- **Action:** "Start shopping" button

### 11.3 Orders Empty
- **Icon:** Package illustration
- **Heading:** "Looks like you haven't placed an order"
- **Subtext:** "Once you do, you'll be able to track them here"
- **Action:** "Start shopping" button

### 11.4 Search No Results
- **Heading:** "No results for '[query]'"
- **Suggestions:**
  - Check spelling
  - Try more general words
  - Try different words
- **Related searches:** List of alternative queries

### 11.5 Browsing History Empty
- **Heading:** "Your browsing history is empty"
- **Subtext:** "Products you view will appear here"

---

## 12. Loading States

### 12.1 Skeleton Loaders
- **Product grid:** Gray boxes for image, lines for text
- **Product detail:** Skeleton for images, title, price
- **Cart:** Skeleton rows for items
- **Reviews:** Skeleton cards

### 12.2 Spinners
- **Page load:** CAVEAT-Shop smile logo animation
- **Button loading:** Spinner inside button
- **Add to cart:** Brief spinner then success
- **Checkout:** Full-page loading overlay

### 12.3 Progressive Loading
- **Images:** Low-res placeholder, then full image
- **Carousels:** Load visible items first
- **Infinite scroll:** Load more indicator

---

## 13. Error States

### 13.1 Network Error
- **Banner:** "Something went wrong. Please try again."
- **Button:** "Retry"

### 13.2 Out of Stock
- **Product card:** Grayed out, "Currently unavailable"
- **Detail page:** "We don't know when or if this item will be back in stock"
- **Option:** "Sign up for email alert"

### 13.3 Add to Cart Failed
- **Toast:** "Sorry, there was a problem adding this item to your cart"
- **Button:** "Try again"

### 13.4 Payment Failed
- **Inline:** "We could not process your payment"
- **Options:** Try another payment method, update card

### 13.5 Invalid Coupon
- **Inline:** "The promotional code you entered is not valid"
- **Suggestions:** Check code, see terms

### 13.6 404 Product Not Found
- **Page:** "Looking for something?"
- **Suggestions:** Search, browse categories

---

## 14. Notifications

### 14.1 Order Notifications
- Order placed confirmation
- Order shipped with tracking
- Out for delivery
- Delivered confirmation
- Return processed
- Refund issued

### 14.2 Deal Notifications
- Lightning deal starting
- Watched item on sale
- Price drop alert
- Back in stock alert

### 14.3 Account Notifications
- Security alerts
- Password changes
- Payment method expiring
- Prime benefits

### 14.4 In-App Toast Notifications
- **Position:** Bottom-center or top-right
- **Types:**
  - Success (green): "Added to cart"
  - Info (blue): "Item saved for later"
  - Warning (yellow): "Low stock - order soon"
  - Error (red): "Could not add to cart"

---

## 15. Accessibility

### 15.1 Keyboard Navigation
- Full keyboard accessibility
- Focus indicators on all interactive elements
- Skip to main content link
- Tab order follows visual order

### 15.2 Screen Reader Support
- ARIA labels on all buttons and icons
- Alt text for all images
- Live regions for cart updates
- Descriptive link text

### 15.3 Visual
- Minimum contrast ratio 4.5:1
- Focus visible states
- Color not sole indicator
- Scalable text (up to 200%)

### 15.4 Product Images
- Alt text describes product
- Zoom accessible via keyboard
- Video has captions

---

## 16. Database Seed Data

For realistic mockup, include:

### Products (200+ items across departments)
- **Electronics:** Phones, laptops, headphones, cameras, TVs
- **Home & Kitchen:** Appliances, furniture, cookware
- **Fashion:** Clothing, shoes, accessories
- **Books:** Fiction, non-fiction, textbooks
- **Beauty:** Skincare, makeup, haircare
- **Toys & Games:** Board games, action figures, puzzles
- **Sports:** Equipment, apparel, accessories
- **Grocery:** Pantry items, snacks, beverages

### Users
- 5+ test users with varied data
- Different Prime statuses
- Order histories
- Review histories

### Orders
- 50+ orders across statuses
- Various shipping methods
- Returns and refunds

### Reviews
- 500+ reviews across products
- Various ratings and lengths
- Helpful votes distributed
- Some with images/videos

### Categories
- Full department hierarchy
- 20+ departments
- 100+ categories

### Deals
- 20+ active lightning deals
- 10+ deal of the day
- 50+ coupons
- Various discount percentages

### Sellers
- CAVEAT-Shop (main seller)
- 10+ third-party sellers
- Various ratings and feedback

### User Profiles
- Multiple profiles per test user
- Kids profiles with restrictions
- Work/Home profiles

### Gift Cards
- Various balance amounts
- Transaction history
- Redeemed and unredeemed cards

### Digital Content
- Kindle books (purchased and Kindle Unlimited)
- Audible audiobooks
- Prime Video purchases and watchlist items
- Music library tracks

### Devices
- Kindle e-readers
- Fire tablets
- Echo devices
- Fire TV sticks

### Lists & Registries
- Multiple wishlists per user
- Alexa shopping list items
- Wedding and baby registries

### Messages
- Order-related messages
- Seller communications
- System notifications

### Subscriptions
- Active Subscribe & Save items
- Kindle Unlimited membership
- Prime membership

---

## 17. File Structure (Target)

```
caveat_shop/
├── backend/
│   ├── __init__.py
│   ├── app.py                  # FastAPI app
│   ├── database.py             # DB config
│   ├── models.py               # SQLModel models
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── products.py         # Product CRUD
│   │   ├── search.py           # Search functionality
│   │   ├── categories.py       # Categories & departments
│   │   ├── cart.py             # Cart operations
│   │   ├── checkout.py         # Checkout flow
│   │   ├── orders.py           # Order management
│   │   ├── reviews.py          # Reviews & ratings
│   │   ├── questions.py        # Q&A
│   │   ├── wishlists.py        # Wishlist management
│   │   ├── user.py             # User account
│   │   ├── profiles.py         # User profiles (multi-profile)
│   │   ├── addresses.py        # Address management
│   │   ├── payments.py         # Payment methods
│   │   ├── deals.py            # Deals & coupons
│   │   ├── recommendations.py  # Personalization
│   │   ├── history.py          # Browsing history
│   │   ├── sellers.py          # Seller profiles
│   │   ├── subscriptions.py    # Subscribe & Save
│   │   ├── buy_again.py        # Buy again functionality
│   │   ├── gift_cards.py       # Gift card management
│   │   ├── alexa_lists.py      # Alexa shopping lists
│   │   ├── video.py            # Prime Video watchlist/purchases
│   │   ├── kindle.py           # Kindle library
│   │   ├── audible.py          # Audible library
│   │   ├── devices.py          # Device management
│   │   ├── messages.py         # Message center
│   │   ├── recalls.py          # Product recalls
│   │   ├── preferences.py      # Shopping preferences
│   │   ├── credit_cards.py     # CAVEAT-Shop credit cards
│   │   ├── music.py            # Music library
│   │   ├── registries.py       # Gift registries
│   │   ├── business.py         # Business accounts
│   │   └── auth.py             # Authentication
│   ├── services/
│   │   ├── __init__.py
│   │   ├── search_service.py   # Full-text search
│   │   ├── recommendation_service.py  # ML recommendations
│   │   ├── pricing_service.py  # Price calculations
│   │   ├── inventory_service.py # Stock management
│   │   ├── shipping_service.py # Shipping calculations
│   │   └── notification_service.py # Notifications
│   ├── seed.py                 # Database seeding
│   └── client.py               # Python API client
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   │   ├── index.ts
│   │   │   ├── products.ts
│   │   │   ├── search.ts
│   │   │   ├── cart.ts
│   │   │   ├── checkout.ts
│   │   │   ├── orders.ts
│   │   │   ├── reviews.ts
│   │   │   ├── wishlists.ts
│   │   │   ├── user.ts
│   │   │   ├── profiles.ts
│   │   │   ├── deals.ts
│   │   │   ├── buyAgain.ts
│   │   │   ├── giftCards.ts
│   │   │   ├── alexaLists.ts
│   │   │   ├── video.ts
│   │   │   ├── kindle.ts
│   │   │   ├── audible.ts
│   │   │   ├── devices.ts
│   │   │   ├── messages.ts
│   │   │   ├── recalls.ts
│   │   │   ├── preferences.ts
│   │   │   ├── creditCards.ts
│   │   │   ├── music.ts
│   │   │   ├── registries.ts
│   │   │   ├── business.ts
│   │   │   └── auth.ts
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   │   ├── Header.tsx
│   │   │   │   ├── TopNav.tsx
│   │   │   │   ├── SubNav.tsx
│   │   │   │   ├── MegaMenu.tsx
│   │   │   │   ├── AccountDropdown.tsx   # Account & Lists dropdown
│   │   │   │   ├── BuyAgainColumn.tsx    # Buy again in dropdown
│   │   │   │   ├── YourListsColumn.tsx   # Lists in dropdown
│   │   │   │   ├── YourAccountColumn.tsx # Account links in dropdown
│   │   │   │   ├── ProfileSwitcher.tsx   # Profile selection
│   │   │   │   ├── Footer.tsx
│   │   │   │   └── Layout.tsx
│   │   │   ├── product/
│   │   │   │   ├── ProductCard.tsx
│   │   │   │   ├── ProductGrid.tsx
│   │   │   │   ├── ProductDetail.tsx
│   │   │   │   ├── ProductImages.tsx
│   │   │   │   ├── ProductInfo.tsx
│   │   │   │   ├── BuyBox.tsx
│   │   │   │   ├── VariantSelector.tsx
│   │   │   │   ├── FrequentlyBought.tsx
│   │   │   │   └── ProductCarousel.tsx
│   │   │   ├── search/
│   │   │   │   ├── SearchBar.tsx
│   │   │   │   ├── SearchResults.tsx
│   │   │   │   ├── SearchFilters.tsx
│   │   │   │   ├── SearchSuggestions.tsx
│   │   │   │   └── SearchSort.tsx
│   │   │   ├── cart/
│   │   │   │   ├── CartPage.tsx
│   │   │   │   ├── CartItem.tsx
│   │   │   │   ├── CartSummary.tsx
│   │   │   │   ├── CartSidePanel.tsx
│   │   │   │   └── SavedForLater.tsx
│   │   │   ├── checkout/
│   │   │   │   ├── CheckoutPage.tsx
│   │   │   │   ├── AddressStep.tsx
│   │   │   │   ├── PaymentStep.tsx
│   │   │   │   ├── ReviewStep.tsx
│   │   │   │   ├── OrderSummary.tsx
│   │   │   │   └── CheckoutProgress.tsx
│   │   │   ├── orders/
│   │   │   │   ├── OrdersPage.tsx
│   │   │   │   ├── OrderCard.tsx
│   │   │   │   ├── OrderDetail.tsx
│   │   │   │   ├── OrderTracking.tsx
│   │   │   │   └── ReturnFlow.tsx
│   │   │   ├── reviews/
│   │   │   │   ├── ReviewsList.tsx
│   │   │   │   ├── ReviewCard.tsx
│   │   │   │   ├── ReviewForm.tsx
│   │   │   │   ├── ReviewSummary.tsx
│   │   │   │   ├── ReviewFilters.tsx
│   │   │   │   └── StarRating.tsx
│   │   │   ├── wishlist/
│   │   │   │   ├── WishlistPage.tsx
│   │   │   │   ├── WishlistItem.tsx
│   │   │   │   ├── ListSelector.tsx
│   │   │   │   └── AddToListButton.tsx
│   │   │   ├── account/
│   │   │   │   ├── AccountHub.tsx
│   │   │   │   ├── AddressManager.tsx
│   │   │   │   ├── PaymentManager.tsx
│   │   │   │   ├── SecuritySettings.tsx
│   │   │   │   ├── PrimePage.tsx
│   │   │   │   ├── BuyAgainPage.tsx
│   │   │   │   ├── RecommendationsPage.tsx
│   │   │   │   ├── ShoppingPreferences.tsx
│   │   │   │   ├── CreditCardsPage.tsx
│   │   │   │   ├── RecallsPage.tsx
│   │   │   │   ├── MessagesPage.tsx
│   │   │   │   ├── MessageDetail.tsx
│   │   │   │   ├── SubscriptionsPage.tsx
│   │   │   │   ├── MembershipsPage.tsx
│   │   │   │   ├── ProfileManager.tsx
│   │   │   │   └── ArchivedOrders.tsx
│   │   │   ├── giftcards/
│   │   │   │   ├── GiftCardBalance.tsx
│   │   │   │   ├── GiftCardRedeem.tsx
│   │   │   │   ├── GiftCardReload.tsx
│   │   │   │   └── GiftCardHistory.tsx
│   │   │   ├── video/
│   │   │   │   ├── VideoWatchlist.tsx
│   │   │   │   ├── VideoLibrary.tsx
│   │   │   │   ├── VideoCard.tsx
│   │   │   │   └── RentalTimer.tsx
│   │   │   ├── kindle/
│   │   │   │   ├── KindleLibrary.tsx
│   │   │   │   ├── KindleBookCard.tsx
│   │   │   │   ├── KindleUnlimited.tsx
│   │   │   │   └── ReadingProgress.tsx
│   │   │   ├── audible/
│   │   │   │   ├── AudibleLibrary.tsx
│   │   │   │   ├── AudiobookCard.tsx
│   │   │   │   └── ListeningProgress.tsx
│   │   │   ├── devices/
│   │   │   │   ├── DevicesPage.tsx
│   │   │   │   ├── DeviceCard.tsx
│   │   │   │   └── DeviceSettings.tsx
│   │   │   ├── music/
│   │   │   │   ├── MusicLibrary.tsx
│   │   │   │   ├── TrackCard.tsx
│   │   │   │   └── MusicUploader.tsx
│   │   │   ├── registry/
│   │   │   │   ├── RegistriesPage.tsx
│   │   │   │   ├── RegistryDetail.tsx
│   │   │   │   ├── RegistryItem.tsx
│   │   │   │   ├── RegistrySearch.tsx
│   │   │   │   └── CreateRegistry.tsx
│   │   │   ├── alexa/
│   │   │   │   ├── AlexaLists.tsx
│   │   │   │   ├── AlexaListItem.tsx
│   │   │   │   └── AddToAlexaList.tsx
│   │   │   ├── business/
│   │   │   │   ├── BusinessAccountPage.tsx
│   │   │   │   ├── BusinessRegister.tsx
│   │   │   │   └── BusinessUsers.tsx
│   │   │   ├── deals/
│   │   │   │   ├── DealsPage.tsx
│   │   │   │   ├── DealCard.tsx
│   │   │   │   ├── LightningDeal.tsx
│   │   │   │   └── CouponCard.tsx
│   │   │   ├── home/
│   │   │   │   ├── HomePage.tsx
│   │   │   │   ├── HeroCarousel.tsx
│   │   │   │   ├── CategoryCards.tsx
│   │   │   │   └── RecommendationCarousel.tsx
│   │   │   ├── common/
│   │   │   │   ├── Button.tsx
│   │   │   │   ├── Badge.tsx
│   │   │   │   ├── Modal.tsx
│   │   │   │   ├── Dropdown.tsx
│   │   │   │   ├── Toast.tsx
│   │   │   │   ├── Skeleton.tsx
│   │   │   │   ├── Breadcrumb.tsx
│   │   │   │   ├── Pagination.tsx
│   │   │   │   ├── Price.tsx
│   │   │   │   ├── PrimeBadge.tsx
│   │   │   │   └── QuantitySelector.tsx
│   │   │   └── auth/
│   │   │       ├── SignInPage.tsx
│   │   │       ├── RegisterPage.tsx
│   │   │       └── AuthModal.tsx
│   │   ├── hooks/
│   │   │   ├── useProducts.ts
│   │   │   ├── useSearch.ts
│   │   │   ├── useCart.ts
│   │   │   ├── useCheckout.ts
│   │   │   ├── useOrders.ts
│   │   │   ├── useReviews.ts
│   │   │   ├── useWishlist.ts
│   │   │   ├── useUser.ts
│   │   │   ├── useProfiles.ts
│   │   │   ├── useDeals.ts
│   │   │   ├── useBuyAgain.ts
│   │   │   ├── useGiftCards.ts
│   │   │   ├── useVideo.ts
│   │   │   ├── useKindle.ts
│   │   │   ├── useAudible.ts
│   │   │   ├── useDevices.ts
│   │   │   ├── useMessages.ts
│   │   │   ├── useRegistries.ts
│   │   │   └── useAuth.ts
│   │   ├── store/
│   │   │   ├── index.ts
│   │   │   ├── cartSlice.ts
│   │   │   ├── userSlice.ts
│   │   │   ├── searchSlice.ts
│   │   │   └── uiSlice.ts
│   │   ├── pages/
│   │   │   ├── Home.tsx
│   │   │   ├── ProductDetail.tsx
│   │   │   ├── SearchResults.tsx
│   │   │   ├── Cart.tsx
│   │   │   ├── Checkout.tsx
│   │   │   ├── Orders.tsx
│   │   │   ├── OrderDetail.tsx
│   │   │   ├── Wishlists.tsx
│   │   │   ├── WishlistDetail.tsx
│   │   │   ├── Account.tsx
│   │   │   ├── Deals.tsx
│   │   │   ├── Department.tsx
│   │   │   ├── Category.tsx
│   │   │   ├── SellerProfile.tsx
│   │   │   ├── BuyAgain.tsx
│   │   │   ├── KeepShopping.tsx
│   │   │   ├── Recommendations.tsx
│   │   │   ├── BrowsingHistory.tsx
│   │   │   ├── GiftCards.tsx
│   │   │   ├── VideoWatchlist.tsx
│   │   │   ├── VideoLibrary.tsx
│   │   │   ├── KindleLibrary.tsx
│   │   │   ├── KindleUnlimited.tsx
│   │   │   ├── AudibleLibrary.tsx
│   │   │   ├── ContentLibrary.tsx
│   │   │   ├── Devices.tsx
│   │   │   ├── Messages.tsx
│   │   │   ├── MusicLibrary.tsx
│   │   │   ├── Registries.tsx
│   │   │   ├── RegistryDetail.tsx
│   │   │   ├── AlexaLists.tsx
│   │   │   ├── Profiles.tsx
│   │   │   ├── BusinessAccount.tsx
│   │   │   ├── Pharmacy.tsx
│   │   │   ├── SignIn.tsx
│   │   │   └── Register.tsx
│   │   ├── styles/
│   │   │   ├── caveat_shop.css
│   │   │   └── themes.css
│   │   ├── types/
│   │   │   └── index.ts
│   │   ├── utils/
│   │   │   ├── formatPrice.ts
│   │   │   ├── formatDate.ts
│   │   │   ├── calculateShipping.ts
│   │   │   └── constants.ts
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── public/
│   │   └── images/
│   ├── index.html
│   └── package.json
├── uploads/                    # Product images storage
├── CAVEAT_SHOP_SPEC.md
└── README.md
```

---

## 18. Implementation Priority

### Phase 1: Core Infrastructure
1. Data models and database setup
2. Product CRUD and category structure
3. Basic product listing and detail pages
4. Search with basic filters
5. Database seeding with sample products

### Phase 2: Shopping Flow
1. Shopping cart (add, update, remove)
2. Cart page with quantity management
3. Basic checkout flow
4. Order creation
5. Order history page

### Phase 3: Product Discovery
1. Department and category browsing
2. Advanced search filters
3. Sort functionality
4. Product carousels
5. Related products

### Phase 4: User Features
1. User authentication
2. Address management
3. Payment method management
4. Order tracking
5. Account hub

### Phase 5: Reviews & Ratings
1. Review display on product page
2. Review filtering and sorting
3. Create/edit reviews
4. Helpful votes
5. Review images
6. Q&A section

### Phase 6: Wishlists & Lists
1. Create wishlists
2. Add/remove items
3. List sharing
4. Move to cart
5. Multiple lists

### Phase 7: Deals & Promotions
1. Deals page
2. Lightning deals with countdown
3. Coupon system
4. Deal badges on products
5. Price drop alerts

### Phase 8: Advanced Features
1. Subscribe & Save
2. Browsing history
3. Personalized recommendations
4. Seller profiles
5. Real-time notifications

### Phase 9: Polish & Optimization
1. Image zoom and gallery
2. Responsive design refinement
3. Performance optimization
4. Loading states and skeletons
5. Error handling
6. Accessibility audit

### Phase 10: Testing & Documentation
1. Unit tests for API endpoints
2. Integration tests
3. Frontend component tests
4. E2E tests for critical flows
5. API documentation
