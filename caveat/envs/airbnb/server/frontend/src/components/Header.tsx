import React, { useState, useRef, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { FiHome, FiMenu, FiUser, FiLogOut, FiHeart, FiMap, FiSettings, FiSearch, FiBell, FiGlobe, FiMessageSquare } from 'react-icons/fi';
import { useAppContext } from '../App';
import { logout, getNotificationUnreadCount, getUnreadCount } from '../api';
import SearchBar from './SearchBar';
import CurrencySelector from './CurrencySelector';
import NotificationsPanel from './NotificationsPanel';

function useToast() {
  const { addToast } = useAppContext();
  return addToast;
}

function AirbnbLogo() {
  return (
    <svg viewBox="0 0 1000 1000" className="w-8 h-8" fill="#FF5A5F" xmlns="http://www.w3.org/2000/svg">
      <path d="M499.3 736.7c-51.5-69.2-144.1-175.7-144.1-239.6 0-44.3 23-79.7 51.5-103.5 28.5-23.8 65.4-37.6 100.7-37.6s72.2 13.8 100.7 37.6c28.5 23.8 51.5 59.2 51.5 103.5 0 63.9-92.6 170.4-144.1 239.6-6.2 8.5-16.2 8.5-16.2 0zm-176.5 89.2c-51.5-30.8-96.8-69.2-134.4-115 0 0-1.5-1.5-3.1-4.6-30.8-41.5-53.8-89.2-67.7-140.7-4.6-16.9-7.7-33.8-10.8-52.3-3.1-16.9-4.6-35.4-4.6-53.8 0-107.3 83.8-196.4 192.6-204.2 7.7 0 15.4-1.5 23.1-1.5 60 0 116.1 21.5 158.4 57.7-10.8-7.7-21.5-15.4-33.8-21.5-30.8-16.9-65.4-26.2-101.5-26.2-107.3 0-196.4 83.8-204.2 192.6-1.5 10.8-1.5 20-1.5 30.8 0 27.7 4.6 55.4 12.3 83.1 24.6 83.1 80 152.3 150.7 200.7 3.1 1.5 4.6 3.1 7.7 4.6 10.8 7.7 23.1 13.8 33.8 20 7.7 4.6 15.4 7.7 23.1 12.3-15.4-1.5-32.3-4.6-47.7-10.8-4.6-1.5-10.8-3.1-16.9-6.2l7.7 4.6c1.5 1.5 4.6 1.5 6.2 3.1 16.9 9.2 35.4 16.9 53.8 21.5 44.6 13.8 93.8 13.8 140 1.5 10.8-3.1 21.5-6.2 32.3-10.8 86.2-35.4 153.8-107.3 180.7-193.4 7.7-27.7 12.3-55.4 12.3-83.1 0-10.8 0-20-1.5-30.8-7.7-108.8-96.8-192.6-204.2-192.6-36.2 0-70.8 9.2-101.5 26.2-12.3 6.2-23.1 13.8-33.8 21.5 42.3-36.2 98.4-57.7 158.4-57.7 7.7 0 15.4 0 23.1 1.5 108.8 7.7 192.6 96.8 192.6 204.2 0 18.5-1.5 36.9-4.6 53.8-3.1 18.5-6.2 35.4-10.8 52.3-13.8 51.5-36.9 99.2-67.7 140.7-1.5 3.1-3.1 4.6-3.1 4.6-37.7 45.8-83.1 84.6-134.4 115-4.6 3.1-10.8 6.2-16.9 9.2-6.2 3.1-12.3 6.2-18.5 7.7-12.3 6.2-26.2 10.8-38.5 13.8-13.8 4.6-27.7 6.2-41.5 7.7-13.8-1.5-27.7-3.1-41.5-7.7-13.8-3.1-26.2-7.7-38.5-13.8-6.2-1.5-12.3-4.6-18.5-7.7-4.7-3-10.8-6.1-16.9-9.1z" />
    </svg>
  );
}

type TabId = 'homes' | 'experiences' | 'services';

export default function Header() {
  const { user, setUser } = useAppContext();
  const addToast = useToast();
  const [menuOpen, setMenuOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>('homes');
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifCount, setNotifCount] = useState(0);
  const [msgCount, setMsgCount] = useState(0);
  const menuRef = useRef<HTMLDivElement>(null);
  const notifRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setNotifOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (user) {
      getNotificationUnreadCount().then((d) => setNotifCount(d.count)).catch(() => {});
      getUnreadCount().then((d) => setMsgCount(d.count)).catch(() => {});
    }
  }, [user]);

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      // ignore
    }
    setUser(null);
    setMenuOpen(false);
    navigate('/');
  };

  const initials = user ? user.name.charAt(0).toUpperCase() : '';

  // Only content that actually exists in this app is offered as a header tab.
  const tabs: { id: TabId; label: string; icon: React.ReactNode; isNew?: boolean }[] = [
    { id: 'homes', label: 'Homes', icon: <FiHome className="w-4 h-4" /> },
  ];

  return (
    <header className="fixed top-0 left-0 right-0 z-50 bg-white border-b border-gray-200">
      <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-10">
        {/* Top row: logo, tabs, right actions */}
        <div className="flex items-center justify-between h-20">
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2 flex-shrink-0">
            <AirbnbLogo />
            <span className="text-xl font-bold hidden sm:inline" style={{ color: '#FF5A5F' }}>
              airbnb
            </span>
          </Link>

          {/* Center tabs — hidden on small screens */}
          <nav className="hidden md:flex items-center gap-1">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => { setActiveTab(tab.id); navigate('/'); }}
                className={`relative flex items-center gap-2 px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'text-[#FF5A5F]'
                    : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {tab.icon}
                <span>{tab.label}</span>
                {tab.isNew && (
                  <span className="text-[10px] font-bold text-rose-500 uppercase ml-0.5">NEW</span>
                )}
                {activeTab === tab.id && (
                  <span className="absolute bottom-0 left-4 right-4 h-0.5 rounded-full bg-[#FF5A5F]" />
                )}
              </button>
            ))}
          </nav>

          {/* Right side */}
          <div className="flex items-center gap-3 flex-shrink-0">

            {/* User avatar */}
            {user && (
              <div className="w-8 h-8 rounded-full bg-gray-800 text-white flex items-center justify-center text-sm font-semibold flex-shrink-0">
                {user.avatar_url ? (
                  <img src={user.avatar_url} alt={initials} className="w-8 h-8 rounded-full object-cover" />
                ) : (
                  initials
                )}
              </div>
            )}

            {/* Notification bell */}
            {user && (
              <div className="relative" ref={notifRef}>
                <button
                  onClick={() => setNotifOpen((prev) => !prev)}
                  className="relative flex items-center justify-center w-10 h-10 rounded-full hover:bg-gray-100 transition"
                  aria-label="Notifications"
                >
                  <FiBell className="w-5 h-5 text-gray-600" />
                  {notifCount > 0 && (
                    <span className="absolute top-0.5 right-0.5 min-w-[18px] h-[18px] rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center px-1">
                      {notifCount > 99 ? '99+' : notifCount}
                    </span>
                  )}
                </button>
                {notifOpen && (
                  <NotificationsPanel onClose={() => { setNotifOpen(false); setNotifCount(0); }} />
                )}
              </div>
            )}

            {/* Hamburger menu */}
            <div className="relative" ref={menuRef}>
              <button
                onClick={() => setMenuOpen((prev) => !prev)}
                className="relative flex items-center justify-center w-10 h-10 rounded-full hover:bg-gray-100 transition"
                aria-label="Main menu"
              >
                <FiMenu className="w-5 h-5 text-gray-600" />
              </button>

              {menuOpen && (
                <div className="absolute right-0 mt-2 w-64 bg-white rounded-xl shadow-lg border border-gray-200 py-2 z-50">
                  {user ? (
                    <>
                      <Link
                        to="/wishlists"
                        onClick={() => setMenuOpen(false)}
                        className="flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <FiHeart className="w-4 h-4" />
                        Wishlists
                      </Link>
                      <Link
                        to="/trips"
                        onClick={() => setMenuOpen(false)}
                        className="flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <FiMap className="w-4 h-4" />
                        Trips
                      </Link>
                      <Link
                        to="/hosting"
                        onClick={() => setMenuOpen(false)}
                        className="flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <FiHome className="w-4 h-4" />
                        Hosting
                      </Link>
                      <Link
                        to="/messages"
                        onClick={() => setMenuOpen(false)}
                        className="flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <FiMessageSquare className="w-4 h-4" />
                        <span className="flex-1">Messages</span>
                        {msgCount > 0 && (
                          <span className="w-5 h-5 rounded-full bg-red-500 text-white text-[11px] font-bold flex items-center justify-center">{msgCount}</span>
                        )}
                      </Link>
                      <Link
                        to="/settings"
                        onClick={() => setMenuOpen(false)}
                        className="flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <FiUser className="w-4 h-4" />
                        Profile
                      </Link>

                      <hr className="my-1.5 border-gray-200" />

                      <Link
                        to="/settings"
                        onClick={() => setMenuOpen(false)}
                        className="flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <FiSettings className="w-4 h-4" />
                        Account settings
                      </Link>
                      <button
                        onClick={() => { setMenuOpen(false); navigate('/settings'); }}
                        className="flex items-center gap-3 w-full px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <FiGlobe className="w-4 h-4" />
                        Languages &amp; currency
                      </button>
                      <Link
                        to="/help"
                        onClick={() => setMenuOpen(false)}
                        className="flex items-center gap-3 w-full px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>
                        Help Centre
                      </Link>

                      <hr className="my-1.5 border-gray-200" />

                      <button
                        onClick={() => { setMenuOpen(false); addToast('success', 'Referral link copied!'); }}
                        className="block w-full text-left px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        Refer a host
                      </button>
                      <button
                        onClick={() => { setMenuOpen(false); addToast('success', 'Referral link copied!'); }}
                        className="block w-full text-left px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        Refer a friend
                      </button>
                      <button
                        onClick={() => { setMenuOpen(false); navigate('/help'); }}
                        className="block w-full text-left px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        Find a co-host
                      </button>

                      <hr className="my-1.5 border-gray-200" />

                      <button
                        onClick={handleLogout}
                        className="flex items-center gap-3 w-full px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <FiLogOut className="w-4 h-4" />
                        Log out
                      </button>
                    </>
                  ) : (
                    <>
                      <Link
                        to="/login"
                        onClick={() => setMenuOpen(false)}
                        className="block px-4 py-2.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
                      >
                        Log in
                      </Link>
                      <Link
                        to="/signup"
                        onClick={() => setMenuOpen(false)}
                        className="block px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        Sign up
                      </Link>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Search bar row — hidden on small screens */}
        <div className="hidden md:flex justify-center pb-4 -mt-1">
          <SearchBar />
        </div>
      </div>

      {/* Mobile search bar */}
      <div className="md:hidden px-4 pb-3">
        <SearchBar />
      </div>
    </header>
  );
}
