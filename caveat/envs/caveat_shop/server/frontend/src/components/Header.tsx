// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useState, useRef, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import type { User, Department, Cart } from '../types';

interface HeaderProps {
  user: User | null;
  cart: Cart | null;
  departments: Department[];
  onLogout: () => void;
}

export function Header({ user, cart, departments, onLogout }: HeaderProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [searchDepartment, setSearchDepartment] = useState('all');
  const [showAccountMenu, setShowAccountMenu] = useState(false);
  const [showMegaMenu, setShowMegaMenu] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (accountMenuRef.current && !accountMenuRef.current.contains(event.target as Node)) {
        setShowAccountMenu(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      const params = new URLSearchParams({ q: searchQuery });
      if (searchDepartment !== 'all') {
        params.set('department', searchDepartment);
      }
      navigate(`/s?${params.toString()}`);
    }
  };

  const cartCount = cart?.item_count || 0;

  return (
    <>
      {/* Main Header */}
      <header className="sticky top-0 z-50" style={{ backgroundColor: 'var(--caveat-shop-header)' }}>
        {/* Top Navigation */}
        <div className="flex items-center px-2 py-2 gap-2">
          {/* Logo */}
          <Link to="/" className="header-nav px-3">
            <div className="text-white font-bold text-2xl">
              CAVEAT-Shop
            </div>
          </Link>

          {/* Delivery Location */}
          <div className="header-nav hidden md:flex flex-col">
            <span className="header-nav-label">Deliver to</span>
            <span className="header-nav-value flex items-center gap-1">
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M5.05 4.05a7 7 0 119.9 9.9L10 18.9l-4.95-4.95a7 7 0 010-9.9zM10 11a2 2 0 100-4 2 2 0 000 4z" clipRule="evenodd" />
              </svg>
              {user?.name || 'Sign in'}
            </span>
          </div>

          {/* Search Bar */}
          <form onSubmit={handleSearch} className="search-bar flex-1 mx-2">
            <select
              value={searchDepartment}
              onChange={(e) => setSearchDepartment(e.target.value)}
              className="hidden sm:block"
            >
              <option value="all">All</option>
              {departments.map((dept) => (
                <option key={dept.id} value={dept.slug}>{dept.name}</option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Search CAVEAT-Shop"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <button type="submit">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </button>
          </form>

          {/* Language (simplified) */}
          <div className="header-nav hidden lg:flex items-center gap-1">
            <span className="text-lg">🇺🇸</span>
            <span className="text-white text-sm font-bold">EN</span>
          </div>

          {/* Account & Lists */}
          <div
            ref={accountMenuRef}
            className="relative"
            onMouseEnter={() => setShowAccountMenu(true)}
            onMouseLeave={() => setShowAccountMenu(false)}
          >
            <div className="header-nav flex flex-col">
              <span className="header-nav-label">Hello, {user?.name?.split(' ')[0] || 'sign in'}</span>
              <span className="header-nav-value flex items-center">
                Account & Lists
                <svg className="w-3 h-3 ml-1" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              </span>
            </div>

            {/* Account Dropdown */}
            {showAccountMenu && (
              <div className="absolute right-0 top-full mt-0 bg-white rounded shadow-xl z-50 p-4" style={{ minWidth: '400px' }}>
                {!user ? (
                  <div className="text-center pb-4 border-b border-gray-200">
                    <Link to="/ap/signin" className="btn-primary w-48 block mx-auto text-center">Sign in</Link>
                    <p className="text-xs mt-2 text-gray-600">
                      New customer? <Link to="/ap/register" className="text-[var(--link-color)]">Start here.</Link>
                    </p>
                  </div>
                ) : (
                  <div className="flex justify-end pb-2 border-b border-gray-200">
                    <button onClick={onLogout} className="text-sm text-[var(--link-color)] hover:text-[var(--link-hover)] hover:underline">
                      Sign Out
                    </button>
                  </div>
                )}
                <div className="flex gap-6 pt-4">
                  <div className="flex-1">
                    <h4 className="font-bold text-base mb-2">Your Lists</h4>
                    <ul className="space-y-1 text-sm">
                      <li><Link to="/hz/wishlist" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Wish List</Link></li>
                      <li><Link to="/hz/wishlist" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Create a List</Link></li>
                      <li><Link to="/registries/search" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Find a List or Registry</Link></li>
                    </ul>
                  </div>
                  <div className="flex-1 border-l border-gray-200 pl-6">
                    <h4 className="font-bold text-base mb-2">Your Account</h4>
                    <ul className="space-y-1 text-sm">
                      <li><Link to="/gp/css/account" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Account</Link></li>
                      <li><Link to="/gp/css/order-history" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Orders</Link></li>
                      <li><Link to="/gp/buyagain" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Buy Again</Link></li>
                      <li><Link to="/gp/yourstore/ref" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Recommendations</Link></li>
                      <li><Link to="/gp/history" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Browsing History</Link></li>
                      <li><Link to="/gp/css/account/subscriptions" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Subscribe & Save</Link></li>
                      <li><Link to="/gp/prime" className="text-[var(--text-secondary)] hover:text-[var(--link-hover)] hover:underline">Prime Membership</Link></li>
                    </ul>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Returns & Orders */}
          <Link to="/gp/css/order-history" className="header-nav hidden md:flex flex-col">
            <span className="header-nav-label">Returns</span>
            <span className="header-nav-value">& Orders</span>
          </Link>

          {/* Cart */}
          <Link to="/gp/cart" className="header-nav flex items-center">
            <div className="cart-icon">
              <span className="cart-count">{cartCount}</span>
              <svg className="w-12 h-10 text-white" viewBox="0 0 40 32" fill="currentColor">
                <path d="M6 6h28l-3 14H9L6 6z" stroke="currentColor" strokeWidth="2" fill="none" />
                <circle cx="13" cy="26" r="3" />
                <circle cx="27" cy="26" r="3" />
              </svg>
            </div>
            <span className="text-white font-bold hidden sm:inline">Cart</span>
          </Link>
        </div>

        {/* Sub Navigation */}
        <div className="flex items-center px-2 py-1 gap-1 text-white text-sm" style={{ backgroundColor: 'var(--caveat-shop-header-secondary)' }}>
          <button
            onClick={() => setShowMegaMenu(true)}
            className="header-nav flex items-center gap-1"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
            <span className="font-bold">All</span>
          </button>

          <Link to="/gp/goldbox" className="header-nav px-2">Today's Deals</Link>
          <Link to="/gp/buyagain" className="header-nav px-2">Buy Again</Link>
          <Link to="/gp/help/customer" className="header-nav px-2">Customer Service</Link>
          <Link to="/registries" className="header-nav px-2 hidden lg:block">Registry</Link>
          <Link to="/gift-cards" className="header-nav px-2 hidden lg:block">Gift Cards</Link>
          {/* <Link to="/gp/browse.html?node=702779011" className="header-nav px-2 hidden xl:block">Sell</Link> */}
        </div>
      </header>

      {/* Mega Menu Sidebar */}
      {showMegaMenu && (
        <>
          <div
            className="fixed inset-0 bg-black bg-opacity-50 z-50"
            onClick={() => setShowMegaMenu(false)}
          />
          <div className="fixed left-0 top-0 h-full w-80 bg-white z-50 overflow-y-auto">
            <div className="p-4 flex items-center gap-3" style={{ backgroundColor: 'var(--caveat-shop-header-secondary)' }}>
              <div className="w-8 h-8 bg-gray-300 rounded-full flex items-center justify-center">
                <svg className="w-5 h-5 text-gray-600" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clipRule="evenodd" />
                </svg>
              </div>
              <span className="text-white font-bold text-lg">Hello, {user?.name?.split(' ')[0] || 'sign in'}</span>
              <button
                onClick={() => setShowMegaMenu(false)}
                className="ml-auto text-white"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="py-2">
              <div className="px-4 py-2 font-bold text-lg">Trending</div>
              <Link to="/products/trending" className="block px-8 py-2 hover:bg-gray-100" onClick={() => setShowMegaMenu(false)}>Trending Now</Link>
              <Link to="/products/best-sellers" className="block px-8 py-2 hover:bg-gray-100" onClick={() => setShowMegaMenu(false)}>Best Sellers</Link>
              <Link to="/products/new-releases" className="block px-8 py-2 hover:bg-gray-100" onClick={() => setShowMegaMenu(false)}>New Releases</Link>
              <Link to="/products/movers-shakers" className="block px-8 py-2 hover:bg-gray-100" onClick={() => setShowMegaMenu(false)}>Movers & Shakers</Link>
            </div>

            <div className="border-t border-gray-200 py-2">
              <div className="px-4 py-2 font-bold text-lg">Shop By Department</div>
              {departments.map((dept) => (
                <Link
                  key={dept.id}
                  to={`/b/${dept.slug}`}
                  className="flex items-center justify-between px-8 py-2 hover:bg-gray-100"
                  onClick={() => setShowMegaMenu(false)}
                >
                  {dept.name}
                  <svg className="w-4 h-4 text-gray-400" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
                  </svg>
                </Link>
              ))}
            </div>

            <div className="border-t border-gray-200 py-2">
              <div className="px-4 py-2 font-bold text-lg">Help & Settings</div>
              <Link to="/gp/css/account" className="block px-8 py-2 hover:bg-gray-100" onClick={() => setShowMegaMenu(false)}>Your Account</Link>
              <Link to="/gp/help/customer" className="block px-8 py-2 hover:bg-gray-100" onClick={() => setShowMegaMenu(false)}>Customer Service</Link>
              {user ? (
                <button onClick={() => { onLogout(); setShowMegaMenu(false); }} className="block w-full text-left px-8 py-2 hover:bg-gray-100">Sign Out</button>
              ) : (
                <Link to="/ap/signin" className="block px-8 py-2 hover:bg-gray-100" onClick={() => setShowMegaMenu(false)}>Sign In</Link>
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
