import React, { useEffect, useState, useCallback } from 'react';
import { FiX } from 'react-icons/fi';
import { format, parseISO, differenceInYears } from 'date-fns';
import type { Review } from '../types';
import { getListingReviews } from '../api';

interface ReviewSectionProps {
  listingId: number;
  avgRating?: number;
  reviewCount: number;
  isGuestFavourite?: boolean;
}

type RatingKey = 'cleanliness_rating' | 'accuracy_rating' | 'checkin_rating' | 'communication_rating' | 'location_rating' | 'value_rating';

const RATING_CATEGORIES: { key: RatingKey; label: string }[] = [
  { key: 'cleanliness_rating', label: 'Cleanliness' },
  { key: 'accuracy_rating', label: 'Accuracy' },
  { key: 'checkin_rating', label: 'Check-in' },
  { key: 'communication_rating', label: 'Communication' },
  { key: 'location_rating', label: 'Location' },
  { key: 'value_rating', label: 'Value' },
];

function RatingBar({ label, value }: { label: string; value: number }) {
  const percent = (value / 5) * 100;
  return (
    <div className="flex items-center gap-3 py-2">
      <span className="text-sm text-gray-700 w-28 flex-shrink-0">{label}</span>
      <div className="flex-1 h-1 bg-gray-200 rounded-full overflow-hidden">
        <div
          className="h-full bg-gray-800 rounded-full transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-xs font-medium text-gray-700 w-6 text-right">
        {value.toFixed(1)}
      </span>
    </div>
  );
}

function RatingDots({ rating }: { rating: number }) {
  const dots = [];
  for (let i = 1; i <= 5; i++) {
    if (rating >= i) {
      dots.push(<span key={i} className="text-xs text-gray-800">●</span>);
    } else if (rating >= i - 0.5) {
      dots.push(<span key={i} className="text-xs text-gray-400">◐</span>);
    } else {
      dots.push(<span key={i} className="text-xs text-gray-300">●</span>);
    }
  }
  return <div className="flex items-center gap-0.5">{dots}</div>;
}

function ReviewCard({ review }: { review: Review }) {
  const reviewer = review.reviewer;
  const initials = reviewer ? reviewer.name.charAt(0).toUpperCase() : '?';
  const name = reviewer ? reviewer.name : 'Anonymous';
  const dateStr = format(parseISO(review.created_at), 'MMMM yyyy');

  const yearsOnAirbnb = reviewer?.member_since
    ? differenceInYears(new Date(), parseISO(reviewer.member_since))
    : null;

  return (
    <div className="py-6">
      {/* Reviewer info */}
      <div className="flex items-center gap-3 mb-3">
        {reviewer?.avatar_url ? (
          <img
            src={reviewer.avatar_url}
            alt={name}
            className="w-12 h-12 rounded-full object-cover"
          />
        ) : (
          <div className="w-12 h-12 rounded-full bg-gray-800 text-white flex items-center justify-center text-lg font-semibold">
            {initials}
          </div>
        )}
        <div>
          <p className="font-semibold text-sm">{name}</p>
          {yearsOnAirbnb !== null && (
            <p className="text-xs text-gray-500">
              {yearsOnAirbnb <= 0 ? 'New on Airbnb' : `${yearsOnAirbnb} year${yearsOnAirbnb !== 1 ? 's' : ''} on Airbnb`}
            </p>
          )}
        </div>
      </div>

      {/* Rating dots + date */}
      <div className="flex items-center gap-2 mb-2">
        <RatingDots rating={review.overall_rating} />
        <span className="text-xs text-gray-500">·</span>
        <span className="text-xs text-gray-500 font-medium">{dateStr}</span>
      </div>

      {/* Comment */}
      <p className="text-sm text-gray-700 leading-relaxed line-clamp-4">{review.comment}</p>
    </div>
  );
}

