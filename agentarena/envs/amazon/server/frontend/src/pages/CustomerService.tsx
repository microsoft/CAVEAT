import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { User } from '../types';

interface CustomerServiceProps {
  user: User | null;
}

const HELP_TOPICS = [
  {
    title: 'Your Orders',
    icon: '📦',
    description: 'Track packages, return or replace items, and more',
    link: '/gp/css/order-history',
    items: [
      { label: 'Track your package', link: '/gp/css/order-history' },
      { label: 'Return or replace items', link: '/gp/returns' },
      { label: 'Manage Subscribe & Save', link: '/gp/css/account/subscriptions' },
    ],
  },
  {
    title: 'Manage Prime',
    icon: '⭐',
    description: 'View benefits, change payment settings, and more',
    link: '/gp/prime',
    items: [
      { label: 'View Prime benefits', link: '/gp/prime' },
      { label: 'Manage Prime membership', link: '/gp/prime' },
      { label: 'End Prime membership', link: '/gp/prime' },
    ],
  },
  {
    title: 'Payment Settings',
    icon: '💳',
    description: 'Add or edit payment methods, view gift card balance',
    link: '/gp/css/account/payment',
    items: [
      { label: 'Add a payment method', link: '/gp/css/account/payment' },
      { label: 'View gift card balance', link: '/gift-cards' },
      { label: 'Learn about payment options', link: '/gp/css/account/payment' },
    ],
  },
  {
    title: 'Account Settings',
    icon: '👤',
    description: 'Update login credentials, addresses, and more',
    link: '/gp/css/account',
    items: [
      { label: 'Change email or password', link: '/gp/css/account/security' },
      { label: 'Update addresses', link: '/gp/css/account/address' },
      { label: 'Manage notifications', link: '/gp/css/account' },
    ],
  },
  {
    title: 'Returns & Refunds',
    icon: '↩️',
    description: 'Return items and view refund status',
    link: '/gp/returns',
    items: [
      { label: 'Return an item', link: '/gp/returns' },
      { label: 'View refund status', link: '/gp/css/order-history' },
      { label: 'Return policy', link: '/gp/returns' },
    ],
  },
];

const FAQ_ITEMS = [
  {
    question: 'Where is my order?',
    answer: 'You can track your package by going to Your Orders and selecting "Track Package" for the order you want to track.',
  },
  {
    question: 'How do I return an item?',
    answer: 'Go to Your Orders, find the order with the item you want to return, and select "Return or Replace Items". Follow the steps to complete your return.',
  },
  {
    question: 'How do I cancel my Prime membership?',
    answer: 'Go to Your Prime Membership, then select "End Membership". You can choose to end at the billing date or immediately.',
  },
  {
    question: 'How do I change my password?',
    answer: 'Go to Your Account > Login & Security, then select "Edit" next to Password to change your password.',
  },
  {
    question: 'How do I update my shipping address?',
    answer: 'Go to Your Account > Your Addresses to add, edit, or remove shipping addresses.',
  },
];

