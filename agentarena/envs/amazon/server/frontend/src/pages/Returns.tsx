import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Order, OrderItem, User } from '../types';

interface ReturnsProps {
  user: User | null;
}

const RETURN_REASONS = [
  'Item arrived damaged',
  'Wrong item received',
  'Item not as described',
  'No longer needed',
  'Better price available',
  'Item defective',
  'Missing parts or accessories',
  'Other',
];

export function Returns({ user }: ReturnsProps) {
  const navigate = useNavigate();
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [activeTab, setActiveTab] = useState<'eligible' | 'inprogress' | 'policy'>('eligible');

  // Return modal state
  const [showReturnModal, setShowReturnModal] = useState(false);
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [selectedItem, setSelectedItem] = useState<OrderItem | null>(null);
  const [returnReason, setReturnReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/gp/returns');
      return;
    }
    loadOrders();
  }, [user]);

  const loadOrders = async () => {
    try {
      const res = await api.getOrders({ status: 'delivered' });
      setOrders(res.orders || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load orders');
    } finally {
      setLoading(false);
    }
  };

  const openReturnModal = (order: Order, item: OrderItem) => {
    setSelectedOrder(order);
    setSelectedItem(item);
    setReturnReason('');
    setShowReturnModal(true);
  };

  const handleSubmitReturn = async () => {
    if (!selectedOrder || !selectedItem || !returnReason) {
      setError('Please select a reason for the return');
      return;
    }

    setSubmitting(true);
    setError('');
    try {
      await api.initiateReturn(selectedOrder.id, selectedItem.id, returnReason);
      setSuccess('Return request submitted successfully');
      setShowReturnModal(false);
      loadOrders();
      setTimeout(() => setSuccess(''), 5000);
    } catch (err: any) {
      setError(err.message || 'Failed to submit return request');
    } finally {
      setSubmitting(false);
    }
  };

  const getReturnableItems = () => {
    const items: { order: Order; item: OrderItem }[] = [];
    orders.forEach(order => {
      order.items?.forEach(item => {
        if (item.is_returnable && (!item.return_status || item.return_status === 'none')) {
          items.push({ order, item });
        }
      });
    });
    return items;
  };

  const getInProgressReturns = () => {
    const items: { order: Order; item: OrderItem }[] = [];
    orders.forEach(order => {
      order.items?.forEach(item => {
        if (item.return_status && item.return_status !== 'none' && item.return_status !== 'refunded') {
          items.push({ order, item });
        }
      });
    });
    return items;
  };

  const getReturnStatusBadge = (status: string) => {
    switch (status) {
      case 'requested':
        return <span className="bg-yellow-100 text-yellow-800 text-xs px-2 py-1 rounded">Return Requested</span>;
      case 'approved':
        return <span className="bg-blue-100 text-blue-800 text-xs px-2 py-1 rounded">Approved - Ship Item</span>;
      case 'received':
        return <span className="bg-purple-100 text-purple-800 text-xs px-2 py-1 rounded">Item Received</span>;
      case 'refunded':
        return <span className="bg-green-100 text-green-800 text-xs px-2 py-1 rounded">Refund Complete</span>;
      default:
        return null;
    }
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
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

  const returnableItems = getReturnableItems();
  const inProgressReturns = getInProgressReturns();

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <nav className="text-sm mb-4">
        <Link to="/gp/css/account" className="text-[var(--link-color)] hover:underline">
          Your Account
        </Link>
        <span className="mx-2">&rsaquo;</span>
        <span>Returns & Refunds</span>
      </nav>

      <h1 className="text-3xl font-bold mb-6">Returns & Refunds</h1>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {success && (
        <div className="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
          {success}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-4 border-b mb-6">
        <button
          onClick={() => setActiveTab('eligible')}
          className={`pb-2 px-1 ${activeTab === 'eligible' ? 'border-b-2 border-[var(--amazon-orange)] text-[var(--amazon-orange)] font-medium' : 'text-[var(--text-secondary)]'}`}
        >
          Eligible Items ({returnableItems.length})
        </button>
        <button
          onClick={() => setActiveTab('inprogress')}
          className={`pb-2 px-1 ${activeTab === 'inprogress' ? 'border-b-2 border-[var(--amazon-orange)] text-[var(--amazon-orange)] font-medium' : 'text-[var(--text-secondary)]'}`}
        >
          In Progress ({inProgressReturns.length})
        </button>
        <button
          onClick={() => setActiveTab('policy')}
          className={`pb-2 px-1 ${activeTab === 'policy' ? 'border-b-2 border-[var(--amazon-orange)] text-[var(--amazon-orange)] font-medium' : 'text-[var(--text-secondary)]'}`}
        >
          Return Policy
        </button>
      </div>

      {/* Eligible Items Tab */}
      {activeTab === 'eligible' && (
        <div>
          {returnableItems.length === 0 ? (
            <div className="bg-white border rounded-lg p-8 text-center">
              <div className="text-5xl mb-4">📦</div>
              <h2 className="text-xl font-bold mb-2">No items eligible for return</h2>
              <p className="text-[var(--text-secondary)] mb-4">
                Items are eligible for return within 30 days of delivery.
              </p>
              <Link to="/gp/css/order-history" className="text-[var(--link-color)] hover:underline">
                View your orders
              </Link>
            </div>
          ) : (
            <div className="space-y-4">
              {returnableItems.map(({ order, item }) => (
                <div key={`${order.id}-${item.id}`} className="bg-white border rounded-lg p-4">
                  <div className="flex gap-4">
                    <div className="w-20 h-20 bg-gray-100 rounded flex items-center justify-center">
                      {item.product?.image_url ? (
                        <img src={item.product.image_url} alt={item.product?.title} className="max-w-full max-h-full object-contain" />
                      ) : (
                        <span className="text-2xl">📦</span>
                      )}
                    </div>
                    <div className="flex-1">
                      <h3 className="font-medium">
                        <Link to={`/dp/${item.product?.asin || item.product_id}`} className="hover:text-[var(--link-color)]">
                          {item.product?.title || `Product #${item.product_id}`}
                        </Link>
                      </h3>
                      <p className="text-sm text-[var(--text-secondary)]">
                        Order #{order.order_number} • Delivered {order.delivered_at ? formatDate(order.delivered_at) : 'recently'}
                      </p>
                      <p className="text-sm text-[var(--text-secondary)]">
                        Qty: {item.quantity} • ${item.total_price.toFixed(2)}
                      </p>
                      {item.return_deadline && (
                        <p className="text-sm text-green-600">
                          Return by {formatDate(item.return_deadline)}
                        </p>
                      )}
                    </div>
                    <div>
                      <button
                        onClick={() => openReturnModal(order, item)}
                        className="btn-secondary text-sm"
                      >
                        Return Item
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* In Progress Tab */}
      {activeTab === 'inprogress' && (
        <div>
          {inProgressReturns.length === 0 ? (
            <div className="bg-white border rounded-lg p-8 text-center">
              <div className="text-5xl mb-4">✓</div>
              <h2 className="text-xl font-bold mb-2">No returns in progress</h2>
              <p className="text-[var(--text-secondary)]">
                You don't have any active return requests.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {inProgressReturns.map(({ order, item }) => (
                <div key={`${order.id}-${item.id}`} className="bg-white border rounded-lg p-4">
                  <div className="flex gap-4">
                    <div className="w-20 h-20 bg-gray-100 rounded flex items-center justify-center">
                      {item.product?.image_url ? (
                        <img src={item.product.image_url} alt={item.product?.title} className="max-w-full max-h-full object-contain" />
                      ) : (
                        <span className="text-2xl">📦</span>
                      )}
                    </div>
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="font-medium">
                          {item.product?.title || `Product #${item.product_id}`}
                        </h3>
                        {item.return_status && getReturnStatusBadge(item.return_status)}
                      </div>
                      <p className="text-sm text-[var(--text-secondary)]">
                        Order #{order.order_number}
                      </p>
                      <p className="text-sm text-[var(--text-secondary)]">
                        Refund amount: ${item.total_price.toFixed(2)}
                      </p>

                      {/* Return Progress */}
                      <div className="mt-3">
                        <div className="flex items-center gap-2 text-xs">
                          <div className={`w-4 h-4 rounded-full flex items-center justify-center ${item.return_status === 'requested' || item.return_status === 'approved' || item.return_status === 'received' ? 'bg-green-500 text-white' : 'bg-gray-200'}`}>
                            {item.return_status !== 'none' && '✓'}
                          </div>
                          <span>Requested</span>
                          <div className="flex-1 h-0.5 bg-gray-200">
                            <div className={`h-full ${item.return_status === 'approved' || item.return_status === 'received' ? 'bg-green-500 w-full' : 'w-0'}`}></div>
                          </div>
                          <div className={`w-4 h-4 rounded-full flex items-center justify-center ${item.return_status === 'approved' || item.return_status === 'received' ? 'bg-green-500 text-white' : 'bg-gray-200'}`}>
                            {(item.return_status === 'approved' || item.return_status === 'received') && '✓'}
                          </div>
                          <span>Approved</span>
                          <div className="flex-1 h-0.5 bg-gray-200">
                            <div className={`h-full ${item.return_status === 'received' ? 'bg-green-500 w-full' : 'w-0'}`}></div>
                          </div>
                          <div className={`w-4 h-4 rounded-full flex items-center justify-center ${item.return_status === 'received' ? 'bg-green-500 text-white' : 'bg-gray-200'}`}>
                            {item.return_status === 'received' && '✓'}
                          </div>
                          <span>Refunded</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Policy Tab */}
      {activeTab === 'policy' && (
        <div className="bg-white border rounded-lg p-6">
          <h2 className="text-xl font-bold mb-4">Return Policy</h2>

          <div className="space-y-6">
            <div>
              <h3 className="font-bold mb-2">30-Day Return Window</h3>
              <p className="text-[var(--text-secondary)]">
                Most items can be returned within 30 days of delivery for a full refund. Items must be in their original condition with all packaging and accessories.
              </p>
            </div>

            <div>
              <h3 className="font-bold mb-2">How Returns Work</h3>
              <ol className="list-decimal list-inside text-[var(--text-secondary)] space-y-2">
                <li>Select the item you want to return from your orders</li>
                <li>Choose a reason for the return</li>
                <li>Print the prepaid return label</li>
                <li>Pack the item securely and attach the label</li>
                <li>Drop off the package at any authorized location</li>
                <li>Receive your refund within 3-5 business days of receipt</li>
              </ol>
            </div>

            <div>
              <h3 className="font-bold mb-2">Non-Returnable Items</h3>
              <ul className="list-disc list-inside text-[var(--text-secondary)] space-y-1">
                <li>Perishable goods (food, flowers, etc.)</li>
                <li>Customized or personalized items</li>
                <li>Hazardous materials</li>
                <li>Digital downloads and gift cards</li>
                <li>Items marked as final sale</li>
              </ul>
            </div>

            <div>
              <h3 className="font-bold mb-2">Refund Methods</h3>
              <p className="text-[var(--text-secondary)]">
                Refunds are issued to your original payment method. Gift card purchases are refunded as gift card balance. Refunds typically process within 3-5 business days after we receive your return.
              </p>
            </div>

            <div className="bg-gray-50 p-4 rounded">
              <h3 className="font-bold mb-2">Need Help?</h3>
              <p className="text-[var(--text-secondary)] mb-3">
                If you have questions about a return or need assistance, our customer service team is here to help.
              </p>
              <Link to="/gp/help/customer" className="btn-secondary inline-block">
                Contact Customer Service
              </Link>
            </div>
          </div>
        </div>
      )}

      {/* Return Modal */}
      {showReturnModal && selectedOrder && selectedItem && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b flex justify-between items-center">
              <h2 className="text-xl font-bold">Return Item</h2>
              <button
                onClick={() => setShowReturnModal(false)}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>

            <div className="p-6">
              {/* Item Summary */}
              <div className="flex gap-4 mb-6 pb-4 border-b">
                <div className="w-16 h-16 bg-gray-100 rounded flex items-center justify-center">
                  {selectedItem.product?.image_url ? (
                    <img src={selectedItem.product.image_url} alt={selectedItem.product?.title} className="max-w-full max-h-full object-contain" />
                  ) : (
                    <span className="text-xl">📦</span>
                  )}
                </div>
                <div>
                  <h3 className="font-medium">{selectedItem.product?.title || `Product #${selectedItem.product_id}`}</h3>
                  <p className="text-sm text-[var(--text-secondary)]">Qty: {selectedItem.quantity}</p>
                  <p className="text-sm font-medium">Refund: ${selectedItem.total_price.toFixed(2)}</p>
                </div>
              </div>

              {/* Return Reason */}
              <div className="mb-6">
                <label className="block font-medium mb-2">Why are you returning this item?</label>
                <select
                  value={returnReason}
                  onChange={(e) => setReturnReason(e.target.value)}
                  className="w-full border rounded px-3 py-2"
                >
                  <option value="">Select a reason</option>
                  {RETURN_REASONS.map((reason) => (
                    <option key={reason} value={reason}>{reason}</option>
                  ))}
                </select>
              </div>

              {/* Return Info */}
              <div className="bg-gray-50 p-4 rounded mb-6">
                <h4 className="font-medium mb-2">What happens next?</h4>
                <ul className="text-sm text-[var(--text-secondary)] space-y-1">
                  <li>1. We'll email you a prepaid return shipping label</li>
                  <li>2. Pack the item and attach the label</li>
                  <li>3. Drop off at any authorized shipping location</li>
                  <li>4. Refund issued within 3-5 business days of receipt</li>
                </ul>
              </div>

              {/* Actions */}
              <div className="flex gap-3">
                <button
                  onClick={handleSubmitReturn}
                  disabled={submitting || !returnReason}
                  className="btn-yellow flex-1"
                >
                  {submitting ? 'Submitting...' : 'Submit Return Request'}
                </button>
                <button
                  onClick={() => setShowReturnModal(false)}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
