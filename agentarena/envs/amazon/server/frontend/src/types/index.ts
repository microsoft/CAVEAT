// User types
export interface User {
  id: number;
  email: string;
  name: string;
  phone?: string;
  avatar_url?: string;
  is_prime: boolean;
  prime_since?: string;
  default_address_id?: number;
  default_payment_id?: number;
  created_at: string;
  updated_at: string;
}

export interface Address {
  id: number;
  user_id: number;
  full_name: string;
  phone: string;
  address_line1: string;
  address_line2?: string;
  city: string;
  state: string;
  zip_code: string;
  country: string;
  is_default: boolean;
  delivery_instructions?: string;
  address_type: string;
}

export interface PaymentMethod {
  id: number;
  user_id: number;
  type: string;
  card_number_last4: string;
  card_brand: string;
  expiry_month: number;
  expiry_year: number;
  cardholder_name: string;
  billing_address_id?: number;
  is_default: boolean;
}

// Product types
export interface Department {
  id: number;
  name: string;
  slug: string;
  description?: string;
  image_url?: string;
  display_order: number;
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
  is_verified: boolean;
}

export interface Seller {
  id: number;
  name: string;
  slug: string;
  description?: string;
  logo_url?: string;
  rating: number;
  rating_count: number;
  is_amazon: boolean;
  feedback_percentage?: number;
  ships_from?: string;
}

export interface Product {
  id: number;
  asin: string;
  title: string;
  slug: string;
  brand_id: number;
  brand_name?: string;
  category_id: number;
  category_name?: string;
  seller_id: number;
  seller_name?: string;
  price: number;
  list_price?: number;
  currency: string;
  description_html: string;
  bullet_points: string[];
  stock_quantity: number;
  availability_status: string;
  images: string[];
  rating: number;
  rating_count: number;
  review_count?: number;
  is_best_seller: boolean;
  best_seller_rank?: number;
  best_seller_category?: string;
  is_amazon_choice: boolean;
  amazon_choice_keyword?: string;
  is_prime_eligible: boolean;
  is_climate_pledge: boolean;
  sponsored?: boolean;
  ad_label?: string;
  bought_past_month: number;
}

export interface ProductVariant {
  id: number;
  product_id: number;
  variant_type: string;
  variant_value: string;
  sku: string;
  price: number;
  stock_quantity: number;
  images: string[];
  is_available: boolean;
}

// Cart types
export interface CartItem {
  id: number;
  product_id: number;
  product_asin?: string;
  product_title?: string;
  product_image?: string;
  product_price?: number;
  variant_id?: number;
  quantity: number;
  is_gift: boolean;
  gift_message?: string;
  saved_for_later: boolean;
}

export interface Cart {
  items: CartItem[];
  subtotal: number;
  item_count: number;
  saved_for_later: CartItem[];
}

// Order types
export interface OrderItem {
  id: number;
  order_id: number;
  product_id: number;
  product_title?: string;
  product_image?: string;
  seller_id: number;
  quantity: number;
  unit_price: number;
  total_price: number;
  status: string;
}

export interface Order {
  id: number;
  order_number: string;
  user_id: number;
  shipping_address_id: number;
  billing_address_id: number;
  payment_method_id: number;
  subtotal: number;
  shipping_cost: number;
  tax: number;
  discount?: number;
  total: number;
  status: string;
  is_gift: boolean;
  gift_message?: string;
  placed_at: string;
  shipped_at?: string;
  delivered_at?: string;
  items?: OrderItem[];
}

// Review types
export interface Review {
  id: number;
  product_id: number;
  user_id: number;
  user_name?: string;
  rating: number;
  title: string;
  body: string;
  images?: string[];
  is_verified_purchase: boolean;
  helpful_count: number;
  created_at: string;
}

// Wishlist types
export interface WishlistItem {
  id: number;
  wishlist_id: number;
  product_id: number;
  product?: Product;
  quantity_desired: number;
  priority: string;
  comment?: string;
  price_when_added: number;
}

export interface Wishlist {
  id: number;
  user_id: number;
  name: string;
  is_default: boolean;
  is_public: boolean;
  items?: WishlistItem[];
}

// Deal types
export interface Deal {
  id: number;
  product_id: number;
  product?: Product;
  deal_type: string;
  discount_percentage: number;
  deal_price: number;
  original_price: number;
  start_time: string;
  end_time: string;
  claimed_count?: number;
  max_claims?: number;
}

export interface Coupon {
  id: number;
  code: string;
  description: string;
  discount_type: string;
  discount_value: number;
  min_order_amount?: number;
  product_id?: number;
  category_id?: number;
  valid_from: string;
  valid_until: string;
}

// Subscription types
export interface Subscription {
  id: number;
  user_id: number;
  product_id: number;
  product?: Product;
  quantity: number;
  frequency_months: number;
  next_delivery_date: string;
  status: string;
}

// Notification types
export interface Notification {
  id: number;
  user_id: number;
  type: string;
  title: string;
  message: string;
  is_read: boolean;
  created_at: string;
}

// History types
export interface BrowsingHistoryItem {
  id: number;
  product_id: number;
  product?: Product;
  viewed_at: string;
}

export interface SearchHistoryItem {
  id: number;
  query: string;
  results_count: number;
  searched_at: string;
}

// Shopping preferences
export interface ShoppingPreference {
  id: number;
  user_id: number;
  language: string;
  currency: string;
  country: string;
}
