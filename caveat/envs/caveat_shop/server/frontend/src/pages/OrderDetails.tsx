import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api';
import type { Order } from '../types';

export function OrderDetails() {
  const { id } = useParams<{ id: string }>();
  const [order, setOrder] = useState<Order | null>(null);
  const [tracking, setTracking] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [showTracking, setShowTracking] = useState(false);
  const [showInvoice, setShowInvoice] = useState(false);

  useEffect(() => {
    if (id) {
      loadOrder(parseInt(id));
    }
  }, [id]);

  const loadOrder = async (orderId: number) => {
    setLoading(true);
    try {
      const result = await api.getOrder(orderId);
      setOrder(result);
    } catch (error) {
      console.error('Failed to load order:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadTracking = async () => {
    if (!id) return;
    try {
      const result = await api.getOrderTracking(parseInt(id));
      setTracking(result);
      setShowTracking(true);
    } catch (error) {
      console.error('Failed to load tracking:', error);
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'delivered': return 'text-[var(--success-color)]';
      case 'shipped': return 'text-[var(--info-color)]';
      case 'processing': return 'text-[var(--warning-color)]';
      case 'cancelled': return 'text-[var(--error-color)]';
      default: return 'text-[var(--text-secondary)]';
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case 'delivered': return 'Delivered';
      case 'shipped': return 'Shipped';
      case 'processing': return 'Processing';
      case 'pending': return 'Pending';
      case 'cancelled': return 'Cancelled';
      case 'returned': return 'Returned';
      default: return status;
    }
  };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-6">
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      </div>
    );
  }

  if (!order) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-6">
        <div className="bg-white p-8 rounded text-center">
          <h2 className="text-xl font-bold mb-2">Order not found</h2>
          <Link to="/gp/css/order-history" className="text-[var(--link-color)] hover:underline">
            Return to Orders
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <div className="text-sm mb-4">
        <Link to="/gp/css/order-history" className="text-[var(--link-color)] hover:underline">
          Your Orders
        </Link>
        <span className="mx-2">&gt;</span>
        <span>Order Details</span>
      </div>

      {/* Order Header */}
      <div className="bg-white rounded border mb-4">
        <div className="p-4 bg-[var(--background-secondary)] border-b">
          <div className="flex justify-between items-start">
            <div>
              <h1 className="text-xl font-bold mb-2">Order Details</h1>
              <p className="text-sm text-[var(--text-secondary)]">
                Ordered on {new Date(order.placed_at).toLocaleDateString('en-US', {
                  weekday: 'long',
                  year: 'numeric',
                  month: 'long',
                  day: 'numeric'
                })}
              </p>
              <p className="text-sm">Order # {order.order_number}</p>
            </div>
            <div className="flex gap-4">
              <button
                onClick={() => setShowInvoice(true)}
                className="text-[var(--link-color)] hover:underline text-sm"
              >
                View Invoice
              </button>
            </div>
          </div>
        </div>

        {/* Order Status */}
        <div className="p-4 border-b">
          <h2 className={`text-lg font-bold ${getStatusColor(order.status)}`}>
            {getStatusText(order.status)}
            {order.delivered_at && (
              <span className="font-normal text-[var(--text-primary)]">
                {' '}on {new Date(order.delivered_at).toLocaleDateString()}
              </span>
            )}
            {order.estimated_delivery_end && order.status === 'shipped' && (
              <span className="font-normal text-[var(--text-primary)]">
                {' '}- Arriving by {new Date(order.estimated_delivery_end).toLocaleDateString()}
              </span>
            )}
          </h2>

          {order.status === 'shipped' && (
            <button
              onClick={loadTracking}
              className="mt-2 btn-secondary"
            >
              Track Package
            </button>
          )}
        </div>

        {/* Order Items */}
        <div className="p-4">
          <h3 className="font-bold mb-4">Items in this order</h3>
          {order.items && order.items.length > 0 ? (
            <div className="space-y-4">
              {order.items.map((item: any) => (
                <div key={item.id} className="flex gap-4 pb-4 border-b last:border-b-0">
                  <img
                    src={item.product?.images?.[0] || 'https://via.placeholder.com/100'}
                    alt={item.product?.title || 'Product'}
                    className="w-24 h-24 object-contain"
                  />
                  <div className="flex-1">
                    <Link
                      to={`/dp/${item.product?.asin || item.product_id}`}
                      className="text-[var(--link-color)] hover:underline font-medium"
                    >
                      {item.product?.title || 'Product'}
                    </Link>
                    <p className="text-sm text-[var(--text-secondary)] mt-1">
                      Qty: {item.quantity}
                    </p>
                    <p className="font-medium mt-1">${item.unit_price.toFixed(2)}</p>
                    {item.tracking_number && (
                      <p className="text-sm text-[var(--text-secondary)] mt-2">
                        Tracking: {item.tracking_number} ({item.carrier})
                      </p>
                    )}
                  </div>
                  <div className="flex flex-col gap-2">
                    <Link
                      to={`/dp/${item.product?.asin || item.product_id}`}
                      className="btn-secondary text-center text-sm"
                    >
                      View item
                    </Link>
                    {order.status === 'delivered' && (
                      <button className="btn-secondary text-sm">Buy it again</button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[var(--text-secondary)]">No items found</p>
          )}
        </div>
      </div>

      {/* Order Summary */}
      <div className="bg-white rounded border p-4">
        <h3 className="font-bold mb-4">Order Summary</h3>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span>Item(s) Subtotal:</span>
            <span>${order.subtotal.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span>Shipping & Handling:</span>
            <span>${order.shipping_cost.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span>Estimated Tax:</span>
            <span>${order.tax.toFixed(2)}</span>
          </div>
          {order.discount > 0 && (
            <div className="flex justify-between text-[var(--success-color)]">
              <span>Discount:</span>
              <span>-${order.discount.toFixed(2)}</span>
            </div>
          )}
          <div className="flex justify-between font-bold text-lg pt-2 border-t mt-2">
            <span>Grand Total:</span>
            <span>${order.total.toFixed(2)}</span>
          </div>
        </div>
      </div>

      {/* Shipping Address */}
      <div className="bg-white rounded border p-4 mt-4">
        <h3 className="font-bold mb-2">Shipping Address</h3>
        <p className="text-sm">
          {order.shipping_method === 'priority' ? 'Priority Shipping' : 'Standard Shipping'}
        </p>
      </div>

      {/* Tracking Modal */}
      {showTracking && tracking && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-lg w-full mx-4 max-h-[80vh] overflow-y-auto">
            <div className="p-4 border-b flex justify-between items-center">
              <h2 className="text-xl font-bold">Track Package</h2>
              <button
                onClick={() => setShowTracking(false)}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>
            <div className="p-4">
              <p className="text-sm text-[var(--text-secondary)] mb-4">
                Order # {tracking.order_number}
              </p>
              <p className={`font-bold mb-4 ${getStatusColor(tracking.status)}`}>
                Status: {getStatusText(tracking.status)}
              </p>

              {tracking.tracking && tracking.tracking.length > 0 ? (
                <div className="space-y-4">
                  {tracking.tracking.map((item: any, index: number) => (
                    <div key={index} className="border-l-4 border-[var(--caveat-shop-primary)] pl-4">
                      <p className="font-medium">
                        {item.carrier || 'Carrier'}: {item.tracking_number || 'N/A'}
                      </p>
                      <p className="text-sm text-[var(--text-secondary)]">
                        Status: {getStatusText(item.status)}
                      </p>
                      {item.tracking_number && (
                        <a
                          href={`https://www.google.com/search?q=${item.carrier}+tracking+${item.tracking_number}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm text-[var(--link-color)] hover:underline"
                        >
                          Track on carrier website
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[var(--text-secondary)]">No tracking information available</p>
              )}
            </div>
            <div className="p-4 border-t">
              <button
                onClick={() => setShowTracking(false)}
                className="btn-secondary w-full"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Invoice Modal */}
      {showInvoice && order && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b flex justify-between items-center">
              <h2 className="text-xl font-bold">Invoice</h2>
              <button
                onClick={() => setShowInvoice(false)}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>
            <div className="p-6" id="invoice-content">
              {/* Invoice Header */}
              <div className="flex justify-between items-start mb-8">
                <div>
                  <h1 className="text-2xl font-bold text-[var(--caveat-shop-header)]">CAVEAT-Shop</h1>
                  <p className="text-sm text-[var(--text-secondary)] mt-1">
                    Invoice for Order #{order.order_number}
                  </p>
                </div>
                <div className="text-right text-sm">
                  <p><strong>Invoice Date:</strong></p>
                  <p>{new Date(order.placed_at).toLocaleDateString()}</p>
                </div>
              </div>

              {/* Order Info */}
              <div className="grid grid-cols-2 gap-8 mb-8">
                <div>
                  <h3 className="font-bold mb-2">Bill To:</h3>
                  <p className="text-sm">Customer Name</p>
                  <p className="text-sm text-[var(--text-secondary)]">Billing Address</p>
                </div>
                <div>
                  <h3 className="font-bold mb-2">Ship To:</h3>
                  <p className="text-sm">Customer Name</p>
                  <p className="text-sm text-[var(--text-secondary)]">Shipping Address</p>
                </div>
              </div>

              {/* Items Table */}
              <table className="w-full mb-8">
                <thead>
                  <tr className="border-b">
                    <th className="text-left py-2">Item</th>
                    <th className="text-center py-2">Qty</th>
                    <th className="text-right py-2">Price</th>
                    <th className="text-right py-2">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {order.items?.map((item: any) => (
                    <tr key={item.id} className="border-b">
                      <td className="py-2 text-sm">{item.product?.title || 'Product'}</td>
                      <td className="py-2 text-center">{item.quantity}</td>
                      <td className="py-2 text-right">${item.unit_price.toFixed(2)}</td>
                      <td className="py-2 text-right">${item.total_price.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Totals */}
              <div className="flex justify-end">
                <div className="w-64">
                  <div className="flex justify-between py-1">
                    <span>Subtotal:</span>
                    <span>${order.subtotal.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between py-1">
                    <span>Shipping:</span>
                    <span>${order.shipping_cost.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between py-1">
                    <span>Tax:</span>
                    <span>${order.tax.toFixed(2)}</span>
                  </div>
                  {order.discount > 0 && (
                    <div className="flex justify-between py-1 text-[var(--success-color)]">
                      <span>Discount:</span>
                      <span>-${order.discount.toFixed(2)}</span>
                    </div>
                  )}
                  <div className="flex justify-between py-2 border-t font-bold text-lg">
                    <span>Total:</span>
                    <span>${order.total.toFixed(2)}</span>
                  </div>
                </div>
              </div>

              {/* Footer */}
              <div className="mt-8 pt-4 border-t text-center text-sm text-[var(--text-secondary)]">
                <p>Thank you for shopping with CAVEAT-Shop!</p>
                <p>Order placed on {new Date(order.placed_at).toLocaleDateString()}</p>
              </div>
            </div>
            <div className="p-4 border-t flex gap-4">
              <button
                onClick={() => window.print()}
                className="btn-primary flex-1"
              >
                Print Invoice
              </button>
              <button
                onClick={() => setShowInvoice(false)}
                className="btn-secondary flex-1"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
