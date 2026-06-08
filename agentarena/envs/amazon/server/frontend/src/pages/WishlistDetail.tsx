import { useState, useEffect } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Wishlist, Product, User } from '../types';
import { StarRating } from '../components/ProductCard';

interface WishlistDetailProps {
  user: User | null;
  onAddToCart: (productId: number) => void;
}

interface WishlistItem {
  id: number;
  product_id: number;
  added_at: string;
  priority: number;
  notes?: string;
  product?: Product;
}

export function WishlistDetail({ user, onAddToCart }: WishlistDetailProps) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [wishlist, setWishlist] = useState<Wishlist | null>(null);
  const [items, setItems] = useState<WishlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showEditModal, setShowEditModal] = useState(false);
  const [editName, setEditName] = useState('');
  const [editPublic, setEditPublic] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/hz/wishlist');
      return;
    }
    if (id) {
      loadWishlist(parseInt(id));
    }
  }, [user, id]);

  const loadWishlist = async (wishlistId: number) => {
    setLoading(true);
    setError('');
    try {
      const res = await api.getWishlist(wishlistId);
      // Backend returns wishlist data directly with items array
      const { items: wishlistItems, ...wishlistData } = res;
      setWishlist(wishlistData as Wishlist);
      setItems(wishlistItems || []);
      setEditName(wishlistData?.name || '');
      setEditPublic(wishlistData?.is_public || false);
    } catch (err: any) {
      setError(err.message || 'Failed to load wishlist');
    } finally {
      setLoading(false);
    }
  };

  const handleRemoveItem = async (itemId: number) => {
    if (!id) return;

    try {
      await api.removeFromWishlist(parseInt(id), itemId);
      setItems(items.filter(item => item.id !== itemId));
    } catch (err: any) {
      setError(err.message || 'Failed to remove item');
    }
  };

  const handleAddToCart = async (productId: number, _itemId: number) => {
    try {
      onAddToCart(productId);
      // Optionally remove from wishlist after adding to cart
      // await handleRemoveItem(itemId);
    } catch (err: any) {
      setError(err.message || 'Failed to add to cart');
    }
  };

  const handleUpdateList = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id || !editName.trim()) return;

    setSaving(true);
    setError('');
    try {
      await api.updateWishlist(parseInt(id), {
        name: editName.trim(),
        is_public: editPublic,
      });
      setWishlist(prev => prev ? { ...prev, name: editName.trim(), is_public: editPublic } : null);
      setShowEditModal(false);
    } catch (err: any) {
      setError(err.message || 'Failed to update list');
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteList = async () => {
    if (!id) return;
    if (!confirm('Are you sure you want to delete this list? This action cannot be undone.')) return;

    try {
      await api.deleteWishlist(parseInt(id));
      navigate('/hz/wishlist');
    } catch (err: any) {
      setError(err.message || 'Failed to delete list');
    }
  };

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      </div>
    );
  }

  if (!wishlist) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="bg-white p-8 rounded text-center">
          <h1 className="text-2xl font-bold mb-4">List not found</h1>
          <p className="text-[var(--text-secondary)] mb-4">
            This list may have been deleted or you don't have permission to view it.
          </p>
          <Link to="/hz/wishlist" className="btn-primary">
            Back to Your Lists
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <nav className="text-sm mb-4">
        <Link to="/hz/wishlist" className="text-[var(--link-color)] hover:underline">
          Your Lists
        </Link>
        <span className="mx-2">/</span>
        <span className="text-[var(--text-secondary)]">{wishlist.name}</span>
      </nav>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {/* Header */}
      <div className="bg-white rounded border p-4 mb-4">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold">{wishlist.name}</h1>
            <p className="text-sm text-[var(--text-secondary)] mt-1">
              {wishlist.is_public ? 'Public' : 'Private'} list • {items.length} item{items.length !== 1 ? 's' : ''}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setShowEditModal(true)}
              className="btn-secondary text-sm"
            >
              Edit list
            </button>
            {!wishlist.is_default && (
              <button
                onClick={handleDeleteList}
                className="btn-secondary text-sm text-red-600 hover:text-red-700"
              >
                Delete list
              </button>
            )}
          </div>
        </div>

        {/* Share link for public lists */}
        {wishlist.is_public && (
          <div className="mt-4 pt-4 border-t">
            <p className="text-sm text-[var(--text-secondary)]">
              Share this list:
              <span className="ml-2 text-[var(--link-color)]">
                {window.location.href}
              </span>
            </p>
          </div>
        )}
      </div>

      {/* Items */}
      {items.length === 0 ? (
        <div className="bg-white p-8 rounded text-center">
          <svg className="w-16 h-16 mx-auto text-gray-400 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
          </svg>
          <h2 className="text-xl font-bold mb-2">This list is empty</h2>
          <p className="text-[var(--text-secondary)] mb-4">
            Start adding items from product pages to save them for later.
          </p>
          <Link to="/" className="btn-primary">
            Start shopping
          </Link>
        </div>
      ) : (
        <div className="space-y-4">
          {items.map((item) => (
            <div key={item.id} className="bg-white rounded border p-4">
              <div className="flex gap-4">
                {/* Product Image */}
                <Link to={`/dp/${item.product?.asin || item.product_id}`} className="flex-shrink-0">
                  <img
                    src={item.product?.images?.[0] || 'https://via.placeholder.com/150'}
                    alt={item.product?.title || 'Product'}
                    className="w-36 h-36 object-contain"
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

                  {/* Rating */}
                  {item.product?.rating && (
                    <div className="flex items-center gap-2 mt-1">
                      <StarRating rating={item.product.rating} />
                      <span className="text-sm text-[var(--link-color)]">
                        {item.product.rating_count?.toLocaleString()}
                      </span>
                    </div>
                  )}

                  {/* Price */}
                  <div className="mt-2">
                    {item.product?.price ? (
                      <div className="flex items-baseline gap-2">
                        <span className="text-xl font-bold">${item.product.price.toFixed(2)}</span>
                        {item.product.list_price && item.product.list_price > item.product.price && (
                          <span className="text-sm text-[var(--text-secondary)] line-through">
                            ${item.product.list_price.toFixed(2)}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span className="text-[var(--text-secondary)]">Price unavailable</span>
                    )}
                  </div>

                  {/* Prime */}
                  {item.product?.is_prime_eligible && (
                    <div className="mt-1">
                      <span className="text-[var(--prime-blue)] font-bold text-sm">prime</span>
                      <span className="text-sm text-[var(--text-secondary)] ml-2">FREE Delivery</span>
                    </div>
                  )}

                  {/* Stock Status */}
                  <p className={`text-sm mt-1 ${item.product?.stock_quantity && item.product.stock_quantity > 0 ? 'text-[var(--success-color)]' : 'text-[var(--error-color)]'}`}>
                    {item.product?.stock_quantity && item.product.stock_quantity > 0 ? 'In Stock' : 'Out of Stock'}
                  </p>

                  {/* Added date */}
                  <p className="text-xs text-[var(--text-secondary)] mt-2">
                    Added on {new Date(item.added_at).toLocaleDateString()}
                  </p>
                </div>

                {/* Actions */}
                <div className="flex flex-col gap-2 w-40">
                  <button
                    onClick={() => handleAddToCart(item.product_id, item.id)}
                    className="btn-yellow w-full"
                    disabled={!item.product?.stock_quantity || item.product.stock_quantity <= 0}
                  >
                    Add to Cart
                  </button>
                  <button
                    onClick={() => handleRemoveItem(item.id)}
                    className="btn-secondary w-full text-sm"
                  >
                    Remove
                  </button>
                  <Link
                    to={`/dp/${item.product?.asin || item.product_id}`}
                    className="text-sm text-[var(--link-color)] hover:underline text-center"
                  >
                    View product
                  </Link>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Edit List Modal */}
      {showEditModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-md">
            <h2 className="text-xl font-bold mb-4">Edit list</h2>

            <form onSubmit={handleUpdateList}>
              <div className="mb-4">
                <label className="block text-sm font-medium mb-1">List name</label>
                <input
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full border rounded px-3 py-2"
                  autoFocus
                />
              </div>

              <div className="mb-6">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={editPublic}
                    onChange={(e) => setEditPublic(e.target.checked)}
                  />
                  <span className="text-sm">Make this list public</span>
                </label>
                <p className="text-xs text-[var(--text-secondary)] mt-1 ml-6">
                  Public lists can be shared and viewed by others
                </p>
              </div>

              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => {
                    setShowEditModal(false);
                    setEditName(wishlist.name);
                    setEditPublic(wishlist.is_public);
                  }}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving || !editName.trim()}
                  className="btn-yellow flex-1"
                >
                  {saving ? 'Saving...' : 'Save changes'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
