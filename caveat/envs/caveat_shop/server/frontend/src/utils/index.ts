// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

export function formatDate(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  const isThisYear = date.getFullYear() === now.getFullYear();

  if (isToday) {
    return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  }

  if (isThisYear) {
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function formatFullDate(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function formatRelativeDate(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins} min ago`;
  if (diffHours < 24 && date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  }
  if (diffDays < 7) {
    return date.toLocaleDateString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit', hour12: true });
  }
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function getInitials(name: string): string {
  return name
    .split(' ')
    .map(n => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);
}

export function getAvatarColor(email: string): string {
  const colors = [
    '#EA4335', '#4285F4', '#34A853', '#FBBC05', '#9334E6',
    '#E52592', '#007B83', '#FA903E', '#795548', '#1A73E8',
  ];
  let hash = 0;
  for (let i = 0; i < email.length; i++) {
    hash = email.charCodeAt(i) + ((hash << 5) - hash);
  }
  return colors[Math.abs(hash) % colors.length];
}

export function truncate(text: string, length: number): string {
  if (text.length <= length) return text;
  return text.slice(0, length) + '...';
}

export function stripHtml(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  return doc.body.textContent || '';
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function getSnoozeTime(option: string): Date {
  const now = new Date();
  switch (option) {
    case 'later_today':
      now.setHours(18, 0, 0, 0);
      if (now < new Date()) now.setDate(now.getDate() + 1);
      return now;
    case 'tomorrow':
      now.setDate(now.getDate() + 1);
      now.setHours(8, 0, 0, 0);
      return now;
    case 'this_weekend':
      const dayOfWeek = now.getDay();
      const daysUntilSaturday = (6 - dayOfWeek + 7) % 7 || 7;
      now.setDate(now.getDate() + daysUntilSaturday);
      now.setHours(8, 0, 0, 0);
      return now;
    case 'next_week':
      const day = now.getDay();
      const daysUntilMonday = (8 - day) % 7 || 7;
      now.setDate(now.getDate() + daysUntilMonday);
      now.setHours(8, 0, 0, 0);
      return now;
    default:
      return now;
  }
}

export function classNames(...classes: (string | boolean | undefined)[]): string {
  return classes.filter(Boolean).join(' ');
}

export const LABEL_COLORS = [
  { name: 'Red', color: '#D93025' },
  { name: 'Orange', color: '#FA903E' },
  { name: 'Yellow', color: '#F9AB00' },
  { name: 'Green', color: '#188038' },
  { name: 'Teal', color: '#007B83' },
  { name: 'Blue', color: '#1A73E8' },
  { name: 'Purple', color: '#9334E6' },
  { name: 'Gray', color: '#5F6368' },
  { name: 'Pink', color: '#E52592' },
  { name: 'Brown', color: '#795548' },
];
