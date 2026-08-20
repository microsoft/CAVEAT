import { Link } from 'react-router-dom';
import type { User } from '../types';

interface AccountHubProps {
  user: User | null;
}

export function AccountHub({ user }: AccountHubProps) {
  if (!user) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-8 text-center">
        <h1 className="text-2xl font-bold mb-4">Please sign in</h1>
        <Link to="/ap/signin" className="btn-primary">Sign In</Link>
      </div>
    );
  }

  const accountCards = [
    {
      title: 'Your Orders',
      description: 'Track, return, cancel an order, download invoice or buy again',
      link: '/gp/css/order-history',
      icon: '📦',
    },
    {
      title: 'Login & security',
      description: 'Edit login, name, and mobile number',
      link: '/gp/css/account/security',
      icon: '🔐',
    },
    {
      title: 'Prime',
      description: 'Manage your membership, view benefits, and payment settings',
      link: '/gp/prime',
      icon: '⭐',
    },
    {
      title: 'Your Addresses',
      description: 'Edit, remove or set default address',
      link: '/gp/css/account/address',
      icon: '🏠',
    },
    {
      title: 'Gift cards',
      description: 'View balance or redeem a card, and purchase a new Gift Card',
      link: '/gift-cards',
      icon: '🎁',
    },
    {
      title: 'Your Payments',
      description: 'View all transactions, manage payment methods and settings',
      link: '/gp/css/account/payment',
      icon: '💳',
    },
    {
      title: 'Your Lists',
      description: 'View, modify, and share your lists, or create new ones',
      link: '/hz/wishlist',
      icon: '📋',
    },
    {
      title: 'Customer Service',
      description: 'Browse self service options, help articles or contact us',
      link: '/gp/help/customer',
      icon: '🎧',
    },
    {
      title: 'Your Messages',
      description: 'View or respond to messages from CAVEAT-Shop, Sellers and Buyers',
      link: '/gp/css/account/messages',
      icon: '✉️',
    },
  ];

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <h1 className="text-3xl font-bold mb-6">Your Account</h1>

      {/* Account Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
        {accountCards.map((card) => (
          <Link
            key={card.title}
            to={card.link}
            className="account-card"
          >
            <div className="account-card-icon">{card.icon}</div>
            <div>
              <h3 className="account-card-title">{card.title}</h3>
              <p className="account-card-desc">{card.description}</p>
            </div>
          </Link>
        ))}
      </div>

    </div>
  );
}
