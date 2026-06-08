import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { ProductCarousel } from '../components/ProductCard';
import type { Product, Department, User } from '../types';

interface HomeProps {
  user: User | null;
  departments: Department[];
  onAddToCart: (productId: number) => void;
}

export function Home({ user, departments, onAddToCart }: HomeProps) {
  const [bestSellers, setBestSellers] = useState<Product[]>([]);
  const [newReleases, setNewReleases] = useState<Product[]>([]);
  const [recommendations, setRecommendations] = useState<Product[]>([]);
  const [deals, setDeals] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [heroIndex, setHeroIndex] = useState(0);

  const heroImages = [
    { image: 'https://images.unsplash.com/photo-1607082348824-0a96f2a4b9da?w=1600&h=400&fit=crop', title: 'Shop great deals' },
    { image: 'https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?w=1600&h=400&fit=crop', title: 'Electronics' },
    { image: 'https://images.unsplash.com/photo-1441984904996-e0b6ba687e04?w=1600&h=400&fit=crop', title: 'Fashion' },
    { image: 'https://images.unsplash.com/photo-1556909114-f6e7ad7d3136?w=1600&h=400&fit=crop', title: 'Home & Kitchen' },
  ];

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      setHeroIndex((prev) => (prev + 1) % heroImages.length);
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  const loadData = async () => {
    try {
      const [bestSellersRes, newReleasesRes, recsRes, dealsRes] = await Promise.all([
        api.getBestSellers(),
        api.getProducts({ sort: 'newest', limit: 10 }),
        api.getRecommendations().catch(() => ({ recommendations: [] })),
        api.getProducts({ limit: 10 }),
      ]);

      setBestSellers(bestSellersRes.products || []);
      setNewReleases(newReleasesRes.products || []);
      setRecommendations(recsRes.recommendations || []);
      setDeals(dealsRes.products || []);
    } catch (error) {
      console.error('Failed to load home data:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="spinner"></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      {/* Hero Carousel */}
      <div className="relative h-[300px] md:h-[400px] overflow-hidden">
        {heroImages.map((hero, index) => (
          <div
            key={index}
            className={`absolute inset-0 transition-opacity duration-500 ${
              index === heroIndex ? 'opacity-100' : 'opacity-0'
            }`}
          >
            <img
              src={hero.image}
              alt={hero.title}
              className="w-full h-full object-cover"
            />
            <div className="absolute inset-0 bg-gradient-to-t from-[var(--background-tertiary)] via-transparent to-transparent" />
          </div>
        ))}

        {/* Hero Navigation */}
        <button
          onClick={() => setHeroIndex((prev) => (prev - 1 + heroImages.length) % heroImages.length)}
          className="absolute left-4 top-1/2 -translate-y-1/2 bg-white/80 hover:bg-white p-3 rounded shadow"
        >
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <button
          onClick={() => setHeroIndex((prev) => (prev + 1) % heroImages.length)}
          className="absolute right-4 top-1/2 -translate-y-1/2 bg-white/80 hover:bg-white p-3 rounded shadow"
        >
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </button>

        {/* Hero Dots */}
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex gap-2">
          {heroImages.map((_, index) => (
            <button
              key={index}
              onClick={() => setHeroIndex(index)}
              className={`w-2 h-2 rounded-full transition-colors ${
                index === heroIndex ? 'bg-white' : 'bg-white/50'
              }`}
            />
          ))}
        </div>
      </div>

      {/* Category Cards Grid */}
      <div className="max-w-7xl mx-auto px-4 -mt-32 relative z-10">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {departments.slice(0, 4).map((dept) => (
            <Link
              key={dept.id}
              to={`/b/${dept.slug}`}
              className="bg-white p-4 rounded shadow hover:shadow-md transition-shadow"
            >
              <h3 className="font-bold text-lg mb-2">{dept.name}</h3>
              <img
                src={dept.image_url || `https://via.placeholder.com/200x150?text=${dept.name}`}
                alt={dept.name}
                className="w-full h-32 object-cover rounded mb-2"
              />
              <span className="text-sm text-[var(--link-color)]">Shop now</span>
            </Link>
          ))}
        </div>

        {/* Sign In Card (for non-logged in users) */}
        {!user && (
          <div className="bg-white p-4 rounded shadow mb-4">
            <h2 className="text-xl font-bold mb-2">Sign in for the best experience</h2>
            <Link to="/ap/signin" className="btn-primary inline-block">Sign in securely</Link>
          </div>
        )}

        {/* Product Carousels */}
        <ProductCarousel
          title="Best Sellers"
          products={bestSellers}
          showAddToCart
          onAddToCart={onAddToCart}
          viewAllLink="/products/best-sellers"
        />

        {recommendations.length > 0 && (
          <ProductCarousel
            title="Recommended for you"
            products={recommendations}
            showAddToCart
            onAddToCart={onAddToCart}
          />
        )}

        <ProductCarousel
          title="New Releases"
          products={newReleases}
          showAddToCart
          onAddToCart={onAddToCart}
        />

        {/* More Category Cards */}
        {departments.length > 4 && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            {departments.slice(4, 8).map((dept) => (
              <Link
                key={dept.id}
                to={`/b/${dept.slug}`}
                className="bg-white p-4 rounded shadow hover:shadow-md transition-shadow"
              >
                <h3 className="font-bold text-lg mb-2">{dept.name}</h3>
                <img
                  src={dept.image_url || `https://via.placeholder.com/200x150?text=${dept.name}`}
                  alt={dept.name}
                  className="w-full h-32 object-cover rounded mb-2"
                />
                <span className="text-sm text-[var(--link-color)]">Shop now</span>
              </Link>
            ))}
          </div>
        )}

        <ProductCarousel
          title="Deals for you"
          products={deals}
          showAddToCart
          onAddToCart={onAddToCart}
          viewAllLink="/gp/goldbox"
        />
      </div>
    </div>
  );
}
