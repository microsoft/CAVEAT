import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { FiX } from 'react-icons/fi';
import { useAppContext } from '../App';

/* ── Inspiration tab data (India inventory) ── */
const INSPIRATION_TABS = [
  'Popular',
  'Beach',
  'Countryside',
  'Cities',
  'Unique stays',
] as const;

const DESTINATION_DATA: Record<string, { city: string; description: string }[]> = {
  Popular: [
    { city: 'Goa', description: 'Villa rentals' },
    { city: 'Mumbai', description: 'Apartment rentals' },
    { city: 'Pune', description: 'Vacation rentals' },
    { city: 'Jaipur', description: 'Heritage homes' },
    { city: 'Anjuna', description: 'Cottage rentals' },
    { city: 'Baga', description: 'Beach stays' },
    { city: 'Calangute', description: 'Holiday homes' },
    { city: 'Candolim', description: 'Villa rentals' },
    { city: 'Vagator', description: 'Cliffside stays' },
    { city: 'Palolem', description: 'Beach huts' },
    { city: 'Maharashtra', description: 'Weekend getaways' },
    { city: 'Rajasthan', description: 'Palace stays' },
  ],
  Beach: [
    { city: 'Goa', description: 'Beachfront stays' },
    { city: 'Palolem', description: 'Beach cottages' },
    { city: 'Anjuna', description: 'Seaside homes' },
    { city: 'Baga', description: 'Beach villas' },
    { city: 'Candolim', description: 'Oceanview stays' },
    { city: 'Morjim', description: 'Quiet beach stays' },
  ],
  Countryside: [
    { city: 'Rajasthan', description: 'Desert retreats' },
    { city: 'Jaipur', description: 'Countryside homes' },
    { city: 'Pune', description: 'Hillside stays' },
    { city: 'Lonavala', description: 'Valley cottages' },
    { city: 'Mahabaleshwar', description: 'Hill station stays' },
    { city: 'Coorg', description: 'Plantation stays' },
  ],
  Cities: [
    { city: 'Mumbai', description: 'City apartments' },
    { city: 'Pune', description: 'Modern flats' },
    { city: 'Jaipur', description: 'Old-town stays' },
    { city: 'Delhi', description: 'City rentals' },
    { city: 'Bengaluru', description: 'Tech-hub stays' },
    { city: 'Hyderabad', description: 'City homes' },
  ],
  'Unique stays': [
    { city: 'Goa', description: 'Portuguese villas' },
    { city: 'Jaipur', description: 'Haveli stays' },
    { city: 'Palolem', description: 'Eco huts' },
    { city: 'Vagator', description: 'Cliff cottages' },
    { city: 'Rajasthan', description: 'Fort stays' },
    { city: 'Kerala', description: 'Houseboat stays' },
  ],
};

const LANGUAGES = [
  { code: 'en-US', label: 'English (US)' },
  { code: 'en-GB', label: 'English (UK)' },
  { code: 'hi', label: 'हिन्दी (Hindi)' },
  { code: 'fr', label: 'Français' },
  { code: 'de', label: 'Deutsch' },
  { code: 'es', label: 'Español' },
  { code: 'pt', label: 'Português' },
  { code: 'ja', label: '日本語 (Japanese)' },
];

/* ── Footer links ── */
const SUPPORT_LINKS = [
  'Help Centre',
  'AirCover',
  'Anti-discrimination',
  'Disability support',
  'Cancellation options',
  'Report neighbourhood concern',
];

const HOSTING_LINKS = [
  'CAVEAT-Stay your home',
  'AirCover for Hosts',
  'Hosting resources',
  'Community forum',
  'Hosting responsibly',
  'Join a free Hosting class',
];

const CAVEAT_STAY_LINKS = [
  'Newsroom',
  'New features',
  'Careers',
  'Investors',
  'Gift cards',
  'CAVEAT-Stay.org emergency stays',
];

