// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useState, useEffect } from 'react';
import { useSearchParams, useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { ProductCard, StarRating } from '../components/ProductCard';
import type { Product, Department } from '../types';

interface SearchResultsProps {
  departments: Department[];
  onAddToCart: (productId: number) => void;
}

export function SearchResults({ departments, onAddToCart }: SearchResultsProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const { department: pathDepartment } = useParams<{ department?: string }>();
  const navigate = useNavigate();
  const [products, setProducts] = useState<Product[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const limit = 24;

  const query = searchParams.get('q') || '';
  // Use path param first (from /b/:department), then fall back to query param
  const department = pathDepartment || searchParams.get('department') || '';
  const minPrice = searchParams.get('min_price') || '';
  const maxPrice = searchParams.get('max_price') || '';
  const minRating = searchParams.get('min_rating') || '';
  const sortBy = searchParams.get('sort') || 'featured';
  const primeOnly = searchParams.get('prime') === 'true';

  useEffect(() => {
    loadProducts();
  }, [query, department, minPrice, maxPrice, minRating, sortBy, primeOnly, page, pathDepartment]);

  const loadProducts = async () => {
    setLoading(true);
    try {
      const params: any = {
        q: query || undefined,
        department: department || undefined,
        min_price: minPrice ? parseFloat(minPrice) : undefined,
        max_price: maxPrice ? parseFloat(maxPrice) : undefined,
        min_rating: minRating ? parseFloat(minRating) : undefined,
        sort: sortBy,
        prime: primeOnly || undefined,
        limit,
        offset: (page - 1) * limit,
      };

      // Always use getProducts which supports all filters
      const result = await api.getProducts(params);

      setProducts(result.products || []);
      setTotal(result.total || 0);
    } catch (error) {
      console.error('Failed to search products:', error);
    } finally {
      setLoading(false);
    }
  };

  const updateFilter = (key: string, value: string) => {
    const newParams = new URLSearchParams(searchParams);
    if (value) {
      newParams.set(key, value);
    } else {
      newParams.delete(key);
    }
    newParams.delete('page');
    setPage(1);

    // If we're on /b/:department route and changing any filter,
    // navigate to /s with query params to preserve filter state
    if (pathDepartment) {
      // Add current path department to params if not overridden
      if (key !== 'department' && !newParams.has('department')) {
        newParams.set('department', pathDepartment);
      }
      navigate(`/s?${newParams.toString()}`);
    } else {
      setSearchParams(newParams);
    }
  };

  const totalPages = Math.ceil(total / limit);

  return (
    <div className="max-w-7xl mx-auto px-4 py-4">
      <div className="flex gap-6">
        {/* Sidebar Filters */}
        <aside className="w-64 flex-shrink-0 hidden lg:block">
          {/* Department Filter */}
          <div className="mb-6">
            <h3 className="font-bold mb-2">Department</h3>
            <ul className="space-y-1">
              <li>
                <button
                  onClick={() => updateFilter('department', '')}
                  className={`text-sm ${!department ? 'font-bold' : 'text-[var(--link-color)] hover:text-[var(--link-hover)] hover:underline'}`}
                >
                  All Departments
                </button>
              </li>
              {departments.map((dept) => (
                <li key={dept.id}>
                  <button
                    onClick={() => updateFilter('department', dept.slug)}
                    className={`text-sm ${department === dept.slug ? 'font-bold' : 'text-[var(--link-color)] hover:text-[var(--link-hover)] hover:underline'}`}
                  >
                    {dept.name}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Rating Filter */}
          <div className="mb-6">
            <h3 className="font-bold mb-2">Customer Reviews</h3>
            <ul className="space-y-2">
              {[4, 3, 2, 1].map((rating) => (
                <li key={rating}>
                  <button
                    onClick={() => updateFilter('min_rating', rating.toString())}
                    className={`flex items-center gap-1 ${minRating === rating.toString() ? 'font-bold' : ''}`}
                  >
                    <StarRating rating={rating} />
                    <span className="text-sm text-[var(--link-color)]">& Up</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Price Filter */}
          <div className="mb-6">
            <h3 className="font-bold mb-2">Price</h3>
            <ul className="space-y-1 text-sm">
              <li>
                <button
                  onClick={() => { updateFilter('min_price', ''); updateFilter('max_price', ''); }}
                  className={`${!minPrice && !maxPrice ? 'font-bold' : 'text-[var(--link-color)] hover:underline'}`}
                >
                  Any Price
                </button>
              </li>
              <li>
                <button
                  onClick={() => { updateFilter('min_price', ''); updateFilter('max_price', '25'); }}
                  className={`${maxPrice === '25' && !minPrice ? 'font-bold' : 'text-[var(--link-color)] hover:underline'}`}
                >
                  Under $25
                </button>
              </li>
              <li>
                <button
                  onClick={() => { updateFilter('min_price', '25'); updateFilter('max_price', '50'); }}
                  className={`${minPrice === '25' && maxPrice === '50' ? 'font-bold' : 'text-[var(--link-color)] hover:underline'}`}
                >
                  $25 to $50
                </button>
              </li>
              <li>
                <button
                  onClick={() => { updateFilter('min_price', '50'); updateFilter('max_price', '100'); }}
                  className={`${minPrice === '50' && maxPrice === '100' ? 'font-bold' : 'text-[var(--link-color)] hover:underline'}`}
                >
                  $50 to $100
                </button>
              </li>
              <li>
                <button
                  onClick={() => { updateFilter('min_price', '100'); updateFilter('max_price', ''); }}
                  className={`${minPrice === '100' && !maxPrice ? 'font-bold' : 'text-[var(--link-color)] hover:underline'}`}
                >
                  $100 & Above
                </button>
              </li>
            </ul>

            <div className="flex items-center gap-2 mt-3">
              <input
                type="number"
                placeholder="Min"
                value={minPrice}
                onChange={(e) => updateFilter('min_price', e.target.value)}
                className="w-20 px-2 py-1 border rounded text-sm"
              />
              <span>-</span>
              <input
                type="number"
                placeholder="Max"
                value={maxPrice}
                onChange={(e) => updateFilter('max_price', e.target.value)}
                className="w-20 px-2 py-1 border rounded text-sm"
              />
              <button
                onClick={loadProducts}
                className="px-3 py-1 bg-gray-100 border rounded text-sm hover:bg-gray-200"
              >
                Go
              </button>
            </div>
          </div>

          {/* Prime Filter */}
          <div className="mb-6">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={primeOnly}
                onChange={(e) => updateFilter('prime', e.target.checked ? 'true' : '')}
                className="rounded"
              />
              <span className="prime-badge">prime</span>
            </label>
          </div>
        </aside>

        {/* Main Content */}
        <main className="flex-1">
          {/* Results Header */}
          <div className="flex items-center justify-between mb-4">
            <div>
              {query && (
                <p className="text-sm text-[var(--text-secondary)]">
                  {total > 0 ? (
                    <>
                      1-{Math.min(page * limit, total)} of {total.toLocaleString()} results for{' '}
                      <span className="text-[var(--link-hover)] font-bold">"{query}"</span>
                    </>
                  ) : (
                    <>No results for "{query}"</>
                  )}
                </p>
              )}
              {!query && (
                <p className="text-sm text-[var(--text-secondary)]">
                  {total.toLocaleString()} results
                </p>
              )}
            </div>

            <div className="flex items-center gap-2">
              <label className="text-sm">Sort by:</label>
              <select
                value={sortBy}
                onChange={(e) => updateFilter('sort', e.target.value)}
                className="border rounded px-2 py-1 text-sm"
              >
                <option value="featured">Featured</option>
                <option value="price_asc">Price: Low to High</option>
                <option value="price_desc">Price: High to Low</option>
                <option value="rating">Avg. Customer Review</option>
                <option value="newest">Newest Arrivals</option>
                <option value="best_selling">Best Selling</option>
              </select>
            </div>
          </div>

          {/* Results Grid */}
          {loading ? (
            <div className="flex justify-center py-12">
              <div className="spinner"></div>
            </div>
          ) : products.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-lg text-[var(--text-secondary)]">No products found</p>
              <p className="text-sm text-[var(--text-muted)] mt-2">
                Try adjusting your search or filter criteria
              </p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                {products.map((product) => (
                  <ProductCard
                    key={product.id}
                    product={product}
                    showAddToCart
                    onAddToCart={onAddToCart}
                  />
                ))}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2 mt-8">
                  <button
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page === 1}
                    className="px-4 py-2 border rounded disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50"
                  >
                    Previous
                  </button>

                  {[...Array(Math.min(5, totalPages))].map((_, i) => {
                    const pageNum = Math.max(1, Math.min(page - 2, totalPages - 4)) + i;
                    if (pageNum > totalPages) return null;
                    return (
                      <button
                        key={pageNum}
                        onClick={() => setPage(pageNum)}
                        className={`px-4 py-2 border rounded ${
                          page === pageNum ? 'bg-[var(--caveat-shop-primary)] text-white' : 'hover:bg-gray-50'
                        }`}
                      >
                        {pageNum}
                      </button>
                    );
                  })}

                  <button
                    onClick={() => setPage(Math.min(totalPages, page + 1))}
                    disabled={page === totalPages}
                    className="px-4 py-2 border rounded disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50"
                  >
                    Next
                  </button>
                </div>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
