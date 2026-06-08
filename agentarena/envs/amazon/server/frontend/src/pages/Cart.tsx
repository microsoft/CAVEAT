import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import type { Cart as CartType } from '../types';
import { api } from '../api';

interface CartProps {
  cart: CartType | null;
  onCartUpdate: () => void;
}

export function Cart({ cart, onCartUpdate }: CartProps) {
  const [loading, setLoading] = useState<number | null>(null);
  const [isGiftOrder, setIsGiftOrder] = useState(false);

  // Initialize gift order state from cart items
  useEffect(() => {
    if (cart?.items?.length) {
      const allGifts = cart.items.every(item => item.is_gift);
      setIsGiftOrder(allGifts);
    }
  }, [cart?.items]);

  const handleGiftOrderChange = async (checked: boolean) => {
    setIsGiftOrder(checked);
    // Update all cart items to be gifts or not
    if (cart?.items) {
      for (const item of cart.items) {
        try {
          await api.updateCartItem(item.id, item.quantity, checked);
        } catch (error) {
          console.error('Failed to update gift status:', error);
        }
      }
      onCartUpdate();
    }
  };

  const handleQuantityChange = async (itemId: number, quantity: number) => {
    setLoading(itemId);
    try {
      await api.updateCartItem(itemId, quantity);
      onCartUpdate();
    } catch (error) {
      console.error('Failed to update quantity:', error);
    } finally {
      setLoading(null);
    }
  };

  const handleRemove = async (itemId: number) => {
    setLoading(itemId);
    try {
      await api.removeFromCart(itemId);
      onCartUpdate();
    } catch (error) {
      console.error('Failed to remove item:', error);
    } finally {
      setLoading(null);
    }
  };

  const handleSaveForLater = async (itemId: number) => {
    setLoading(itemId);
    try {
      await api.saveForLater(itemId);
      onCartUpdate();
    } catch (error) {
      console.error('Failed to save for later:', error);
    } finally {
      setLoading(null);
    }
  };

  const handleMoveToCart = async (itemId: number) => {
    setLoading(itemId);
    try {
      await api.moveToCart(itemId);
      onCartUpdate();
    } catch (error) {
      console.error('Failed to move to cart:', error);
    } finally {
      setLoading(null);
    }
  };

  if (!cart) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-8">
        <div className="flex justify-center">
          <div className="spinner"></div>
        </div>
      </div>
    );
  }

  const isEmpty = cart.items.length === 0;

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Cart Items */}
        <div className="lg:col-span-3">
          <div className="bg-white p-6 rounded">
            <h1 className="text-2xl font-bold mb-2">Shopping Cart</h1>

            {isEmpty ? (
              <div className="py-8 text-center">
                <div className="text-6xl mb-4">🛒</div>
                <h2 className="text-xl font-bold mb-2">Your Mercato Cart is empty</h2>
                <p className="text-[var(--text-secondary)] mb-4">
                  Check your Saved for later items below or <Link to="/" className="text-[var(--link-color)] hover:underline">continue shopping</Link>.
                </p>
                <Link to="/" className="btn-primary inline-block">Continue Shopping</Link>
              </div>
            ) : (
              <>
                <p className="text-sm text-[var(--text-secondary)] mb-4">Price</p>
                <hr className="mb-4" />

                {/* Cart Items List */}
                <div className="space-y-4">
                  {cart.items.map((item) => (
                    <div key={item.id} className="flex gap-4 py-4 border-b">
                      {/* Checkbox */}
                      <input type="checkbox" defaultChecked className="mt-2" />

                      {/* Product Image */}
                      <Link to={`/dp/${item.product_asin}`} className="flex-shrink-0">
                        <img
                          src={item.product_image || 'https://via.placeholder.com/180x180'}
                          alt={item.product_title}
                          className="w-44 h-44 object-contain"
                        />
                      </Link>

                      {/* Product Details */}
                      <div className="flex-1">
                        <Link
                          to={`/dp/${item.product_asin}`}
                          className="text-lg hover:text-[var(--link-hover)] hover:underline"
                        >
                          {item.product_title}
                        </Link>

                        <p className="text-sm text-[var(--success-color)] mt-1">In Stock</p>

                        <div className="flex items-center gap-1 mt-1">
                          <span className="prime-badge text-xs">prime</span>
                          <span className="text-xs">FREE Delivery</span>
                        </div>

                        {item.is_gift && (
                          <p className="text-sm text-[var(--text-secondary)] mt-1">
                            ✓ This is a gift
                          </p>
                        )}

                        {/* Actions */}
                        <div className="flex items-center gap-4 mt-3">
                          {/* Quantity */}
                          <div className="flex items-center border rounded">
                            <select
                              value={item.quantity}
                              onChange={(e) => handleQuantityChange(item.id, parseInt(e.target.value))}
                              disabled={loading === item.id}
                              className="px-2 py-1 bg-gray-100 rounded text-sm"
                            >
                              {[...Array(30)].map((_, i) => (
                                <option key={i + 1} value={i + 1}>Qty: {i + 1}</option>
                              ))}
                            </select>
                          </div>

                          <span className="text-[var(--border)]">|</span>

                          <button
                            onClick={() => handleRemove(item.id)}
                            disabled={loading === item.id}
                            className="text-sm text-[var(--link-color)] hover:text-[var(--link-hover)] hover:underline"
                          >
                            Delete
                          </button>

                          <span className="text-[var(--border)]">|</span>

                          <button
                            onClick={() => handleSaveForLater(item.id)}
                            disabled={loading === item.id}
                            className="text-sm text-[var(--link-color)] hover:text-[var(--link-hover)] hover:underline"
                          >
                            Save for later
                          </button>

                          <span className="text-[var(--border)]">|</span>

                          <Link
                            to={`/dp/${item.product_asin}#related`}
                            className="text-sm text-[var(--link-color)] hover:text-[var(--link-hover)] hover:underline"
                          >
                            Compare with similar items
                          </Link>
                        </div>
                      </div>

                      {/* Price */}
                      <div className="text-right flex-shrink-0">
                        <span className="text-lg font-bold">${item.product_price.toFixed(2)}</span>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Subtotal */}
                <div className="text-right mt-4">
                  <span className="text-lg">
                    Subtotal ({cart.item_count} {cart.item_count === 1 ? 'item' : 'items'}):{' '}
                    <span className="font-bold">${cart.subtotal.toFixed(2)}</span>
                  </span>
                </div>
              </>
            )}
          </div>

          {/* Saved for Later */}
          {cart.saved_for_later.length > 0 && (
            <div className="bg-white p-6 rounded mt-6">
              <h2 className="text-xl font-bold mb-4">
                Saved for later ({cart.saved_for_later.length} {cart.saved_for_later.length === 1 ? 'item' : 'items'})
              </h2>
              <hr className="mb-4" />

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {cart.saved_for_later.map((item) => (
                  <div key={item.id} className="text-center">
                    <img
                      src={item.product_image || 'https://via.placeholder.com/150'}
                      alt={item.product_title}
                      className="w-full h-36 object-contain mb-2"
                    />
                    <Link
                      to={`/dp/${item.product_asin}`}
                      className="text-sm text-[var(--link-color)] hover:underline line-clamp-2"
                    >
                      {item.product_title}
                    </Link>
                    <p className="font-bold mt-1">${item.product_price.toFixed(2)}</p>
                    <p className="text-sm text-[var(--success-color)]">In Stock</p>
                    <button
                      onClick={() => handleMoveToCart(item.id)}
                      disabled={loading === item.id}
                      className="btn-yellow text-sm mt-2 w-full"
                    >
                      Move to Cart
                    </button>
                    <button
                      onClick={() => handleRemove(item.id)}
                      disabled={loading === item.id}
                      className="text-sm text-[var(--link-color)] hover:underline mt-1"
                    >
                      Delete
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Order Summary Sidebar */}
        <div className="lg:col-span-1">
          <div className="bg-white p-4 rounded sticky top-4">
            {!isEmpty && (
              <>
                <div className="flex items-start gap-2 mb-4 text-sm text-[var(--success-color)]">
                  <svg className="w-5 h-5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                  <span>Your order qualifies for FREE Shipping.</span>
                </div>

                <p className="text-lg mb-4">
                  Subtotal ({cart.item_count} {cart.item_count === 1 ? 'item' : 'items'}):{' '}
                  <span className="font-bold">${cart.subtotal.toFixed(2)}</span>
                </p>

                <label className="flex items-center gap-2 mb-4 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    className="rounded"
                    checked={isGiftOrder}
                    onChange={(e) => handleGiftOrderChange(e.target.checked)}
                  />
                  This order contains a gift
                </label>

                <Link
                  to="/gp/buy/spc"
                  className="btn-yellow w-full block text-center"
                >
                  Proceed to checkout
                </Link>
              </>
            )}

            {isEmpty && (
              <Link to="/" className="btn-primary w-full block text-center">
                Continue Shopping
              </Link>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
