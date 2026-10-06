// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useState, useEffect } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Registry, RegistryItem, User, Product } from '../types';
import { StarRating } from '../components/ProductCard';

interface RegistryDetailProps {
  user: User | null;
  onAddToCart: (productId: number, quantity?: number) => void;
}

const REGISTRY_TYPES: Record<string, { label: string; icon: string }> = {
  wedding: { label: 'Wedding Registry', icon: '💒' },
  baby: { label: 'Baby Registry', icon: '👶' },
  birthday: { label: 'Birthday Registry', icon: '🎂' },
  custom: { label: 'Custom Registry', icon: '🎁' },
};

const PRIORITY_COLORS: Record<string, string> = {
  high: 'bg-red-100 text-red-700',
  medium: 'bg-yellow-100 text-yellow-700',
  low: 'bg-gray-100 text-gray-600',
};

export function RegistryDetail({ user, onAddToCart }: RegistryDetailProps) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [registry, setRegistry] = useState<(Registry & { items: RegistryItem[] }) | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Edit mode
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState('');
  const [editEventDate, setEditEventDate] = useState('');
  const [editIsPublic, setEditIsPublic] = useState(true);
  const [saving, setSaving] = useState(false);

  // Add product modal
  const [showAddModal, setShowAddModal] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Product[]>([]);
  const [searching, setSearching] = useState(false);
  const [addingProduct, setAddingProduct] = useState<number | null>(null);

  useEffect(() => {
    if (id) {
      loadRegistry();
    }
  }, [id]);

  const loadRegistry = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await api.getRegistry(parseInt(id!));
      setRegistry(res);
      setEditName(res.name);
      setEditEventDate(res.event_date || '');
      setEditIsPublic(res.is_public);
    } catch (err: any) {
      setError(err.message || 'Failed to load registry');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!registry || !editName.trim()) return;

    setSaving(true);
    setError('');
    try {
      await api.updateRegistry(registry.id, {
        name: editName.trim(),
        event_date: editEventDate || undefined,
        is_public: editIsPublic,
      });
      setRegistry({
        ...registry,
        name: editName.trim(),
        event_date: editEventDate || undefined,
        is_public: editIsPublic,
      });
      setEditing(false);
    } catch (err: any) {
      setError(err.message || 'Failed to update registry');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!registry || !confirm('Are you sure you want to delete this registry?')) return;

    try {
      await api.deleteRegistry(registry.id);
      navigate('/registries');
    } catch (err: any) {
      setError(err.message || 'Failed to delete registry');
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;

    setSearching(true);
    try {
      const res = await api.getProducts({ q: searchQuery, limit: 10 });
      setSearchResults(res.products || []);
    } catch (err) {
      console.error('Search failed:', err);
    } finally {
      setSearching(false);
    }
  };

  const handleAddProduct = async (productId: number) => {
    if (!registry) return;

    setAddingProduct(productId);
    try {
      await api.addRegistryItem(registry.id, {
        product_id: productId,
        quantity_desired: 1,
        priority: 'medium',
      });
      await loadRegistry();
      setShowAddModal(false);
      setSearchQuery('');
      setSearchResults([]);
    } catch (err: any) {
      setError(err.message || 'Failed to add item');
    } finally {
      setAddingProduct(null);
    }
  };

  const handleRemoveItem = async (itemId: number) => {
    if (!registry || !confirm('Remove this item from the registry?')) return;

    try {
      await api.removeRegistryItem(registry.id, itemId);
      setRegistry({
        ...registry,
        items: registry.items.filter(item => item.id !== itemId),
      });
    } catch (err: any) {
      setError(err.message || 'Failed to remove item');
    }
  };

  const handleBuyForRegistry = (item: RegistryItem) => {
    if (item.product) {
      onAddToCart(item.product.id, item.quantity_desired - item.quantity_purchased);
    }
  };

  const isOwner = user && registry && user.id === registry.user_id;
  const typeInfo = registry ? REGISTRY_TYPES[registry.type] || REGISTRY_TYPES.custom : REGISTRY_TYPES.custom;

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      </div>
    );
  }

  if (error && !registry) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">
          {error}
        </div>
        <Link to="/registries" className="text-[var(--link-color)] hover:underline mt-4 inline-block">
          Back to Registries
        </Link>
      </div>
    );
  }

  if (!registry) return null;

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <nav className="text-sm mb-4">
        <Link to="/registries" className="text-[var(--link-color)] hover:underline">
          Registries
        </Link>
        <span className="mx-2">/</span>
        <span className="text-[var(--text-secondary)]">{registry.name}</span>
      </nav>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {/* Header */}
      <div className="bg-white rounded border p-6 mb-6">
        {editing ? (
          <div className="space-y-4">
            <div>
              <label className="block font-medium mb-2">Registry Name</label>
              <input
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full border rounded px-3 py-2"
              />
            </div>
            <div>
              <label className="block font-medium mb-2">Event Date</label>
              <input
                type="date"
                value={editEventDate}
                onChange={(e) => setEditEventDate(e.target.value)}
                className="border rounded px-3 py-2"
              />
            </div>
            <div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editIsPublic}
                  onChange={(e) => setEditIsPublic(e.target.checked)}
                />
                <span>Public registry</span>
              </label>
            </div>
            <div className="flex gap-3">
              <button onClick={handleSave} disabled={saving} className="btn-primary">
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
              <button onClick={() => setEditing(false)} className="btn-secondary">
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="flex items-start justify-between">
            <div className="flex items-start gap-4">
              <span className="text-5xl">{typeInfo.icon}</span>
              <div>
                <h1 className="text-2xl font-bold">{registry.name}</h1>
                <p className="text-[var(--text-secondary)]">
                  {typeInfo.label}
                  {registry.event_date && (
                    <> &middot; {new Date(registry.event_date).toLocaleDateString()}</>
                  )}
                </p>
                <div className="flex items-center gap-2 mt-2">
                  <span className={`text-xs px-2 py-0.5 rounded ${
                    registry.is_public
                      ? 'bg-green-100 text-green-700'
                      : 'bg-gray-100 text-gray-600'
                  }`}>
                    {registry.is_public ? 'Public' : 'Private'}
                  </span>
                  <span className="text-sm text-[var(--text-secondary)]">
                    {registry.items.length} {registry.items.length === 1 ? 'item' : 'items'}
                  </span>
                </div>
              </div>
            </div>
            {isOwner && (
              <div className="flex gap-2">
                <button onClick={() => setEditing(true)} className="btn-secondary">
                  Edit
                </button>
                <button onClick={handleDelete} className="text-[var(--error-color)] hover:underline">
                  Delete
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Add Items Button (Owner only) */}
      {isOwner && (
        <div className="mb-6">
          <button onClick={() => setShowAddModal(true)} className="btn-primary">
            Add Items to Registry
          </button>
        </div>
      )}

      {/* Items List */}
      {registry.items.length === 0 ? (
        <div className="bg-white p-8 rounded text-center">
          <h2 className="text-xl font-bold mb-2">No items yet</h2>
          <p className="text-[var(--text-secondary)] mb-4">
            {isOwner
              ? 'Start adding products to your registry.'
              : 'This registry is empty.'}
          </p>
          {isOwner && (
            <button onClick={() => setShowAddModal(true)} className="btn-primary">
              Add Items
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          {registry.items.map((item) => (
            <div key={item.id} className="bg-white rounded border p-4">
              <div className="flex gap-4">
                {/* Product Image */}
                <Link to={`/dp/${item.product?.asin || item.product_id}`}>
                  <img
                    src={item.product?.images?.[0] || 'https://via.placeholder.com/120'}
                    alt={item.product?.title || 'Product'}
                    className="w-28 h-28 object-contain"
                  />
                </Link>

                {/* Product Info */}
                <div className="flex-1">
                  <Link
                    to={`/dp/${item.product?.asin || item.product_id}`}
                    className="text-lg text-[var(--link-color)] hover:underline line-clamp-2"
                  >
                    {item.product?.title || 'Product'}
                  </Link>

                  {item.product?.rating && (
                    <div className="flex items-center gap-2 mt-1">
                      <StarRating rating={item.product.rating} />
                      <span className="text-sm text-[var(--link-color)]">
                        {item.product.rating_count?.toLocaleString()}
                      </span>
                    </div>
                  )}

                  <div className="flex items-center gap-4 mt-2">
                    <span className="text-lg font-bold text-[var(--price-color)]">
                      ${item.product?.price?.toFixed(2) || '0.00'}
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded ${PRIORITY_COLORS[item.priority] || PRIORITY_COLORS.medium}`}>
                      {item.priority} priority
                    </span>
                  </div>

                  <div className="mt-2 text-sm">
                    <span className="text-[var(--text-secondary)]">
                      Wanted: {item.quantity_desired}
                    </span>
                    {item.quantity_purchased > 0 && (
                      <span className="text-green-600 ml-3">
                        Purchased: {item.quantity_purchased}
                      </span>
                    )}
                    {item.quantity_purchased >= item.quantity_desired && (
                      <span className="ml-3 text-green-600 font-medium">
                        &#10003; Fulfilled
                      </span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex flex-col gap-2">
                  {item.quantity_purchased < item.quantity_desired && (
                    <button
                      onClick={() => handleBuyForRegistry(item)}
                      className="btn-yellow whitespace-nowrap"
                    >
                      Buy as Gift
                    </button>
                  )}
                  {isOwner && (
                    <button
                      onClick={() => handleRemoveItem(item.id)}
                      className="text-sm text-[var(--link-color)] hover:underline"
                    >
                      Remove
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Add Product Modal */}
      {showAddModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b flex justify-between items-center">
              <h2 className="text-xl font-bold">Add Items to Registry</h2>
              <button
                onClick={() => {
                  setShowAddModal(false);
                  setSearchQuery('');
                  setSearchResults([]);
                }}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>
            <div className="p-4">
              {/* Search */}
              <div className="flex gap-2 mb-4">
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                  placeholder="Search for products to add..."
                  className="flex-1 border rounded px-3 py-2"
                />
                <button onClick={handleSearch} disabled={searching} className="btn-primary">
                  {searching ? 'Searching...' : 'Search'}
                </button>
              </div>

              {/* Results */}
              {searchResults.length > 0 ? (
                <div className="space-y-3">
                  {searchResults.map((product) => {
                    const alreadyAdded = registry?.items.some(item => item.product_id === product.id);
                    return (
                      <div key={product.id} className="flex items-center gap-3 p-3 border rounded">
                        <img
                          src={product.images?.[0] || 'https://via.placeholder.com/60'}
                          alt={product.title}
                          className="w-16 h-16 object-contain"
                        />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm line-clamp-2">{product.title}</p>
                          <p className="text-sm font-bold text-[var(--price-color)]">
                            ${product.price.toFixed(2)}
                          </p>
                        </div>
                        {alreadyAdded ? (
                          <span className="text-sm text-green-600">Added</span>
                        ) : (
                          <button
                            onClick={() => handleAddProduct(product.id)}
                            disabled={addingProduct === product.id}
                            className="btn-secondary text-sm whitespace-nowrap"
                          >
                            {addingProduct === product.id ? 'Adding...' : 'Add'}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : searchQuery && !searching ? (
                <p className="text-center text-[var(--text-secondary)] py-4">
                  No products found. Try a different search.
                </p>
              ) : (
                <p className="text-center text-[var(--text-secondary)] py-4">
                  Search for products to add to your registry.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
