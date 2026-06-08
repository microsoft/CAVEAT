import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Wishlist, User } from '../types';

interface WishlistsProps {
  user: User | null;
}

export function Wishlists({ user }: WishlistsProps) {
  const navigate = useNavigate();
  const [wishlists, setWishlists] = useState<Wishlist[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newListName, setNewListName] = useState('');
  const [newListPublic, setNewListPublic] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/hz/wishlist');
      return;
    }
    loadWishlists();
  }, [user]);

  const loadWishlists = async () => {
    try {
      const res = await api.getWishlists();
      setWishlists(res.wishlists || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load wishlists');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateList = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newListName.trim()) return;

    setCreating(true);
    setError('');
    try {
      const res = await api.createWishlist(newListName.trim(), newListPublic);
      setShowCreateModal(false);
      setNewListName('');
      setNewListPublic(false);
      // Navigate to the new wishlist
      navigate(`/hz/wishlist/ls/${res.id}`);
    } catch (err: any) {
      setError(err.message || 'Failed to create wishlist');
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteList = async (id: number) => {
    if (!confirm('Are you sure you want to delete this list?')) return;

    try {
      await api.deleteWishlist(id);
      setWishlists(wishlists.filter(w => w.id !== id));
    } catch (err: any) {
      setError(err.message || 'Failed to delete wishlist');
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
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-3xl font-bold">Your Lists</h1>
        <button
          onClick={() => setShowCreateModal(true)}
          className="btn-primary"
        >
          Create a List
        </button>
      </div>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {/* Wishlists Grid */}
      {wishlists.length === 0 ? (
        <div className="bg-white p-8 rounded text-center">
          <svg className="w-16 h-16 mx-auto text-gray-400 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
          </svg>
          <h2 className="text-xl font-bold mb-2">No lists yet</h2>
          <p className="text-[var(--text-secondary)] mb-4">
            Create a list to save items you want to buy later or share with others.
          </p>
          <button
            onClick={() => setShowCreateModal(true)}
            className="btn-primary"
          >
            Create your first list
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {wishlists.map((wishlist) => (
            <div key={wishlist.id} className="bg-white rounded border hover:shadow-md transition-shadow">
              <Link to={`/hz/wishlist/ls/${wishlist.id}`} className="block p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-bold text-lg text-[var(--link-color)] hover:underline">
                      {wishlist.name}
                    </h3>
                    <p className="text-sm text-[var(--text-secondary)]">
                      {wishlist.is_public ? 'Public' : 'Private'} list
                    </p>
                  </div>
                  {wishlist.is_default && (
                    <span className="text-xs bg-[var(--amazon-orange)] text-white px-2 py-1 rounded">
                      Default
                    </span>
                  )}
                </div>

                {/* Preview images */}
                <div className="mt-4 flex gap-2">
                  {(wishlist as any).preview_images?.length > 0 ? (
                    <>
                      {(wishlist as any).preview_images.slice(0, 3).map((img: string, idx: number) => (
                        <div key={idx} className="w-16 h-16 bg-gray-100 rounded flex items-center justify-center overflow-hidden">
                          <img src={img} alt="" className="w-full h-full object-contain" />
                        </div>
                      ))}
                      {(wishlist as any).preview_images.length < 3 && (
                        <div className="w-16 h-16 bg-gray-100 rounded flex items-center justify-center">
                          <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                          </svg>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="w-16 h-16 bg-gray-100 rounded flex items-center justify-center">
                        <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                        </svg>
                      </div>
                      <div className="w-16 h-16 bg-gray-100 rounded flex items-center justify-center">
                        <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                        </svg>
                      </div>
                    </>
                  )}
                </div>
              </Link>

              {/* Actions */}
              <div className="px-4 pb-4 flex gap-2">
                <Link
                  to={`/hz/wishlist/ls/${wishlist.id}`}
                  className="text-sm text-[var(--link-color)] hover:underline"
                >
                  View list
                </Link>
                {!wishlist.is_default && (
                  <>
                    <span className="text-gray-300">|</span>
                    <button
                      onClick={(e) => {
                        e.preventDefault();
                        handleDeleteList(wishlist.id);
                      }}
                      className="text-sm text-[var(--link-color)] hover:underline"
                    >
                      Delete
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create List Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-md">
            <h2 className="text-xl font-bold mb-4">Create a new list</h2>

            <form onSubmit={handleCreateList}>
              <div className="mb-4">
                <label className="block text-sm font-medium mb-1">List name</label>
                <input
                  type="text"
                  value={newListName}
                  onChange={(e) => setNewListName(e.target.value)}
                  placeholder="e.g., Birthday ideas, Home decor"
                  className="w-full border rounded px-3 py-2"
                  autoFocus
                />
              </div>

              <div className="mb-6">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={newListPublic}
                    onChange={(e) => setNewListPublic(e.target.checked)}
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
                    setShowCreateModal(false);
                    setNewListName('');
                    setNewListPublic(false);
                  }}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating || !newListName.trim()}
                  className="btn-yellow flex-1"
                >
                  {creating ? 'Creating...' : 'Create list'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
