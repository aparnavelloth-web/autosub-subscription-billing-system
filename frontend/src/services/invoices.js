/**
 * Invoices Service for AutoSub Frontend.
 * Communicates with backend endpoints:
 * - GET /api/v1/invoices (list, search, filter, pagination)
 * - GET /api/v1/invoices/:id (detail & line items)
 * - GET /api/v1/invoices/:id/pdf (downloadable PDF statement)
 */

export const invoiceService = {
  /**
   * Fetches paginated invoices with optional search and filters.
   */
  async listInvoices({
    page = 1,
    size = 20,
    status = '',
    query = '',
    dateFrom = '',
    dateTo = '',
    customerId = null,
    subscriptionId = null,
    billingCycleId = null,
  } = {}) {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('size', String(size));

    if (status && status !== 'all') {
      params.set('status', status.toLowerCase());
    }
    if (query && query.trim()) {
      params.set('query', query.trim());
    }
    if (dateFrom) {
      params.set('date_from', `${dateFrom}T00:00:00Z`);
    }
    if (dateTo) {
      params.set('date_to', `${dateTo}T23:59:59Z`);
    }
    if (customerId) {
      params.set('customer_id', String(customerId));
    }
    if (subscriptionId) {
      params.set('subscription_id', String(subscriptionId));
    }
    if (billingCycleId) {
      params.set('billing_cycle_id', String(billingCycleId));
    }

    const token = localStorage.getItem('autosub_access_token');
    const headers = { 'Content-Type': 'application/json' };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const res = await fetch(`/api/v1/invoices?${params.toString()}`, { headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to fetch invoices (HTTP ${res.status})`);
    }
    return res.json();
  },

  /**
   * Retrieves single invoice details with full itemized line charges.
   */
  async getInvoice(invoiceId) {
    const token = localStorage.getItem('autosub_access_token');
    const headers = { 'Content-Type': 'application/json' };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const res = await fetch(`/api/v1/invoices/${invoiceId}`, { headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Invoice #${invoiceId} not found`);
    }
    return res.json();
  },

  /**
   * Downloads the generated ReportLab PDF statement directly to the user's browser.
   */
  async downloadInvoicePdf(invoiceId, invoiceNumber = '') {
    const token = localStorage.getItem('autosub_access_token');
    const headers = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const res = await fetch(`/api/v1/invoices/${invoiceId}/pdf`, { headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to download PDF for invoice #${invoiceId}`);
    }

    const blob = await res.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = `invoice-${invoiceNumber || invoiceId}.pdf`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(downloadUrl);
  },
};