export default function ReviewSection({ listingId, avgRating, reviewCount, isGuestFavourite }: ReviewSectionProps) {
  const [reviews, setReviews] = useState<Review[]>([]);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [showAllModal, setShowAllModal] = useState(false);

  const fetchPage = useCallback(
    async (p: number) => {
      setLoading(true);
      try {
        const res = await getListingReviews(listingId, p);
        setReviews((prev) => (p === 1 ? res.reviews : [...prev, ...res.reviews]));
        setTotalPages(res.total_pages);
      } catch {
        // silently fail
      } finally {
        setLoading(false);
      }
    },
    [listingId],
  );

  useEffect(() => {
    fetchPage(1);
  }, [fetchPage]);

  // pull any remaining pages when the "show all" modal opens
  useEffect(() => {
    if (showAllModal && totalPages > 1) {
      for (let p = 2; p <= totalPages; p++) fetchPage(p);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showAllModal]);

  const categoryAverages = RATING_CATEGORIES.map(({ key, label }) => {
    if (reviews.length === 0) return { label, value: 0 };
    const sum = reviews.reduce((acc, r) => acc + (r[key] as number), 0);
    return { label, value: sum / reviews.length };
  });

  if (reviewCount === 0 && !loading) {
    return (
      <div className="py-8">
        <h2 className="text-xl font-semibold">No reviews yet</h2>
      </div>
    );
  }

  const previewReviews = reviews.slice(0, 6);

  return (
    <div className="py-12">
      {/* Large rating display */}
      <div className="flex flex-col items-center text-center mb-8">
        <div className="text-7xl font-bold tracking-tight mb-2">
          {avgRating != null ? avgRating.toFixed(2) : '—'}
        </div>

        {/* Guest favourite badge — only when the listing carries the flag */}
        {isGuestFavourite && (
          <div className="inline-flex items-center gap-3 bg-gradient-to-r from-rose-50 to-pink-50 border border-rose-200 rounded-2xl px-6 py-4 mt-4 max-w-lg">
            <span className="text-3xl">🏅</span>
            <div className="text-left">
              <p className="font-semibold text-gray-900">Guest favourite</p>
              <p className="text-xs text-gray-600 leading-snug">
                One of the most loved homes on Airbnb based on ratings, reviews, and reliability
              </p>
            </div>
          </div>
        )}
      </div>

      {/* 6 category rating bars in 2×3 grid */}
      {reviews.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-16 gap-y-1 mb-10 border-y border-gray-200 py-6">
          {categoryAverages.map(({ label, value }) => (
            <RatingBar key={label} label={label} value={value} />
          ))}
        </div>
      )}

      {/* Loading spinner */}
      {loading && reviews.length === 0 && (
        <div className="flex justify-center py-12">
          <div className="w-8 h-8 border-4 border-gray-200 border-t-gray-800 rounded-full animate-spin" />
        </div>
      )}

      {/* Review cards in 2-column grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-16">
        {previewReviews.map((review) => (
          <ReviewCard key={review.id} review={review} />
        ))}
      </div>

      {/* Show all reviews button — opens the full recent-review list */}
      {reviews.length > 0 && (
        <div className="mt-8">
          <button
            onClick={() => setShowAllModal(true)}
            className="px-6 py-3 border border-gray-800 rounded-lg font-medium text-gray-800 hover:bg-gray-100 transition"
          >
            Show all {reviewCount.toLocaleString()} reviews
          </button>
        </div>
      )}

      {/* All-reviews modal */}
      {showAllModal && (
        <div
          className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center"
          onClick={() => setShowAllModal(false)}
        >
          <div
            className="bg-white w-full max-w-3xl max-h-[85vh] rounded-2xl overflow-y-auto relative mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center gap-4 z-10">
              <button
                onClick={() => setShowAllModal(false)}
                className="p-1.5 rounded-full hover:bg-gray-100 transition"
                aria-label="Close reviews"
              >
                <FiX className="w-4 h-4" />
              </button>
              <div>
                <h3 className="text-base font-semibold text-gray-900">
                  ★ {avgRating != null ? avgRating.toFixed(2) : '—'} · {reviewCount.toLocaleString()} reviews
                </h3>
                <p className="text-xs text-gray-500">Showing the most recent reviews</p>
              </div>
            </div>
            <div className="px-6 py-2 divide-y divide-gray-100">
              {reviews.map((review) => (
                <ReviewCard key={review.id} review={review} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
