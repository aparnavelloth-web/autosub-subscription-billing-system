/**
 * Subscriptions & Proration Service for AutoSub Frontend.
 * Communicates with backend endpoints:
 * - GET /api/v1/subscriptions (list, filter, pagination)
 * - GET /api/v1/subscriptions/:id (detail & snapshot)
 * - GET /api/v1/plans (active subscription plans)
 * - POST /api/v1/subscriptions/:id/proration-preview (calculate proration preview)
 * - POST /api/v1/subscriptions/:id/change-plan (confirm plan change & generate adjustment invoice)
 * - GET /api/v1/subscriptions/:id/history (actual chronological timeline)
 */

function getAuthHeaders() {
  const token = localStorage.getItem('autosub_access_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export const subscriptionService = {
  /**
   * Fetches paginated subscriptions.
   */
  async listSubscriptions({ page = 1, size = 20, status = '', customerId = null, planId = null } = {}) {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('size', String(size));
    if (status && status !== 'all') {
      params.set('status', status.toLowerCase());
    }
    if (customerId) {
      params.set('customer_id', String(customerId));
    }
    if (planId) {
      params.set('plan_id', String(planId));
    }

    const res = await fetch(`/api/v1/subscriptions?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to fetch subscriptions (HTTP ${res.status})`);
    }
    return res.json();
  },

  /**
   * Retrieves single subscription details.
   */
  async getSubscription(subscriptionId) {
    const res = await fetch(`/api/v1/subscriptions/${subscriptionId}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Subscription #${subscriptionId} not found`);
    }
    return res.json();
  },

  /**
   * Retrieves active subscription plans for plan selection.
   */
  async listActivePlans() {
    const res = await fetch(`/api/v1/plans?active_only=true&include_archived=false&size=100`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to fetch plans`);
    }
    const data = await res.json();
    return data.items || [];
  },

  /**
   * Calculates and returns proration preview without modifying state.
   */
  async previewProration(subscriptionId, { newPlanId, quantity = null, asOf = null } = {}) {
    const body = { new_plan_id: Number(newPlanId) };
    if (quantity !== null && quantity !== undefined) {
      body.quantity = Number(quantity);
    }
    if (asOf) {
      body.as_of = asOf;
    }

    const res = await fetch(`/api/v1/subscriptions/${subscriptionId}/proration-preview`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Proration preview failed (HTTP ${res.status})`);
    }
    return res.json();
  },

  /**
   * Confirms and executes plan change, generating an adjustment invoice.
   */
  async changePlan(subscriptionId, { newPlanId, quantity = null, resetBillingPeriod = false, asOf = null } = {}) {
    const body = {
      new_plan_id: Number(newPlanId),
      reset_billing_period: Boolean(resetBillingPeriod),
    };
    if (quantity !== null && quantity !== undefined) {
      body.quantity = Number(quantity);
    }
    if (asOf) {
      body.as_of = asOf;
    }

    const res = await fetch(`/api/v1/subscriptions/${subscriptionId}/change-plan`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Plan change execution failed (HTTP ${res.status})`);
    }
    return res.json();
  },

  /**
   * Fetches the actual subscription timeline from backend audit logs & invoices.
   */
  async getSubscriptionHistory(subscriptionId) {
    const res = await fetch(`/api/v1/subscriptions/${subscriptionId}/history`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to fetch subscription history`);
    }
    return res.json();
  },
};