export default function Footer() {
  const { currencies, selectedCurrency, setSelectedCurrency, addToast } = useAppContext();
  const [activeTab, setActiveTab] = useState<string>('Popular');
  const [langModal, setLangModal] = useState(false);
  const [currencyModal, setCurrencyModal] = useState(false);
  const [language, setLanguage] = useState(LANGUAGES[0]);

  const destinations = DESTINATION_DATA[activeTab] || [];

  return (
    <footer className="bg-gray-100 border-t border-gray-200">
      {/* ── Section 1: Inspiration ── */}
      <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-10 py-10">
        <h2 className="text-[22px] font-semibold text-gray-900 mb-4">
          Inspiration for future getaways
        </h2>

        {/* Tabs */}
        <div className="flex gap-6 border-b border-gray-300 mb-6 overflow-x-auto">
          {INSPIRATION_TABS.map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-3 text-sm font-medium whitespace-nowrap transition border-b-2 ${
                activeTab === tab
                  ? 'border-gray-900 text-gray-900'
                  : 'border-transparent text-gray-500 hover:text-gray-800 hover:border-gray-300'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* Destination grid */}
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-x-6 gap-y-4">
          {destinations.map((dest) => (
            <Link
              key={dest.city}
              to={`/search?q=${encodeURIComponent(dest.city)}`}
              className="group"
            >
              <p className="text-sm font-medium text-gray-900 group-hover:underline">
                {dest.city}
              </p>
              <p className="text-sm text-gray-500">{dest.description}</p>
            </Link>
          ))}
        </div>
      </div>

      {/* ── Section 2: Three-column links ── */}
      <div className="border-t border-gray-300">
        <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-10 py-10">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {/* Support */}
            <div>
              <h3 className="text-sm font-semibold text-gray-900 mb-3">Support</h3>
              <ul className="space-y-3">
                {SUPPORT_LINKS.map((link) => (
                  <li key={link}>
                    <Link to="/help" className="text-sm text-gray-600 hover:underline hover:text-gray-900">
                      {link}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>

            {/* Hosting */}
            <div>
              <h3 className="text-sm font-semibold text-gray-900 mb-3">Hosting</h3>
              <ul className="space-y-3">
                {HOSTING_LINKS.map((link) => (
                  <li key={link}>
                    <Link to="/hosting" className="text-sm text-gray-600 hover:underline hover:text-gray-900">
                      {link}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>

            {/* CAVEAT-Stay */}
            <div>
              <h3 className="text-sm font-semibold text-gray-900 mb-3">CAVEAT-Stay</h3>
              <ul className="space-y-3">
                {CAVEAT_STAY_LINKS.map((link) => (
                  <li key={link}>
                    <Link to="/help" className="text-sm text-gray-600 hover:underline hover:text-gray-900">
                      {link}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>

      {/* ── Section 3: Bottom bar ── */}
      <div className="border-t border-gray-300">
        <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 lg:px-10 py-5">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            {/* Left: copyright & links */}
            <div className="flex flex-wrap items-center gap-1 text-sm text-gray-600">
              <span>© 2025 CAVEAT-Stay, Inc.</span>
              <span className="mx-1">·</span>
              <Link to="/help" className="hover:underline">Terms</Link>
              <span className="mx-1">·</span>
              <Link to="/help" className="hover:underline">Sitemap</Link>
              <span className="mx-1">·</span>
              <Link to="/help" className="hover:underline">Privacy</Link>
              <span className="mx-1">·</span>
              <Link to="/help" className="hover:underline">Your Privacy Choices</Link>
            </div>

            {/* Right: language & currency */}
            <div className="flex items-center gap-4">
              <button
                onClick={() => setLangModal(true)}
                className="flex items-center gap-1.5 text-sm font-medium text-gray-700 hover:underline"
              >
                <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="8" cy="8" r="6.5" />
                  <path d="M1.5 8h13M8 1.5c-2 2.5-2 11 0 13M8 1.5c2 2.5 2 11 0 13" />
                </svg>
                {language.label}
              </button>
              <button
                onClick={() => setCurrencyModal(true)}
                className="text-sm font-medium text-gray-700 hover:underline"
              >
                {selectedCurrency ? `${selectedCurrency.symbol} ${selectedCurrency.code}` : '$ USD'}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Language modal */}
      {langModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setLangModal(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-gray-900">Choose a language</h3>
              <button onClick={() => setLangModal(false)} className="p-1.5 rounded-full hover:bg-gray-100" aria-label="Close">
                <FiX className="w-4 h-4" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {LANGUAGES.map((lang) => (
                <button
                  key={lang.code}
                  onClick={async () => {
                    setLanguage(lang);
                    setLangModal(false);
                    try {
                      await fetch('/api/settings', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ preferred_language: lang.code }),
                      });
                    } catch { /* ignore */ }
                    addToast('success', `Language set to ${lang.label}`);
                  }}
                  className={`text-left px-3 py-2.5 rounded-lg border text-sm transition ${
                    language.code === lang.code
                      ? 'border-gray-900 bg-gray-50 font-semibold'
                      : 'border-gray-200 hover:border-gray-400'
                  }`}
                >
                  {lang.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Currency modal */}
      {currencyModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setCurrencyModal(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4 p-6 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-gray-900">Choose a currency</h3>
              <button onClick={() => setCurrencyModal(false)} className="p-1.5 rounded-full hover:bg-gray-100" aria-label="Close">
                <FiX className="w-4 h-4" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {currencies.map((currency) => (
                <button
                  key={currency.code}
                  onClick={async () => {
                    setSelectedCurrency(currency);
                    setCurrencyModal(false);
                    try {
                      await fetch('/api/settings', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ preferred_currency: currency.code }),
                      });
                    } catch { /* ignore */ }
                  }}
                  className={`text-left px-3 py-2.5 rounded-lg border text-sm transition ${
                    selectedCurrency?.code === currency.code
                      ? 'border-gray-900 bg-gray-50 font-semibold'
                      : 'border-gray-200 hover:border-gray-400'
                  }`}
                >
                  <span className="block">{currency.name}</span>
                  <span className="block text-xs text-gray-500">{currency.symbol} · {currency.code}</span>
                </button>
              ))}
              {currencies.length === 0 && (
                <p className="col-span-2 text-sm text-gray-400 px-1 py-2">No currencies available</p>
              )}
            </div>
          </div>
        </div>
      )}
    </footer>
  );
}
