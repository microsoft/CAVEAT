// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiChevronLeft, FiChevronRight, FiX, FiMinus, FiPlus } from 'react-icons/fi';
import { useAppContext } from '../App';
import { getCategories } from '../api';
import type { CategoryItem } from '../types';

/* SVG icon map — reliable cross-platform rendering (emoji fallback for unmapped) */
const CATEGORY_ICONS: Record<string, React.ReactNode> = {
  Icons: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <polygon points="16,2 20.9,11.9 31.7,13.5 23.8,21.1 25.8,31.8 16,26.6 6.2,31.8 8.2,21.1 0.3,13.5 11.1,11.9" />
    </svg>
  ),
  'Amazing views': (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 28 L12 14 L18 22 L22 18 L28 28 Z" />
      <circle cx="22" cy="8" r="3" />
    </svg>
  ),
  Rooms: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 24 V14 H28 V24" />
      <path d="M4 14 Q4 8, 16 8 Q28 8, 28 14" />
      <line x1="2" y1="24" x2="30" y2="24" />
    </svg>
  ),
  Beachfront: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M16 4 V18 M16 4 Q8 10, 8 18 M16 4 Q24 10, 24 18" />
      <path d="M2 28 Q8 22, 16 28 Q24 22, 30 28" />
    </svg>
  ),
  Cabins: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 18 L16 6 L28 18 V28 H4 Z" />
      <rect x="12" y="20" width="8" height="8" />
    </svg>
  ),
  'OMG!': (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="16" cy="16" r="13" />
      <path d="M10 20 Q16 26, 22 20" />
      <circle cx="11" cy="13" r="1.5" fill="currentColor" />
      <circle cx="21" cy="13" r="1.5" fill="currentColor" />
    </svg>
  ),
  Lakefront: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 20 Q8 16, 16 20 Q24 24, 30 20" />
      <path d="M2 24 Q8 20, 16 24 Q24 28, 30 24" />
      <path d="M10 18 V8 L22 8 V18" />
    </svg>
  ),
  Trending: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M16 4 C16 4 20 12 20 18 C20 22 18 26 16 28 C14 26 12 22 12 18 C12 12 16 4 16 4 Z" />
    </svg>
  ),
  Countryside: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 28 L8 14 L14 28" />
      <path d="M10 28 L18 8 L26 28" />
      <path d="M22 28 L28 16 L30 28" />
    </svg>
  ),
  Mansions: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="6" y="12" width="20" height="16" />
      <path d="M4 12 L16 4 L28 12" />
      <rect x="13" y="20" width="6" height="8" />
      <rect x="8" y="14" width="4" height="4" />
      <rect x="20" y="14" width="4" height="4" />
    </svg>
  ),
  Treehouses: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <line x1="16" y1="18" x2="16" y2="30" />
      <circle cx="16" cy="12" r="8" />
      <rect x="12" y="14" width="8" height="6" />
    </svg>
  ),
  Castles: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M6 28 V10 H10 V6 H14 V10 H18 V6 H22 V10 H26 V28 Z" />
      <rect x="13" y="20" width="6" height="8" />
    </svg>
  ),
  Pools: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 22 Q8 18, 16 22 Q24 26, 30 22" />
      <path d="M2 26 Q8 22, 16 26 Q24 30, 30 26" />
      <path d="M10 20 V8" />
      <path d="M22 20 V8" />
      <path d="M10 8 H22" />
      <path d="M10 14 H22" />
    </svg>
  ),
  Farms: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 28 V14 L16 6 L28 14 V28 Z" />
      <path d="M4 14 L16 22 L28 14" />
      <rect x="13" y="22" width="6" height="6" />
    </svg>
  ),
  Tropical: (
    <svg viewBox="0 0 32 32" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M16 30 V14" />
      <path d="M16 14 Q8 4, 4 8 Q2 14, 16 14" />
      <path d="M16 14 Q24 4, 28 8 Q30 14, 16 14" />
    </svg>
  ),
};

interface CategoryRibbonProps {
  selectedCategory: number | null;
  onSelectCategory: (id: number | null) => void;
}

const ROOM_TYPES = ['Entire place', 'Private room', 'Shared room'];

