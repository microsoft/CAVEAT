import React, { useEffect, useState, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAppContext } from '../App';
import { getListings, saveSearch } from '../api';
import type { Listing, SearchFilters } from '../types';
import ListingCard from './ListingCard';
import MapView from './MapView';

const SORT_OPTIONS: { label: string; value: string | undefined }[] = [
  { label: 'Recommended', value: undefined },
  { label: 'Price: low to high', value: 'price_asc' },
  { label: 'Price: high to low', value: 'price_desc' },
  { label: 'Top rated', value: 'rating' },
  { label: 'Newest', value: 'newest' },
];

const ROOM_TYPES = ['Entire place', 'Private room', 'Shared room'];

const PROPERTY_TYPES = [
  'Entire rental unit', 'Private room in rental unit', 'Private room in home',
  'Entire condo', 'Entire home', 'Room in hotel',
  'Entire serviced apartment', 'Entire guest suite',
  'Private room in condo', 'Private room in townhouse',
];

// Neighbourhoods are fetched dynamically from the API inside the component.

const INACTIVE_PILLS = ['Amenities', 'Booking options', 'Accessibility', 'Host language', 'Top-tier stays'];

type OpenDropdown = 'price' | 'room_type' | 'property_type' | 'neighbourhood' | 'rooms_beds' | 'sort' | null;

function useClickOutside(ref: React.RefObject<HTMLElement | null>, handler: () => void) {
  useEffect(() => {
    function listener(e: MouseEvent) {
      if (!ref.current || ref.current.contains(e.target as Node)) return;
      handler();
    }
    document.addEventListener('mousedown', listener);
    return () => document.removeEventListener('mousedown', listener);
  }, [ref, handler]);
}

function Counter({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <div className="flex items-center justify-between py-2">
      <span className="text-sm text-gray-700">{label}</span>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => onChange(Math.max(0, value - 1))}
          disabled={value === 0}
          className="w-8 h-8 rounded-full border border-gray-300 flex items-center justify-center text-gray-600 hover:border-gray-900 disabled:opacity-30 disabled:cursor-not-allowed"
        >–</button>
        <span className="w-6 text-center text-sm">{value}</span>
        <button
          type="button"
          onClick={() => onChange(value + 1)}
          className="w-8 h-8 rounded-full border border-gray-300 flex items-center justify-center text-gray-600 hover:border-gray-900"
        >+</button>
      </div>
    </div>
  );
}

