// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import type {
  User,
  Address,
  PaymentMethod,
  Department,
  Category,
  Product,
  Cart,
  Order,
  Review,
  Wishlist,
  Deal,
  Coupon,
  Subscription,
  Notification,
  Seller,
  ShoppingPreference,
  GiftCard,
  GiftCardTransaction,
  Registry,
  Message,
  RegistryItem,
} from './types';

const API_BASE = '/api';

// Session-scoped client credential: the server injects a per-session token into the
// served page (<meta name="sf-client">) and the API only answers requests that echo
// it back — the same pattern a real SPA marketplace's XHR surface uses. Falls back
// to the legacy 'web' constant under `npm run dev` (no injection, gate off).
function clientToken(): string {
  return (
    (typeof document !== 'undefined' &&
      document.querySelector('meta[name="sf-client"]')?.getAttribute('content')) ||
    'web'
  );
}

async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      // Identifies traffic from the storefront's own web client (i.e. the page a human is
      // actually looking at), as distinct from a program calling the JSON API directly.
      'X-Storefront-Client': clientToken(),
      ...options.headers,
    },
    credentials: 'include',
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

export const api = {
  // Auth
  register: (data: { email: string; password: string; name: string; phone?: string }) =>
    request<User>('/auth/register', { method: 'POST', body: JSON.stringify(data) }),

  login: (email: string, password: string) =>
    request<{ message: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  logout: () => request<{ message: string }>('/auth/logout', { method: 'POST' }),

  getCurrentUser: () => request<User>('/auth/me'),

  // Products
  getProducts: (params?: {
    q?: string;
    department?: string;
    category_id?: number;
    brand_id?: number;
    min_price?: number;
    max_price?: number;
    min_rating?: number;
    prime?: boolean;
    sort?: string;
    limit?: number;
    offset?: number;
  }) => {
    const searchParams = new URLSearchParams();
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined) searchParams.set(key, String(value));
      });
    }
    const query = searchParams.toString();
    return request<{ products: Product[]; total: number }>(`/products${query ? `?${query}` : ''}`);
  },

  getProduct: (id: number) => request<Product>(`/products/${id}`),

  getProductByAsin: (asin: string) => request<Product>(`/products/asin/${asin}`),

  getProductVariants: (productId: number) =>
    request<{ variants: { id: number; variant_type: string; variant_value: string; price: number }[] }>(
      `/products/${productId}/variants`),

  getBestSellers: () => request<{ products: Product[] }>('/products/best-sellers'),

  getNewReleases: () => request<{ products: Product[] }>('/products/new-releases'),

  getMoversShakers: () => request<{ products: Product[] }>('/products/movers-shakers'),

  getTrending: () => request<{ products: Product[] }>('/products/trending'),

  getProductReviews: (productId: number) =>
    request<{ reviews: Review[]; total: number }>(`/products/${productId}/reviews`),

  getReviewsSummary: (productId: number) =>
    request<{ average_rating: number; total_reviews: number; rating_breakdown: { [key: string]: { count: number; percentage: number } } }>(`/products/${productId}/reviews/summary`),

  createReview: (productId: number, data: { rating: number; title: string; body: string }) =>
    request<{ id: number }>(`/products/${productId}/reviews`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Search
  search: (query: string, limit?: number, offset?: number) => {
    const params = new URLSearchParams({ q: query });
    if (limit) params.set('limit', String(limit));
    if (offset) params.set('offset', String(offset));
    return request<{ products: Product[]; total: number }>(`/search?${params}`);
  },

  getSearchSuggestions: (query: string) =>
    request<{ suggestions: string[] }>(`/search/suggestions?q=${encodeURIComponent(query)}`),

  // Departments & Categories
  getDepartments: () => request<{ departments: Department[] }>('/departments'),

  getDepartment: (slug: string) => request<Department>(`/departments/${slug}`),

  getCategories: () => request<{ categories: Category[] }>('/categories'),

  // Cart
  getCart: () => request<Cart>('/cart'),

  addToCart: (productId: number, quantity: number = 1, variantId?: number) =>
    request<{ message: string }>('/cart/items', {
      method: 'POST',
      body: JSON.stringify({ product_id: productId, quantity, variant_id: variantId }),
    }),

  updateCartItem: (itemId: number, quantity?: number, isGift?: boolean, selected?: boolean) =>
    request<{ message: string }>(`/cart/items/${itemId}`, {
      method: 'PUT',
      body: JSON.stringify({ quantity, is_gift: isGift, selected }),
    }),

  removeFromCart: (itemId: number) =>
    request<{ message: string }>(`/cart/items/${itemId}`, { method: 'DELETE' }),

  saveForLater: (itemId: number) =>
    request<{ message: string }>(`/cart/items/${itemId}/save-for-later`, { method: 'POST' }),

  moveToCart: (itemId: number) =>
    request<{ message: string }>(`/cart/items/${itemId}/move-to-cart`, { method: 'POST' }),

  // Checkout
  startCheckout: () =>
    request<{ message: string; step: number }>('/checkout/start', { method: 'POST' }),

  setShippingAddress: (addressId: number, shippingMethod: string = 'standard') =>
    request<{ message: string; step: number }>('/checkout/shipping', {
      method: 'PUT',
      body: JSON.stringify({ address_id: addressId, shipping_method: shippingMethod }),
    }),

  setPaymentMethod: (paymentMethodId: number) =>
    request<{ message: string; step: number }>('/checkout/payment', {
      method: 'PUT',
      body: JSON.stringify({ payment_method_id: paymentMethodId }),
    }),

  getCheckoutSummary: () =>
    request<{
      subtotal: number;
      service_fee?: number;
      fee_label?: string | null;
      shipping_cost: number;
      tax: number;
      total: number;
      shipping_address_id: number | null;
      payment_method_id: number | null;
      shipping_method: string;
    }>('/checkout/summary'),

  getShippingOptions: () =>
    request<{ options: { id: string; name: string; price: number; days: string }[] }>('/checkout/shipping-options'),

  placeOrder: (giftMessage?: string) =>
    request<{ order_id: number; order_number: string }>('/checkout/place-order', {
      method: 'POST',
      body: JSON.stringify({ gift_message: giftMessage }),
    }),

  // Orders
  getOrders: (params?: { status?: string; q?: string; period?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set('status', params.status);
    if (params?.q) searchParams.set('q', params.q);
    if (params?.period) searchParams.set('period', params.period);
    const query = searchParams.toString();
    return request<{ orders: Order[]; total: number }>(`/orders${query ? `?${query}` : ''}`);
  },

  getOrder: (id: number) => request<Order>(`/orders/${id}`),

  getOrderTracking: (id: number) =>
    request<{ order_number: string; status: string; tracking: any[] }>(`/orders/${id}/tracking`),

  cancelOrder: (id: number) =>
    request<{ message: string }>(`/orders/${id}/cancel`, { method: 'POST' }),

  archiveOrder: (id: number) =>
    request<{ message: string }>(`/orders/${id}/archive`, { method: 'POST' }),

  getArchivedOrders: () =>
    request<{ orders: Order[] }>('/orders/archived'),

  initiateReturn: (orderId: number, itemId: number, reason?: string) =>
    request<{ message: string }>(`/orders/${orderId}/items/${itemId}/return`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),

  getBuyAgain: (limit?: number) => {
    const params = limit ? `?limit=${limit}` : '';
    return request<{ products: Product[] }>(`/buy-again${params}`);
  },

  // Wishlists
  getWishlists: () => request<{ wishlists: Wishlist[] }>('/wishlists'),

  getWishlist: (id: number) =>
    request<Wishlist & { items: any[] }>(`/wishlists/${id}`),

  createWishlist: (name: string, isPublic: boolean = false) =>
    request<{ id: number }>('/wishlists', {
      method: 'POST',
      body: JSON.stringify({ name, is_public: isPublic }),
    }),

  updateWishlist: (id: number, data: { name?: string; is_public?: boolean }) =>
    request<{ message: string }>(`/wishlists/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  deleteWishlist: (id: number) =>
    request<{ message: string }>(`/wishlists/${id}`, { method: 'DELETE' }),

  addToWishlist: (wishlistId: number, productId: number) =>
    request<{ message: string }>(`/wishlists/${wishlistId}/items`, {
      method: 'POST',
      body: JSON.stringify({ product_id: productId }),
    }),

  removeFromWishlist: (wishlistId: number, itemId: number) =>
    request<{ message: string }>(`/wishlists/${wishlistId}/items/${itemId}`, { method: 'DELETE' }),

  // Addresses
  getAddresses: () => request<{ addresses: Address[] }>('/user/addresses'),

  createAddress: (data: Omit<Address, 'id' | 'user_id'>) =>
    request<{ id: number }>('/user/addresses', { method: 'POST', body: JSON.stringify(data) }),

  updateAddress: (id: number, data: Partial<Address>) =>
    request<{ message: string }>(`/user/addresses/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  deleteAddress: (id: number) =>
    request<{ message: string }>(`/user/addresses/${id}`, { method: 'DELETE' }),

  // Payment Methods
  getPaymentMethods: () => request<{ payment_methods: PaymentMethod[] }>('/user/payment-methods'),

  createPaymentMethod: (data: {
    type: string;
    card_number_last4: string;
    card_brand: string;
    expiry_month: number;
    expiry_year: number;
    cardholder_name: string;
    is_default?: boolean;
  }) =>
    request<{ id: number; message: string }>('/user/payment-methods', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  deletePaymentMethod: (id: number) =>
    request<{ message: string }>(`/user/payment-methods/${id}`, { method: 'DELETE' }),

  // Messages
  getMessages: () => request<{ messages: Message[] }>('/messages'),

  getMessage: (id: number) => request<Message>(`/messages/${id}`),

  markMessageRead: (id: number) =>
    request<{ message: string }>(`/messages/${id}/read`, { method: 'PUT' }),

  deleteMessage: (id: number) =>
    request<{ message: string }>(`/messages/${id}`, { method: 'DELETE' }),

  sendMessage: (data: { subject: string; body: string; related_order_id?: number }) =>
    request<{ id: number; message: string }>('/messages/reply', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Prime
  getPrimeStatus: () =>
    request<{ is_prime: boolean; prime_since?: string; benefits: string[] }>('/user/prime'),

  // User Profile
  updateProfile: (data: { name?: string; email?: string; phone?: string }) =>
    request<{ id: number; name: string; email: string; phone: string; avatar_url: string | null }>('/user', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ message: string }>('/user/password', {
      method: 'PUT',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  // Deals
  getDeals: () => request<{ deals: Deal[] }>('/deals'),

  getLightningDeals: () => request<{ deals: Deal[] }>('/deals/lightning'),

  getCoupons: () => request<{ coupons: Coupon[] }>('/deals/coupons'),

  clipCoupon: (id: number) => request<{ message: string }>(`/coupons/${id}/clip`, { method: 'POST' }),

  // Subscriptions
  getSubscriptions: () => request<{ subscriptions: Subscription[] }>('/subscriptions'),

  updateSubscription: (id: number, data: { frequency_months?: number; quantity?: number }) =>
    request<{ message: string }>(`/subscriptions/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Notifications
  getNotifications: () => request<{ notifications: Notification[] }>('/user/notifications'),

  markNotificationRead: (id: number) =>
    request<{ message: string }>(`/user/notifications/${id}/read`, { method: 'PUT' }),

  // History
  getBrowsingHistory: () => request<{ history: any[] }>('/history'),

  // Sellers
  getSeller: (id: number) => request<Seller>(`/sellers/${id}`),

  getSellerProducts: (id: number) => request<{ products: Product[] }>(`/sellers/${id}/products`),

  // Recommendations
  getRecommendations: () => request<{ recommendations: Product[] }>('/recommendations'),

  // Preferences
  getPreferences: () => request<ShoppingPreference>('/preferences'),

  updatePreferences: (data: { language?: string; currency?: string }) =>
    request<{ message: string }>('/preferences', { method: 'PUT', body: JSON.stringify(data) }),

  // Gift Cards
  getGiftCards: () => request<{ gift_cards: GiftCard[] }>('/gift-cards'),

  getGiftCardBalance: () => request<{ balance: number; currency: string }>('/gift-cards/balance'),

  redeemGiftCard: (code: string) =>
    request<{ message: string; amount: number }>('/gift-cards/redeem', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),

  reloadGiftCard: (amount: number) =>
    request<{ message: string; new_balance: number }>('/gift-cards/reload', {
      method: 'POST',
      body: JSON.stringify({ amount }),
    }),

  getGiftCardTransactions: () =>
    request<{ transactions: GiftCardTransaction[] }>('/gift-cards/transactions'),

  // Registries
  getRegistries: () => request<{ registries: Registry[] }>('/registries'),

  createRegistry: (data: {
    type: string;
    name: string;
    event_date?: string;
    is_public?: boolean;
    shipping_address_id?: number;
  }) =>
    request<{ id: number; message: string }>('/registries', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  getRegistry: (id: number) =>
    request<Registry & { items: RegistryItem[] }>(`/registries/${id}`),

  updateRegistry: (id: number, data: { name?: string; event_date?: string; is_public?: boolean }) =>
    request<{ message: string }>(`/registries/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  deleteRegistry: (id: number) =>
    request<{ message: string }>(`/registries/${id}`, { method: 'DELETE' }),

  addRegistryItem: (registryId: number, data: { product_id: number; quantity_desired?: number; priority?: string }) =>
    request<{ message: string }>(`/registries/${registryId}/items`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  removeRegistryItem: (registryId: number, itemId: number) =>
    request<{ message: string }>(`/registries/${registryId}/items/${itemId}`, { method: 'DELETE' }),

  searchRegistries: (params: { name?: string; type?: string }) => {
    const searchParams = new URLSearchParams();
    if (params.name) searchParams.set('name', params.name);
    if (params.type) searchParams.set('type', params.type);
    const query = searchParams.toString();
    return request<{ registries: Registry[] }>(`/registries/search${query ? `?${query}` : ''}`);
  },
};
