/**
 * Plans Service for AutoSub Frontend.
 * Communicates with backend endpoints:
 * - GET /api/v1/plans (list, filter, include_archived, pagination)
 * - GET /api/v1/plans/:id (single plan details)
 * - POST /api/v1/plans (create subscription plan)
 * - PUT /api/v1/plans/:id (update plan)
 * - PATCH /api/v1/plans/:id/archive (archive plan)
 * - DELETE /api/v1/plans/:id (soft-delete plan)
 */

function getAuthHeaders() {
  const token = localStorage.getItem('autosub_access_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export const planService = {
  /**
   * Fetches paginated subscription plans with filtering.
   */
  async listPlans({
    page = 1,
    size = 50,
    include_archived = true,
    active_only = false,
    billing_interval = null,
  } = {}) {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('size', String(size));
    params.set('include_archived', String(include_archived));
    params.set('active_only', String(active_only));
    if (billing_interval && billing_interval !== 'all') {
      params.set('billing_interval', billing_interval.toLowerCase());
    }

    const res = await fetch(`/api/v1/plans?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to fetch plans (HTTP ${res.status})`);
    }
    return res.json();
  },

  /**
   * Retrieves single plan by ID.
   */
  async getPlan(planId) {
    const res = await fetch(`/api/v1/plans/${planId}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Plan #${planId} not found`);
    }
    return res.json();
  },

  /**
   * Creates a new subscription plan.
   */
  async createPlan(payload) {
    const res = await fetch('/api/v1/plans', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Failed to create plan (HTTP ${res.status})`);
    }
    return data;
  },

  /**
   * Updates an existing subscription plan.
   */
  async updatePlan(planId, payload) {
    const res = await fetch(`/api/v1/plans/${planId}`, {
      method: 'PUT',
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Failed to update plan #${planId} (HTTP ${res.status})`);
    }
    return data;
  },

  /**
   * Archives a plan via PATCH /api/v1/plans/:id/archive.
   */
  async archivePlan(planId) {
    const res = await fetch(`/api/v1/plans/${planId}/archive`, {
      method: 'PATCH',
      headers: getAuthHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Failed to archive plan #${planId} (HTTP ${res.status})`);
    }
    return data;
  },

  /**
   * Deletes (soft-deletes) a plan via DELETE /api/v1/plans/:id.
   */
  async deletePlan(planId) {
    const res = await fetch(`/api/v1/plans/${planId}`, {
      method: 'DELETE',
      headers: getAuthHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Failed to delete plan #${planId} (HTTP ${res.status})`);
    }
    return data;
  },
};
