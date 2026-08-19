import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { User } from '../types';

interface PrimeProps {
  user: User | null;
  onUpdateUser: (user: User) => void;
}

const PRIME_BENEFITS = [
  {
    icon: '🚚',
    title: 'FREE Two-Day Delivery',
    description: 'Get unlimited FREE Two-Day Delivery on millions of items.',
  },
  {
    icon: '📺',
    title: 'Prime Video',
    description: 'Stream thousands of movies and TV shows, including award-winning Prime Originals.',
  },
  {
    icon: '🎵',
    title: 'Prime Music',
    description: 'Listen to over 2 million songs ad-free with unlimited skips.',
  },
  {
    icon: '📚',
    title: 'Prime Reading',
    description: 'Read a rotating selection of popular books, magazines, and more.',
  },
  {
    icon: '📸',
    title: 'Mercato Photos',
    description: 'Unlimited full-resolution photo storage plus 5 GB for videos.',
  },
  {
    icon: '🎮',
    title: 'Mercato Gaming',
    description: 'Get free games, in-game content, and a Twitch channel subscription.',
  },
  {
    icon: '🛒',
    title: 'Prime Early Access',
    description: 'Get 30-minute early access to Lightning Deals.',
  },
  {
    icon: '💊',
    title: 'Mercato Pharmacy',
    description: 'Save up to 80% on prescription medications.',
  },
];

export function Prime({ user, onUpdateUser }: PrimeProps) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/gp/prime');
    }
  }, [user]);

  const handleJoinPrime = async () => {
    setLoading(true);
    setError('');
    try {
      // This would normally call an API to subscribe to Prime
      // For demo, we'll just show a message
      alert('Prime membership activated! (Demo mode)');
      if (user) {
        onUpdateUser({ ...user, is_prime: true, prime_since: new Date().toISOString() });
      }
    } catch (err: any) {
      setError(err.message || 'Failed to activate Prime');
    } finally {
      setLoading(false);
    }
  };

  const handleCancelPrime = async () => {
    if (!confirm('Are you sure you want to cancel your Prime membership?')) return;

    setLoading(true);
    setError('');
    try {
      // This would normally call an API to cancel Prime
      alert('Prime membership cancelled. (Demo mode)');
      if (user) {
        onUpdateUser({ ...user, is_prime: false, prime_since: undefined });
      }
    } catch (err: any) {
      setError(err.message || 'Failed to cancel Prime');
    } finally {
      setLoading(false);
    }
  };

  if (!user) {
    return null;
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <nav className="text-sm mb-4">
        <Link to="/gp/css/account" className="text-[var(--link-color)] hover:underline">
          Your Account
        </Link>
        <span className="mx-2">&rsaquo;</span>
        <span>Prime Membership</span>
      </nav>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {user.is_prime ? (
        /* Prime Member View */
        <div>
          {/* Member Header */}
          <div className="bg-gradient-to-r from-[#00485e] to-[#00a8e1] text-white rounded-lg p-8 mb-8">
            <div className="flex items-center gap-4 mb-4">
              <span className="text-4xl">⭐</span>
              <div>
                <h1 className="text-3xl font-bold">Prime</h1>
                <p className="text-lg opacity-90">You're a Prime member!</p>
              </div>
            </div>
            <p className="opacity-80">
              Member since {user.prime_since ? new Date(user.prime_since).toLocaleDateString() : 'today'}
            </p>
          </div>

          {/* Benefits Grid */}
          <h2 className="text-2xl font-bold mb-4">Your Prime Benefits</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            {PRIME_BENEFITS.map((benefit) => (
              <div key={benefit.title} className="bg-white border rounded-lg p-4">
                <span className="text-3xl">{benefit.icon}</span>
                <h3 className="font-bold mt-2">{benefit.title}</h3>
                <p className="text-sm text-[var(--text-secondary)] mt-1">{benefit.description}</p>
              </div>
            ))}
          </div>

          {/* Manage Section */}
          <div className="bg-white border rounded-lg p-6">
            <h2 className="text-xl font-bold mb-4">Manage Your Membership</h2>
            <div className="space-y-4">
              <div className="flex items-center justify-between py-3 border-b">
                <div>
                  <p className="font-medium">Payment Method</p>
                  <p className="text-sm text-[var(--text-secondary)]">Manage how you pay for Prime</p>
                </div>
                <Link to="/gp/css/account/payment" className="text-[var(--link-color)] hover:underline">
                  Edit
                </Link>
              </div>
              <div className="flex items-center justify-between py-3 border-b">
                <div>
                  <p className="font-medium">Renewal Date</p>
                  <p className="text-sm text-[var(--text-secondary)]">
                    Your membership renews on the 1st of each month
                  </p>
                </div>
              </div>
              <div className="flex items-center justify-between py-3">
                <div>
                  <p className="font-medium">End Membership</p>
                  <p className="text-sm text-[var(--text-secondary)]">
                    Cancel your Prime membership
                  </p>
                </div>
                <button
                  onClick={handleCancelPrime}
                  disabled={loading}
                  className="text-[var(--error-color)] hover:underline"
                >
                  {loading ? 'Processing...' : 'End Membership'}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : (
        /* Non-Member View */
        <div>
          {/* Hero Section */}
          <div className="bg-gradient-to-r from-[#00485e] to-[#00a8e1] text-white rounded-lg p-8 mb-8 text-center">
            <h1 className="text-4xl font-bold mb-4">Mercato Prime</h1>
            <p className="text-xl mb-6 opacity-90">
              Fast, FREE delivery, video, music, and more
            </p>
            <button
              onClick={handleJoinPrime}
              disabled={loading}
              className="bg-[var(--amazon-orange)] hover:bg-[var(--amazon-orange-hover)] text-black font-bold py-3 px-8 rounded-full text-lg"
            >
              {loading ? 'Processing...' : 'Start your 30-day free trial'}
            </button>
            <p className="text-sm mt-4 opacity-80">
              $14.99/month after trial. Cancel anytime.
            </p>
          </div>

          {/* Benefits Grid */}
          <h2 className="text-2xl font-bold mb-4 text-center">Prime includes these great benefits</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            {PRIME_BENEFITS.map((benefit) => (
              <div key={benefit.title} className="bg-white border rounded-lg p-4 text-center">
                <span className="text-4xl">{benefit.icon}</span>
                <h3 className="font-bold mt-3">{benefit.title}</h3>
                <p className="text-sm text-[var(--text-secondary)] mt-2">{benefit.description}</p>
              </div>
            ))}
          </div>

          {/* CTA */}
          <div className="bg-white border rounded-lg p-8 text-center">
            <h2 className="text-2xl font-bold mb-4">Ready to join Prime?</h2>
            <p className="text-[var(--text-secondary)] mb-6">
              Try Prime free for 30 days. After your trial, Prime is just $14.99/month.
            </p>
            <button
              onClick={handleJoinPrime}
              disabled={loading}
              className="btn-yellow text-lg px-8 py-3"
            >
              {loading ? 'Processing...' : 'Try Prime FREE'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
