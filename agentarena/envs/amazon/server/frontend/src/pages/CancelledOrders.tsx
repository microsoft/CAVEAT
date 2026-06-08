import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { Order } from '../types';

export function CancelledOrders() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadArchivedOrders();
  }, []);

  const loadArchivedOrders = async () => {
    setLoading(true);
    try {
      const result = await api.getArchivedOrders();
      setOrders(result.orders || []);
    } catch (error) {
      console.error('Failed to load archived orders:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-3xl font-bold">Archived Orders</h1>
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-6 border-b mb-6">
        <Link to="/gp/css/order-history" className="pb-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
          Orders
        </Link>
        <Link to="/gp/buyagain" className="pb-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
          Buy Again
        </Link>
        <button className="pb-2 border-b-2 border-[var(--amazon-orange)] text-[var(--amazon-orange)] font-medium">
          Archived Orders
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      ) : orders.length === 0 ? (
        <div className="bg-white p-8 rounded text-center">
          <h2 className="text-xl font-bold mb-2">No archived orders</h2>
          <p className="text-[var(--text-secondary)] mb-4">
            You don't have any archived orders.
          </p>
          <Link to="/gp/css/order-history" className="text-[var(--link-color)] hover:underline">
            View all orders
          </Link>
        </div>
      ) : (
        <div className="space-y-4">
          {orders.map((order) => (
            <div key={order.id} className="bg-white rounded border">
              {/* Order Header */}
              <div className="flex items-center justify-between p-4 bg-[var(--background-secondary)] border-b text-sm">
                <div className="flex gap-8">
                  <div>
                    <span className="text-[var(--text-secondary)] block">ORDER PLACED</span>
                    <span>{new Date(order.placed_at).toLocaleDateString()}</span>
                  </div>
                  <div>
                    <span className="text-[var(--text-secondary)] block">TOTAL</span>
                    <span>${order.total.toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="text-[var(--text-secondary)] block">STATUS</span>
                    <span className="capitalize">{order.status}</span>
                  </div>
                </div>
                <div className="text-right">
                  <span className="text-[var(--text-secondary)] block">ORDER # {order.order_number}</span>
                  <Link
                    to={`/gp/your-account/order-details/${order.id}`}
                    className="text-[var(--link-color)] hover:underline"
                  >
                    View order details
                  </Link>
                </div>
              </div>

              {/* Order Content */}
              <div className="p-4">
                <p className="font-medium text-[var(--text-secondary)]">Archived</p>

                {/* Order Items */}
                {order.items && order.items.length > 0 ? (
                  <div className="mt-4 space-y-4">
                    {order.items.map((item: any) => (
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
                          <p className="text-sm text-[var(--text-secondary)] mt-1">
                            Qty: {item.quantity} | ${item.unit_price.toFixed(2)}
                          </p>
                        </div>
                        <div>
                          <Link
                            to={`/dp/${item.product?.asin || item.product_id}`}
                            className="btn-secondary"
                          >
                            Buy it again
                          </Link>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-[var(--text-secondary)] mt-2">
                    Order items not available
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
