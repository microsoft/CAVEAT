import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Address, User } from '../types';

interface AddressesProps {
  user: User | null;
}

const COUNTRIES = ['United States', 'Canada', 'United Kingdom', 'Australia'];
const STATES = ['CA', 'NY', 'TX', 'FL', 'WA', 'IL', 'PA', 'OH', 'GA', 'NC'];

export function Addresses({ user }: AddressesProps) {
  const navigate = useNavigate();
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [editingAddress, setEditingAddress] = useState<Address | null>(null);
  const [saving, setSaving] = useState(false);

  // Form state
  const [formData, setFormData] = useState({
    full_name: '',
    phone: '',
    address_line1: '',
    address_line2: '',
    city: '',
    state: '',
    zip_code: '',
    country: 'United States',
    is_default: false,
    address_type: 'residential',
    delivery_instructions: '',
  });

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/gp/css/account/address');
      return;
    }
    loadAddresses();
  }, [user]);

  const loadAddresses = async () => {
    try {
      const res = await api.getAddresses();
      setAddresses(res.addresses || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load addresses');
    } finally {
      setLoading(false);
    }
  };

  const resetForm = () => {
    setFormData({
      full_name: user?.name || '',
      phone: user?.phone || '',
      address_line1: '',
      address_line2: '',
      city: '',
      state: '',
      zip_code: '',
      country: 'United States',
      is_default: false,
      address_type: 'residential',
      delivery_instructions: '',
    });
    setEditingAddress(null);
  };

  const openAddModal = () => {
    resetForm();
    setShowModal(true);
  };

  const openEditModal = (address: Address) => {
    setEditingAddress(address);
    setFormData({
      full_name: address.full_name,
      phone: address.phone,
      address_line1: address.address_line1,
      address_line2: address.address_line2 || '',
      city: address.city,
      state: address.state,
      zip_code: address.zip_code,
      country: address.country,
      is_default: address.is_default,
      address_type: address.address_type,
      delivery_instructions: address.delivery_instructions || '',
    });
    setShowModal(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.full_name || !formData.address_line1 || !formData.city || !formData.state || !formData.zip_code) {
      setError('Please fill in all required fields');
      return;
    }

    setSaving(true);
    setError('');
    try {
      if (editingAddress) {
        await api.updateAddress(editingAddress.id, formData);
        setSuccess('Address updated successfully');
      } else {
        await api.createAddress(formData as any);
        setSuccess('Address added successfully');
      }
      setShowModal(false);
      resetForm();
      loadAddresses();
      setTimeout(() => setSuccess(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to save address');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this address?')) return;

    try {
      await api.deleteAddress(id);
      setAddresses(addresses.filter(a => a.id !== id));
      setSuccess('Address deleted successfully');
      setTimeout(() => setSuccess(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to delete address');
    }
  };

  const handleSetDefault = async (id: number) => {
    try {
      await api.updateAddress(id, { is_default: true });
      loadAddresses();
      setSuccess('Default address updated');
      setTimeout(() => setSuccess(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to set default address');
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
        <span>Your Addresses</span>
      </nav>

      <h1 className="text-3xl font-bold mb-6">Your Addresses</h1>

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

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Add Address Card */}
        <button
          onClick={openAddModal}
          className="border-2 border-dashed border-gray-300 rounded-lg p-6 flex flex-col items-center justify-center min-h-[200px] hover:border-gray-400 hover:bg-gray-50 transition-colors"
        >
          <div className="text-4xl text-gray-400 mb-2">+</div>
          <span className="text-lg font-medium text-gray-600">Add address</span>
        </button>

        {/* Address Cards */}
        {addresses.map((address) => (
          <div key={address.id} className="bg-white border rounded-lg p-4 flex flex-col min-h-[200px]">
            <div className="flex-1">
              {address.is_default && (
                <div className="inline-block bg-gray-100 text-xs px-2 py-1 rounded mb-2">
                  Default
                </div>
              )}
              <p className="font-bold">{address.full_name}</p>
              <p className="text-sm text-[var(--text-secondary)]">{address.address_line1}</p>
              {address.address_line2 && (
                <p className="text-sm text-[var(--text-secondary)]">{address.address_line2}</p>
              )}
              <p className="text-sm text-[var(--text-secondary)]">
                {address.city}, {address.state} {address.zip_code}
              </p>
              <p className="text-sm text-[var(--text-secondary)]">{address.country}</p>
              {address.phone && (
                <p className="text-sm text-[var(--text-secondary)]">Phone: {address.phone}</p>
              )}
            </div>

            <div className="mt-4 pt-4 border-t flex items-center text-sm">
              <button
                onClick={() => openEditModal(address)}
                className="text-[var(--link-color)] hover:underline"
              >
                Edit
              </button>
              <span className="text-gray-300 mx-2">|</span>
              <button
                onClick={() => handleDelete(address.id)}
                className="text-[var(--link-color)] hover:underline"
              >
                Remove
              </button>
              <span className="text-gray-300 mx-2">|</span>
              {!address.is_default ? (
                <button
                  onClick={() => handleSetDefault(address.id)}
                  className="text-[var(--link-color)] hover:underline"
                >
                  Set as Default
                </button>
              ) : (
                <span className="text-gray-400">Default address</span>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Add/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b flex justify-between items-center">
              <h2 className="text-xl font-bold">
                {editingAddress ? 'Edit address' : 'Add a new address'}
              </h2>
              <button
                onClick={() => {
                  setShowModal(false);
                  resetForm();
                }}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>

            <form onSubmit={handleSubmit} className="p-6 space-y-4">
              <div>
                <label className="block font-medium mb-1">Country/Region</label>
                <select
                  value={formData.country}
                  onChange={(e) => setFormData({ ...formData, country: e.target.value })}
                  className="w-full border rounded px-3 py-2"
                >
                  {COUNTRIES.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block font-medium mb-1">Full name (First and Last name) *</label>
                <input
                  type="text"
                  value={formData.full_name}
                  onChange={(e) => setFormData({ ...formData, full_name: e.target.value })}
                  className="w-full border rounded px-3 py-2"
                  required
                />
              </div>

              <div>
                <label className="block font-medium mb-1">Phone number *</label>
                <input
                  type="tel"
                  value={formData.phone}
                  onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                  className="w-full border rounded px-3 py-2"
                  placeholder="May be used to assist delivery"
                  required
                />
              </div>

              <div>
                <label className="block font-medium mb-1">Address *</label>
                <input
                  type="text"
                  value={formData.address_line1}
                  onChange={(e) => setFormData({ ...formData, address_line1: e.target.value })}
                  className="w-full border rounded px-3 py-2"
                  placeholder="Street address or P.O. Box"
                  required
                />
                <input
                  type="text"
                  value={formData.address_line2}
                  onChange={(e) => setFormData({ ...formData, address_line2: e.target.value })}
                  className="w-full border rounded px-3 py-2 mt-2"
                  placeholder="Apt, suite, unit, building, floor, etc."
                />
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block font-medium mb-1">City *</label>
                  <input
                    type="text"
                    value={formData.city}
                    onChange={(e) => setFormData({ ...formData, city: e.target.value })}
                    className="w-full border rounded px-3 py-2"
                    required
                  />
                </div>
                <div>
                  <label className="block font-medium mb-1">State *</label>
                  <select
                    value={formData.state}
                    onChange={(e) => setFormData({ ...formData, state: e.target.value })}
                    className="w-full border rounded px-3 py-2"
                    required
                  >
                    <option value="">Select</option>
                    {STATES.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block font-medium mb-1">ZIP *</label>
                  <input
                    type="text"
                    value={formData.zip_code}
                    onChange={(e) => setFormData({ ...formData, zip_code: e.target.value })}
                    className="w-full border rounded px-3 py-2"
                    required
                  />
                </div>
              </div>

              <div>
                <label className="block font-medium mb-1">Delivery instructions (optional)</label>
                <textarea
                  value={formData.delivery_instructions}
                  onChange={(e) => setFormData({ ...formData, delivery_instructions: e.target.value })}
                  className="w-full border rounded px-3 py-2"
                  rows={2}
                  placeholder="Add preferences, notes, access codes and more"
                />
              </div>

              <div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.is_default}
                    onChange={(e) => setFormData({ ...formData, is_default: e.target.checked })}
                  />
                  <span>Make this my default address</span>
                </label>
              </div>

              <div className="flex gap-3 pt-4">
                <button type="submit" disabled={saving} className="btn-yellow flex-1">
                  {saving ? 'Saving...' : editingAddress ? 'Save changes' : 'Add address'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowModal(false);
                    resetForm();
                  }}
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
