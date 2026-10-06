// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { User } from '../types';

interface LoginSecurityProps {
  user: User | null;
  onUpdateUser: (user: User) => void;
}

type EditMode = 'name' | 'email' | 'phone' | 'password' | null;

export function LoginSecurity({ user, onUpdateUser }: LoginSecurityProps) {
  const navigate = useNavigate();
  const [editMode, setEditMode] = useState<EditMode>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Form fields
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  useEffect(() => {
    if (!user) {
      navigate('/ap/signin?returnUrl=/gp/css/account/security');
      return;
    }
    setName(user.name);
    setEmail(user.email);
    setPhone(user.phone || '');
  }, [user]);

  const handleEdit = (mode: EditMode) => {
    setEditMode(mode);
    setError('');
    setSuccess('');
    if (mode === 'name') setName(user?.name || '');
    if (mode === 'email') setEmail(user?.email || '');
    if (mode === 'phone') setPhone(user?.phone || '');
    if (mode === 'password') {
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    }
  };

  const handleCancel = () => {
    setEditMode(null);
    setError('');
    if (user) {
      setName(user.name);
      setEmail(user.email);
      setPhone(user.phone || '');
    }
  };

  const handleSaveName = async () => {
    if (!name.trim()) {
      setError('Name cannot be empty');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await api.updateProfile({ name: name.trim() });
      if (user) {
        onUpdateUser({ ...user, name: name.trim() });
      }
      setSuccess('Name updated successfully');
      setEditMode(null);
      setTimeout(() => setSuccess(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to update name');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveEmail = async () => {
    if (!email.trim() || !email.includes('@')) {
      setError('Please enter a valid email address');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await api.updateProfile({ email: email.trim() });
      if (user) {
        onUpdateUser({ ...user, email: email.trim() });
      }
      setSuccess('Email updated successfully');
      setEditMode(null);
      setTimeout(() => setSuccess(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to update email');
    } finally {
      setSaving(false);
    }
  };

  const handleSavePhone = async () => {
    setSaving(true);
    setError('');
    try {
      await api.updateProfile({ phone: phone.trim() || undefined });
      if (user) {
        onUpdateUser({ ...user, phone: phone.trim() || undefined });
      }
      setSuccess('Phone number updated successfully');
      setEditMode(null);
      setTimeout(() => setSuccess(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to update phone');
    } finally {
      setSaving(false);
    }
  };

  const handleChangePassword = async () => {
    if (!currentPassword) {
      setError('Please enter your current password');
      return;
    }
    if (newPassword.length < 6) {
      setError('New password must be at least 6 characters');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('New passwords do not match');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await api.changePassword(currentPassword, newPassword);
      setSuccess('Password changed successfully');
      setEditMode(null);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setTimeout(() => setSuccess(''), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to change password');
    } finally {
      setSaving(false);
    }
  };

  if (!user) {
    return null;
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <nav className="text-sm mb-4">
        <Link to="/gp/css/account" className="text-[var(--link-color)] hover:underline">
          Your Account
        </Link>
        <span className="mx-2">&rsaquo;</span>
        <span>Login & Security</span>
      </nav>

      <h1 className="text-3xl font-bold mb-6">Login & Security</h1>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {success && (
        <div className="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
          {success}
        </div>
      )}

      <div className="bg-white border rounded-lg">
        {/* Name */}
        <div className="p-4 border-b">
          {editMode === 'name' ? (
            <div>
              <label className="block font-medium mb-2">Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full border rounded px-3 py-2 mb-3"
              />
              <div className="flex gap-2">
                <button onClick={handleSaveName} disabled={saving} className="btn-yellow">
                  {saving ? 'Saving...' : 'Save Changes'}
                </button>
                <button onClick={handleCancel} className="btn-secondary">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium">Name</p>
                <p className="text-[var(--text-secondary)]">{user.name}</p>
              </div>
              <button
                onClick={() => handleEdit('name')}
                className="text-[var(--link-color)] hover:underline"
              >
                Edit
              </button>
            </div>
          )}
        </div>

        {/* Email */}
        <div className="p-4 border-b">
          {editMode === 'email' ? (
            <div>
              <label className="block font-medium mb-2">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full border rounded px-3 py-2 mb-3"
              />
              <div className="flex gap-2">
                <button onClick={handleSaveEmail} disabled={saving} className="btn-yellow">
                  {saving ? 'Saving...' : 'Save Changes'}
                </button>
                <button onClick={handleCancel} className="btn-secondary">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium">Email</p>
                <p className="text-[var(--text-secondary)]">{user.email}</p>
              </div>
              <button
                onClick={() => handleEdit('email')}
                className="text-[var(--link-color)] hover:underline"
              >
                Edit
              </button>
            </div>
          )}
        </div>

        {/* Phone */}
        <div className="p-4 border-b">
          {editMode === 'phone' ? (
            <div>
              <label className="block font-medium mb-2">Mobile Phone Number</label>
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                className="w-full border rounded px-3 py-2 mb-3"
                placeholder="Enter phone number"
              />
              <div className="flex gap-2">
                <button onClick={handleSavePhone} disabled={saving} className="btn-yellow">
                  {saving ? 'Saving...' : 'Save Changes'}
                </button>
                <button onClick={handleCancel} className="btn-secondary">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium">Mobile Phone Number</p>
                <p className="text-[var(--text-secondary)]">{user.phone || 'Not set'}</p>
              </div>
              <button
                onClick={() => handleEdit('phone')}
                className="text-[var(--link-color)] hover:underline"
              >
                {user.phone ? 'Edit' : 'Add'}
              </button>
            </div>
          )}
        </div>

        {/* Password */}
        <div className="p-4">
          {editMode === 'password' ? (
            <div>
              <p className="font-medium mb-3">Change Password</p>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm mb-1">Current password</label>
                  <input
                    type="password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    className="w-full border rounded px-3 py-2"
                  />
                </div>
                <div>
                  <label className="block text-sm mb-1">New password</label>
                  <input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="w-full border rounded px-3 py-2"
                  />
                </div>
                <div>
                  <label className="block text-sm mb-1">Confirm new password</label>
                  <input
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="w-full border rounded px-3 py-2"
                  />
                </div>
              </div>
              <div className="flex gap-2 mt-4">
                <button onClick={handleChangePassword} disabled={saving} className="btn-yellow">
                  {saving ? 'Saving...' : 'Save Changes'}
                </button>
                <button onClick={handleCancel} className="btn-secondary">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium">Password</p>
                <p className="text-[var(--text-secondary)]">••••••••</p>
              </div>
              <button
                onClick={() => handleEdit('password')}
                className="text-[var(--link-color)] hover:underline"
              >
                Edit
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Two-Step Verification Section */}
      <div className="bg-white border rounded-lg mt-6 p-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="font-medium">Two-Step Verification (2SV)</p>
            <p className="text-sm text-[var(--text-secondary)]">
              Add an extra layer of security to your account
            </p>
          </div>
          <button className="text-[var(--link-color)] hover:underline">
            Manage
          </button>
        </div>
      </div>

      {/* Login History */}
      <div className="bg-white border rounded-lg mt-6 p-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="font-medium">Devices</p>
            <p className="text-sm text-[var(--text-secondary)]">
              Manage devices where you're signed in
            </p>
          </div>
          <button className="text-[var(--link-color)] hover:underline">
            Manage
          </button>
        </div>
      </div>
    </div>
  );
}
