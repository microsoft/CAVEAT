import { useState, useEffect, useCallback } from 'react';
import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom';
import { api } from './api';
import type { User, Department, Cart } from './types';

// Components
import { Header } from './components/Header';
import { Footer } from './components/Footer';

// Pages
import { Home } from './pages/Home';
import { SearchResults } from './pages/SearchResults';
import { ProductDetail } from './pages/ProductDetail';
import { Cart as CartPage } from './pages/Cart';
import { AccountHub } from './pages/AccountHub';
import { Orders } from './pages/Orders';
import { SignIn, Register } from './pages/Auth';
import { Checkout } from './pages/Checkout';
import { OrderConfirmation } from './pages/OrderConfirmation';
import { Wishlists } from './pages/Wishlists';
import { WishlistDetail } from './pages/WishlistDetail';
import { BuyAgain } from './pages/BuyAgain';
import { OrderDetails } from './pages/OrderDetails';
import { CancelledOrders } from './pages/CancelledOrders';
import { GiftCards } from './pages/GiftCards';
import { BestSellersPage, NewReleasesPage, MoversShakersPage, TrendingPage } from './pages/ProductListPage';
import { Registries } from './pages/Registries';
import { RegistryDetail } from './pages/RegistryDetail';
import { RegistrySearch } from './pages/RegistrySearch';
import { Addresses } from './pages/Addresses';
import { Payments } from './pages/Payments';
import { Messages } from './pages/Messages';
import { CustomerService } from './pages/CustomerService';
import { Prime } from './pages/Prime';
import { LoginSecurity } from './pages/LoginSecurity';
import { Returns } from './pages/Returns';

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

