import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { User } from '../types';

interface SignInProps {
  onAuth: (user: User) => void;
}

export function SignIn({ onAuth }: SignInProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await api.login(email, password);
      const user = await api.getCurrentUser();
      onAuth(user);
      navigate('/');
    } catch (err: any) {
      setError(err.message || 'Invalid email or password');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white">
      <div className="flex flex-col items-center pt-8 pb-16">
        {/* Logo */}
        <Link to="/" className="mb-6">
          <div className="text-3xl font-bold text-[var(--caveat-shop-header)]">
            CAVEAT-Shop
          </div>
        </Link>

        {/* Sign In Form */}
        <div className="w-full max-w-sm">
          <div className="border rounded p-6">
            <h1 className="text-2xl font-normal mb-4">Sign in</h1>

            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-4 text-sm">
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit}>
              <div className="mb-4">
                <label className="block text-sm font-bold mb-1">Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full border rounded px-3 py-2"
                  required
                />
              </div>

              <div className="mb-4">
                <div className="flex justify-between items-center mb-1">
                  <label className="text-sm font-bold">Password</label>
                  <Link to="#" className="text-xs text-[var(--link-color)] hover:underline">
                    Forgot your password?
                  </Link>
                </div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full border rounded px-3 py-2"
                  required
                />
              </div>

              <button
                type="submit"
                disabled={loading}
                className="btn-primary w-full"
              >
                {loading ? 'Signing in...' : 'Sign in'}
              </button>
            </form>

            <p className="text-xs text-[var(--text-secondary)] mt-4">
              By continuing, you agree to CAVEAT-Shop's <Link to="#" className="text-[var(--link-color)] hover:underline">Conditions of Use</Link> and <Link to="#" className="text-[var(--link-color)] hover:underline">Privacy Notice</Link>.
            </p>
          </div>

          {/* Create Account */}
          <div className="relative mt-6 mb-4">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-gray-200"></div>
            </div>
            <div className="relative flex justify-center text-xs">
              <span className="px-2 bg-white text-[var(--text-secondary)]">New to CAVEAT-Shop?</span>
            </div>
          </div>

          <Link
            to="/ap/register"
            className="btn-secondary w-full block text-center"
          >
            Create your CAVEAT-Shop account
          </Link>
        </div>
      </div>

      {/* Footer */}
      <div className="border-t bg-gradient-to-b from-white to-gray-100 py-8">
        <div className="flex justify-center gap-6 text-xs text-[var(--link-color)]">
          <Link to="#" className="hover:underline">Conditions of Use</Link>
          <Link to="#" className="hover:underline">Privacy Notice</Link>
          <Link to="#" className="hover:underline">Help</Link>
        </div>
        <p className="text-center text-xs text-[var(--text-secondary)] mt-2">
          © 2024 CAVEAT-Shop
        </p>
      </div>
    </div>
  );
}

export function Register({ onAuth }: SignInProps) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    if (password.length < 6) {
      setError('Password must be at least 6 characters');
      return;
    }

    setLoading(true);

    try {
      const user = await api.register({ email, password, name });
      onAuth(user);
      navigate('/');
    } catch (err: any) {
      setError(err.message || 'Registration failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white">
      <div className="flex flex-col items-center pt-8 pb-16">
        {/* Logo */}
        <Link to="/" className="mb-6">
          <div className="text-3xl font-bold text-[var(--caveat-shop-header)]">
            CAVEAT-Shop
          </div>
        </Link>

        {/* Register Form */}
        <div className="w-full max-w-sm">
          <div className="border rounded p-6">
            <h1 className="text-2xl font-normal mb-4">Create account</h1>

            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-4 text-sm">
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit}>
              <div className="mb-4">
                <label className="block text-sm font-bold mb-1">Your name</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="First and last name"
                  className="w-full border rounded px-3 py-2"
                  required
                />
              </div>

              <div className="mb-4">
                <label className="block text-sm font-bold mb-1">Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full border rounded px-3 py-2"
                  required
                />
              </div>

              <div className="mb-4">
                <label className="block text-sm font-bold mb-1">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="At least 6 characters"
                  className="w-full border rounded px-3 py-2"
                  required
                />
                <p className="text-xs text-[var(--text-secondary)] mt-1">
                  <span className="text-[var(--info-color)]">ⓘ</span> Passwords must be at least 6 characters.
                </p>
              </div>

              <div className="mb-4">
                <label className="block text-sm font-bold mb-1">Re-enter password</label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full border rounded px-3 py-2"
                  required
                />
              </div>

              <button
                type="submit"
                disabled={loading}
                className="btn-primary w-full"
              >
                {loading ? 'Creating account...' : 'Create your CAVEAT-Shop account'}
              </button>
            </form>

            <p className="text-xs text-[var(--text-secondary)] mt-4">
              By creating an account, you agree to CAVEAT-Shop's <Link to="#" className="text-[var(--link-color)] hover:underline">Conditions of Use</Link> and <Link to="#" className="text-[var(--link-color)] hover:underline">Privacy Notice</Link>.
            </p>

            <hr className="my-4" />

            <p className="text-sm">
              Already have an account?{' '}
              <Link to="/ap/signin" className="text-[var(--link-color)] hover:underline">
                Sign in
              </Link>
            </p>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="border-t bg-gradient-to-b from-white to-gray-100 py-8">
        <div className="flex justify-center gap-6 text-xs text-[var(--link-color)]">
          <Link to="#" className="hover:underline">Conditions of Use</Link>
          <Link to="#" className="hover:underline">Privacy Notice</Link>
          <Link to="#" className="hover:underline">Help</Link>
        </div>
        <p className="text-center text-xs text-[var(--text-secondary)] mt-2">
          © 2024 CAVEAT-Shop
        </p>
      </div>
    </div>
  );
}
