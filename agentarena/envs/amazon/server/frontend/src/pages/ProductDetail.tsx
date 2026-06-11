import { useState, useEffect } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { StarRating, ProductCarousel } from '../components/ProductCard';
import type { Product, Review, User, Wishlist } from '../types';

interface ProductDetailProps {
  user: User | null;
  onAddToCart: (productId: number, quantity?: number) => void;
}

export function ProductDetail({ user, onAddToCart }: ProductDetailProps) {
  const { asin } = useParams<{ asin: string }>();
  const navigate = useNavigate();
  const [product, setProduct] = useState<Product | null>(null);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [relatedProducts, setRelatedProducts] = useState<Product[]>([]);
  const [ratingBreakdown, setRatingBreakdown] = useState<{ [key: string]: { count: number; percentage: number } }>({});
  const [frequentlyBought, setFrequentlyBought] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedImage, setSelectedImage] = useState(0);
  const [quantity, setQuantity] = useState(1);
  const [addedToCart, setAddedToCart] = useState(false);

  // Wishlist state
  const [wishlists, setWishlists] = useState<Wishlist[]>([]);
  const [showWishlistDropdown, setShowWishlistDropdown] = useState(false);
  const [addingToWishlist, setAddingToWishlist] = useState(false);
  const [wishlistMessage, setWishlistMessage] = useState('');

  // Review form state
  const [showReviewForm, setShowReviewForm] = useState(false);
  const [reviewRating, setReviewRating] = useState(5);
  const [reviewTitle, setReviewTitle] = useState('');
  const [reviewBody, setReviewBody] = useState('');
  const [submittingReview, setSubmittingReview] = useState(false);
  const [reviewError, setReviewError] = useState('');

  useEffect(() => {
    if (asin) {
      loadProduct();
    }
  }, [asin]);

  const loadProduct = async () => {
    setLoading(true);
    try {
      const productData = await api.getProductByAsin(asin!);
      setProduct(productData);

      const [reviewsRes, relatedRes, frequentRes, summaryRes] = await Promise.all([
        api.getProductReviews(productData.id).catch(() => ({ reviews: [] })),
        api.getProducts({ limit: 6 }).catch(() => ({ products: [] })),
        api.getProducts({ limit: 3 }).catch(() => ({ products: [] })),
        api.getReviewsSummary(productData.id).catch(() => ({ rating_breakdown: {} })),
      ]);

      setReviews(reviewsRes.reviews || []);
      setRelatedProducts(relatedRes.products || []);
      setRatingBreakdown(summaryRes.rating_breakdown || {});
      setFrequentlyBought(frequentRes.products?.filter((p: Product) => p.id !== productData.id).slice(0, 2) || []);
    } catch (error) {
      console.error('Failed to load product:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleAddToCart = () => {
    if (product) {
      onAddToCart(product.id, quantity);
      setAddedToCart(true);
      setTimeout(() => setAddedToCart(false), 3000);
    }
  };

  const handleBuyNow = async () => {
    if (!product) return;
    try {
      await api.addToCart(product.id, quantity);
    } catch (error) {
      console.error('Buy Now failed to add to cart:', error);
      return;                       // don't proceed to checkout if the add failed
    }
    navigate('/gp/buy/spc');        // the Checkout route (NOT /checkout, which doesn't exist)
  };

  const handleAddAllToCart = () => {
    if (product) {
      // Add the main product
      onAddToCart(product.id, 1);
      // Add all frequently bought together products
      frequentlyBought.forEach(p => {
        onAddToCart(p.id, 1);
      });
      setAddedToCart(true);
      setTimeout(() => setAddedToCart(false), 3000);
    }
  };

  const loadWishlists = async () => {
    if (!user) {
      navigate('/ap/signin?returnUrl=' + encodeURIComponent(window.location.pathname));
      return;
    }
    try {
      const res = await api.getWishlists();
      setWishlists(res.wishlists || []);
      setShowWishlistDropdown(true);
    } catch (error) {
      console.error('Failed to load wishlists:', error);
    }
  };

  const handleSubmitReview = async () => {
    if (!user) {
      navigate('/ap/signin?returnUrl=' + encodeURIComponent(window.location.pathname));
      return;
    }
    if (!reviewTitle.trim() || !reviewBody.trim()) {
      setReviewError('Please fill in all fields');
      return;
    }
    setSubmittingReview(true);
    setReviewError('');
    try {
      await api.createReview(product!.id, {
        rating: reviewRating,
        title: reviewTitle,
        body: reviewBody,
      });
      // Reload reviews
      const reviewsRes = await api.getProductReviews(product!.id);
      setReviews(reviewsRes.reviews || []);
      // Reset form
      setShowReviewForm(false);
      setReviewRating(5);
      setReviewTitle('');
      setReviewBody('');
    } catch (error: any) {
      setReviewError(error.message || 'Failed to submit review');
    } finally {
      setSubmittingReview(false);
    }
  };

  const handleAddToWishlist = async (wishlistId: number) => {
    if (!product) return;
    setAddingToWishlist(true);
    try {
      await api.addToWishlist(wishlistId, product.id);
      const wishlist = wishlists.find(w => w.id === wishlistId);
      setWishlistMessage(`Added to ${wishlist?.name || 'list'}`);
      setShowWishlistDropdown(false);
      setTimeout(() => setWishlistMessage(''), 3000);
    } catch (error: any) {
      setWishlistMessage(error.message || 'Failed to add to list');
      setTimeout(() => setWishlistMessage(''), 3000);
    } finally {
      setAddingToWishlist(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="spinner"></div>
      </div>
    );
  }

  if (!product) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-8 text-center">
        <h1 className="text-2xl font-bold mb-4">Product not found</h1>
        <Link to="/" className="text-[var(--link-color)] hover:underline">Return to Home</Link>
      </div>
    );
  }

  const images = product.images?.length > 0 ? product.images : ['https://via.placeholder.com/500x500?text=No+Image'];
  const priceParts = product.price.toFixed(2).split('.');
  const hasDiscount = product.list_price && product.list_price > product.price;
  const discountPercent = hasDiscount
    ? Math.round(((product.list_price! - product.price) / product.list_price!) * 100)
    : 0;

  return (
    <div className="bg-white">
      <div className="max-w-7xl mx-auto px-4 py-6">
        {/* Breadcrumb */}
        <nav className="breadcrumb">
          <Link to="/">Home</Link>
          <span>/</span>
          <span className="text-[var(--text-primary)]">{product.title.slice(0, 50)}...</span>
        </nav>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left Column - Images */}
          <div className="lg:col-span-5">
            <div className="sticky top-4">
              {/* Main Image */}
              <div className="border rounded p-4 mb-4">
                <img
                  src={images[selectedImage]}
                  alt={product.title}
                  className="w-full h-[400px] object-contain"
                />
              </div>

              {/* Thumbnail Gallery */}
              {images.length > 1 && (
                <div className="flex gap-2 overflow-x-auto">
                  {images.map((img, idx) => (
                    <button
                      key={idx}
                      onClick={() => setSelectedImage(idx)}
                      className={`flex-shrink-0 w-16 h-16 border-2 rounded p-1 ${selectedImage === idx ? 'border-[var(--amazon-teal)]' : 'border-gray-200'
                        }`}
                    >
                      <img src={img} alt="" className="w-full h-full object-contain" />
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Center Column - Product Info */}
          <div className="lg:col-span-4">
            <h1 className="text-xl font-medium mb-2">{product.title}</h1>

            {/* Brand */}
            <p className="text-sm mb-2">
              Visit the <Link to="#" className="text-[var(--link-color)] hover:underline">Brand Store</Link>
            </p>

            {/* Rating */}
            <div className="flex items-center gap-2 mb-3">
              <StarRating rating={product.rating} showNumber />
              <a
                href="#reviews"
                onClick={(e) => {
                  e.preventDefault();
                  document.getElementById('reviews')?.scrollIntoView({ behavior: 'smooth' });
                }}
                className="text-sm text-[var(--link-color)] hover:underline cursor-pointer"
              >
                {product.rating_count?.toLocaleString()} ratings
              </a>
            </div>

            {/* Badges */}
            <div className="flex flex-wrap gap-2 mb-4">
              {product.is_best_seller && (
                <span className="bestseller-badge">#1 Best Seller</span>
              )}
              {product.is_amazon_choice && (
                <span className="amazon-choice-badge">Mercato's Choice</span>
              )}
              {product.is_climate_pledge && (
                <span className="bg-[var(--climate-green)] text-white text-xs px-2 py-0.5 rounded">
                  Climate Pledge Friendly
                </span>
              )}
            </div>

            <hr className="my-4" />

            {/* Price */}
            <div className="mb-4">
              {hasDiscount && (
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-red-600 text-lg">-{discountPercent}%</span>
                </div>
              )}
              <div className="price-display text-2xl">
                <span className="price-symbol">$</span>
                <span className="price-whole text-3xl">{priceParts[0]}</span>
                <span className="price-fraction">{priceParts[1]}</span>
              </div>
              {hasDiscount && (
                <p className="text-sm text-[var(--text-secondary)] mt-1">
                  List Price: <span className="line-through">${product.list_price?.toFixed(2)}</span>
                </p>
              )}
            </div>

            {/* Prime */}
            {product.is_prime_eligible && (
              <div className="mb-4">
                <span className="prime-badge text-base">prime</span>
                <p className="text-sm text-[var(--text-secondary)] mt-1">
                  FREE delivery <span className="font-bold text-[var(--text-primary)]">Tomorrow</span>
                </p>
                <p className="text-sm text-[var(--text-secondary)]">
                  Or fastest delivery <span className="font-bold text-[var(--text-primary)]">Today</span>
                </p>
              </div>
            )}

            <hr className="my-4" />

            {/* About this item */}
            <div className="mb-4">
              <h3 className="font-bold mb-2">About this item</h3>
              <ul className="list-disc pl-5 space-y-1 text-sm">
                {product.bullet_points?.map((point, idx) => (
                  <li key={idx}>{point}</li>
                )) || (
                    <li>High-quality product</li>
                  )}
              </ul>
            </div>
          </div>

          {/* Right Column - Buy Box */}
          <div className="lg:col-span-3">
            <div className="border rounded p-4 sticky top-4">
              {/* Price */}
              <div className="price-display mb-2">
                <span className="price-symbol">$</span>
                <span className="price-whole text-2xl">{priceParts[0]}</span>
                <span className="price-fraction">{priceParts[1]}</span>
              </div>

              {/* Delivery */}
              {product.is_prime_eligible && (
                <p className="text-sm mb-2">
                  <span className="prime-badge">prime</span> FREE delivery <span className="font-bold">Tomorrow</span>
                </p>
              )}

              {/* Availability */}
              <p className={`text-lg font-medium mb-4 ${product.availability_status === 'in_stock' ? 'text-[var(--success-color)]' :
                product.availability_status === 'low_stock' ? 'text-[var(--warning-color)]' :
                  'text-[var(--error-color)]'
                }`}>
                {product.availability_status === 'in_stock' && 'In Stock'}
                {product.availability_status === 'low_stock' && `Only ${product.stock_quantity} left in stock`}
                {product.availability_status === 'out_of_stock' && 'Currently unavailable'}
              </p>

              {/* Quantity */}
              {product.availability_status !== 'out_of_stock' && (
                <>
                  <div className="flex items-center gap-2 mb-4">
                    <label className="text-sm">Qty:</label>
                    <select
                      value={quantity}
                      onChange={(e) => setQuantity(parseInt(e.target.value))}
                      className="border rounded px-2 py-1"
                    >
                      {[...Array(Math.min(30, product.stock_quantity || 30))].map((_, i) => (
                        <option key={i + 1} value={i + 1}>{i + 1}</option>
                      ))}
                    </select>
                  </div>

                  {/* Add to Cart */}
                  <button
                    onClick={handleAddToCart}
                    className="btn-yellow w-full mb-2"
                  >
                    {addedToCart ? 'Added to Cart!' : 'Add to Cart'}
                  </button>

                  {/* Buy Now — add this item and go straight to checkout */}
                  <button onClick={handleBuyNow} className="btn-orange w-full block text-center">
                    Buy Now
                  </button>
                </>
              )}

              {/* Secure Transaction */}
              <div className="flex items-center gap-2 mt-4 text-xs text-[var(--text-secondary)]">
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clipRule="evenodd" />
                </svg>
                <span>Secure transaction</span>
              </div>

              {/* Sold By */}
              <div className="mt-4 text-sm">
                <div className="flex justify-between">
                  <span className="text-[var(--text-secondary)]">Ships from</span>
                  <span>Mercato</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[var(--text-secondary)]">Sold by</span>
                  <span>Mercato</span>
                </div>
              </div>

              {/* Add to List */}
              <div className="relative mt-4">
                <button
                  onClick={loadWishlists}
                  className="w-full text-sm text-[var(--link-color)] hover:text-[var(--link-hover)] hover:underline"
                >
                  Add to List
                </button>

                {/* Wishlist Dropdown */}
                {showWishlistDropdown && (
                  <div className="absolute top-full left-0 right-0 mt-1 bg-white border rounded shadow-lg z-10">
                    <div className="p-2 border-b">
                      <span className="text-sm font-medium">Add to a list</span>
                      <button
                        onClick={() => setShowWishlistDropdown(false)}
                        className="float-right text-gray-500 hover:text-gray-700"
                      >
                        &times;
                      </button>
                    </div>
                    <div className="max-h-48 overflow-y-auto">
                      {wishlists.length === 0 ? (
                        <p className="p-2 text-sm text-gray-500">No lists yet</p>
                      ) : (
                        wishlists.map((wishlist) => (
                          <button
                            key={wishlist.id}
                            onClick={() => handleAddToWishlist(wishlist.id)}
                            disabled={addingToWishlist}
                            className="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 flex items-center gap-2"
                          >
                            <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
                            </svg>
                            {wishlist.name}
                            {wishlist.is_default && (
                              <span className="text-xs text-gray-400">(default)</span>
                            )}
                          </button>
                        ))
                      )}
                    </div>
                    <div className="p-2 border-t">
                      <Link
                        to="/hz/wishlist"
                        className="text-sm text-[var(--link-color)] hover:underline"
                        onClick={() => setShowWishlistDropdown(false)}
                      >
                        Manage lists
                      </Link>
                    </div>
                  </div>
                )}

                {/* Wishlist Message */}
                {wishlistMessage && (
                  <div className="absolute top-full left-0 right-0 mt-1 p-2 bg-green-100 border border-green-300 rounded text-sm text-green-700 text-center">
                    {wishlistMessage}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Frequently Bought Together */}
        {frequentlyBought.length > 0 && (
          <div className="mt-8 p-4 border rounded">
            <h2 className="text-xl font-bold mb-4">Frequently bought together</h2>
            <div className="flex items-center gap-4 flex-wrap">
              <div className="w-32">
                <img src={images[0]} alt={product.title} className="w-full h-32 object-contain" />
              </div>
              {frequentlyBought.map((p) => (
                <div key={p.id} className="flex items-center gap-4">
                  <span className="text-2xl text-gray-400">+</span>
                  <Link to={`/dp/${p.asin}`} className="w-32">
                    <img
                      src={p.images?.[0] || 'https://via.placeholder.com/128'}
                      alt={p.title}
                      className="w-full h-32 object-contain"
                    />
                  </Link>
                </div>
              ))}
            </div>
            <div className="mt-4">
              <p className="text-lg">
                Total price: <span className="font-bold">
                  ${(product.price + frequentlyBought.reduce((sum, p) => sum + p.price, 0)).toFixed(2)}
                </span>
              </p>
              <button onClick={handleAddAllToCart} className="btn-yellow mt-2">Add all to Cart</button>
            </div>
          </div>
        )}

        {/* Related Products */}
        {relatedProducts.length > 0 && (
          <div id="related" className="mt-8 scroll-mt-4">
            <ProductCarousel
              title="Customers who viewed this also viewed"
              products={relatedProducts}
              showAddToCart
              onAddToCart={(id) => onAddToCart(id, 1)}
            />
          </div>
        )}

        {/* Reviews Section */}
        <div id="reviews" className="mt-8 scroll-mt-4">
          <h2 className="text-xl font-bold mb-4">Customer Reviews</h2>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {/* Review Summary */}
            <div>
              <div className="flex items-center gap-2 mb-2">
                <StarRating rating={product.rating} />
                <span className="text-lg font-medium">{product.rating.toFixed(1)} out of 5</span>
              </div>
              <p className="text-sm text-[var(--text-secondary)] mb-4">
                {product.rating_count?.toLocaleString()} global ratings
              </p>

              {/* Rating Breakdown */}
              <div className="space-y-2">
                {[5, 4, 3, 2, 1].map((star) => {
                  const breakdown = ratingBreakdown[String(star)];
                  const percentage = breakdown?.percentage ?? 0;
                  return (
                    <div key={star} className="rating-breakdown">
                      <span className="w-16">{star} star</span>
                      <div className="rating-bar">
                        <div
                          className="rating-bar-fill"
                          style={{ width: `${percentage}%` }}
                        />
                      </div>
                      <span className="w-12 text-right text-[var(--link-color)]">
                        {percentage.toFixed(0)}%
                      </span>
                    </div>
                  );
                })}
              </div>

              {/* Write a Review */}
              <div className="mt-6 pt-4 border-t">
                <h3 className="font-bold mb-2">Review this product</h3>
                <p className="text-sm text-[var(--text-secondary)] mb-3">Share your thoughts with other customers</p>
                {!showReviewForm ? (
                  <button
                    onClick={() => setShowReviewForm(true)}
                    className="btn-secondary w-full"
                  >
                    Write a customer review
                  </button>
                ) : (
                  <div className="space-y-3">
                    {reviewError && (
                      <p className="text-sm text-red-600">{reviewError}</p>
                    )}
                    <div>
                      <label className="block text-sm font-medium mb-1">Rating</label>
                      <div className="flex gap-1">
                        {[1, 2, 3, 4, 5].map((star) => (
                          <button
                            key={star}
                            onClick={() => setReviewRating(star)}
                            className={`text-2xl ${star <= reviewRating ? 'text-[var(--star-color)]' : 'text-gray-300'}`}
                          >
                            ★
                          </button>
                        ))}
                      </div>
                    </div>
                    <div>
                      <label className="block text-sm font-medium mb-1">Title</label>
                      <input
                        type="text"
                        value={reviewTitle}
                        onChange={(e) => setReviewTitle(e.target.value)}
                        placeholder="What's most important to know?"
                        className="w-full border rounded px-3 py-2 text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium mb-1">Review</label>
                      <textarea
                        value={reviewBody}
                        onChange={(e) => setReviewBody(e.target.value)}
                        placeholder="What did you like or dislike? What did you use this product for?"
                        rows={4}
                        className="w-full border rounded px-3 py-2 text-sm"
                      />
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={handleSubmitReview}
                        disabled={submittingReview}
                        className="btn-yellow flex-1"
                      >
                        {submittingReview ? 'Submitting...' : 'Submit'}
                      </button>
                      <button
                        onClick={() => {
                          setShowReviewForm(false);
                          setReviewError('');
                        }}
                        className="btn-secondary"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Reviews List */}
            <div className="md:col-span-2">
              {reviews.length === 0 ? (
                <p className="text-[var(--text-secondary)]">No reviews yet. Be the first to review this product!</p>
              ) : (
                <div className="space-y-6">
                  {reviews.map((review) => (
                    <div key={review.id} className="border-b pb-6">
                      <div className="flex items-center gap-2 mb-2">
                        <div className="w-8 h-8 bg-gray-200 rounded-full"></div>
                        <span className="font-medium">{review.user_name || 'Customer'}</span>
                      </div>
                      <div className="flex items-center gap-2 mb-1">
                        <StarRating rating={review.rating} />
                        <span className="font-bold">{review.title}</span>
                      </div>
                      <p className="text-xs text-[var(--text-secondary)] mb-2">
                        Reviewed on {new Date(review.created_at).toLocaleDateString()}
                        {review.is_verified_purchase && (
                          <span className="ml-2 text-[var(--link-hover)]">Verified Purchase</span>
                        )}
                      </p>
                      <p className="text-sm">{review.body}</p>
                      {review.helpful_votes > 0 && (
                        <p className="text-xs text-[var(--text-secondary)] mt-2">
                          {review.helpful_votes} people found this helpful
                        </p>
                      )}
                      <button className="text-xs text-[var(--link-color)] hover:underline mt-2">
                        Helpful
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
