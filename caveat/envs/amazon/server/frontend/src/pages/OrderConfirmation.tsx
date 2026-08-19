import { useState, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import type { Order, User } from '../types';

interface OrderConfirmationProps {
  user: User | null;
}

export function OrderConfirmation({ user }: OrderConfirmationProps) {
  const [searchParams] = useSearchParams();
  const orderId = searchParams.get('orderId');
  const orderNumber = searchParams.get('orderNumber');

  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (orderId) {
      loadOrder(parseInt(orderId));
    } else {
      setLoading(false);
    }
  }, [orderId]);

  const loadOrder = async (id: number) => {
    try {
      const orderData = await api.getOrder(id);
      setOrder(orderData);
    } catch (err: any) {
      setError(err.message || 'Failed to load order details');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-[var(--background-secondary)] min-h-screen">
      {/* Success Header */}
      <div className="bg-[#067d62] text-white">
        <div className="max-w-4xl mx-auto px-4 py-6">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 bg-white rounded-full flex items-center justify-center">
              <svg className="w-10 h-10 text-[#067d62]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div>
              <h1 className="text-2xl font-bold">Order placed, thank you!</h1>
              <p className="text-green-100">Confirmation will be sent to {user?.email}</p>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-4 py-6">
        {error && (
          <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Main Content */}
          <div className="md:col-span-2 space-y-4">
            {/* Order Info */}
            <div className="bg-white rounded border p-4">
              <div className="flex justify-between items-start mb-4">
                <div>
                  <h2 className="text-lg font-bold">Order #{orderNumber || order?.order_number}</h2>
                  <p className="text-sm text-[var(--text-secondary)]">
                    Placed on {order?.placed_at ? new Date(order.placed_at).toLocaleDateString() : new Date().toLocaleDateString()}
                  </p>
                </div>
                <Link
                  to={`/gp/your-account/order-details/${orderId}`}
                  className="text-[var(--link-color)] hover:underline text-sm"
                >
                  View order details
                </Link>
              </div>

              {/* Delivery Info */}
              <div className="bg-[var(--background-secondary)] p-4 rounded mb-4">
                <div className="flex items-center gap-2 mb-2">
                  <svg className="w-5 h-5 text-[var(--success-color)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
                  </svg>
                  <span className="font-bold text-[var(--success-color)]">
                    Estimated delivery: {order?.estimated_delivery_start
                      ? `${new Date(order.estimated_delivery_start).toLocaleDateString()} - ${new Date(order.estimated_delivery_end!).toLocaleDateString()}`
                      : 'In 2-5 business days'}
                  </span>
                </div>
                <p className="text-sm text-[var(--text-secondary)]">
                  We'll send you an email when your package ships
                </p>
              </div>

              {/* Order Items */}
              {order?.items && order.items.length > 0 ? (
                <div className="space-y-4">
                  {order.items.map((item) => (
                    <div key={item.id} className="flex gap-4">
                      <img
                        src={item.product?.images?.[0] || 'https://via.placeholder.com/80'}
                        alt={item.product?.title || 'Product'}
                        className="w-20 h-20 object-contain"
                      />
                      <div className="flex-1">
                        <Link
                          to={`/dp/${item.product?.asin || item.product_id}`}
                          className="text-[var(--link-color)] hover:underline"
                        >
                          {item.product?.title || 'Product'}
                        </Link>
                        <p className="text-sm text-[var(--text-secondary)]">Qty: {item.quantity}</p>
                        <p className="font-bold">${item.unit_price.toFixed(2)}</p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[var(--text-secondary)]">Order items will appear here shortly.</p>
              )}
            </div>

            {/* Shipping Address */}
            {order?.shipping_address && (
              <div className="bg-white rounded border p-4">
                <h3 className="font-bold mb-2">Shipping address</h3>
                <p>{order.shipping_address.full_name}</p>
                <p>{order.shipping_address.address_line1}</p>
                {order.shipping_address.address_line2 && <p>{order.shipping_address.address_line2}</p>}
                <p>{order.shipping_address.city}, {order.shipping_address.state} {order.shipping_address.zip_code}</p>
                <p>{order.shipping_address.country}</p>
              </div>
            )}

            {/* Actions */}
            <div className="bg-white rounded border p-4">
              <div className="flex flex-wrap gap-4">
                <Link to="/gp/css/order-history" className="btn-secondary">
                  View all orders
                </Link>
                <Link to="/" className="btn-secondary">
                  Continue shopping
                </Link>
              </div>
            </div>
          </div>

          {/* Sidebar */}
          <div className="space-y-4">
            {/* Order Summary */}
            <div className="bg-white rounded border p-4">
              <h3 className="font-bold mb-3">Order Summary</h3>
              {order && (
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span>Subtotal:</span>
                    <span>${order.subtotal?.toFixed(2) || '0.00'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Shipping:</span>
                    <span>${order.shipping_cost?.toFixed(2) || '0.00'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Tax:</span>
                    <span>${order.tax?.toFixed(2) || '0.00'}</span>
                  </div>
                  <div className="flex justify-between font-bold text-lg pt-2 border-t">
                    <span>Total:</span>
                    <span>${order.total?.toFixed(2) || '0.00'}</span>
                  </div>
                </div>
              )}
            </div>

            {/* Prime Promo */}
            <div className="bg-white rounded border p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-[var(--prime-blue)] font-bold">prime</span>
              </div>
              <p className="text-sm mb-3">Get FREE Same-Day or One-Day delivery on millions of items</p>
              <Link to="/gp/prime" className="btn-primary w-full text-sm block text-center">Try Prime FREE</Link>
            </div>

            {/* Recommendations */}
            <div className="bg-white rounded border p-4">
              <h3 className="font-bold mb-3">You might also like</h3>
              <div className="space-y-3">
                <Link to="/dp/B09G9FPHY6" className="flex gap-2 hover:bg-gray-50 p-2 rounded">
                  <img
                    src="https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=60&h=60&fit=crop"
                    alt="Headphones"
                    className="w-12 h-12 object-contain"
                  />
                  <div className="flex-1">
                    <p className="text-sm text-[var(--link-color)] line-clamp-2">Sony WH-1000XM5</p>
                    <p className="text-sm font-bold">$348.00</p>
                  </div>
                </Link>
                <Link to="/dp/B08N5WRWNW" className="flex gap-2 hover:bg-gray-50 p-2 rounded">
                  <img
                    src="https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=60&h=60&fit=crop"
                    alt="Cable"
                    className="w-12 h-12 object-contain"
                  />
                  <div className="flex-1">
                    <p className="text-sm text-[var(--link-color)] line-clamp-2">USB-C Cable 2-Pack</p>
                    <p className="text-sm font-bold">$9.99</p>
                  </div>
                </Link>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
