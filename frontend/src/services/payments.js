/**
 * Payments Service for AutoSub Frontend.
 * Communicates with backend endpoints:
 * - GET /api/v1/payments (list, search, filter, pagination)
 * - GET /api/v1/payments/:id (payment details with refunds)
 * - POST /payments/process (Milestone 2 Task 3: 80/20 simulated outcome processing)
 * - POST /api/v1/payments/:id/refund (partial or full refund)
 */

function getAuthHeaders() {
  const token = localStorage.getItem('autosub_access_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export const paymentsService = {
  /**
   * Fetches paginated payment records with optional filters.
   */
  async listPayments({
    page = 1,
    size = 20,
    status = '',
    customerId = null,
    invoiceId = null,
    paymentMethod = null,
  } = {}) {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('size', String(size));

    if (status && status !== 'all') {
      params.set('status', status.toLowerCase());
    }
    if (customerId) {
      params.set('customer_id', String(customerId));
    }
    if (invoiceId) {
      params.set('invoice_id', String(invoiceId));
    }
    if (paymentMethod && paymentMethod !== 'all') {
      params.set('payment_method', paymentMethod);
    }

    const res = await fetch(`/api/v1/payments?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to fetch payments (HTTP ${res.status})`);
    }
    return res.json();
  },

  /**
   * Retrieves single payment details with refund history.
   */
  async getPayment(paymentId) {
    const res = await fetch(`/api/v1/payments/${paymentId}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Payment #${paymentId} not found`);
    }
    return res.json();
  },

  /**
   * Initiates payment processing via the required POST /payments/process endpoint.
   * Auto-simulates 80% success and 20% failure probability on the backend.
   */
  async processPayment({
    invoice_id,
    amount = null,
    currency = null,
    customer_id = null,
    idempotency_key = null,
    simulate_outcome = null,
  }) {
    const payload = {
      invoice_id: Number(invoice_id),
    };
    if (amount !== null && amount !== undefined && String(amount).trim() !== '') {
      payload.amount = String(amount);
    }
    if (currency) {
      payload.currency = currency;
    }
    if (customer_id) {
      payload.customer_id = Number(customer_id);
    }
    if (idempotency_key) {
      payload.idempotency_key = idempotency_key;
    }
    if (simulate_outcome) {
      payload.simulate_outcome = simulate_outcome;
    }

    // Try primary Milestone 2 Task 3 route POST /payments/process, fallback to /api/v1/payments/process
    let res = await fetch('/payments/process', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });

    if (res.status === 404) {
      res = await fetch('/api/v1/payments/process', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
      });
    }

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Payment processing failed (HTTP ${res.status})`);
    }
    return data;
  },

  /**
   * Issues a simulated partial or full refund against a settled payment.
   */
  async refundPayment(paymentId, {
    amount = null,
    currency = null,
    reason = '',
    idempotency_key = null,
  } = {}) {
    const payload = {};
    if (amount !== null && amount !== undefined && String(amount).trim() !== '') {
      payload.amount = String(amount);
    }
    if (currency) {
      payload.currency = currency;
    }
    if (reason && reason.trim()) {
      payload.reason = reason.trim();
    }
    if (idempotency_key) {
      payload.idempotency_key = idempotency_key;
    }

    const res = await fetch(`/api/v1/payments/${paymentId}/refund`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Refund request failed (HTTP ${res.status})`);
    }
    return data;
  },
};