export function CustomerService({ user }: CustomerServiceProps) {
  const [expandedFaq, setExpandedFaq] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  // Chat state
  const [showChat, setShowChat] = useState(false);
  const [chatMessages, setChatMessages] = useState<{ from: 'user' | 'agent'; text: string }[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatConnected, setChatConnected] = useState(false);

  // Call state
  const [showCallModal, setShowCallModal] = useState(false);
  const [phoneNumber, setPhoneNumber] = useState('');
  const [callRequested, setCallRequested] = useState(false);
  const [callIssue, setCallIssue] = useState('');

  // Filter topics and FAQs based on search query
  const filteredTopics = searchQuery.trim()
    ? HELP_TOPICS.filter(topic =>
      topic.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      topic.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      topic.items.some(item => item.label.toLowerCase().includes(searchQuery.toLowerCase()))
    )
    : HELP_TOPICS;

  const filteredFaqs = searchQuery.trim()
    ? FAQ_ITEMS.filter(faq =>
      faq.question.toLowerCase().includes(searchQuery.toLowerCase()) ||
      faq.answer.toLowerCase().includes(searchQuery.toLowerCase())
    )
    : FAQ_ITEMS;

  const clearSearch = () => {
    setSearchQuery('');
  };

  // Chat functions
  const startChat = () => {
    setShowChat(true);
    setChatConnected(false);
    setChatMessages([]);
    // Simulate connection delay
    setTimeout(() => {
      setChatConnected(true);
      setChatMessages([
        { from: 'agent', text: 'Hello! Thank you for contacting Mercato Customer Service. My name is Alex. How can I help you today?' }
      ]);
    }, 1500);
  };

  const sendChatMessage = () => {
    if (!chatInput.trim()) return;
    const userMessage = chatInput.trim();
    setChatMessages(prev => [...prev, { from: 'user', text: userMessage }]);
    setChatInput('');

    // Simulate agent response
    setTimeout(() => {
      let response = "I understand. Let me look into that for you. Is there anything specific about your order or account I can help with?";
      if (userMessage.toLowerCase().includes('order')) {
        response = "I can help you with your order. Could you please provide your order number? You can find it in Your Orders or in your order confirmation email.";
      } else if (userMessage.toLowerCase().includes('return')) {
        response = "I can help you with a return. You can initiate a return by going to Your Orders and clicking 'Return or Replace Items'. Would you like me to guide you through the process?";
      } else if (userMessage.toLowerCase().includes('refund')) {
        response = "Refunds typically take 3-5 business days to process after we receive your returned item. Would you like me to check the status of a specific refund?";
      } else if (userMessage.toLowerCase().includes('prime')) {
        response = "I can help you with your Prime membership. Are you looking to manage your benefits, update payment settings, or have questions about Prime services?";
      }
      setChatMessages(prev => [...prev, { from: 'agent', text: response }]);
    }, 1000);
  };

  const closeChat = () => {
    setShowChat(false);
    setChatMessages([]);
    setChatInput('');
    setChatConnected(false);
  };

  // Call functions
  const requestCall = () => {
    if (!phoneNumber.trim() || !callIssue) return;
    setCallRequested(true);
  };

  const closeCallModal = () => {
    setShowCallModal(false);
    setPhoneNumber('');
    setCallIssue('');
    setCallRequested(false);
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="text-center mb-8">
        <h1 className="text-3xl font-bold mb-2">Hello{user ? `, ${user.name}` : ''}.</h1>
        <p className="text-xl text-[var(--text-secondary)]">What would you like help with today?</p>
      </div>

      {/* Search */}
      <div className="max-w-2xl mx-auto mb-8">
        <div className="relative">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search for help topics"
            className="w-full border rounded-full px-6 py-3 pr-12 text-lg"
          />
          {searchQuery ? (
            <button
              onClick={clearSearch}
              className="absolute right-4 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
            >
              ✕
            </button>
          ) : (
            <span className="absolute right-4 top-1/2 -translate-y-1/2 text-gray-400">
              🔍
            </span>
          )}
        </div>
        {searchQuery && (
          <p className="text-center text-sm text-[var(--text-secondary)] mt-2">
            {filteredTopics.length + filteredFaqs.length} results for "{searchQuery}"
          </p>
        )}
      </div>

      {/* Help Topics Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-10">
        {filteredTopics.map((topic) => (
          <div key={topic.title} className="bg-white border rounded-lg p-4">
            <Link to={topic.link} className="flex items-start gap-3 mb-3">
              <span className="text-2xl">{topic.icon}</span>
              <div>
                <h3 className="font-bold text-[var(--link-color)] hover:underline">{topic.title}</h3>
                <p className="text-sm text-[var(--text-secondary)]">{topic.description}</p>
              </div>
            </Link>
            <ul className="ml-9 space-y-1">
              {topic.items.map((item) => (
                <li key={item.label}>
                  <Link
                    to={item.link}
                    className="text-sm text-[var(--link-color)] hover:underline"
                  >
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* FAQ Section */}
      {filteredFaqs.length > 0 && (
        <div className="bg-white border rounded-lg p-6 mb-8">
          <h2 className="text-xl font-bold mb-4">Frequently Asked Questions</h2>
          <div className="space-y-2">
            {filteredFaqs.map((faq, index) => (
              <div key={faq.question} className="border rounded">
                <button
                  onClick={() => setExpandedFaq(expandedFaq === index ? null : index)}
                  className="w-full p-4 text-left flex items-center justify-between hover:bg-gray-50"
                >
                  <span className="font-medium">{faq.question}</span>
                  <span className="text-xl">{expandedFaq === index ? '−' : '+'}</span>
                </button>
                {expandedFaq === index && (
                  <div className="p-4 pt-0 text-[var(--text-secondary)]">
                    {faq.answer}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* No Results Message */}
      {searchQuery && filteredTopics.length === 0 && filteredFaqs.length === 0 && (
        <div className="bg-white border rounded-lg p-8 text-center mb-8">
          <div className="text-5xl mb-4">🔍</div>
          <h2 className="text-xl font-bold mb-2">No results found</h2>
          <p className="text-[var(--text-secondary)] mb-4">
            We couldn't find any help topics matching "{searchQuery}"
          </p>
          <button
            onClick={clearSearch}
            className="text-[var(--link-color)] hover:underline"
          >
            Clear search
          </button>
        </div>
      )}

      {/* Contact Section */}
      <div className="bg-gray-50 rounded-lg p-8 text-center">
        <h2 className="text-xl font-bold mb-2">Need more help?</h2>
        <p className="text-[var(--text-secondary)] mb-6">
          Contact us through one of these channels
        </p>
        <div className="flex justify-center gap-6 flex-wrap">
          <div className="bg-white rounded-lg p-6 min-w-[200px] border">
            <div className="text-3xl mb-2">💬</div>
            <h3 className="font-bold mb-1">Start Chatting</h3>
            <p className="text-sm text-[var(--text-secondary)] mb-3">
              Chat with our support team
            </p>
            <button onClick={startChat} className="btn-secondary text-sm">Start Chat</button>
          </div>
          <div className="bg-white rounded-lg p-6 min-w-[200px] border">
            <div className="text-3xl mb-2">📞</div>
            <h3 className="font-bold mb-1">Call Us</h3>
            <p className="text-sm text-[var(--text-secondary)] mb-3">
              Talk to a representative
            </p>
            <button onClick={() => setShowCallModal(true)} className="btn-secondary text-sm">Request a Call</button>
          </div>
          <div className="bg-white rounded-lg p-6 min-w-[200px] border">
            <div className="text-3xl mb-2">📧</div>
            <h3 className="font-bold mb-1">Email Us</h3>
            <p className="text-sm text-[var(--text-secondary)] mb-3">
              Get help via email
            </p>
            <Link to="/gp/css/account/messages" className="btn-secondary text-sm inline-block">
              Send Email
            </Link>
          </div>
        </div>
      </div>

      {/* Quick Links */}
      <div className="mt-8 text-center">
        <p className="text-[var(--text-secondary)] mb-4">Quick Links</p>
        <div className="flex justify-center gap-6 flex-wrap text-sm">
          <Link to="/gp/css/order-history" className="text-[var(--link-color)] hover:underline">Your Orders</Link>
          <Link to="/gp/returns" className="text-[var(--link-color)] hover:underline">Returns & Refunds</Link>
          <Link to="/gp/css/account" className="text-[var(--link-color)] hover:underline">Manage Account</Link>
          <Link to="/gp/prime" className="text-[var(--link-color)] hover:underline">Prime Membership</Link>
          <Link to="/gp/css/account/payment" className="text-[var(--link-color)] hover:underline">Payment Settings</Link>
        </div>
      </div>

      {/* Chat Modal */}
      {showChat && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-end justify-end p-4 z-50" onClick={closeChat}>
          <div className="bg-white rounded-lg w-full max-w-md shadow-xl flex flex-col" style={{ height: '500px' }} onClick={(e) => e.stopPropagation()}>
            {/* Chat Header */}
            <div className="bg-[var(--amazon-dark)] text-white p-4 rounded-t-lg flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-2xl">💬</span>
                <div>
                  <h3 className="font-bold">Mercato Customer Service</h3>
                  <p className="text-xs text-gray-300">
                    {chatConnected ? 'Connected with Alex' : 'Connecting...'}
                  </p>
                </div>
              </div>
              <button onClick={closeChat} className="text-white hover:text-gray-300 text-2xl">
                &times;
              </button>
            </div>

            {/* Chat Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {!chatConnected ? (
                <div className="flex items-center justify-center h-full">
                  <div className="text-center">
                    <div className="spinner mb-4"></div>
                    <p className="text-[var(--text-secondary)]">Connecting you with an agent...</p>
                  </div>
                </div>
              ) : (
                chatMessages.map((msg, index) => (
                  <div
                    key={index}
                    className={`flex ${msg.from === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    <div
                      className={`max-w-[80%] p-3 rounded-lg ${msg.from === 'user'
                        ? 'bg-[var(--amazon-orange)] text-white'
                        : 'bg-gray-100 text-gray-800'
                        }`}
                    >
                      {msg.text}
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* Chat Input */}
            <div className="p-4 border-t">
              <div className="flex gap-2">
                <input
                  type="text"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && sendChatMessage()}
                  placeholder="Type your message..."
                  disabled={!chatConnected}
                  className="flex-1 px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-[var(--amazon-orange)] disabled:bg-gray-100"
                />
                <button
                  onClick={sendChatMessage}
                  disabled={!chatConnected || !chatInput.trim()}
                  className="btn-primary disabled:opacity-50"
                >
                  Send
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Request a Call Modal */}
      {showCallModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg w-full max-w-md mx-4">
            <div className="p-4 border-b flex items-center justify-between">
              <h2 className="text-xl font-bold">Request a Call</h2>
              <button
                onClick={closeCallModal}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>

            {!callRequested ? (
              <div className="p-4">
                <p className="text-[var(--text-secondary)] mb-4">
                  Enter your phone number and we'll call you back within 5 minutes.
                </p>

                <div className="mb-4">
                  <label htmlFor="call-issue" className="block text-sm font-medium mb-1">
                    What do you need help with?
                  </label>
                  <select
                    id="call-issue"
                    value={callIssue}
                    onChange={(e) => setCallIssue(e.target.value)}
                    className="w-full px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-[var(--amazon-orange)]"
                    required
                  >
                    <option value="">Select an issue</option>
                    <option value="order">Order issue</option>
                    <option value="return">Return or refund</option>
                    <option value="prime">Prime membership</option>
                    <option value="payment">Payment issue</option>
                    <option value="account">Account settings</option>
                    <option value="other">Other</option>
                  </select>
                </div>

                <div className="mb-4">
                  <label htmlFor="phone" className="block text-sm font-medium mb-1">
                    Phone Number
                  </label>
                  <input
                    type="tel"
                    id="phone"
                    value={phoneNumber}
                    onChange={(e) => setPhoneNumber(e.target.value)}
                    placeholder="(555) 123-4567"
                    className="w-full px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-[var(--amazon-orange)]"
                    required
                  />
                </div>

                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={closeCallModal}
                    className="px-4 py-2 border rounded hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={requestCall}
                    disabled={!phoneNumber.trim() || !callIssue}
                    className="btn-primary disabled:opacity-50"
                  >
                    Request Call
                  </button>
                </div>
              </div>
            ) : (
              <div className="p-8 text-center">
                <div className="text-5xl mb-4">📞</div>
                <h3 className="text-xl font-bold mb-2">Call Requested!</h3>
                <p className="text-[var(--text-secondary)] mb-4">
                  We'll call you at <strong>{phoneNumber}</strong> within the next 5 minutes.
                </p>
                <p className="text-sm text-[var(--text-secondary)] mb-6">
                  Please keep your phone nearby and ready to answer.
                </p>
                <button
                  onClick={closeCallModal}
                  className="btn-primary"
                >
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
