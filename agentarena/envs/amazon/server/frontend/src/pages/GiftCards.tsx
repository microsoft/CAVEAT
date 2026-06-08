import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { GiftCard, GiftCardTransaction } from '../types';

export function GiftCards() {
  const [giftCards, setGiftCards] = useState<GiftCard[]>([]);
  const [balance, setBalance] = useState<number>(0);
  const [transactions, setTransactions] = useState<GiftCardTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Redeem form state
  const [redeemCode, setRedeemCode] = useState('');
  const [redeemLoading, setRedeemLoading] = useState(false);

  // Reload form state
  const [reloadAmount, setReloadAmount] = useState<string>('25');
  const [reloadLoading, setReloadLoading] = useState(false);

  // Tab state
  const [activeTab, setActiveTab] = useState<'balance' | 'redeem' | 'reload' | 'history'>('balance');

  useEffect(() => {
    loadGiftCardData();
  }, []);

  const loadGiftCardData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [cardsRes, balanceRes, transactionsRes] = await Promise.all([
        api.getGiftCards(),
        api.getGiftCardBalance(),
        api.getGiftCardTransactions(),
      ]);
      setGiftCards(cardsRes.gift_cards || []);
      setBalance(balanceRes.balance || 0);
      setTransactions(transactionsRes.transactions || []);
    } catch (err) {
      console.error('Failed to load gift card data:', err);
      setError('Failed to load gift card data. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleRedeemGiftCard = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!redeemCode.trim()) return;

    setRedeemLoading(true);
    setError(null);
    setSuccessMessage(null);

    try {
      const result = await api.redeemGiftCard(redeemCode.trim());
      setSuccessMessage(`Gift card redeemed! $${result.amount.toFixed(2)} added to your balance.`);
      setRedeemCode('');
      loadGiftCardData();
    } catch (err: any) {
      setError(err.message || 'Failed to redeem gift card. Please check the code and try again.');
    } finally {
      setRedeemLoading(false);
    }
  };

  const handleReloadBalance = async (e: React.FormEvent) => {
    e.preventDefault();
    const amount = parseFloat(reloadAmount);
    if (isNaN(amount) || amount <= 0) {
      setError('Please enter a valid amount');
      return;
    }

    setReloadLoading(true);
    setError(null);
    setSuccessMessage(null);

    try {
      const result = await api.reloadGiftCard(amount);
      setSuccessMessage(`Balance reloaded! New balance: $${result.new_balance.toFixed(2)}`);
      loadGiftCardData();
    } catch (err: any) {
      setError(err.message || 'Failed to reload balance. Please try again.');
    } finally {
      setReloadLoading(false);
    }
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getTransactionTypeLabel = (type: string) => {
    switch (type) {
      case 'redemption': return 'Gift Card Redeemed';
      case 'reload': return 'Balance Reload';
      case 'purchase': return 'Purchase';
      case 'refund': return 'Refund';
      default: return type;
    }
  };

  const getTransactionTypeColor = (type: string) => {
    switch (type) {
      case 'redemption': return 'text-green-600';
      case 'reload': return 'text-green-600';
      case 'purchase': return 'text-red-600';
      case 'refund': return 'text-blue-600';
      default: return 'text-[var(--text-primary)]';
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <nav className="text-sm mb-4">
        <Link to="/gp/css/account" className="text-[var(--link-color)] hover:underline">
          Your Account
        </Link>
        <span className="mx-2">&rsaquo;</span>
        <span>Gift Cards</span>
      </nav>

      <h1 className="text-3xl font-bold mb-6">Gift Cards</h1>

      {/* Balance Card */}
      <div className="bg-gradient-to-r from-[#232f3e] to-[#37475a] text-white rounded-lg p-6 mb-6">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm opacity-80 mb-1">Your Gift Card Balance</p>
            <p className="text-4xl font-bold">${balance.toFixed(2)}</p>
          </div>
          <div className="text-6xl opacity-50">🎁</div>
        </div>
      </div>

      {/* Error/Success Messages */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}
      {successMessage && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded mb-4">
          {successMessage}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-4 border-b mb-6">
        <button
          onClick={() => setActiveTab('balance')}
          className={`pb-2 px-1 ${activeTab === 'balance' ? 'border-b-2 border-[var(--amazon-orange)] text-[var(--amazon-orange)] font-medium' : 'text-[var(--text-secondary)]'}`}
        >
          Your Gift Cards
        </button>
        <button
          onClick={() => setActiveTab('redeem')}
          className={`pb-2 px-1 ${activeTab === 'redeem' ? 'border-b-2 border-[var(--amazon-orange)] text-[var(--amazon-orange)] font-medium' : 'text-[var(--text-secondary)]'}`}
        >
          Redeem a Gift Card
        </button>
        <button
          onClick={() => setActiveTab('reload')}
          className={`pb-2 px-1 ${activeTab === 'reload' ? 'border-b-2 border-[var(--amazon-orange)] text-[var(--amazon-orange)] font-medium' : 'text-[var(--text-secondary)]'}`}
        >
          Reload Balance
        </button>
        <button
          onClick={() => setActiveTab('history')}
          className={`pb-2 px-1 ${activeTab === 'history' ? 'border-b-2 border-[var(--amazon-orange)] text-[var(--amazon-orange)] font-medium' : 'text-[var(--text-secondary)]'}`}
        >
          Transaction History
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      ) : (
        <>
          {/* Your Gift Cards Tab */}
          {activeTab === 'balance' && (
            <div className="bg-white rounded border p-6">
              <h2 className="text-xl font-bold mb-4">Your Gift Cards</h2>
              {giftCards.length === 0 ? (
                <div className="text-center py-8">
                  <p className="text-[var(--text-secondary)] mb-4">You don't have any gift cards yet.</p>
                  <button
                    onClick={() => setActiveTab('redeem')}
                    className="btn-primary"
                  >
                    Redeem a Gift Card
                  </button>
                </div>
              ) : (
                <div className="space-y-4">
                  {giftCards.map((card) => (
                    <div key={card.id} className="border rounded p-4 flex justify-between items-center">
                      <div>
                        <p className="font-mono text-lg">{card.code}</p>
                        <p className="text-sm text-[var(--text-secondary)]">
                          Original: ${card.original_amount.toFixed(2)}
                          {card.redeemed_at && (
                            <> • Redeemed: {formatDate(card.redeemed_at)}</>
                          )}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-2xl font-bold text-green-600">
                          ${card.current_balance.toFixed(2)}
                        </p>
                        <p className="text-sm text-[var(--text-secondary)]">Current Balance</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Redeem Tab */}
          {activeTab === 'redeem' && (
            <div className="bg-white rounded border p-6 max-w-md">
              <h2 className="text-xl font-bold mb-4">Redeem a Gift Card</h2>
              <form onSubmit={handleRedeemGiftCard}>
                <div className="mb-4">
                  <label htmlFor="giftCardCode" className="block text-sm font-medium mb-1">
                    Gift Card Claim Code
                  </label>
                  <input
                    type="text"
                    id="giftCardCode"
                    value={redeemCode}
                    onChange={(e) => setRedeemCode(e.target.value.toUpperCase())}
                    placeholder="Enter claim code"
                    className="w-full px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-[var(--amazon-orange)]"
                    disabled={redeemLoading}
                  />
                  <p className="text-xs text-[var(--text-secondary)] mt-1">
                    Enter the claim code found on the back of your gift card.
                  </p>
                </div>
                <button
                  type="submit"
                  className="btn-primary w-full"
                  disabled={!redeemCode.trim() || redeemLoading}
                >
                  {redeemLoading ? 'Redeeming...' : 'Apply to Your Balance'}
                </button>
              </form>
            </div>
          )}

          {/* Reload Tab */}
          {activeTab === 'reload' && (
            <div className="bg-white rounded border p-6 max-w-md">
              <h2 className="text-xl font-bold mb-4">Reload Your Balance</h2>
              <form onSubmit={handleReloadBalance}>
                <div className="mb-4">
                  <label className="block text-sm font-medium mb-2">Select Amount</label>
                  <div className="grid grid-cols-4 gap-2 mb-3">
                    {['25', '50', '100', '200'].map((amount) => (
                      <button
                        key={amount}
                        type="button"
                        onClick={() => setReloadAmount(amount)}
                        className={`py-2 border rounded text-center ${reloadAmount === amount ? 'border-[var(--amazon-orange)] bg-orange-50' : 'border-gray-300 hover:border-gray-400'}`}
                      >
                        ${amount}
                      </button>
                    ))}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-lg">$</span>
                    <input
                      type="number"
                      value={reloadAmount}
                      onChange={(e) => setReloadAmount(e.target.value)}
                      min="1"
                      max="2000"
                      step="1"
                      className="flex-1 px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-[var(--amazon-orange)]"
                      disabled={reloadLoading}
                    />
                  </div>
                  <p className="text-xs text-[var(--text-secondary)] mt-1">
                    Enter an amount between $1 and $2,000
                  </p>
                </div>
                <button
                  type="submit"
                  className="btn-primary w-full"
                  disabled={!reloadAmount || reloadLoading}
                >
                  {reloadLoading ? 'Reloading...' : `Reload $${parseFloat(reloadAmount || '0').toFixed(2)}`}
                </button>
                <p className="text-xs text-[var(--text-secondary)] mt-2 text-center">
                  Funds will be added to your gift card balance
                </p>
              </form>
            </div>
          )}

          {/* Transaction History Tab */}
          {activeTab === 'history' && (
            <div className="bg-white rounded border p-6">
              <h2 className="text-xl font-bold mb-4">Transaction History</h2>
              {transactions.length === 0 ? (
                <p className="text-[var(--text-secondary)] text-center py-8">
                  No transactions yet.
                </p>
              ) : (
                <div className="space-y-3">
                  {transactions.map((transaction) => (
                    <div key={transaction.id} className="border-b pb-3 last:border-b-0">
                      <div className="flex justify-between items-start">
                        <div>
                          <p className="font-medium">{getTransactionTypeLabel(transaction.type)}</p>
                          <p className="text-sm text-[var(--text-secondary)]">
                            {formatDate(transaction.created_at)}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className={`font-bold ${getTransactionTypeColor(transaction.type)}`}>
                            {transaction.type === 'purchase' ? '-' : '+'}${transaction.amount.toFixed(2)}
                          </p>
                          <p className="text-sm text-[var(--text-secondary)]">
                            Balance: ${transaction.balance_after.toFixed(2)}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
