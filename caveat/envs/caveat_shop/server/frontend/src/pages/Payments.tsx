import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { PaymentMethod, User } from '../types';

interface PaymentsProps {
  user: User | null;
}

const CARD_BRANDS = ['Visa', 'Mastercard', 'American Express', 'Discover'];
const MONTHS = Array.from({ length: 12 }, (_, i) => i + 1);
const YEARS = Array.from({ length: 10 }, (_, i) => new Date().getFullYear() + i);

export function Payments({ user }: PaymentsProps) {
  const navigate = useNavigate();
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [saving, setSaving] = useState(false);

  // Form state
  const [formData, setFormData] = useState({
    cardholder_name: '',
    card_number: '',
    card_brand: 'Visa',
    expiry_month: new Date().getMonth() + 1,
    expiry_year: new Date().getFullYear(),
    is_default: false,
  });

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/gp/css/account/payment');
      return;
    }
    loadPaymentMethods();
  }, [user]);

  const loadPaymentMethods = async () => {
    try {
      const res = await api.getPaymentMethods();
      setPaymentMethods(res.payment_methods || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load payment methods');
    } finally {
      setLoading(false);
    }
  };

  const resetForm = () => {
    setFormData({
      cardholder_name: user?.name || '',
      card_number: '',
      card_brand: 'Visa',
      expiry_month: new Date().getMonth() + 1,
      expiry_year: new Date().getFullYear(),
      is_default: false,
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.cardholder_name || !formData.card_number || formData.card_number.length < 4) {
      setError('Please fill in all required fields');
      return;
    }

    setSaving(true);
    setError('');
    try {
      await api.createPaymentMethod({
        type: 'credit_card',
        cardholder_name: formData.cardholder_name,
        card_number_last4: formData.card_number.slice(-4),
        card_brand: formData.card_brand,
        expiry_month: formData.expiry_month,
        expiry_year: formData.expiry_year,
        is_default: formData.is_default,
      });
      setSuccess('Payment method added successfully');
      setShowModal(false);
      resetForm();
      loadPaymentMethods();
      setTimeout(() => setSuccess(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to add payment method');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to remove this payment method?')) return;

    try {
      await api.deletePaymentMethod(id);
      setPaymentMethods(paymentMethods.filter(p => p.id !== id));
      setSuccess('Payment method removed successfully');
      setTimeout(() => setSuccess(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to remove payment method');
    }
  };

  const getCardIcon = (brand: string) => {
    switch (brand.toLowerCase()) {
      case 'visa':
        return '💳';
      case 'mastercard':
        return '💳';
      case 'american express':
        return '💳';
      default:
        return '💳';
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
    <div className="max-w-4xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <nav className="text-sm mb-4">
        <Link to="/gp/css/account" className="text-[var(--link-color)] hover:underline">
          Your Account
        </Link>
        <span className="mx-2">&rsaquo;</span>
        <span>Your Payments</span>
      </nav>

      <h1 className="text-3xl font-bold mb-6">Manage Your Payment Methods</h1>

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

      {/* Cards Section */}
      <div className="bg-white border rounded-lg mb-6">
        <div className="p-4 border-b">
          <h2 className="text-lg font-bold">Credit and debit cards</h2>
        </div>
        <div className="p-4">
          {paymentMethods.length === 0 ? (
            <p className="text-[var(--text-secondary)]">No payment methods saved.</p>
          ) : (
            <div className="space-y-4">
              {paymentMethods.map((method) => (
                <div key={method.id} className="flex items-center justify-between p-4 border rounded">
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-8 bg-gradient-to-r from-blue-600 to-blue-800 rounded flex items-center justify-center text-white text-xs font-bold">
                      {method.card_brand?.toUpperCase().slice(0, 4) || 'CARD'}
                    </div>
                    <div>
                      <p className="font-medium">
                        {method.card_brand} ending in {method.card_number_last4}
                        {method.is_default && (
                          <span className="ml-2 text-xs bg-gray-100 px-2 py-0.5 rounded">Default</span>
                        )}
                      </p>
                      <p className="text-sm text-[var(--text-secondary)]">
                        {method.cardholder_name} | Expires {method.expiry_month}/{method.expiry_year}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => handleDelete(method.id)}
                    className="text-[var(--link-color)] hover:underline text-sm"
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}

          <button
            onClick={() => {
              resetForm();
              setShowModal(true);
            }}
            className="mt-4 text-[var(--link-color)] hover:underline flex items-center gap-2"
          >
            <span className="text-xl">+</span> Add a credit or debit card
          </button>
        </div>
      </div>

      {/* Gift Cards Section */}
      <div className="bg-white border rounded-lg mb-6">
        <div className="p-4 border-b">
          <h2 className="text-lg font-bold">Gift Cards</h2>
        </div>
        <div className="p-4">
          <p className="text-[var(--text-secondary)] mb-3">
            Use your CAVEAT-Shop Gift Card balance to pay for eligible orders.
          </p>
          <Link to="/gift-cards" className="text-[var(--link-color)] hover:underline">
            Manage Gift Card Balance
          </Link>
        </div>
      </div>

      {/* Transactions Link */}
      <div className="bg-white border rounded-lg">
        <div className="p-4 border-b">
          <h2 className="text-lg font-bold">Transactions</h2>
        </div>
        <div className="p-4">
          <p className="text-[var(--text-secondary)] mb-3">
            View your recent purchases and transactions.
          </p>
          <Link to="/gp/css/order-history" className="text-[var(--link-color)] hover:underline">
            View All Orders
          </Link>
        </div>
      </div>

      {/* Add Payment Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-md w-full mx-4">
            <div className="p-4 border-b flex justify-between items-center">
              <h2 className="text-xl font-bold">Add a credit or debit card</h2>
              <button
                onClick={() => setShowModal(false)}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>

            <form onSubmit={handleSubmit} className="p-6 space-y-4">
              <div>
                <label className="block font-medium mb-1">Card number *</label>
                <input
                  type="text"
                  value={formData.card_number}
                  onChange={(e) => setFormData({ ...formData, card_number: e.target.value.replace(/\D/g, '').slice(0, 16) })}
                  className="w-full border rounded px-3 py-2"
                  placeholder="Enter card number"
                  required
                />
              </div>

              <div>
                <label className="block font-medium mb-1">Name on card *</label>
                <input
                  type="text"
                  value={formData.cardholder_name}
                  onChange={(e) => setFormData({ ...formData, cardholder_name: e.target.value })}
                  className="w-full border rounded px-3 py-2"
                  required
                />
              </div>

              <div>
                <label className="block font-medium mb-1">Card type *</label>
                <select
                  value={formData.card_brand}
                  onChange={(e) => setFormData({ ...formData, card_brand: e.target.value })}
                  className="w-full border rounded px-3 py-2"
                >
                  {CARD_BRANDS.map((brand) => (
                    <option key={brand} value={brand}>{brand}</option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block font-medium mb-1">Expiry month *</label>
                  <select
                    value={formData.expiry_month}
                    onChange={(e) => setFormData({ ...formData, expiry_month: parseInt(e.target.value) })}
                    className="w-full border rounded px-3 py-2"
                  >
                    {MONTHS.map((m) => (
                      <option key={m} value={m}>{m.toString().padStart(2, '0')}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block font-medium mb-1">Expiry year *</label>
                  <select
                    value={formData.expiry_year}
                    onChange={(e) => setFormData({ ...formData, expiry_year: parseInt(e.target.value) })}
                    className="w-full border rounded px-3 py-2"
                  >
                    {YEARS.map((y) => (
                      <option key={y} value={y}>{y}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.is_default}
                    onChange={(e) => setFormData({ ...formData, is_default: e.target.checked })}
                  />
                  <span>Set as default payment method</span>
                </label>
              </div>

              <div className="flex gap-3 pt-4">
                <button type="submit" disabled={saving} className="btn-yellow flex-1">
                  {saving ? 'Adding...' : 'Add your card'}
                </button>
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
