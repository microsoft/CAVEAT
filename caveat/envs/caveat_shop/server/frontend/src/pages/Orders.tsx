// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Order } from '../types';

export function Orders() {
  const navigate = useNavigate();
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState('3months');
  const [searchQuery, setSearchQuery] = useState('');

  // Modal states
  const [showTracking, setShowTracking] = useState(false);
  const [showInvoice, setShowInvoice] = useState(false);
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [tracking, setTracking] = useState<any>(null);

  // Review modal states
  const [showReviewModal, setShowReviewModal] = useState(false);
  const [reviewProduct, setReviewProduct] = useState<any>(null);
  const [reviewRating, setReviewRating] = useState(5);
  const [reviewTitle, setReviewTitle] = useState('');
  const [reviewBody, setReviewBody] = useState('');
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewError, setReviewError] = useState('');

  useEffect(() => {
    loadOrders();
  }, [period]);

  const loadOrders = async (query?: string) => {
    setLoading(true);
    try {
      const result = await api.getOrders({
        period,
        q: query || undefined
      });
      setOrders(result.orders || []);
    } catch (error) {
      console.error('Failed to load orders:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = () => {
    loadOrders(searchQuery);
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  const handleTrackPackage = async (order: Order) => {
    try {
      const result = await api.getOrderTracking(order.id);
      setTracking(result);
      setSelectedOrder(order);
      setShowTracking(true);
    } catch (error) {
      console.error('Failed to load tracking:', error);
    }
  };

  const handleShowInvoice = (order: Order) => {
    setSelectedOrder(order);
    setShowInvoice(true);
  };

  const handleBuyAgain = (_order: Order) => {
    // Navigate to Buy Again page
    navigate('/gp/buyagain');
  };

  const handleWriteReview = (product: any) => {
    setReviewProduct(product);
    setReviewRating(5);
    setReviewTitle('');
    setReviewBody('');
    setReviewError('');
    setShowReviewModal(true);
  };

  const handleSubmitReview = async () => {
    if (!reviewProduct) return;
    if (!reviewTitle.trim()) {
      setReviewError('Please enter a review title');
      return;
    }
    if (!reviewBody.trim()) {
      setReviewError('Please enter your review');
      return;
    }

    setReviewSubmitting(true);
    setReviewError('');
    try {
      await api.createReview(reviewProduct.id, {
        rating: reviewRating,
        title: reviewTitle,
        body: reviewBody,
      });
      setShowReviewModal(false);
      // Optionally show success message
    } catch (error: any) {
      setReviewError(error.message || 'Failed to submit review');
    } finally {
      setReviewSubmitting(false);
    }
  };

  const handleArchiveOrder = async (orderId: number) => {
    try {
      await api.archiveOrder(orderId);
      // Remove the order from the list
      setOrders(orders.filter(o => o.id !== orderId));
    } catch (error) {
      console.error('Failed to archive order:', error);
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

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-3xl font-bold">Your Orders</h1>
        <div className="flex items-center gap-4">
          <input
            type="text"
            placeholder="Search all orders"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={handleSearchKeyDown}
            className="border rounded px-3 py-2 w-64"
          />
          <button onClick={handleSearch} className="btn-primary">Search Orders</button>
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-6 border-b mb-6">
        <button className="pb-2 border-b-2 border-[var(--caveat-shop-primary)] text-[var(--caveat-shop-primary)] font-medium">
          Orders
        </button>
        <Link to="/gp/buyagain" className="pb-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
          Buy Again
        </Link>
        <Link to="/gp/css/account/archived" className="pb-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
          Archived Orders
        </Link>
      </div>

      {/* Period Filter */}
      <div className="flex items-center gap-2 mb-6">
        <span className="text-sm">{orders.length} orders placed in</span>
        <select
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className="border rounded px-2 py-1 text-sm"
        >
          <option value="3months">past 3 months</option>
          <option value="6months">past 6 months</option>
          <option value="year">past year</option>
          <option value="all">all time</option>
        </select>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      ) : orders.length === 0 ? (
        <div className="bg-white p-8 rounded text-center">
          <h2 className="text-xl font-bold mb-2">No orders found</h2>
          <p className="text-[var(--text-secondary)] mb-4">
            Looks like you haven't placed any orders yet.
          </p>
          <Link to="/" className="btn-primary">Start Shopping</Link>
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
                    <span className="text-[var(--text-secondary)] block">SHIP TO</span>
                    <span className="text-[var(--link-color)]">Customer</span>
                  </div>
                </div>
                <div className="text-right">
                  <span className="text-[var(--text-secondary)] block">ORDER # {order.order_number}</span>
                  <div className="flex gap-4 mt-1">
                    <Link to={`/gp/your-account/order-details/${order.id}`} className="text-[var(--link-color)] hover:underline">
                      View order details
                    </Link>
                    <span className="text-[var(--border)]">|</span>
                    <button
                      onClick={() => handleShowInvoice(order)}
                      className="text-[var(--link-color)] hover:underline"
                    >
                      Invoice
                    </button>
                  </div>
                </div>
              </div>

              {/* Order Content */}
              <div className="p-4">
                <div className="flex items-start gap-4">
                  <div className="flex-1">
                    <p className={`font-medium ${getStatusColor(order.status)}`}>
                      {getStatusText(order.status)}
                      {order.delivered_at && ` on ${new Date(order.delivered_at).toLocaleDateString()}`}
                      {order.estimated_delivery_end && order.status === 'shipped' && (
                        <> - Arriving by {new Date(order.estimated_delivery_end).toLocaleDateString()}</>
                      )}
                    </p>

                    {/* Order Items */}
                    {order.items && order.items.length > 0 ? (
                      <div className="mt-4 space-y-4">
                        {order.items.map((item) => (
                          <div key={item.id} className="flex gap-4">
                            <img
                              src={item.product?.images?.[0] || 'https://via.placeholder.com/80'}
                              alt={item.product?.title || 'Product'}
                              className="w-20 h-20 object-contain"
                            />
                            <div>
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
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-[var(--text-secondary)] mt-2">
                        {order.shipping_method === 'standard' ? 'Standard Shipping' : 'Express Shipping'}
                      </p>
                    )}
                  </div>

                  {/* Order Actions */}
                  <div className="flex flex-col gap-2 w-48">
                    {order.status === 'delivered' && (
                      <>
                        <button
                          onClick={() => handleBuyAgain(order)}
                          className="btn-secondary w-full"
                        >
                          Buy it again
                        </button>
                        <Link
                          to={`/gp/your-account/order-details/${order.id}`}
                          className="btn-secondary w-full text-center"
                        >
                          View your item
                        </Link>
                        {order.items && order.items.length > 0 && (
                          <button
                            onClick={() => handleWriteReview(order.items![0].product)}
                            className="text-sm text-[var(--link-color)] hover:underline"
                          >
                            Write a product review
                          </button>
                        )}
                      </>
                    )}
                    {order.status === 'shipped' && (
                      <>
                        <button
                          onClick={() => handleTrackPackage(order)}
                          className="btn-secondary w-full"
                        >
                          Track package
                        </button>
                        <Link
                          to={`/gp/your-account/order-details/${order.id}`}
                          className="btn-secondary w-full text-center"
                        >
                          View order details
                        </Link>
                      </>
                    )}
                    {(order.status === 'pending' || order.status === 'processing') && (
                      <>
                        <button className="btn-secondary w-full">Cancel order</button>
                        <Link
                          to={`/gp/your-account/order-details/${order.id}`}
                          className="btn-secondary w-full text-center"
                        >
                          View order details
                        </Link>
                      </>
                    )}
                  </div>
                </div>

                <div className="flex gap-4 mt-4 text-sm">
                  <button
                    onClick={() => handleArchiveOrder(order.id)}
                    className="text-[var(--link-color)] hover:underline"
                  >
                    Archive order
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tracking Modal */}
      {showTracking && selectedOrder && tracking && (
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
      {showInvoice && selectedOrder && (
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
                    Invoice for Order #{selectedOrder.order_number}
                  </p>
                </div>
                <div className="text-right text-sm">
                  <p><strong>Invoice Date:</strong></p>
                  <p>{new Date(selectedOrder.placed_at).toLocaleDateString()}</p>
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
                  {selectedOrder.items?.map((item: any) => (
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
                    <span>${selectedOrder.subtotal.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between py-1">
                    <span>Shipping:</span>
                    <span>${selectedOrder.shipping_cost.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between py-1">
                    <span>Tax:</span>
                    <span>${selectedOrder.tax.toFixed(2)}</span>
                  </div>
                  {selectedOrder.discount > 0 && (
                    <div className="flex justify-between py-1 text-[var(--success-color)]">
                      <span>Discount:</span>
                      <span>-${selectedOrder.discount.toFixed(2)}</span>
                    </div>
                  )}
                  <div className="flex justify-between py-2 border-t font-bold text-lg">
                    <span>Total:</span>
                    <span>${selectedOrder.total.toFixed(2)}</span>
                  </div>
                </div>
              </div>

              {/* Footer */}
              <div className="mt-8 pt-4 border-t text-center text-sm text-[var(--text-secondary)]">
                <p>Thank you for shopping with CAVEAT-Shop!</p>
                <p>Order placed on {new Date(selectedOrder.placed_at).toLocaleDateString()}</p>
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

      {/* Review Modal */}
      {showReviewModal && reviewProduct && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-xl w-full mx-4 max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b flex justify-between items-center">
              <h2 className="text-xl font-bold">Write a Review</h2>
              <button
                onClick={() => setShowReviewModal(false)}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>
            <div className="p-6">
              {/* Product Info */}
              <div className="flex gap-4 mb-6 pb-4 border-b">
                <img
                  src={reviewProduct.images?.[0] || 'https://via.placeholder.com/80'}
                  alt={reviewProduct.title}
                  className="w-20 h-20 object-contain"
                />
                <div>
                  <h3 className="font-medium">{reviewProduct.title}</h3>
                </div>
              </div>

              {/* Rating */}
              <div className="mb-6">
                <label className="block font-medium mb-2">Overall rating</label>
                <div className="flex gap-2">
                  {[1, 2, 3, 4, 5].map((star) => (
                    <button
                      key={star}
                      onClick={() => setReviewRating(star)}
                      className="text-3xl focus:outline-none"
                    >
                      {star <= reviewRating ? (
                        <span className="text-[var(--caveat-shop-primary)]">★</span>
                      ) : (
                        <span className="text-gray-300">★</span>
                      )}
                    </button>
                  ))}
                </div>
              </div>

              {/* Title */}
              <div className="mb-4">
                <label className="block font-medium mb-2">Add a headline</label>
                <input
                  type="text"
                  value={reviewTitle}
                  onChange={(e) => setReviewTitle(e.target.value)}
                  placeholder="What's most important to know?"
                  className="w-full border rounded px-3 py-2"
                />
              </div>

              {/* Body */}
              <div className="mb-4">
                <label className="block font-medium mb-2">Add a written review</label>
                <textarea
                  value={reviewBody}
                  onChange={(e) => setReviewBody(e.target.value)}
                  placeholder="What did you like or dislike? What did you use this product for?"
                  rows={5}
                  className="w-full border rounded px-3 py-2"
                />
              </div>

              {/* Error */}
              {reviewError && (
                <div className="mb-4 p-3 bg-red-100 border border-red-300 rounded text-red-700 text-sm">
                  {reviewError}
                </div>
              )}
            </div>
            <div className="p-4 border-t flex gap-4">
              <button
                onClick={handleSubmitReview}
                disabled={reviewSubmitting}
                className="btn-primary flex-1"
              >
                {reviewSubmitting ? 'Submitting...' : 'Submit Review'}
              </button>
              <button
                onClick={() => setShowReviewModal(false)}
                className="btn-secondary flex-1"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
