import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { Product } from '../types';

interface ProductListPageProps {
  title: string;
  subtitle?: string;
  fetchProducts: () => Promise<{ products: Product[] }>;
  onAddToCart: (productId: number, quantity?: number) => void;
}

export function ProductListPage({ title, subtitle, fetchProducts, onAddToCart }: ProductListPageProps) {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadProducts();
  }, []);

  const loadProducts = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchProducts();
      setProducts(result.products || []);
    } catch (err) {
      console.error('Failed to load products:', err);
      setError('Failed to load products. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const renderStars = (rating: number) => {
    const fullStars = Math.floor(rating);
    const hasHalfStar = rating % 1 >= 0.5;
    const stars = [];

    // Full stars
    for (let i = 0; i < fullStars; i++) {
      stars.push(<span key={`full-${i}`} className="text-[var(--amazon-orange)]">&#9733;</span>);
    }

    // Half star
    if (hasHalfStar && fullStars < 5) {
      stars.push(
        <span key="half" className="relative inline-block">
          <span className="text-gray-300">&#9733;</span>
          <span className="absolute left-0 top-0 overflow-hidden w-[50%] text-[var(--amazon-orange)]">&#9733;</span>
        </span>
      );
    }

    // Empty stars
    const emptyStars = 5 - fullStars - (hasHalfStar ? 1 : 0);
    for (let i = 0; i < emptyStars; i++) {
      stars.push(<span key={`empty-${i}`} className="text-gray-300">&#9733;</span>);
    }

    return stars;
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-3xl font-bold">{title}</h1>
        {subtitle && <p className="text-[var(--text-secondary)] mt-1">{subtitle}</p>}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      ) : products.length === 0 ? (
        <div className="bg-white p-8 rounded text-center">
          <h2 className="text-xl font-bold mb-2">No products found</h2>
          <p className="text-[var(--text-secondary)] mb-4">
            Check back later for updates.
          </p>
          <Link to="/" className="text-[var(--link-color)] hover:underline">
            Continue shopping
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
          {products.map((product, index) => (
            <div key={product.id} className="bg-white rounded border p-4 pt-10 flex flex-col relative">
              {/* Rank Badge */}
              <div className="absolute top-2 left-2 bg-[var(--amazon-orange)] text-white text-xs font-bold px-2 py-1 rounded z-10">
                #{index + 1}
              </div>

              {/* Product Image */}
              <Link to={`/dp/${product.asin}`} className="block mb-3">
                <img
                  src={product.images?.[0] || 'https://via.placeholder.com/200'}
                  alt={product.title}
                  className="w-full h-48 object-contain"
                />
              </Link>

              {/* Product Info */}
              <div className="flex-1 flex flex-col">
                <Link
                  to={`/dp/${product.asin}`}
                  className="text-sm hover:text-[var(--amazon-orange)] line-clamp-2 mb-2"
                >
                  {product.title}
                </Link>

                {/* Rating */}
                <div className="flex items-center gap-1 mb-2">
                  <div className="flex text-sm">{renderStars(product.rating)}</div>
                  <span className="text-xs text-[var(--link-color)]">
                    ({product.rating_count.toLocaleString()})
                  </span>
                </div>

                {/* Price */}
                <div className="mb-2">
                  {product.list_price && product.list_price > product.price && (
                    <span className="text-xs text-red-600 font-medium mr-2">
                      {Math.round((1 - product.price / product.list_price) * 100)}% off
                    </span>
                  )}
                  <span className="text-lg font-bold">
                    ${product.price.toFixed(2)}
                  </span>
                  {product.list_price && product.list_price > product.price && (
                    <span className="text-xs text-[var(--text-secondary)] line-through ml-2">
                      ${product.list_price.toFixed(2)}
                    </span>
                  )}
                </div>

                {/* Badges */}
                <div className="flex flex-wrap gap-1 mb-2">
                  {product.is_best_seller && (
                    <span className="text-xs bg-[var(--amazon-orange)] text-white px-1.5 py-0.5 rounded">
                      #1 Best Seller
                    </span>
                  )}
                  {product.is_amazon_choice && (
                    <span className="text-xs bg-[#232f3e] text-white px-1.5 py-0.5 rounded">
                      Mercato's Choice
                    </span>
                  )}
                  {product.is_prime_eligible && (
                    <span className="text-xs text-[var(--prime-blue)]">&#10003; prime</span>
                  )}
                </div>

                {/* Bought count */}
                {product.bought_past_month > 0 && (
                  <p className="text-xs text-[var(--text-secondary)] mb-2">
                    {product.bought_past_month.toLocaleString()}+ bought in past month
                  </p>
                )}

                {/* Add to Cart */}
                <button
                  onClick={() => onAddToCart(product.id)}
                  className="btn-primary mt-auto text-sm py-1.5"
                >
                  Add to cart
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Pre-configured page components
export function BestSellersPage({ onAddToCart }: { onAddToCart: (id: number, qty?: number) => void }) {
  return (
    <ProductListPage
      title="Best Sellers"
      subtitle="Our most popular products based on sales"
      fetchProducts={api.getBestSellers}
      onAddToCart={onAddToCart}
    />
  );
}

export function NewReleasesPage({ onAddToCart }: { onAddToCart: (id: number, qty?: number) => void }) {
  return (
    <ProductListPage
      title="New Releases"
      subtitle="The hottest new arrivals"
      fetchProducts={api.getNewReleases}
      onAddToCart={onAddToCart}
    />
  );
}

export function MoversShakersPage({ onAddToCart }: { onAddToCart: (id: number, qty?: number) => void }) {
  return (
    <ProductListPage
      title="Movers & Shakers"
      subtitle="Products with the biggest gains in sales rank"
      fetchProducts={api.getMoversShakers}
      onAddToCart={onAddToCart}
    />
  );
}

export function TrendingPage({ onAddToCart }: { onAddToCart: (id: number, qty?: number) => void }) {
  return (
    <ProductListPage
      title="Trending Now"
      subtitle="What customers are viewing and buying right now"
      fetchProducts={api.getTrending}
      onAddToCart={onAddToCart}
    />
  );
}
