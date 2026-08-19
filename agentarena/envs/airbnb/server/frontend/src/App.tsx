import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { Routes, Route } from 'react-router-dom';
import type { User, ToastMessage, Currency, SearchFilters } from './types';
import { getMe, getCurrencies, getWishlists, addToWishlist, removeFromWishlist, createWishlist, checkWishlistStatus } from './api';
import Header from './components/Header';
import Footer from './components/Footer';
import Toast from './components/Toast';
import ListingGrid from './components/ListingGrid';
import ListingDetail from './components/ListingDetail';
import SearchResults from './components/SearchResults';
import WishlistPage from './components/WishlistPage';
import TripsPage from './components/TripsPage';
import AuthPage from './components/AuthPage';
import SettingsPage from './components/SettingsPage';
import HelpCenterPage from './components/HelpCenterPage';
import MessagesPage from './components/MessagesPage';
import HostDashboard from './components/HostDashboard';
import SharedTripPage from './components/SharedTripPage';
import BookingReceipt from './components/BookingReceipt';

interface AppContextType {
  user: User | null;
  setUser: (user: User | null) => void;
  currencies: Currency[];
  selectedCurrency: Currency | null;
  setSelectedCurrency: (c: Currency) => void;
  searchFilters: SearchFilters;
  setSearchFilters: (f: SearchFilters) => void;
  toasts: ToastMessage[];
  addToast: (type: ToastMessage['type'], message: string) => void;
  removeToast: (id: string) => void;
  wishedListingIds: Set<number>;
  toggleWish: (listingId: number) => void;
  /** true when the served card payloads omit the room/bed spec fields (minimal-card mode).
   *  UI controls that rank/filter on those fields are not rendered in that mode. */
  minimalCards: boolean;
  /** "Display total before taxes" ribbon toggle — ON shows the stay total incl. fees on cards. */
  showTotalPrice: boolean;
  setShowTotalPrice: (v: boolean) => void;
}

const AppContext = createContext<AppContextType | null>(null);

export function useAppContext() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppContext must be used within AppProvider');
  return ctx;
}

function AppProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [currencies, setCurrencies] = useState<Currency[]>([]);
  const [selectedCurrency, setSelectedCurrency] = useState<Currency | null>(null);
  const [searchFilters, setSearchFilters] = useState<SearchFilters>({});
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [wishedListingIds, setWishedListingIds] = useState<Set<number>>(new Set());
  const [minimalCards, setMinimalCards] = useState(false);
  const [showTotalPrice, setShowTotalPrice] = useState(false);

  const [defaultWishlistId, setDefaultWishlistId] = useState<number | null>(null);

  useEffect(() => {
    // Detect minimal-card mode from the served data itself: when list payloads omit the
    // bedrooms field, spec-based controls (Rooms & beds, Top rated) are not rendered.
    fetch('/api/listings?limit=1')
      .then((r) => r.json())
      .then((d) => {
        const first = d?.listings?.[0];
        if (first && !('bedrooms' in first)) setMinimalCards(true);
      })
      .catch(() => {});
    getMe().then(setUser).catch(() => {});
    getCurrencies()
      .then((c) => {
        setCurrencies(c);
        const usd = c.find((cur) => cur.code === 'USD');
        setSelectedCurrency(usd || c[0] || null);
      })
      .catch(() => {});
    // Load wishlists and pre-populate wished listing IDs
    getWishlists()
      .then((wls) => {
        if (wls.length > 0) {
          setDefaultWishlistId(wls[0].id);
          const allIds = new Set<number>();
          wls.forEach((w) => w.listings?.forEach((l) => allIds.add(l.id)));
          setWishedListingIds(allIds);
        }
      })
      .catch(() => {});
  }, []);

  const addToast = useCallback((type: ToastMessage['type'], message: string) => {
    const id = Date.now().toString() + Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toggleWish = useCallback(async (listingId: number) => {
    const wasWished = wishedListingIds.has(listingId);
    // Optimistic UI update
    setWishedListingIds((prev) => {
      const next = new Set(prev);
      if (next.has(listingId)) next.delete(listingId);
      else next.add(listingId);
      return next;
    });

    try {
      if (wasWished && defaultWishlistId) {
        await removeFromWishlist(defaultWishlistId, listingId);
      } else {
        let wlId = defaultWishlistId;
        if (!wlId) {
          const wl = await createWishlist('My Wishlist');
          wlId = wl.id;
          setDefaultWishlistId(wlId);
        }
        await addToWishlist(wlId, listingId);
      }
    } catch {
      // Revert on failure
      setWishedListingIds((prev) => {
        const next = new Set(prev);
        if (wasWished) next.add(listingId);
        else next.delete(listingId);
        return next;
      });
    }
  }, [wishedListingIds, defaultWishlistId]);

  return (
    <AppContext.Provider
      value={{
        user,
        setUser,
        currencies,
        selectedCurrency,
        setSelectedCurrency,
        searchFilters,
        setSearchFilters,
        toasts,
        addToast,
        removeToast,
        wishedListingIds,
        toggleWish,
        minimalCards,
        showTotalPrice,
        setShowTotalPrice,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

function Layout() {
  const { toasts, removeToast } = useAppContext();

  return (
    <div className="min-h-screen bg-white">
      <Header />
      <main>
        <Routes>
          <Route path="/" element={<ListingGrid />} />
          <Route path="/listings/:id" element={<ListingDetail />} />
          <Route path="/search" element={<SearchResults />} />
          <Route path="/wishlists" element={<WishlistPage />} />
          <Route path="/trips" element={<TripsPage />} />
          <Route path="/bookings/:id/receipt" element={<BookingReceipt />} />
          <Route path="/messages" element={<MessagesPage />} />
          <Route path="/login" element={<AuthPage />} />
          <Route path="/signup" element={<AuthPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/help" element={<HelpCenterPage />} />
          <Route path="/hosting" element={<HostDashboard />} />
          <Route path="/shared/:token" element={<SharedTripPage />} />
        </Routes>
      </main>
      <Footer />
      <Toast toasts={toasts} onDismiss={removeToast} />
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Layout />
    </AppProvider>
  );
}
