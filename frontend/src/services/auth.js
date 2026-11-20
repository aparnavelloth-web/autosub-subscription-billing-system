/**
 * Authentication Service for Subscription Billing Frontend.
 * Manages JWT tokens, persistent session storage, and API network calls.
 */

const TOKEN_KEY = 'autosub_access_token';
const USER_KEY = 'autosub_user';

export const authService = {
  getToken() {
    return localStorage.getItem(TOKEN_KEY);
  },

  getUser() {
    try {
      const raw = localStorage.getItem(USER_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  },

  setSession(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },

  clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },

  isAuthenticated() {
    const token = this.getToken();
    return Boolean(token);
  },

  async signup({ email, full_name, password }) {
    const res = await fetch('/api/v1/auth/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, full_name, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Signup failed. Please try again.');
    }
    return data;
  },

  async signin({ email, password }) {
    const res = await fetch('/api/v1/auth/signin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Invalid email or password.');
    }
    if (data.access_token) {
      this.setSession(data.access_token, data.user);
    }
    return data;
  },

  async getMe() {
    const token = this.getToken();
    if (!token) {
      throw new Error('No authentication token found.');
    }
    const res = await fetch('/api/v1/auth/me', {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
    });
    if (res.status === 401 || res.status === 403) {
      this.clearSession();
      throw new Error('Session expired or unauthorized. Please sign in again.');
    }
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Failed to retrieve user profile.');
    }
    // Update stored user profile
    localStorage.setItem(USER_KEY, JSON.stringify(data));
    return data;
  },
};
