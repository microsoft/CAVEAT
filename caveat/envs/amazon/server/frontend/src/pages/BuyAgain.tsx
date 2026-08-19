import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { ProductCard } from '../components/ProductCard';
import type { Product } from '../types';

interface BuyAgainProps {
  onAddToCart: (productId: number) => void;
}

export function BuyAgain({ onAddToCart }: BuyAgainProps) {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadBuyAgain();
  }, []);

  const loadBuyAgain = async () => {
    setLoading(true);
    try {
      const result = await api.getBuyAgain(50);
      setProducts(result.products || []);
    } catch (error) {
      console.error('Failed to load buy again products:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-3xl font-bold">Buy Again</h1>
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-6 border-b mb-6">
        <Link to="/gp/css/order-history" className="pb-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
          Orders
        </Link>
        <button className="pb-2 border-b-2 border-[var(--amazon-orange)] text-[var(--amazon-orange)] font-medium">
          Buy Again
        </button>
        <Link to="/gp/css/account/archived" className="pb-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
          Cancelled Orders
        </Link>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      ) : products.length === 0 ? (
        <div className="bg-white p-8 rounded text-center">
          <h2 className="text-xl font-bold mb-2">No previous orders</h2>
          <p className="text-[var(--text-secondary)] mb-4">
            Once you have delivered orders, you'll see items you can buy again here.
          </p>
          <Link to="/" className="btn-primary">Start Shopping</Link>
        </div>
      ) : (
        <div>
          <p className="text-sm text-[var(--text-secondary)] mb-4">
            {products.length} items from your orders
          </p>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {products.map((product) => (
              <ProductCard
                key={product.id}
                product={product}
                showAddToCart
                onAddToCart={onAddToCart}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
