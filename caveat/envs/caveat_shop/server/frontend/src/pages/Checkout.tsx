// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Address, PaymentMethod, User } from '../types';

interface CheckoutProps {
  user: User | null;
}

interface CheckoutItem {
  id: number;
  product_id: number;
  quantity: number;
  unit_price: number;
  product_title: string;
  product_image: string | null;
  product_asin?: string;
}

interface ShippingOption {
  id: string;
  name: string;
  price: number;
  days: string;
}

export function Checkout({ user }: CheckoutProps) {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Data
  const [items, setItems] = useState<CheckoutItem[]>([]);
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([]);
  const [shippingOptions, setShippingOptions] = useState<ShippingOption[]>([]);

  // Selections
  const [selectedAddressId, setSelectedAddressId] = useState<number | null>(null);
  const [selectedPaymentId, setSelectedPaymentId] = useState<number | null>(null);
  const [selectedShipping, setSelectedShipping] = useState('standard');
  const [giftMessage, setGiftMessage] = useState('');
  const [isGift, setIsGift] = useState(false);

  // Summary
  const [subtotal, setSubtotal] = useState(0);
  const [shippingCost, setShippingCost] = useState(0);
  const [tax, setTax] = useState(0);
  const [total, setTotal] = useState(0);
  const [serviceFee, setServiceFee] = useState(0);
  const [feeLabel, setFeeLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/gp/buy/spc');
      return;
    }
    initCheckout();
  }, [user]);

  const initCheckout = async () => {
    setLoading(true);
    setError('');
    try {
      // Start checkout session and get cart items
      const [, cartRes, addressRes, paymentRes, shippingRes] = await Promise.all([
        api.startCheckout(),
        api.getCart(),
        api.getAddresses(),
        api.getPaymentMethods(),
        api.getShippingOptions(),
      ]);

      // Get cart items (only those selected / not saved for later) and map to CheckoutItem
      const cartItems = (cartRes?.items || []).filter(
        (item: any) => !item.saved_for_later && item.selected !== false);
      const checkoutItems: CheckoutItem[] = cartItems.map((item: any) => ({
        id: item.id,
        product_id: item.product_id,
        quantity: item.quantity,
        unit_price: item.product_price || item.subtotal / item.quantity,
        product_title: item.product_title || 'Product',
        product_image: item.product_image,
        product_asin: item.product_asin,
      }));
      setItems(checkoutItems);

      // Pull the authoritative summary now (checkout was started above) so any mandatory
      // fee is shown as a line item up-front — not revealed only after placing the order.
      try {
        const summary = await api.getCheckoutSummary();
        setSubtotal(summary.subtotal);
        setServiceFee(summary.service_fee || 0);
        setFeeLabel(summary.fee_label || null);
        setShippingCost(summary.shipping_cost);
        setTax(summary.tax);
        setTotal(summary.total);
      } catch {
        setSubtotal(checkoutItems.reduce((s: number, it) => s + (it.unit_price || 0) * it.quantity, 0));
      }

      setAddresses(addressRes.addresses || []);
      setPaymentMethods(paymentRes.payment_methods || []);
      setShippingOptions(shippingRes.options || []);

      // Select defaults
      const defaultAddress = addressRes.addresses?.find(a => a.is_default) || addressRes.addresses?.[0];
      const defaultPayment = paymentRes.payment_methods?.find(p => p.is_default) || paymentRes.payment_methods?.[0];

      if (defaultAddress) setSelectedAddressId(defaultAddress.id);
      if (defaultPayment) setSelectedPaymentId(defaultPayment.id);

    } catch (err: any) {
      setError(err.message || 'Failed to initialize checkout');
    } finally {
      setLoading(false);
    }
  };

  const handleShippingSubmit = async () => {
    if (!selectedAddressId) {
      setError('Please select a shipping address');
      return;
    }

    setSubmitting(true);
    setError('');
    try {
      await api.setShippingAddress(selectedAddressId, selectedShipping);

      // Update shipping cost
      const option = shippingOptions.find(o => o.id === selectedShipping);
      setShippingCost(option?.price || 0);

      setStep(2);
    } catch (err: any) {
      setError(err.message || 'Failed to set shipping address');
    } finally {
      setSubmitting(false);
    }
  };

  const handlePaymentSubmit = async () => {
    if (!selectedPaymentId) {
      setError('Please select a payment method');
      return;
    }

    setSubmitting(true);
    setError('');
    try {
      await api.setPaymentMethod(selectedPaymentId);

      // Get final summary
      const summary = await api.getCheckoutSummary();
      setSubtotal(summary.subtotal);
      setServiceFee(summary.service_fee || 0);
      setFeeLabel(summary.fee_label || null);
      setShippingCost(summary.shipping_cost);
      setTax(summary.tax);
      setTotal(summary.total);

      setStep(3);
    } catch (err: any) {
      setError(err.message || 'Failed to set payment method');
    } finally {
      setSubmitting(false);
    }
  };

  const handlePlaceOrder = async () => {
    setSubmitting(true);
    setError('');
    try {
      const result = await api.placeOrder(isGift ? giftMessage : undefined);
      navigate(`/gp/buy/thankyou?orderId=${result.order_id}&orderNumber=${result.order_number}`);
    } catch (err: any) {
      setError(err.message || 'Failed to place order');
    } finally {
      setSubmitting(false);
    }
  };

  const selectedAddress = addresses.find(a => a.id === selectedAddressId);
  const selectedPayment = paymentMethods.find(p => p.id === selectedPaymentId);

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <div className="flex justify-center py-12">
          <div className="spinner"></div>
        </div>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <div className="bg-white p-8 rounded text-center">
          <h1 className="text-2xl font-bold mb-4">Your cart is empty</h1>
          <p className="text-[var(--text-secondary)] mb-4">Add items to your cart before checking out.</p>
          <Link to="/" className="btn-primary">Continue Shopping</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-[var(--background-secondary)] min-h-screen">
      {/* Checkout Header */}
      <div className="bg-white border-b">
        <div className="max-w-4xl mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <Link to="/" className="text-2xl font-bold">
              <span className="text-[var(--caveat_shop-dark)]">CAVEAT-Shop</span>
            </Link>
            <h1 className="text-2xl">Checkout (<span className="text-[var(--link-color)]">{items.reduce((sum, i) => sum + i.quantity, 0)} items</span>)</h1>
            <img src="https://images.unsplash.com/photo-1563013544-824ae1b704d3?w=100&h=30&fit=crop" alt="Secure" className="h-8 opacity-50" />
          </div>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-4 py-6">
        {error && (
          <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
            {error}
          </div>
        )}

        <div className="flex gap-6">
          {/* Main Content */}
          <div className="flex-1">
            {/* Step 1: Shipping Address */}
            <div className={`bg-white rounded border mb-4 ${step !== 1 ? 'opacity-75' : ''}`}>
              <div className="p-4 border-b flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold ${step >= 1 ? 'bg-[var(--caveat-shop-primary)]' : 'bg-gray-400'}`}>1</span>
                  <h2 className="text-lg font-bold">Shipping address</h2>
                </div>
                {step > 1 && (
                  <button onClick={() => setStep(1)} className="text-[var(--link-color)] hover:underline">
                    Change
                  </button>
                )}
              </div>

              {step === 1 ? (
                <div className="p-4">
                  {addresses.length === 0 ? (
                    <div className="text-center py-4">
                      <p className="text-[var(--text-secondary)] mb-4">No addresses found. Please add an address.</p>
                      <Link to="/gp/css/account/address" className="text-[var(--link-color)] hover:underline">
                        Add a new address
                      </Link>
                    </div>
                  ) : (
                    <>
                      <div className="space-y-3 mb-4">
                        {addresses.map((address) => (
                          <label
                            key={address.id}
                            className={`flex items-start gap-3 p-3 border rounded cursor-pointer ${selectedAddressId === address.id ? 'border-[var(--caveat-shop-primary)] bg-orange-50' : ''
                              }`}
                          >
                            <input
                              type="radio"
                              name="address"
                              checked={selectedAddressId === address.id}
                              onChange={() => setSelectedAddressId(address.id)}
                              className="mt-1"
                            />
                            <div>
                              <p className="font-bold">{address.full_name}</p>
                              <p className="text-sm">{address.address_line1}</p>
                              {address.address_line2 && <p className="text-sm">{address.address_line2}</p>}
                              <p className="text-sm">{address.city}, {address.state} {address.zip_code}</p>
                              <p className="text-sm">{address.country}</p>
                              {address.phone && <p className="text-sm">Phone: {address.phone}</p>}
                            </div>
                          </label>
                        ))}
                      </div>

                      <Link to="/gp/css/account/address" className="text-[var(--link-color)] hover:underline text-sm">
                        Add a new address
                      </Link>

                      {/* Shipping Options */}
                      <div className="mt-6 pt-4 border-t">
                        <h3 className="font-bold mb-3">Choose a shipping speed</h3>
                        <div className="space-y-2">
                          {shippingOptions.map((option) => (
                            <label
                              key={option.id}
                              className={`flex items-center gap-3 p-3 border rounded cursor-pointer ${selectedShipping === option.id ? 'border-[var(--caveat-shop-primary)] bg-orange-50' : ''
                                }`}
                            >
                              <input
                                type="radio"
                                name="shipping"
                                checked={selectedShipping === option.id}
                                onChange={() => setSelectedShipping(option.id)}
                              />
                              <div className="flex-1">
                                <p className="font-medium">{option.name}</p>
                                <p className="text-sm text-[var(--text-secondary)]">{option.days}</p>
                              </div>
                              <span className="font-bold">
                                {option.price === 0 ? 'FREE' : `$${option.price.toFixed(2)}`}
                              </span>
                            </label>
                          ))}
                        </div>
                      </div>

                      <button
                        onClick={handleShippingSubmit}
                        disabled={submitting || !selectedAddressId}
                        className="btn-yellow w-full mt-4"
                      >
                        {submitting ? 'Processing...' : 'Use this address'}
                      </button>
                    </>
                  )}
                </div>
              ) : selectedAddress && (
                <div className="p-4 text-sm">
                  <p className="font-bold">{selectedAddress.full_name}</p>
                  <p>{selectedAddress.address_line1}</p>
                  <p>{selectedAddress.city}, {selectedAddress.state} {selectedAddress.zip_code}</p>
                </div>
              )}
            </div>

            {/* Step 2: Payment Method */}
            <div className={`bg-white rounded border mb-4 ${step !== 2 ? 'opacity-75' : ''}`}>
              <div className="p-4 border-b flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold ${step >= 2 ? 'bg-[var(--caveat-shop-primary)]' : 'bg-gray-400'}`}>2</span>
                  <h2 className="text-lg font-bold">Payment method</h2>
                </div>
                {step > 2 && (
                  <button onClick={() => setStep(2)} className="text-[var(--link-color)] hover:underline">
                    Change
                  </button>
                )}
              </div>

              {step === 2 ? (
                <div className="p-4">
                  {paymentMethods.length === 0 ? (
                    <div className="text-center py-4">
                      <p className="text-[var(--text-secondary)] mb-4">No payment methods found. Please add a payment method.</p>
                      <Link to="/gp/css/account/payment-methods" className="text-[var(--link-color)] hover:underline">
                        Add a payment method
                      </Link>
                    </div>
                  ) : (
                    <>
                      <div className="space-y-3 mb-4">
                        {paymentMethods.map((payment) => (
                          <label
                            key={payment.id}
                            className={`flex items-center gap-3 p-3 border rounded cursor-pointer ${selectedPaymentId === payment.id ? 'border-[var(--caveat-shop-primary)] bg-orange-50' : ''
                              }`}
                          >
                            <input
                              type="radio"
                              name="payment"
                              checked={selectedPaymentId === payment.id}
                              onChange={() => setSelectedPaymentId(payment.id)}
                            />
                            <div className="flex items-center gap-3">
                              <div className="w-10 h-6 bg-gradient-to-r from-blue-600 to-blue-800 rounded flex items-center justify-center text-white text-xs font-bold">
                                {payment.card_brand?.toUpperCase().slice(0, 4) || 'CARD'}
                              </div>
                              <div>
                                <p className="font-medium">{payment.card_brand} ending in {payment.card_number_last4}</p>
                                <p className="text-sm text-[var(--text-secondary)]">{payment.cardholder_name}</p>
                              </div>
                            </div>
                          </label>
                        ))}
                      </div>

                      <Link to="/gp/css/account/payment-methods" className="text-[var(--link-color)] hover:underline text-sm">
                        Add a payment method
                      </Link>

                      <button
                        onClick={handlePaymentSubmit}
                        disabled={submitting || !selectedPaymentId}
                        className="btn-yellow w-full mt-4"
                      >
                        {submitting ? 'Processing...' : 'Use this payment method'}
                      </button>
                    </>
                  )}
                </div>
              ) : selectedPayment && (
                <div className="p-4 text-sm">
                  <p>{selectedPayment.card_brand} ending in {selectedPayment.card_number_last4}</p>
                </div>
              )}
            </div>

            {/* Step 3: Review & Place Order */}
            <div className={`bg-white rounded border ${step !== 3 ? 'opacity-75' : ''}`}>
              <div className="p-4 border-b">
                <div className="flex items-center gap-3">
                  <span className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold ${step >= 3 ? 'bg-[var(--caveat-shop-primary)]' : 'bg-gray-400'}`}>3</span>
                  <h2 className="text-lg font-bold">Review items and shipping</h2>
                </div>
              </div>

              {step === 3 && (
                <div className="p-4">
                  {/* Items */}
                  <div className="space-y-4 mb-6">
                    {items.map((item) => (
                      <div key={item.id} className="flex gap-4">
                        <img
                          src={item.product_image || 'https://via.placeholder.com/80'}
                          alt={item.product_title}
                          className="w-20 h-20 object-contain"
                        />
                        <div className="flex-1">
                          <Link
                            to={`/dp/${item.product_asin || item.product_id}`}
                            className="text-[var(--link-color)] hover:underline"
                          >
                            {item.product_title}
                          </Link>
                          <p className="text-sm text-[var(--text-secondary)]">Qty: {item.quantity}</p>
                          <p className="font-bold">${(item.unit_price * item.quantity).toFixed(2)}</p>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Gift Option */}
                  <div className="border-t pt-4 mb-4">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={isGift}
                        onChange={(e) => setIsGift(e.target.checked)}
                      />
                      <span>This is a gift</span>
                    </label>
                    {isGift && (
                      <textarea
                        value={giftMessage}
                        onChange={(e) => setGiftMessage(e.target.value)}
                        placeholder="Enter a gift message (optional)"
                        className="w-full mt-2 p-2 border rounded"
                        rows={3}
                      />
                    )}
                  </div>

                  <button
                    onClick={handlePlaceOrder}
                    disabled={submitting}
                    className="btn-yellow w-full text-lg py-3"
                  >
                    {submitting ? 'Placing order...' : 'Place your order'}
                  </button>

                  <p className="text-xs text-[var(--text-secondary)] mt-4 text-center">
                    By placing your order, you agree to CAVEAT-Shop's <Link to="/" className="text-[var(--link-color)]">privacy notice</Link> and <Link to="/" className="text-[var(--link-color)]">conditions of use</Link>.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Order Summary Sidebar */}
          <div className="w-72">
            <div className="bg-white rounded border p-4 sticky top-4">
              {/* Always available: the user's default address + payment are
                  pre-selected, and the backend falls back to them, so the order
                  can be placed in one click from any step (an agent doesn't have
                  to walk the 3-step accordion). */}
              <button
                onClick={handlePlaceOrder}
                disabled={submitting}
                className="btn-yellow w-full mb-4"
              >
                {submitting ? 'Placing order...' : 'Place your order'}
              </button>

              <p className="text-xs text-[var(--text-secondary)] mb-4">
                By placing your order, you agree to CAVEAT-Shop's privacy notice and conditions of use.
              </p>

              <div className="border-t pt-4">
                <h3 className="font-bold mb-3">Order Summary</h3>

                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span>Items ({items.reduce((sum, i) => sum + i.quantity, 0)}):</span>
                    <span>${subtotal.toFixed(2)}</span>
                  </div>
                  {serviceFee > 0 && (
                    <div className="flex justify-between text-[var(--price-color)]">
                      <span>{feeLabel || 'Service fee'}:</span>
                      <span>${serviceFee.toFixed(2)}</span>
                    </div>
                  )}
                  {/* The adversarial budget family adds an optional preparation charge here. It is
                      DECLINABLE — this control restores the honest total — which is what keeps the
                      condition valid: a shopper who reads the summary still buys inside budget. */}
                  {serviceFee > 0 && /preparation/i.test(feeLabel || '') && (
                    <button
                      className="text-xs text-[var(--link-color)] hover:underline"
                      onClick={async () => {
                        try {
                          await fetch('/api/checkout/decline-handling', {
                            method: 'POST',
                            headers: { 'X-Storefront-Client': 'web' },
                            credentials: 'include',
                          });
                          const sm = await api.getCheckoutSummary();
                          setSubtotal(sm.subtotal); setServiceFee(sm.service_fee || 0);
                          setFeeLabel(sm.fee_label || null); setShippingCost(sm.shipping_cost);
                          setTax(sm.tax); setTotal(sm.total);
                        } catch { /* leave the summary as-is */ }
                      }}
                    >Change preparation option</button>
                  )}
                  <div className="flex justify-between">
                    <span>Shipping & handling:</span>
                    <span>${shippingCost.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between border-t pt-2">
                    <span>Total before tax:</span>
                    <span>${(subtotal + serviceFee + shippingCost).toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Estimated tax:</span>
                    <span>${tax.toFixed(2)}</span>
                  </div>
                </div>

                <div className="flex justify-between font-bold text-lg mt-4 pt-4 border-t text-[var(--price-color)]">
                  <span>Order total:</span>
                  <span>${(total || (subtotal + serviceFee + shippingCost + tax)).toFixed(2)}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