function ModalCounter({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <div className="flex items-center justify-between py-2">
      <span className="text-sm text-gray-700">{label}</span>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => onChange(Math.max(0, value - 1))}
          disabled={value === 0}
          className="w-8 h-8 rounded-full border border-gray-300 flex items-center justify-center text-gray-600 hover:border-gray-900 disabled:opacity-30 disabled:cursor-not-allowed"
          aria-label={`Decrease ${label}`}
        ><FiMinus className="w-3.5 h-3.5" /></button>
        <span className="w-8 text-center text-sm">{value === 0 ? 'Any' : `${value}+`}</span>
        <button
          type="button"
          onClick={() => onChange(value + 1)}
          className="w-8 h-8 rounded-full border border-gray-300 flex items-center justify-center text-gray-600 hover:border-gray-900"
          aria-label={`Increase ${label}`}
        ><FiPlus className="w-3.5 h-3.5" /></button>
      </div>
    </div>
  );
}

/** Real Filters modal (price range + type of place; Rooms & beds shown only when the served
 *  card payloads carry the spec fields). Applying navigates to /search with the filters set. */
function FiltersModal({ onClose }: { onClose: () => void }) {
  const { searchFilters, setSearchFilters, minimalCards } = useAppContext();
  const navigate = useNavigate();
  const [minPrice, setMinPrice] = useState(searchFilters.min_price?.toString() ?? '');
  const [maxPrice, setMaxPrice] = useState(searchFilters.max_price?.toString() ?? '');
  const [roomType, setRoomType] = useState<string | undefined>(searchFilters.room_type);
  const [bedrooms, setBedrooms] = useState(searchFilters.min_bedrooms ?? 0);
  const [beds, setBeds] = useState(searchFilters.min_beds ?? 0);
  const [bathrooms, setBathrooms] = useState(searchFilters.min_bathrooms ?? 0);

  const apply = () => {
    setSearchFilters({
      ...searchFilters,
      min_price: minPrice ? Number(minPrice) : undefined,
      max_price: maxPrice ? Number(maxPrice) : undefined,
      room_type: roomType,
      min_bedrooms: !minimalCards && bedrooms > 0 ? bedrooms : undefined,
      min_beds: !minimalCards && beds > 0 ? beds : undefined,
      min_bathrooms: !minimalCards && bathrooms > 0 ? bathrooms : undefined,
    });
    onClose();
    navigate('/search');
  };

  const clearAll = () => {
    setMinPrice(''); setMaxPrice(''); setRoomType(undefined);
    setBedrooms(0); setBeds(0); setBathrooms(0);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 sticky top-0 bg-white rounded-t-2xl">
          <button onClick={onClose} className="p-1.5 rounded-full hover:bg-gray-100 transition" aria-label="Close filters">
            <FiX className="w-4 h-4" />
          </button>
          <h3 className="text-base font-semibold text-gray-900">Filters</h3>
          <span className="w-7" />
        </div>

        <div className="px-6 py-4 border-b border-gray-100">
          <h4 className="text-sm font-semibold text-gray-900 mb-3">Price range</h4>
          <p className="text-xs text-gray-500 mb-3">Nightly price before fees and taxes</p>
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <label className="block text-xs text-gray-500 mb-1">Minimum</label>
              <input
                type="number" min={0} placeholder="$0" value={minPrice}
                onChange={(e) => setMinPrice(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
              />
            </div>
            <span className="text-gray-400 mt-4">–</span>
            <div className="flex-1">
              <label className="block text-xs text-gray-500 mb-1">Maximum</label>
              <input
                type="number" min={0} placeholder="Any" value={maxPrice}
                onChange={(e) => setMaxPrice(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
              />
            </div>
          </div>
        </div>

        <div className="px-6 py-4 border-b border-gray-100">
          <h4 className="text-sm font-semibold text-gray-900 mb-3">Type of place</h4>
          <div className="space-y-2">
            {ROOM_TYPES.map((rt) => (
              <label key={rt} className="flex items-center gap-2 cursor-pointer py-1">
                <input
                  type="checkbox"
                  checked={roomType === rt}
                  onChange={() => setRoomType(roomType === rt ? undefined : rt)}
                  className="rounded border-gray-300"
                />
                <span className="text-sm text-gray-700">{rt}</span>
              </label>
            ))}
          </div>
        </div>

        {!minimalCards && (
          <div className="px-6 py-4 border-b border-gray-100">
            <h4 className="text-sm font-semibold text-gray-900 mb-1">Rooms and beds</h4>
            <ModalCounter label="Bedrooms" value={bedrooms} onChange={setBedrooms} />
            <ModalCounter label="Beds" value={beds} onChange={setBeds} />
            <ModalCounter label="Bathrooms" value={bathrooms} onChange={setBathrooms} />
          </div>
        )}

        <div className="flex items-center justify-between px-6 py-4 sticky bottom-0 bg-white rounded-b-2xl border-t border-gray-200">
          <button onClick={clearAll} className="text-sm font-semibold text-gray-900 underline hover:text-gray-600">
            Clear all
          </button>
          <button
            onClick={apply}
            className="bg-gray-900 text-white rounded-lg px-6 py-2.5 text-sm font-semibold hover:bg-gray-800"
          >
            Show places
          </button>
        </div>
      </div>
    </div>
  );
}

export default function CategoryRibbon({ selectedCategory, onSelectCategory }: CategoryRibbonProps) {
  const { showTotalPrice, setShowTotalPrice } = useAppContext();
  const [categories, setCategories] = useState<CategoryItem[]>([]);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  useEffect(() => {
    getCategories()
      .then(setCategories)
      .catch(() => {});
  }, []);

  const checkScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 4);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    checkScroll();
    el.addEventListener('scroll', checkScroll, { passive: true });
    window.addEventListener('resize', checkScroll);
    return () => {
      el.removeEventListener('scroll', checkScroll);
      window.removeEventListener('resize', checkScroll);
    };
  }, [checkScroll, categories]);

  const scroll = (dir: 'left' | 'right') => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({ left: dir === 'left' ? -300 : 300, behavior: 'smooth' });
  };

  if (categories.length === 0) return null;

  return (
    <div className="border-b border-gray-200 bg-white">
      <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-10">
        <div className="flex items-center gap-4 py-3">
          {/* Scrollable category icons */}
          <div className="relative flex-1 min-w-0 group/ribbon">
            {canScrollLeft && (
              <button
                onClick={() => scroll('left')}
                className="absolute left-0 top-1/2 -translate-y-1/2 z-10 w-7 h-7 bg-white border border-gray-300 rounded-full flex items-center justify-center shadow-sm hover:shadow-md transition"
                aria-label="Scroll categories left"
              >
                <FiChevronLeft className="w-3.5 h-3.5 text-gray-700" />
              </button>
            )}

            <div
              ref={scrollRef}
              className="flex gap-8 overflow-x-auto scrollbar-hide scroll-smooth px-1"
              style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
            >
              {categories.map((cat) => {
                const isActive = selectedCategory === cat.id;
                const icon = CATEGORY_ICONS[cat.name];
                return (
                  <button
                    key={cat.id}
                    onClick={() => onSelectCategory(isActive ? null : cat.id)}
                    className={`flex flex-col items-center gap-1.5 min-w-[56px] py-2 border-b-2 transition-all whitespace-nowrap ${
                      isActive
                        ? 'border-gray-900 text-gray-900'
                        : 'border-transparent text-gray-500 hover:text-gray-800 hover:border-gray-300'
                    }`}
                    aria-label={cat.name}
                    title={cat.name}
                  >
                    <span className={`flex items-center justify-center w-6 h-6 ${isActive ? 'opacity-100' : 'opacity-60'}`}>
                      {icon || <span className="text-xl leading-none">{cat.icon}</span>}
                    </span>
                    <span className="text-[11px] font-medium">{cat.name}</span>
                  </button>
                );
              })}
            </div>

            {canScrollRight && (
              <button
                onClick={() => scroll('right')}
                className="absolute right-0 top-1/2 -translate-y-1/2 z-10 w-7 h-7 bg-white border border-gray-300 rounded-full flex items-center justify-center shadow-sm hover:shadow-md transition"
                aria-label="Scroll categories right"
              >
                <FiChevronRight className="w-3.5 h-3.5 text-gray-700" />
              </button>
            )}
          </div>

          {/* Filters button (opens the real filters modal) + total-price toggle */}
          <div className="hidden lg:flex items-center gap-3 pl-4 border-l border-gray-200 flex-shrink-0">
            <button
              onClick={() => setFiltersOpen(true)}
              className="flex items-center gap-2 px-4 py-2.5 border border-gray-300 rounded-xl text-sm font-medium text-gray-700 hover:border-gray-500 transition whitespace-nowrap"
            >
              <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M2 4h12M4 8h8M6 12h4" />
              </svg>
              Filters
            </button>
            <label
              className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer whitespace-nowrap"
              onClick={(e) => { e.preventDefault(); setShowTotalPrice(!showTotalPrice); }}
            >
              <span>Display total before taxes</span>
              <div
                className={`relative w-10 h-5 rounded-full transition-colors ${
                  showTotalPrice ? 'bg-gray-900' : 'bg-gray-300'
                }`}
                role="switch"
                aria-checked={showTotalPrice}
              >
                <div
                  className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
                    showTotalPrice ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                />
              </div>
            </label>
          </div>
        </div>
      </div>

      {filtersOpen && <FiltersModal onClose={() => setFiltersOpen(false)} />}
    </div>
  );
}
