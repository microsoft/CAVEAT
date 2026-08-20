import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Registry, User, Address } from '../types';

interface RegistriesProps {
  user: User | null;
}

const REGISTRY_TYPES = [
  { value: 'wedding', label: 'Wedding Registry', icon: '💒' },
  { value: 'baby', label: 'Baby Registry', icon: '👶' },
  { value: 'birthday', label: 'Birthday Registry', icon: '🎂' },
  { value: 'custom', label: 'Custom Registry', icon: '🎁' },
];

export function Registries({ user }: RegistriesProps) {
  const navigate = useNavigate();
  const [registries, setRegistries] = useState<Registry[]>([]);
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  // Create form state
  const [newType, setNewType] = useState('wedding');
  const [newName, setNewName] = useState('');
  const [newEventDate, setNewEventDate] = useState('');
  const [newIsPublic, setNewIsPublic] = useState(true);
  const [newAddressId, setNewAddressId] = useState<number | undefined>();

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/registries');
      return;
    }
    loadData();
  }, [user]);

  const loadData = async () => {
    try {
      const [registriesRes, addressesRes] = await Promise.all([
        api.getRegistries(),
        api.getAddresses(),
      ]);
      setRegistries(registriesRes.registries || []);
      setAddresses(addressesRes.addresses || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load registries');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateRegistry = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) {
      setError('Please enter a registry name');
      return;
    }

    setCreating(true);
    setError('');
    try {
      const res = await api.createRegistry({
        type: newType,
        name: newName.trim(),
        event_date: newEventDate || undefined,
        is_public: newIsPublic,
        shipping_address_id: newAddressId,
      });
      setShowCreateModal(false);
      resetForm();
      navigate(`/registries/${res.id}`);
    } catch (err: any) {
      setError(err.message || 'Failed to create registry');
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteRegistry = async (id: number) => {
    if (!confirm('Are you sure you want to delete this registry?')) return;

    try {
      await api.deleteRegistry(id);
      setRegistries(registries.filter(r => r.id !== id));
    } catch (err: any) {
      setError(err.message || 'Failed to delete registry');
    }
  };

  const resetForm = () => {
    setNewType('wedding');
    setNewName('');
    setNewEventDate('');
    setNewIsPublic(true);
    setNewAddressId(undefined);
  };

  const getTypeInfo = (type: string) => {
    return REGISTRY_TYPES.find(t => t.value === type) || REGISTRY_TYPES[3];
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
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold">Your Registries</h1>
          <p className="text-[var(--text-secondary)] mt-1">
            Create and manage gift registries for special occasions
          </p>
        </div>
        <div className="flex gap-3">
          <Link to="/registries/search" className="btn-secondary">
            Find a Registry
          </Link>
          <button
            onClick={() => setShowCreateModal(true)}
            className="btn-primary"
          >
            Create a Registry
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {/* Registry Types Info */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        {REGISTRY_TYPES.map((type) => (
          <div
            key={type.value}
            onClick={() => {
              setNewType(type.value);
              setShowCreateModal(true);
            }}
            className="bg-white rounded border p-4 text-center cursor-pointer hover:border-[var(--caveat-shop-primary)] transition-colors"
          >
            <span className="text-3xl">{type.icon}</span>
            <p className="font-medium mt-2">{type.label}</p>
          </div>
        ))}
      </div>

      {/* Registries List */}
      {registries.length === 0 ? (
        <div className="bg-white p-8 rounded text-center">
          <h2 className="text-xl font-bold mb-2">No registries yet</h2>
          <p className="text-[var(--text-secondary)] mb-4">
            Create your first registry for a wedding, baby shower, birthday, or any special occasion.
          </p>
          <button
            onClick={() => setShowCreateModal(true)}
            className="btn-primary"
          >
            Create a Registry
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {registries.map((registry) => {
            const typeInfo = getTypeInfo(registry.type);
            return (
              <div key={registry.id} className="bg-white rounded border p-4">
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-4">
                    <span className="text-4xl">{typeInfo.icon}</span>
                    <div>
                      <Link
                        to={`/registries/${registry.id}`}
                        className="text-lg font-bold text-[var(--link-color)] hover:underline"
                      >
                        {registry.name}
                      </Link>
                      <p className="text-sm text-[var(--text-secondary)]">
                        {typeInfo.label}
                        {registry.event_date && (
                          <> &middot; {new Date(registry.event_date).toLocaleDateString()}</>
                        )}
                      </p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          registry.is_public
                            ? 'bg-green-100 text-green-700'
                            : 'bg-gray-100 text-gray-600'
                        }`}>
                          {registry.is_public ? 'Public' : 'Private'}
                        </span>
                        {registry.item_count !== undefined && (
                          <span className="text-xs text-[var(--text-secondary)]">
                            {registry.item_count} {registry.item_count === 1 ? 'item' : 'items'}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Link
                      to={`/registries/${registry.id}`}
                      className="btn-secondary text-sm"
                    >
                      View
                    </Link>
                    <button
                      onClick={() => handleDeleteRegistry(registry.id)}
                      className="text-[var(--error-color)] hover:underline text-sm"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b flex justify-between items-center">
              <h2 className="text-xl font-bold">Create a Registry</h2>
              <button
                onClick={() => {
                  setShowCreateModal(false);
                  resetForm();
                }}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>
            <form onSubmit={handleCreateRegistry} className="p-6">
              {/* Type Selection */}
              <div className="mb-4">
                <label className="block font-medium mb-2">Registry Type</label>
                <div className="grid grid-cols-2 gap-2">
                  {REGISTRY_TYPES.map((type) => (
                    <label
                      key={type.value}
                      className={`flex items-center gap-2 p-3 border rounded cursor-pointer ${
                        newType === type.value
                          ? 'border-[var(--caveat-shop-primary)] bg-orange-50'
                          : 'hover:border-gray-400'
                      }`}
                    >
                      <input
                        type="radio"
                        name="type"
                        value={type.value}
                        checked={newType === type.value}
                        onChange={(e) => setNewType(e.target.value)}
                        className="sr-only"
                      />
                      <span>{type.icon}</span>
                      <span className="text-sm">{type.label}</span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Name */}
              <div className="mb-4">
                <label className="block font-medium mb-2">Registry Name</label>
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g., John & Jane's Wedding"
                  className="w-full border rounded px-3 py-2"
                  required
                />
              </div>

              {/* Event Date */}
              <div className="mb-4">
                <label className="block font-medium mb-2">Event Date (Optional)</label>
                <input
                  type="date"
                  value={newEventDate}
                  onChange={(e) => setNewEventDate(e.target.value)}
                  className="w-full border rounded px-3 py-2"
                />
              </div>

              {/* Shipping Address */}
              {addresses.length > 0 && (
                <div className="mb-4">
                  <label className="block font-medium mb-2">Shipping Address</label>
                  <select
                    value={newAddressId || ''}
                    onChange={(e) => setNewAddressId(e.target.value ? parseInt(e.target.value) : undefined)}
                    className="w-full border rounded px-3 py-2"
                  >
                    <option value="">Select an address</option>
                    {addresses.map((addr) => (
                      <option key={addr.id} value={addr.id}>
                        {addr.full_name} - {addr.address_line1}, {addr.city}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {/* Privacy */}
              <div className="mb-6">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={newIsPublic}
                    onChange={(e) => setNewIsPublic(e.target.checked)}
                  />
                  <span>Make this registry public (searchable by others)</span>
                </label>
              </div>

              {error && (
                <div className="mb-4 p-3 bg-red-100 border border-red-300 rounded text-red-700 text-sm">
                  {error}
                </div>
              )}

              <div className="flex gap-3">
                <button
                  type="submit"
                  disabled={creating}
                  className="btn-primary flex-1"
                >
                  {creating ? 'Creating...' : 'Create Registry'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowCreateModal(false);
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