export default function SearchResults() {
  const { searchFilters, setSearchFilters } = useAppContext();
  const [searchParams] = useSearchParams();

  const [listings, setListings] = useState<Listing[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [neighbourhoodCounts, setNeighbourhoodCounts] = useState<Array<{neighbourhood_name: string; count: number}>>([]);
  const [loading, setLoading] = useState(true);
  const [openDropdown, setOpenDropdown] = useState<OpenDropdown>(null);

  const [neighbourhoods, setNeighbourhoods] = useState<Array<{id: number; name: string; listing_count: number}>>([]);
  useEffect(() => {
    fetch('/api/neighbourhoods').then(res => res.json()).then(data => setNeighbourhoods(data)).catch(() => {});
  }, []);

  // Local draft state for price filter
  const [draftMinPrice, setDraftMinPrice] = useState<string>(searchFilters.min_price?.toString() ?? '');
  const [draftMaxPrice, setDraftMaxPrice] = useState<string>(searchFilters.max_price?.toString() ?? '');

  const dropdownRef = useRef<HTMLDivElement>(null);

  const closeDropdown = useCallback(() => setOpenDropdown(null), []);
  useClickOutside(dropdownRef, closeDropdown);

  const effectiveFilters: SearchFilters = {
    location: searchParams.get('location') || searchFilters.location || undefined,
    check_in: searchParams.get('check_in') || searchFilters.check_in || undefined,
    check_out: searchParams.get('check_out') || searchFilters.check_out || undefined,
    guests: searchParams.get('guests') ? Number(searchParams.get('guests')) : searchFilters.guests || undefined,
    min_price: searchFilters.min_price,
    max_price: searchFilters.max_price,
    property_type: searchFilters.property_type,
    room_type: searchFilters.room_type,
    min_bedrooms: searchFilters.min_bedrooms,
    min_beds: searchFilters.min_beds,
    min_bathrooms: searchFilters.min_bathrooms,
    sort_by: searchFilters.sort_by,
    neighbourhood_id: searchFilters.neighbourhood_id,
  };

  const filterKey = JSON.stringify(effectiveFilters);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getListings(effectiveFilters)
      .then((result) => {
        if (!cancelled) {
          setListings(result.listings);
          setTotalCount(result.total);
          setNeighbourhoodCounts(result.neighbourhood_counts || []);
          // Save search to history
          const query = effectiveFilters.location || '';
          if (query) {
            saveSearch({ query, filters: JSON.stringify(effectiveFilters), result_count: result.total }).catch(() => {});
          }
        }
      })
      .catch(() => {
        if (!cancelled) {
          setListings([]);
          setTotalCount(0);
          setNeighbourhoodCounts([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  const updateFilter = (patch: Partial<SearchFilters>) => {
    setSearchFilters({ ...searchFilters, ...patch });
  };

  const clearFilter = (keys: (keyof SearchFilters)[]) => {
    const next = { ...searchFilters };
    keys.forEach((k) => { (next as Record<string, unknown>)[k] = undefined; });
    setSearchFilters(next);
  };

  // Count active filters
  const activeFilterCount = [
    effectiveFilters.min_price !== undefined || effectiveFilters.max_price !== undefined,
    effectiveFilters.room_type !== undefined,
    effectiveFilters.property_type !== undefined,
    effectiveFilters.neighbourhood_id !== undefined,
    (effectiveFilters.min_bedrooms ?? 0) > 0 || (effectiveFilters.min_beds ?? 0) > 0 || (effectiveFilters.min_bathrooms ?? 0) > 0,
  ].filter(Boolean).length;

  const isPriceActive = effectiveFilters.min_price !== undefined || effectiveFilters.max_price !== undefined;
  const isRoomTypeActive = effectiveFilters.room_type !== undefined;
  const isPropertyTypeActive = effectiveFilters.property_type !== undefined;
  const isNeighbourhoodActive = effectiveFilters.neighbourhood_id !== undefined;
  const isRoomsBedsActive = (effectiveFilters.min_bedrooms ?? 0) > 0 || (effectiveFilters.min_beds ?? 0) > 0 || (effectiveFilters.min_bathrooms ?? 0) > 0;

  const pillClass = (active: boolean) =>
    `flex-shrink-0 px-4 py-2 rounded-full text-sm border transition-colors flex items-center gap-1 ${
      active
        ? 'bg-gray-900 text-white border-gray-900'
        : 'bg-white text-gray-700 border-gray-300 hover:border-gray-900'
    }`;

  const toggleDropdown = (name: OpenDropdown) => {
    setOpenDropdown(openDropdown === name ? null : name);
    if (name === 'price' && openDropdown !== 'price') {
      setDraftMinPrice(searchFilters.min_price?.toString() ?? '');
      setDraftMaxPrice(searchFilters.max_price?.toString() ?? '');
    }
  };

  const locationLabel = effectiveFilters.location;
  const sortLabel = SORT_OPTIONS.find((o) => o.value === effectiveFilters.sort_by)?.label ?? 'Recommended';

  return (
    <div className="pt-[160px] min-h-screen flex flex-col">
      {/* Filter pills bar */}
      <div className="border-b border-gray-200 bg-white sticky top-[140px] z-10">
        <div className="px-6 py-3">
          <div className="flex items-center gap-2 overflow-x-auto no-scrollbar" ref={dropdownRef}>
            {/* Price pill */}
            <div className="relative flex-shrink-0">
              <button onClick={() => toggleDropdown('price')} className={pillClass(isPriceActive)}>
                <span>Price</span>
                {isPriceActive && (
                  <span
                    onClick={(e) => { e.stopPropagation(); clearFilter(['min_price', 'max_price']); }}
                    className="ml-1 cursor-pointer"
                  >×</span>
                )}
              </button>
              {openDropdown === 'price' && (
                <div className="absolute top-full left-0 mt-2 bg-white border border-gray-200 rounded-xl shadow-lg p-4 z-20 w-64">
                  <div className="space-y-3">
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">Min price</label>
                      <input
                        type="number"
                        min={0}
                        placeholder="0"
                        value={draftMinPrice}
                        onChange={(e) => setDraftMinPrice(e.target.value)}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">Max price</label>
                      <input
                        type="number"
                        min={0}
                        placeholder="Any"
                        value={draftMaxPrice}
                        onChange={(e) => setDraftMaxPrice(e.target.value)}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
                      />
                    </div>
                    <button
                      onClick={() => {
                        updateFilter({
                          min_price: draftMinPrice ? Number(draftMinPrice) : undefined,
                          max_price: draftMaxPrice ? Number(draftMaxPrice) : undefined,
                        });
                        setOpenDropdown(null);
                      }}
                      className="w-full bg-gray-900 text-white rounded-lg py-2 text-sm font-medium hover:bg-gray-800"
                    >Apply</button>
                  </div>
                </div>
              )}
            </div>

            {/* Type of place pill */}
            <div className="relative flex-shrink-0">
              <button onClick={() => toggleDropdown('room_type')} className={pillClass(isRoomTypeActive)}>
                <span>Type of place</span>
                {isRoomTypeActive && (
                  <span
                    onClick={(e) => { e.stopPropagation(); clearFilter(['room_type']); }}
                    className="ml-1 cursor-pointer"
                  >×</span>
                )}
              </button>
              {openDropdown === 'room_type' && (
                <div className="absolute top-full left-0 mt-2 bg-white border border-gray-200 rounded-xl shadow-lg p-4 z-20 w-56">
                  <div className="space-y-2">
                    {ROOM_TYPES.map((rt) => (
                      <label key={rt} className="flex items-center gap-2 cursor-pointer py-1">
                        <input
                          type="checkbox"
                          checked={effectiveFilters.room_type === rt}
                          onChange={() => {
                            updateFilter({ room_type: effectiveFilters.room_type === rt ? undefined : rt });
                            setOpenDropdown(null);
                          }}
                          className="rounded border-gray-300"
                        />
                        <span className="text-sm text-gray-700">{rt}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Property type pill */}
            <div className="relative flex-shrink-0">
              <button onClick={() => toggleDropdown('property_type')} className={pillClass(isPropertyTypeActive)}>
                <span>Property type</span>
                {isPropertyTypeActive && (
                  <span
                    onClick={(e) => { e.stopPropagation(); clearFilter(['property_type']); }}
                    className="ml-1 cursor-pointer"
                  >×</span>
                )}
              </button>
              {openDropdown === 'property_type' && (
                <div className="absolute top-full left-0 mt-2 bg-white border border-gray-200 rounded-xl shadow-lg p-4 z-20 w-56 max-h-64 overflow-y-auto">
                  <div className="space-y-2">
                    {PROPERTY_TYPES.map((pt) => (
                      <label key={pt} className="flex items-center gap-2 cursor-pointer py-1">
                        <input
                          type="checkbox"
                          checked={effectiveFilters.property_type === pt}
                          onChange={() => {
                            updateFilter({ property_type: effectiveFilters.property_type === pt ? undefined : pt });
                            setOpenDropdown(null);
                          }}
                          className="rounded border-gray-300"
                        />
                        <span className="text-sm text-gray-700">{pt}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Neighbourhood pill */}
            <div className="relative flex-shrink-0">
              <button onClick={() => toggleDropdown('neighbourhood')} className={pillClass(isNeighbourhoodActive)}>
                <span>Neighbourhood</span>
                {isNeighbourhoodActive && (
                  <span
                    onClick={(e) => { e.stopPropagation(); clearFilter(['neighbourhood_id']); }}
                    className="ml-1 cursor-pointer"
                  >×</span>
                )}
              </button>
              {openDropdown === 'neighbourhood' && (
                <div className="absolute top-full left-0 mt-2 bg-white border border-gray-200 rounded-xl shadow-lg p-4 z-20 w-56 max-h-64 overflow-y-auto">
                  <div className="space-y-2">
                    {neighbourhoods.map((n) => (
                      <label key={n.id} className="flex items-center gap-2 cursor-pointer py-1">
                        <input
                          type="checkbox"
                          checked={effectiveFilters.neighbourhood_id === n.id}
                          onChange={() => {
                            updateFilter({ neighbourhood_id: effectiveFilters.neighbourhood_id === n.id ? undefined : n.id });
                            setOpenDropdown(null);
                          }}
                          className="rounded border-gray-300"
                        />
                        <span className="text-sm text-gray-700">{n.name} ({n.listing_count})</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Rooms and beds pill */}
            <div className="relative flex-shrink-0">
              <button onClick={() => toggleDropdown('rooms_beds')} className={pillClass(isRoomsBedsActive)}>
                <span>Rooms and beds</span>
                {isRoomsBedsActive && (
                  <span
                    onClick={(e) => { e.stopPropagation(); clearFilter(['min_bedrooms', 'min_beds', 'min_bathrooms']); }}
                    className="ml-1 cursor-pointer"
                  >×</span>
                )}
              </button>
              {openDropdown === 'rooms_beds' && (
                <div className="absolute top-full left-0 mt-2 bg-white border border-gray-200 rounded-xl shadow-lg p-4 z-20 w-64">
                  <Counter
                    label="Bedrooms"
                    value={effectiveFilters.min_bedrooms ?? 0}
                    onChange={(v) => updateFilter({ min_bedrooms: v || undefined })}
                  />
                  <Counter
                    label="Beds"
                    value={effectiveFilters.min_beds ?? 0}
                    onChange={(v) => updateFilter({ min_beds: v || undefined })}
                  />
                  <Counter
                    label="Bathrooms"
                    value={effectiveFilters.min_bathrooms ?? 0}
                    onChange={(v) => updateFilter({ min_bathrooms: v || undefined })}
                  />
                </div>
              )}
            </div>

            {/* Non-functional pills */}
            {INACTIVE_PILLS.map((pill) => (
              <button
                key={pill}
                className="flex-shrink-0 px-4 py-2 rounded-full text-sm border bg-white text-gray-700 border-gray-300 hover:border-gray-900 transition-colors"
              >
                {pill}
              </button>
            ))}

            {/* Active filter count badge */}
            {activeFilterCount > 0 && (
              <span className="flex-shrink-0 ml-2 bg-gray-900 text-white text-xs rounded-full w-6 h-6 flex items-center justify-center">
                {activeFilterCount}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Results header with count and sort */}
      <div className="px-6 pt-4 pb-2 flex items-center justify-between">
        {!loading && (
          <div>
            <p className="text-sm text-gray-600">
              {totalCount.toLocaleString()} {totalCount === 1 ? 'place' : 'places'}
              {locationLabel ? ` in ${locationLabel}` : ''}
            </p>
            {neighbourhoodCounts.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-1">
                {neighbourhoodCounts.map((nc) => (
                  <span
                    key={nc.neighbourhood_name}
                    className="inline-flex items-center text-xs bg-gray-100 text-gray-700 rounded-full px-2.5 py-0.5 font-medium"
                  >
                    {nc.count} {nc.count === 1 ? 'listing' : 'listings'} in {nc.neighbourhood_name}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Sort dropdown */}
        <div className="relative">
          <button
            onClick={() => toggleDropdown('sort')}
            className="flex items-center gap-1 text-sm text-gray-700 hover:text-gray-900 font-medium"
          >
            <span>{sortLabel}</span>
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {openDropdown === 'sort' && (
            <div className="absolute top-full right-0 mt-2 bg-white border border-gray-200 rounded-xl shadow-lg py-2 z-20 w-52">
              {SORT_OPTIONS.map((opt) => (
                <button
                  key={opt.label}
                  onClick={() => {
                    updateFilter({ sort_by: opt.value });
                    setOpenDropdown(null);
                  }}
                  className={`w-full text-left px-4 py-2 text-sm hover:bg-gray-50 ${
                    effectiveFilters.sort_by === opt.value ? 'font-semibold text-gray-900' : 'text-gray-700'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Split view: listings + map */}
      <div className="flex flex-1 min-h-0">
        {/* LEFT: listing cards */}
        <div className="w-full lg:w-[58%] overflow-y-auto px-6 pb-8">
          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 pt-2">
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <div key={i} className="animate-pulse">
                  <div className="aspect-square bg-gray-200 rounded-xl mb-2" />
                  <div className="h-4 bg-gray-200 rounded w-3/4 mb-1" />
                  <div className="h-3 bg-gray-100 rounded w-1/2" />
                </div>
              ))}
            </div>
          ) : listings.length === 0 ? (
            <div className="text-center py-16">
              <h2 className="text-xl font-semibold text-gray-900 mb-2">No results found</h2>
              <p className="text-gray-500">
                Try adjusting your search or filters to find what you&apos;re looking for.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 pt-2">
              {listings.map((listing) => (
                <ListingCard key={listing.id} listing={listing} />
              ))}
            </div>
          )}
        </div>

        {/* RIGHT: map */}
        <div className="hidden lg:block lg:w-[42%] sticky top-[200px] h-[calc(100vh-200px)]">
          <MapView listings={listings} />
        </div>
      </div>
    </div>
  );
}
