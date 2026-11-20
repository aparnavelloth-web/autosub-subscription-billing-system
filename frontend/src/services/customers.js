/**
 * Customers Service for AutoSub Frontend.
 * Communicates with backend endpoints:
 * - GET /api/v1/customers (list, pagination, active/deleted filters)
 * - GET /api/v1/customers/:id (single customer details)
 * - POST /api/v1/customers (create customer)
 * - PUT /api/v1/customers/:id (update customer)
 * - GET /api/v1/customers/:id/subscriptions (customer subscriptions)
 * - GET /api/v1/customers/:id/invoices (customer invoices ledger)
 */

function getAuthHeaders() {
  const token = localStorage.getItem('autosub_access_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export const customerService = {
  /**
   * Fetches paginated customers with optional deletion and active filters.
   */
  async listCustomers({
    page = 1,
    size = 50,
    include_deleted = false,
    active_only = false,
  } = {}) {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('size', String(size));
    params.set('include_deleted', String(include_deleted));
    params.set('active_only', String(active_only));

    const res = await fetch(`/api/v1/customers?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to fetch customers (HTTP ${res.status})`);
    }
    return res.json();
  },

  /**
   * Retrieves single customer by ID.
   */
  async getCustomer(customerId) {
    const res = await fetch(`/api/v1/customers/${customerId}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Customer #${customerId} not found`);
    }
    return res.json();
  },

  /**
   * Registers a new billing customer.
   */
  async createCustomer(payload) {
    const res = await fetch('/api/v1/customers', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Failed to create customer (HTTP ${res.status})`);
    }
    return data;
  },

  /**
   * Updates an existing customer profile.
   */
  async updateCustomer(customerId, payload) {
    const res = await fetch(`/api/v1/customers/${customerId}`, {
      method: 'PUT',
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Failed to update customer #${customerId} (HTTP ${res.status})`);
    }
    return data;
  },

  /**
   * Retrieves subscriptions belonging to this customer.
   */
  async getCustomerSubscriptions(customerId, { page = 1, size = 20 } = {}) {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('size', String(size));

    const res = await fetch(`/api/v1/customers/${customerId}/subscriptions?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to fetch subscriptions for customer #${customerId}`);
    }
    return res.json();
  },

  /**
   * Retrieves invoices belonging to this customer.
   */
  async getCustomerInvoices(customerId, { page = 1, size = 20, status = '' } = {}) {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('size', String(size));
    if (status) {
      params.set('status', status);
    }

    const res = await fetch(`/api/v1/customers/${customerId}/invoices?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to fetch invoices for customer #${customerId}`);
    }
    return res.json();
  },
};
