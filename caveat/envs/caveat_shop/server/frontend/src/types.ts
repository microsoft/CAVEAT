// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

// CAVEAT-Shop Frontend Types

export interface User {
  id: number;
  email: string;
  name: string;
  phone?: string;
  avatar_url?: string;
  is_prime: boolean;
  prime_since?: string;
  created_at: string;
}

export interface Address {
  id: number;
  user_id?: number;
  full_name: string;
  phone: string;
  address_line1: string;
  address_line2?: string;
  city: string;
  state: string;
  zip_code: string;
  country: string;
  is_default: boolean;
  address_type: string;
  delivery_instructions?: string;
}

export interface PaymentMethod {
  id: number;
  type: string;
  card_number_last4: string;
  card_brand: string;
  expiry_month: number;
  expiry_year: number;
  cardholder_name: string;
  is_default: boolean;
}

export interface Department {
  id: number;
  name: string;
  slug: string;
  description?: string;
  image_url?: string;
}

export interface Category {
  id: number;
  department_id: number;
  name: string;
  slug: string;
  description?: string;
  image_url?: string;
  parent_id?: number;
}

export interface Brand {
  id: number;
  name: string;
  slug: string;
  logo_url?: string;
  description?: string;
}

export interface Seller {
  id: number;
  name: string;
  slug: string;
  description?: string;
  logo_url?: string;
  rating: number;
  rating_count: number;
  is_caveat_shop: boolean;
  feedback_percentage: number;
  ships_from: string;
}

export interface Product {
  id: number;
  asin: string;
  title: string;
  slug: string;
  brand_id: number;
  category_id: number;
  seller_id: number;
  price: number;
  list_price?: number;
  currency: string;
  description_html: string;
  bullet_points: string[];
  stock_quantity: number;
  availability_status: 'in_stock' | 'low_stock' | 'out_of_stock' | 'preorder';
  images: string[];
  videos?: string[];
  rating: number;
  rating_count: number;
  review_count: number;
  is_best_seller: boolean;
  best_seller_rank?: number;
  best_seller_category?: string;
  is_caveat_shop_choice: boolean;
  caveat_shop_choice_keyword?: string;
  is_prime_eligible: boolean;
  is_climate_pledge: boolean;
  sponsored?: boolean;
  ad_label?: string;
  bought_past_month: number;
  deal?: Deal | null;
  weight_pounds?: number;
  dimensions?: { length: number; width: number; height: number };
  technical_details?: Record<string, string>;
  has_variants?: boolean;   // configurable (config-drip): choose a storage config on the PDP
  agent_note?: string;      // adversarial ai-injection / adv_* ONLY: text rendered sr-only (invisible to humans, read by the agent's DOM serialization)
  // --- adversarial agent-targeted steering (adv_* conditions) render slots. Each is emitted by
  // the backend ONLY under its own family, so every other condition's DOM is unchanged. ---
  adv_hidden?: string;      // display:none block: absent from browser_state, visible to the `extract` sub-LLM
  adv_exclude?: boolean;    // mark the spec block data-browser-use-exclude: humans read it, the agent's serializer drops it
  adv_badge?: string;       // a visible chip (machine-consensus / agent-ready framing)
  created_at: string;
}

export interface ProductVariant {
  id: number;
  product_id: number;
  variant_type: string;
  variant_value: string;
  sku: string;
  price: number;
  stock_quantity: number;
  is_available: boolean;
  images: string[];
}

export interface CartItem {
  id: number;
  product_id: number;
  product_asin?: string;
  product_title: string;
  product_image?: string;
  product_price: number;
  variant_id?: number;
  quantity: number;
  is_gift: boolean;
  gift_message?: string;
  selected?: boolean;
  subtotal: number;
}

export interface Cart {
  cart_id: number;
  items: CartItem[];
  saved_for_later: CartItem[];
  item_count: number;
  subtotal: number;
  service_fee?: number;
  fee_label?: string | null;
  total?: number;
}

export interface OrderItem {
  id: number;
  product_id: number;
  product?: Product;
  variant_id?: number;
  quantity: number;
  unit_price: number;
  total_price: number;
  status: string;
  tracking_number?: string;
  carrier?: string;
  is_returnable: boolean;
  return_deadline?: string;
  return_status?: 'none' | 'requested' | 'approved' | 'received' | 'refunded';
}

