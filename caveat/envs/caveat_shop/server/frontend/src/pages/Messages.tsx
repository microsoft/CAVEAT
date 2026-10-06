// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Message, User } from '../types';

interface MessagesProps {
  user: User | null;
}

const SENDER_ICONS: Record<string, string> = {
  caveat_shop: '📦',
  seller: '🏪',
  system: '🔔',
};

const SENDER_LABELS: Record<string, string> = {
  caveat_shop: 'CAVEAT-Shop',
  seller: 'Seller',
  system: 'System',
};

export function Messages({ user }: MessagesProps) {
  const navigate = useNavigate();
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedMessage, setSelectedMessage] = useState<Message | null>(null);
  const [filter, setFilter] = useState<'all' | 'unread'>('all');

  // Compose state
  const [showCompose, setShowCompose] = useState(false);
  const [composeSubject, setComposeSubject] = useState('');
  const [composeBody, setComposeBody] = useState('');
  const [composeSending, setComposeSending] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/gp/css/account/messages');
      return;
    }
    loadMessages();
  }, [user]);

  const loadMessages = async () => {
    try {
      const res = await api.getMessages();
      setMessages(res.messages || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load messages');
    } finally {
      setLoading(false);
    }
  };

  const handleSelectMessage = async (message: Message) => {
    setSelectedMessage(message);
    if (!message.is_read) {
      try {
        await api.markMessageRead(message.id);
        setMessages(messages.map(m =>
          m.id === message.id ? { ...m, is_read: true } : m
        ));
      } catch (err) {
        console.error('Failed to mark message as read:', err);
      }
    }
  };

  const handleDeleteMessage = async (id: number) => {
    if (!confirm('Are you sure you want to delete this message?')) return;

    try {
      await api.deleteMessage(id);
      setMessages(messages.filter(m => m.id !== id));
      if (selectedMessage?.id === id) {
        setSelectedMessage(null);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to delete message');
    }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!composeSubject.trim() || !composeBody.trim()) return;

    setComposeSending(true);
    setError('');

    try {
      await api.sendMessage({
        subject: composeSubject.trim(),
        body: composeBody.trim(),
      });
      setShowCompose(false);
      setComposeSubject('');
      setComposeBody('');
      setSuccessMessage('Message sent successfully!');
      loadMessages();
      setTimeout(() => setSuccessMessage(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to send message');
    } finally {
      setComposeSending(false);
    }
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffDays = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24));

    if (diffDays === 0) {
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } else if (diffDays === 1) {
      return 'Yesterday';
    } else if (diffDays < 7) {
      return date.toLocaleDateString([], { weekday: 'short' });
    } else {
      return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
    }
  };

  const filteredMessages = filter === 'unread'
    ? messages.filter(m => !m.is_read)
    : messages;

  const unreadCount = messages.filter(m => !m.is_read).length;

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <nav className="text-sm mb-4">
        <Link to="/gp/css/account" className="text-[var(--link-color)] hover:underline">
          Your Account
        </Link>
        <span className="mx-2">&rsaquo;</span>
        <span>Your Messages</span>
      </nav>

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold">Your Messages</h1>
          {unreadCount > 0 && (
            <p className="text-[var(--text-secondary)]">{unreadCount} unread</p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowCompose(true)}
            className="btn-primary"
          >
            Compose
          </button>
          <button
            onClick={() => setFilter('all')}
            className={`px-4 py-2 rounded ${filter === 'all' ? 'bg-[var(--caveat-shop-primary)] text-white' : 'bg-gray-100'}`}
          >
            All
          </button>
          <button
            onClick={() => setFilter('unread')}
            className={`px-4 py-2 rounded ${filter === 'unread' ? 'bg-[var(--caveat-shop-primary)] text-white' : 'bg-gray-100'}`}
          >
            Unread ({unreadCount})
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {successMessage && (
        <div className="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
          {successMessage}
        </div>
      )}

      {filteredMessages.length === 0 ? (
        <div className="bg-white border rounded-lg p-8 text-center">
          <div className="text-5xl mb-4">📭</div>
          <h2 className="text-xl font-bold mb-2">
            {filter === 'unread' ? 'No unread messages' : 'No messages'}
          </h2>
          <p className="text-[var(--text-secondary)]">
            {filter === 'unread'
              ? 'You\'ve read all your messages.'
              : 'Messages from CAVEAT-Shop, sellers, and system notifications will appear here.'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Messages List */}
          <div className="lg:col-span-1 bg-white border rounded-lg overflow-hidden">
            <div className="max-h-[600px] overflow-y-auto">
              {filteredMessages.map((message) => (
                <div
                  key={message.id}
                  onClick={() => handleSelectMessage(message)}
                  className={`p-4 border-b cursor-pointer hover:bg-gray-50 transition-colors ${selectedMessage?.id === message.id ? 'bg-blue-50' : ''
                    } ${!message.is_read ? 'bg-orange-50' : ''}`}
                >
                  <div className="flex items-start gap-3">
                    <span className="text-xl">{SENDER_ICONS[message.sender_type] || '📧'}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between">
                        <span className={`font-medium text-sm ${!message.is_read ? 'font-bold' : ''}`}>
                          {message.sender_name || SENDER_LABELS[message.sender_type]}
                        </span>
                        <span className="text-xs text-[var(--text-secondary)]">
                          {formatDate(message.created_at)}
                        </span>
                      </div>
                      <p className={`text-sm truncate ${!message.is_read ? 'font-semibold' : ''}`}>
                        {message.subject}
                      </p>
                      <p className="text-xs text-[var(--text-secondary)] truncate mt-1">
                        {message.body.substring(0, 50)}...
                      </p>
                    </div>
                    {!message.is_read && (
                      <div className="w-2 h-2 bg-[var(--caveat-shop-primary)] rounded-full mt-2"></div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Message Detail */}
          <div className="lg:col-span-2 bg-white border rounded-lg">
            {selectedMessage ? (
              <div className="h-full flex flex-col">
                <div className="p-4 border-b flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">{SENDER_ICONS[selectedMessage.sender_type] || '📧'}</span>
                    <div>
                      <p className="font-bold">
                        {selectedMessage.sender_name || SENDER_LABELS[selectedMessage.sender_type]}
                      </p>
                      <p className="text-sm text-[var(--text-secondary)]">
                        {new Date(selectedMessage.created_at).toLocaleString()}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => handleDeleteMessage(selectedMessage.id)}
                    className="text-[var(--link-color)] hover:underline text-sm"
                  >
                    Delete
                  </button>
                </div>
                <div className="p-4 flex-1">
                  <h2 className="text-xl font-bold mb-4">{selectedMessage.subject}</h2>
                  <div className="prose max-w-none">
                    <p className="whitespace-pre-wrap">{selectedMessage.body}</p>
                  </div>
                  {selectedMessage.related_order_id && (
                    <div className="mt-6 p-4 bg-gray-50 rounded">
                      <p className="text-sm text-[var(--text-secondary)]">Related Order:</p>
                      <Link
                        to={`/gp/your-account/order-details/${selectedMessage.related_order_id}`}
                        className="text-[var(--link-color)] hover:underline"
                      >
                        View Order Details
                      </Link>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="h-full flex items-center justify-center p-8 text-center">
                <div>
                  <div className="text-5xl mb-4">📧</div>
                  <p className="text-[var(--text-secondary)]">Select a message to read</p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Compose Modal */}
      {showCompose && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg w-full max-w-lg mx-4">
            <div className="p-4 border-b flex items-center justify-between">
              <h2 className="text-xl font-bold">Compose Message</h2>
              <button
                onClick={() => setShowCompose(false)}
                className="text-gray-500 hover:text-gray-700 text-2xl"
              >
                &times;
              </button>
            </div>
            <form onSubmit={handleSendMessage} className="p-4">
              <div className="mb-4">
                <label htmlFor="subject" className="block text-sm font-medium mb-1">
                  Subject
                </label>
                <input
                  type="text"
                  id="subject"
                  value={composeSubject}
                  onChange={(e) => setComposeSubject(e.target.value)}
                  placeholder="Enter subject"
                  className="w-full px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-[var(--caveat-shop-primary)]"
                  required
                />
              </div>
              <div className="mb-4">
                <label htmlFor="body" className="block text-sm font-medium mb-1">
                  Message
                </label>
                <textarea
                  id="body"
                  value={composeBody}
                  onChange={(e) => setComposeBody(e.target.value)}
                  placeholder="Type your message here..."
                  rows={6}
                  className="w-full px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-[var(--caveat-shop-primary)] resize-none"
                  required
                />
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setShowCompose(false)}
                  className="px-4 py-2 border rounded hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={composeSending || !composeSubject.trim() || !composeBody.trim()}
                  className="btn-primary disabled:opacity-50"
                >
                  {composeSending ? 'Sending...' : 'Send Message'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