function AppContent() {
  const [user, setUser] = useState<User | null>(null);
  const [cart, setCart] = useState<Cart | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [loading, setLoading] = useState(true);
  const location = useLocation();

  // Check if we're on an auth page (no header/footer)
  const isAuthPage = location.pathname.startsWith('/ap/');
  // Check if we're on checkout pages (custom minimal header)
  const isCheckoutPage = location.pathname.startsWith('/gp/buy/');

  useEffect(() => {
    loadInitialData();
  }, []);

  const loadInitialData = async () => {
    try {
      const [userRes, deptRes, cartRes] = await Promise.all([
        api.getCurrentUser().catch(() => null),
        api.getDepartments().catch(() => ({ departments: [] })),
        api.getCart().catch(() => null),
      ]);

      setUser(userRes);
      setDepartments(deptRes.departments || []);
      setCart(cartRes);
    } catch (error) {
      console.error('Failed to load initial data:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadCart = useCallback(async () => {
    try {
      const cartRes = await api.getCart();
      setCart(cartRes);
    } catch (error) {
      console.error('Failed to load cart:', error);
    }
  }, []);

  const handleAddToCart = async (productId: number, quantity: number = 1, variantId?: number) => {
    try {
      await api.addToCart(productId, quantity, variantId);
      loadCart();
    } catch (error) {
      console.error('Failed to add to cart:', error);
    }
  };

  const handleLogout = async () => {
    try {
      await api.logout();
      setUser(null);
      setCart(null);
    } catch (error) {
      console.error('Logout failed:', error);
    }
  };

  const handleAuth = (authenticatedUser: User) => {
    setUser(authenticatedUser);
    loadCart();
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--background-tertiary)]">
        <div className="text-center">
          <div className="spinner mx-auto"></div>
          <p className="mt-4 text-[var(--text-secondary)]">Loading...</p>
        </div>
      </div>
    );
  }

  // Auth pages without header/footer
  if (isAuthPage) {
    return (
      <Routes>
        <Route path="/ap/signin" element={<SignIn onAuth={handleAuth} />} />
        <Route path="/ap/register" element={<Register onAuth={handleAuth} />} />
      </Routes>
    );
  }

  // Checkout pages with custom minimal header
  if (isCheckoutPage) {
    return (
      <Routes>
        <Route path="/gp/buy/spc" element={<Checkout user={user} />} />
        <Route path="/gp/buy/thankyou" element={<OrderConfirmation user={user} />} />
      </Routes>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-[var(--background-tertiary)]">
      <Header
        user={user}
        cart={cart}
        departments={departments}
        onLogout={handleLogout}
      />

      <main className="flex-1">
        <Routes>
          {/* Home */}
          <Route
            path="/"
            element={
              <Home
                user={user}
                departments={departments}
                onAddToCart={handleAddToCart}
              />
            }
          />

          {/* Search */}
          <Route
            path="/s"
            element={
              <SearchResults
                departments={departments}
                onAddToCart={handleAddToCart}
              />
            }
          />

          {/* Product Detail */}
          <Route
            path="/dp/:asin"
            element={<ProductDetail user={user} onAddToCart={handleAddToCart} />}
          />

          {/* Cart */}
          <Route
            path="/gp/cart"
            element={<CartPage cart={cart} onCartUpdate={loadCart} />}
          />

          {/* Account */}
          <Route path="/gp/css/account" element={<AccountHub user={user} />} />

          {/* Orders */}
          <Route path="/gp/css/order-history" element={<Orders />} />

          {/* Deals */}
          <Route
            path="/gp/goldbox"
            element={
              <SearchResults
                departments={departments}
                onAddToCart={handleAddToCart}
              />
            }
          />
          <Route
            path="/deals"
            element={
              <SearchResults
                departments={departments}
                onAddToCart={handleAddToCart}
              />
            }
          />

          {/* Department Browse */}
          <Route
            path="/b/:department"
            element={
              <SearchResults
                departments={departments}
                onAddToCart={handleAddToCart}
              />
            }
          />

          {/* Buy Again */}
          <Route
            path="/gp/buyagain"
            element={<BuyAgain onAddToCart={handleAddToCart} />}
          />

          {/* History */}
          <Route
            path="/gp/history"
            element={
              <SearchResults
                departments={departments}
                onAddToCart={handleAddToCart}
              />
            }
          />

          {/* Wishlists */}
          <Route
            path="/hz/wishlist"
            element={<Wishlists user={user} />}
          />
          <Route
            path="/hz/wishlist/ls/:id"
            element={<WishlistDetail user={user} onAddToCart={handleAddToCart} />}
          />

          {/* Prime */}
          <Route path="/gp/prime" element={<Prime user={user} onUpdateUser={setUser} />} />

          {/* Gift Cards */}
          <Route path="/gift-cards" element={<GiftCards />} />

          {/* Registries */}
          <Route path="/registries" element={<Registries user={user} />} />
          <Route path="/registries/search" element={<RegistrySearch />} />
          <Route
            path="/registries/:id"
            element={<RegistryDetail user={user} onAddToCart={handleAddToCart} />}
          />

          {/* Subscriptions */}
          <Route path="/gp/css/account/subscriptions" element={<AccountHub user={user} />} />

          {/* Help */}
          <Route path="/gp/help/customer" element={<CustomerService user={user} />} />

          {/* Security */}
          <Route path="/gp/css/account/security" element={<LoginSecurity user={user} onUpdateUser={setUser} />} />

          {/* Addresses */}
          <Route path="/gp/css/account/address" element={<Addresses user={user} />} />

          {/* Payment Methods */}
          <Route path="/gp/css/account/payment" element={<Payments user={user} />} />

          {/* Messages */}
          <Route path="/gp/css/account/messages" element={<Messages user={user} />} />

          {/* Archived Orders */}
          <Route path="/gp/css/account/archived" element={<CancelledOrders />} />

          {/* Recommendations */}
          <Route
            path="/gp/yourstore/ref"
            element={
              <SearchResults
                departments={departments}
                onAddToCart={handleAddToCart}
              />
            }
          />

          {/* Seller Account */}
          <Route path="/gp/seller-account" element={<AccountHub user={user} />} />

          {/* Best Sellers */}
          <Route
            path="/products/best-sellers"
            element={<BestSellersPage onAddToCart={handleAddToCart} />}
          />

          {/* New Releases */}
          <Route
            path="/products/new-releases"
            element={<NewReleasesPage onAddToCart={handleAddToCart} />}
          />

          {/* Movers & Shakers */}
          <Route
            path="/products/movers-shakers"
            element={<MoversShakersPage onAddToCart={handleAddToCart} />}
          />

          {/* Trending */}
          <Route
            path="/products/trending"
            element={<TrendingPage onAddToCart={handleAddToCart} />}
          />

          {/* Returns */}
          <Route path="/gp/returns" element={<Returns user={user} />} />

          {/* Order Details */}
          <Route
            path="/gp/your-account/order-details/:id"
            element={<OrderDetails />}
          />

          {/* 404 */}
          <Route
            path="*"
            element={
              <div className="max-w-4xl mx-auto px-4 py-8 text-center">
                <h1 className="text-3xl font-bold mb-4">Page Not Found</h1>
                <p className="text-[var(--text-secondary)]">Sorry, we couldn't find that page.</p>
              </div>
            }
          />
        </Routes>
      </main>

      <Footer />
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <ScrollToTop />
      <AppContent />
    </BrowserRouter>
  );
}

export default App;