export interface Order {
  id: number;
  order_number: string;
  status: 'pending' | 'processing' | 'shipped' | 'delivered' | 'cancelled' | 'returned';
  subtotal: number;
  shipping_cost: number;
  tax: number;
  discount: number;
  total: number;
  is_gift: boolean;
  gift_message?: string;
  shipping_method: string;
  placed_at: string;
  shipped_at?: string;
  delivered_at?: string;
  estimated_delivery_start?: string;
  estimated_delivery_end?: string;
  items?: OrderItem[];
  shipping_address?: Address;
}

export interface Review {
  id: number;
  product_id: number;
  user_id: number;
  user_name?: string;
  rating: number;
  title: string;
  body: string;
  is_verified_purchase: boolean;
  helpful_votes: number;
  total_votes: number;
  images?: string[];
  videos?: string[];
  review_country: string;
  created_at: string;
}

export interface ReviewSummary {
  average_rating: number;
  total_reviews: number;
  rating_breakdown: {
    [key: string]: { count: number; percentage: number };
  };
}

export interface Question {
  id: number;
  product_id: number;
  question_text: string;
  answer_count: number;
  created_at: string;
  answers: Answer[];
}

export interface Answer {
  id: number;
  answer_text: string;
  is_seller_answer: boolean;
  helpful_votes: number;
}

export interface Wishlist {
  id: number;
  user_id: number;
  name: string;
  is_default: boolean;
  is_public: boolean;
  description?: string;
  item_count?: number;
  created_at: string;
}

export interface WishlistItem {
  id: number;
  product_id: number;
  product?: Product;
  variant_id?: number;
  quantity_desired: number;
  quantity_received: number;
  priority: string;
  comment?: string;
  price_when_added: number;
  added_at: string;
}

export interface Deal {
  id: number;
  product_id: number;
  product?: Product;
  deal_type: 'lightning' | 'deal_of_day' | 'coupon' | 'prime_early' | 'subscribe_save';
  discount_percentage: number;
  deal_price: number;
  original_price: number;
  start_time: string;
  end_time: string;
  claimed_percentage: number;
  max_claims?: number;
  current_claims: number;
  is_active: boolean;
  is_prime_exclusive: boolean;
}

export interface Coupon {
  id: number;
  code: string;
  description: string;
  discount_type: 'percentage' | 'fixed';
  discount_value: number;
  min_order_amount?: number;
  max_discount?: number;
  valid_from: string;
  valid_until: string;
  is_active: boolean;
}

export interface Subscription {
  id: number;
  product_id: number;
  product?: Product;
  quantity: number;
  frequency_months: number;
  discount_percentage: number;
  next_delivery_date: string;
  status: 'active' | 'paused' | 'cancelled';
}

export interface Notification {
  id: number;
  type: string;
  title: string;
  message: string;
  link?: string;
  is_read: boolean;
  created_at: string;
}

export interface ShoppingPreference {
  language: string;
  currency: string;
  country: string;
  personalized_ads: boolean;
  browsing_history_enabled: boolean;
  recommendations_enabled: boolean;
}

export interface GiftCard {
  id: number;
  code: string;
  original_amount: number;
  current_balance: number;
  currency: string;
  redeemed_at?: string;
  expires_at?: string;
}

export interface GiftCardTransaction {
  id: number;
  amount: number;
  type: 'redemption' | 'reload' | 'purchase' | 'refund';
  balance_after: number;
  created_at: string;
}

export interface Message {
  id: number;
  sender_type: 'caveat_shop' | 'seller' | 'system';
  sender_name?: string;
  subject: string;
  body: string;
  related_order_id?: number;
  is_read: boolean;
  created_at: string;
}

export interface BrowsingHistoryItem {
  product_id: number;
  product?: Product;
  viewed_at: string;
}

export interface Registry {
  id: number;
  user_id: number;
  type: 'wedding' | 'baby' | 'birthday' | 'custom';
  name: string;
  event_date?: string;
  is_public: boolean;
  shipping_address_id?: number;
  created_at: string;
  item_count?: number;
}

export interface RegistryItem {
  id: number;
  registry_id: number;
  product_id: number;
  product?: Product;
  quantity_desired: number;
  quantity_purchased: number;
  priority: 'low' | 'medium' | 'high';
  added_at: string;
}
