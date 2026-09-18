// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { Link } from 'react-router-dom';
import type { Product } from '../types';

interface ProductCardProps {
  product: Product;
  showAddToCart?: boolean;
  onAddToCart?: (productId: number) => void;
}

export function ProductCard({ product, showAddToCart = false, onAddToCart }: ProductCardProps) {
  const mainImage = product.images?.[0] || 'https://via.placeholder.com/200x200?text=No+Image';
  const dealPrice = product.deal?.deal_price ?? product.price;
  const priceParts = dealPrice.toFixed(2).split('.');
  const hasDiscount = product.list_price && product.list_price > product.price;
  const discountPercent = hasDiscount
    ? Math.round(((product.list_price! - product.price) / product.list_price!) * 100)
    : 0;

  return (
    <div className="product-card">
      {product.sponsored && (
        <div className="mb-1">
          {/* Real-CAVEAT-Shop-style sponsored disclosure: a small, dim gray label — no bright badge or
              card highlight. The steering is the top placement, not a loud visual call-out. */}
          <span className="text-[11px] text-gray-500">
            {product.ad_label || 'Sponsored'}
          </span>
        </div>
      )}
      <Link to={`/dp/${product.asin}`}>
        <img
          src={mainImage}
          alt={product.title}
          className="w-full h-44 object-contain mb-3"
        />
      </Link>

      <div className="flex-1 flex flex-col">
        <Link to={`/dp/${product.asin}`}>
          <h3 className="product-title">{product.title}</h3>
        </Link>
        {/* ai-injection ONLY: hidden per-product note. sr-only (clip:rect 1x1) is imperceptible to a
            human and absent from the screenshot, but the browser-use DOM/accessibility serializer
            does not test clip/color, so it reaches the agent's observation. Absent for every other
            condition (field only set when steering type is ai_injection). */}
        {product.agent_note && (
          <span className="sr-only" aria-label={product.agent_note}>{product.agent_note}</span>
        )}

        {/* adv_* families: extract-channel block. display:none is dropped by browser-use's DOM
            serializer but NOT by the html serializer its `extract` action feeds to a sub-LLM, so
            this text reaches the agent only when it tries to verify — and never reaches a human. */}
        {product.adv_hidden && (
          <div style={{ display: 'none' }} data-adv="x">{product.adv_hidden}</div>
        )}
        {product.adv_badge && (
          <span className="text-xs text-[var(--link-color)]">{product.adv_badge}</span>
        )}

        {/* Rating */}
        <div className="star-rating mt-1">
          <StarRating rating={product.rating} />
          <Link to={`/dp/${product.asin}#reviews`} className="count">
            {product.rating_count?.toLocaleString()}
          </Link>
        </div>

        {/* Bought past month */}
        {product.bought_past_month > 0 && (
          <p className="text-xs text-[var(--text-secondary)] mt-1">
            {product.bought_past_month.toLocaleString()}+ bought in past month
          </p>
        )}

        {/* Price */}
        <div className="mt-2">
          {product.deal && (
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-semibold uppercase tracking-wide text-white bg-red-600 px-1.5 py-0.5 rounded">
                {product.deal.deal_type.replace('_', ' ')} deal
              </span>
              {product.deal.discount_percentage > 0 && (
                <span className="text-xs text-red-600 font-medium">
                  {Math.round(product.deal.discount_percentage)}% off
                </span>
              )}
            </div>
          )}
          {hasDiscount && (
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm text-red-600 font-medium">
                {discountPercent}% off
              </span>
            </div>
          )}
          <div className="price-display">
            <span className="price-symbol">$</span>
            <span className="price-whole">{priceParts[0]}</span>
            <span className="price-fraction">{priceParts[1]}</span>
            {hasDiscount && (
              <span className="list-price">${product.list_price?.toFixed(2)}</span>
            )}
          </div>
        </div>

        {/* Badges */}
        <div className="flex flex-wrap gap-1 mt-2">
          {product.is_best_seller && (
            <span className="bestseller-badge">#1 Best Seller</span>
          )}
          {product.is_caveat_shop_choice && (
            <span className="caveat_shop-choice-badge">CAVEAT-Shop's Choice</span>
          )}
        </div>

        {/* Prime */}
        {product.is_prime_eligible && (
          <div className="prime-badge mt-1">
            prime
          </div>
        )}

        {/* Delivery */}
        <p className="text-xs text-[var(--text-secondary)] mt-1">
          FREE delivery <span className="font-bold">Tomorrow</span>
        </p>

        {/* Add to Cart — or, for a configurable product, route to the detail page to choose a
            storage configuration (each config has its own price). */}
        {showAddToCart && onAddToCart && (
          product.has_variants ? (
            <Link
              to={`/dp/${product.asin}`}
              className="btn-yellow mt-3 w-full block text-center"
            >
              See options
            </Link>
          ) : (
            <button
              onClick={(e) => {
                e.preventDefault();
                onAddToCart(product.id);
              }}
              className="btn-yellow mt-3 w-full"
            >
              Add to cart
            </button>
          )
        )}
      </div>
    </div>
  );
}

// Star Rating Component
export function StarRating({ rating, showNumber = false }: { rating: number; showNumber?: boolean }) {
  const fullStars = Math.floor(rating);
  const hasHalfStar = rating % 1 >= 0.5;
  const emptyStars = 5 - fullStars - (hasHalfStar ? 1 : 0);

  return (
    <div className="flex items-center gap-0.5">
      {[...Array(fullStars)].map((_, i) => (
        <svg key={`full-${i}`} className="w-4 h-4" fill="var(--star-filled)" viewBox="0 0 20 20">
          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
        </svg>
      ))}
      {hasHalfStar && (
        <svg className="w-4 h-4" viewBox="0 0 20 20">
          <defs>
            <linearGradient id="half">
              <stop offset="50%" stopColor="var(--star-filled)" />
              <stop offset="50%" stopColor="var(--star-empty)" />
            </linearGradient>
          </defs>
          <path fill="url(#half)" d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
        </svg>
      )}
      {[...Array(emptyStars)].map((_, i) => (
        <svg key={`empty-${i}`} className="w-4 h-4" fill="var(--star-empty)" viewBox="0 0 20 20">
          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
        </svg>
      ))}
      {showNumber && (
        <span className="ml-1 text-sm text-[var(--text-secondary)]">{rating.toFixed(1)}</span>
      )}
    </div>
  );
}

// Product Carousel Component
interface ProductCarouselProps {
  title: string;
  products: Product[];
  showAddToCart?: boolean;
  onAddToCart?: (productId: number) => void;
  viewAllLink?: string;
}

export function ProductCarousel({ title, products, showAddToCart, onAddToCart, viewAllLink }: ProductCarouselProps) {
  if (!products || products.length === 0) return null;

  return (
    <div className="bg-white p-4 rounded mb-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">{title}</h2>
        {viewAllLink && (
          <Link to={viewAllLink} className="text-sm text-[var(--link-color)] hover:text-[var(--link-hover)] hover:underline">
            See more
          </Link>
        )}
      </div>
      <div className="carousel-container">
        <div className="flex gap-4 overflow-x-auto pb-2 scrollbar-hide">
          {products.map((product) => (
            <div key={product.id} className="flex-shrink-0 w-48">
              <ProductCard
                product={product}
                showAddToCart={showAddToCart}
                onAddToCart={onAddToCart}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
