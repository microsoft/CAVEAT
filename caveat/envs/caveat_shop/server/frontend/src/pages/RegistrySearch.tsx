// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { Registry } from '../types';

const REGISTRY_TYPES = [
  { value: '', label: 'All Types' },
  { value: 'wedding', label: 'Wedding', icon: '💒' },
  { value: 'baby', label: 'Baby', icon: '👶' },
  { value: 'birthday', label: 'Birthday', icon: '🎂' },
  { value: 'custom', label: 'Custom', icon: '🎁' },
];

const TYPE_INFO: Record<string, { label: string; icon: string }> = {
  wedding: { label: 'Wedding Registry', icon: '💒' },
  baby: { label: 'Baby Registry', icon: '👶' },
  birthday: { label: 'Birthday Registry', icon: '🎂' },
  custom: { label: 'Custom Registry', icon: '🎁' },
};

export function RegistrySearch() {
  const [searchName, setSearchName] = useState('');
  const [searchType, setSearchType] = useState('');
  const [results, setResults] = useState<Registry[]>([]);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchName.trim() && !searchType) {
      setError('Please enter a name or select a type to search');
      return;
    }

    setLoading(true);
    setError('');
    setSearched(true);
    try {
      const res = await api.searchRegistries({
        name: searchName.trim() || undefined,
        type: searchType || undefined,
      });
      setResults(res.registries || []);
    } catch (err: any) {
      setError(err.message || 'Search failed');
      setResults([]);
    } finally {
      setLoading(false);
    }
  };

  const getTypeInfo = (type: string) => {
    return TYPE_INFO[type] || TYPE_INFO.custom;
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold">Find a Registry</h1>
        <p className="text-[var(--text-secondary)] mt-1">
          Search for a friend or family member's gift registry
        </p>
      </div>

      {/* Search Form */}
      <div className="bg-white rounded border p-6 mb-6">
        <form onSubmit={handleSearch}>
          <div className="grid md:grid-cols-3 gap-4">
            <div className="md:col-span-2">
              <label className="block font-medium mb-2">Registrant's Name</label>
              <input
                type="text"
                value={searchName}
                onChange={(e) => setSearchName(e.target.value)}
                placeholder="Enter first or last name"
                className="w-full border rounded px-3 py-2"
              />
            </div>
            <div>
              <label className="block font-medium mb-2">Registry Type</label>
              <select
                value={searchType}
                onChange={(e) => setSearchType(e.target.value)}
                className="w-full border rounded px-3 py-2"
              >
                {REGISTRY_TYPES.map((type) => (
                  <option key={type.value} value={type.value}>
                    {type.icon ? `${type.icon} ${type.label}` : type.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="mt-4 flex gap-4 items-center">
            <button type="submit" disabled={loading} className="btn-primary">
              {loading ? 'Searching...' : 'Search'}
            </button>
            <Link to="/registries" className="text-[var(--link-color)] hover:underline">
              View Your Registries
            </Link>
          </div>
        </form>
      </div>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {/* Results */}
      {searched && (
        <div>
          <h2 className="text-xl font-bold mb-4">
            {results.length === 0
              ? 'No registries found'
              : `Found ${results.length} ${results.length === 1 ? 'registry' : 'registries'}`}
          </h2>

          {results.length === 0 ? (
            <div className="bg-white p-8 rounded text-center">
              <p className="text-[var(--text-secondary)] mb-4">
                We couldn't find any public registries matching your search.
              </p>
              <p className="text-sm text-[var(--text-secondary)]">
                Try a different name or registry type.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {results.map((registry) => {
                const typeInfo = getTypeInfo(registry.type);
                return (
                  <div key={registry.id} className="bg-white rounded border p-4">
                    <div className="flex items-start gap-4">
                      <span className="text-4xl">{typeInfo.icon}</span>
                      <div className="flex-1">
                        <Link
                          to={`/registries/${registry.id}`}
                          className="text-lg font-bold text-[var(--link-color)] hover:underline"
                        >
                          {registry.name}
                        </Link>
                        <p className="text-sm text-[var(--text-secondary)]">
                          {typeInfo.label}
                          {registry.event_date && (
                            <> &middot; Event Date: {new Date(registry.event_date).toLocaleDateString()}</>
                          )}
                        </p>
                        {registry.item_count !== undefined && (
                          <p className="text-sm text-[var(--text-secondary)] mt-1">
                            {registry.item_count} {registry.item_count === 1 ? 'item' : 'items'} on registry
                          </p>
                        )}
                      </div>
                      <Link to={`/registries/${registry.id}`} className="btn-secondary">
                        View Registry
                      </Link>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Info Section */}
      {!searched && (
        <div className="grid md:grid-cols-2 gap-6 mt-8">
          <div className="bg-white rounded border p-6">
            <h3 className="font-bold text-lg mb-3">Looking for a Registry?</h3>
            <p className="text-[var(--text-secondary)] text-sm">
              Search by the registrant's name to find their gift registry. You can then browse their list and purchase gifts that will be shipped directly to them.
            </p>
          </div>
          <div className="bg-white rounded border p-6">
            <h3 className="font-bold text-lg mb-3">Create Your Own Registry</h3>
            <p className="text-[var(--text-secondary)] text-sm mb-3">
              Planning a wedding, baby shower, or special event? Create a registry to share with friends and family.
            </p>
            <Link to="/registries" className="text-[var(--link-color)] hover:underline text-sm">
              Create a Registry &rarr;
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
