"""Test configuration and fixtures for Mercato API tests."""

import json
import pytest
from datetime import datetime, timedelta, date
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

from backend.app import create_app
from backend.database import get_session
from backend.models import (
    User,
    UserSession,
    Address,
    PaymentMethod,
    Department,
    Category,
    Brand,
    Seller,
    Product,
    ProductVariant,
    ProductImage,
    Cart,
    CartItem,
    Order,
    OrderItem,
    Review,
    Question,
    Answer,
    Wishlist,
    WishlistItem,
    BrowsingHistory,
    SearchHistory,
    Deal,
    Coupon,
    Subscription,
    Notification,
    PriceWatch,
    AlexaShoppingList,
    GiftCard,
    GiftCardTransaction,
    Registry,
    RegistryItem,
    Message,
    ShoppingPreference,
)


@pytest.fixture(name="engine")
def engine_fixture():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="session")
def session_fixture(engine):
    """Create a database session for testing."""
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    """Create a test client with empty database."""

    def get_session_override():
        return session

    app = create_app()
    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="seeded_session")
def seeded_session_fixture(session: Session):
    """Create a seeded database session with test data."""
    # Create user
    user = User(
        id=1,
        email="john.doe@example.com",
        password_hash="hashed_password",
        name="John Doe",
        phone="+1-555-123-4567",
        is_prime=True,
        prime_since=datetime.utcnow() - timedelta(days=365),
    )
    session.add(user)
    session.commit()

    # Create session token
    user_session = UserSession(
        user_id=1,
        session_token="test_session_token",
        device_type="desktop",
        device_name="Test Browser",
        ip_address="127.0.0.1",
        is_current=True,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    session.add(user_session)

    # Create addresses
    address = Address(
        id=1,
        user_id=1,
        full_name="John Doe",
        phone="+1-555-123-4567",
        address_line1="123 Main Street",
        city="San Francisco",
        state="CA",
        zip_code="94102",
        country="United States",
        is_default=True,
    )
    session.add(address)

    # Create payment method
    payment = PaymentMethod(
        id=1,
        user_id=1,
        type="credit_card",
        card_number_last4="4242",
        card_brand="visa",
        expiry_month=12,
        expiry_year=2027,
        cardholder_name="John Doe",
        billing_address_id=1,
        is_default=True,
    )
    session.add(payment)

    # Create department
    department = Department(
        id=1,
        name="Electronics",
        slug="electronics",
        display_order=1,
    )
    session.add(department)

    # Create category
    category = Category(
        id=1,
        department_id=1,
        name="Smartphones",
        slug="smartphones",
    )
    session.add(category)

    # Create brand
    brand = Brand(
        id=1,
        name="Apple",
        slug="apple",
        is_verified=True,
    )
    session.add(brand)

    # Create seller
    seller = Seller(
        id=1,
        name="Mercato.com",
        slug="Mercato",
        rating=4.9,
        rating_count=1000000,
        is_amazon=True,
    )
    session.add(seller)
    session.commit()

    # Create products
    product1 = Product(
        id=1,
        asin="B09V3KXJPB",
        title="Apple iPhone 14 Pro",
        slug="apple-iphone-14-pro",
        brand_id=1,
        category_id=1,
        seller_id=1,
        price=999.00,
        list_price=1099.00,
        description_html="<p>The ultimate iPhone.</p>",
        bullet_points=json.dumps(["Feature 1", "Feature 2"]),
        stock_quantity=50,
        rating=4.8,
        rating_count=12500,
        is_best_seller=True,
        is_prime_eligible=True,
    )
    session.add(product1)

    product2 = Product(
        id=2,
        asin="B09G9FPHY6",
        title="Sony Headphones",
        slug="sony-headphones",
        brand_id=1,
        category_id=1,
        seller_id=1,
        price=348.00,
        list_price=399.99,
        description_html="<p>Great headphones.</p>",
        bullet_points=json.dumps(["Noise canceling"]),
        stock_quantity=120,
        rating=4.7,
        rating_count=8900,
        is_prime_eligible=True,
    )
    session.add(product2)
    session.commit()

    # Create cart
    cart = Cart(id=1, user_id=1)
    session.add(cart)
    session.commit()

    # Create cart item
    cart_item = CartItem(
        id=1,
        cart_id=1,
        product_id=1,
        quantity=1,
    )
    session.add(cart_item)

    # Create wishlist
    wishlist = Wishlist(
        id=1,
        user_id=1,
        name="My Wish List",
        is_default=True,
    )
    session.add(wishlist)
    session.commit()

    # Create wishlist item
    wishlist_item = WishlistItem(
        id=1,
        wishlist_id=1,
        product_id=2,
        price_when_added=348.00,
    )
    session.add(wishlist_item)

    # Create order
    order = Order(
        id=1,
        order_number="112-3456789-0123456",
        user_id=1,
        shipping_address_id=1,
        billing_address_id=1,
        payment_method_id=1,
        subtotal=999.00,
        shipping_cost=0.00,
        tax=82.42,
        total=1081.42,
        status="delivered",
        placed_at=datetime.utcnow() - timedelta(days=30),
        delivered_at=datetime.utcnow() - timedelta(days=26),
    )
    session.add(order)
    session.commit()

    # Create order item
    order_item = OrderItem(
        id=1,
        order_id=1,
        product_id=1,
        seller_id=1,
        quantity=1,
        unit_price=999.00,
        total_price=999.00,
        status="delivered",
    )
    session.add(order_item)

    # Create review
    review = Review(
        id=1,
        product_id=1,
        user_id=1,
        order_item_id=1,
        rating=5,
        title="Great product!",
        body="I love this product, highly recommend it.",
        is_verified_purchase=True,
    )
    session.add(review)

    # Create notification
    notification = Notification(
        id=1,
        user_id=1,
        type="delivery",
        title="Package arriving",
        message="Your package is on the way.",
        is_read=False,
    )
    session.add(notification)

    # Create deal
    deal = Deal(
        id=1,
        product_id=2,
        deal_type="lightning",
        discount_percentage=13,
        deal_price=348.00,
        original_price=399.99,
        start_time=datetime.utcnow() - timedelta(hours=1),
        end_time=datetime.utcnow() + timedelta(hours=5),
    )
    session.add(deal)

    # Create coupon
    coupon = Coupon(
        id=1,
        code="SAVE10",
        description="Save $10",
        discount_type="fixed",
        discount_value=10.00,
        min_order_amount=50.00,
        valid_from=datetime.utcnow() - timedelta(days=7),
        valid_until=datetime.utcnow() + timedelta(days=30),
    )
    session.add(coupon)

    # Create subscription
    subscription = Subscription(
        id=1,
        user_id=1,
        product_id=1,
        quantity=1,
        frequency_months=1,
        next_delivery_date=date.today() + timedelta(days=30),
        shipping_address_id=1,
        payment_method_id=1,
        status="active",
    )
    session.add(subscription)

    # Create browsing history
    history = BrowsingHistory(
        id=1,
        user_id=1,
        product_id=1,
    )
    session.add(history)

    # Create search history
    search = SearchHistory(
        id=1,
        user_id=1,
        query="iphone",
        results_count=100,
    )
    session.add(search)

    # Create preferences
    prefs = ShoppingPreference(
        id=1,
        user_id=1,
        language="en_US",
        currency="USD",
        country="US",
    )
    session.add(prefs)

    session.commit()
    return session


@pytest.fixture(name="seeded_client")
def seeded_client_fixture(seeded_session: Session):
    """Create a test client with seeded database."""

    def get_session_override():
        return seeded_session

    app = create_app()
    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="auth_client")
def auth_client_fixture(seeded_session: Session):
    """Create a test client with authentication cookie set."""

    def get_session_override():
        return seeded_session

    app = create_app()
    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    client.cookies.set("session_token", "test_session_token")
    yield client
    app.dependency_overrides.clear()
