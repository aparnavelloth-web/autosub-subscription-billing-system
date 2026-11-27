import React, { useState, useEffect, useCallback } from 'react';
import { invoiceService } from '../services/invoices';
import { paymentsService } from '../services/payments';
import InvoiceDetailModal from './InvoiceDetailModal';

export default function InvoicesManager() {
  const [invoices, setInvoices] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const [pages, setPages] = useState(0);

  // Filters state
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  // UI state
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedInvoice, setSelectedInvoice] = useState(null);
  const [isDetailOpen, setIsDetailOpen] = useState(false);
  const [downloadingId, setDownloadingId] = useState(null);
  const [processingId, setProcessingId] = useState(null);
  const [paymentFeedback, setPaymentFeedback] = useState(null);

  const fetchInvoices = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await invoiceService.listInvoices({
        page,
        size,
        status: statusFilter,
        query,
        dateFrom,
        dateTo,
      });
      setInvoices(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 0);
    } catch (err) {
      setError(err.message || 'Failed to retrieve invoice records.');
      setInvoices([]);
    } finally {
      setLoading(false);
    }
  }, [page, size, statusFilter, query, dateFrom, dateTo]);

  useEffect(() => {
    fetchInvoices();
  }, [fetchInvoices]);

  const handleSearchSubmit = (e) => {
    e.preventDefault();
    setPage(1);
    fetchInvoices();
  };

  const handleResetFilters = () => {
    setQuery('');
    setStatusFilter('all');
    setDateFrom('');
    setDateTo('');
    setPage(1);
  };

  const handleViewDetails = (inv) => {
    setSelectedInvoice(inv);
    setIsDetailOpen(true);
  };

  const handleDownloadPdf = async (inv, e) => {
    e.stopPropagation();
    try {
      setDownloadingId(inv.id);
      await invoiceService.downloadInvoicePdf(inv.id, inv.invoice_number);
    } catch (err) {
      alert(`PDF Download Error: ${err.message}`);
    } finally {
      setDownloadingId(null);
    }
  };

  const handleProcessPayment = async (inv, e) => {
    e.stopPropagation();
    if (processingId) return;

    try {
      setProcessingId(inv.id);
      setPaymentFeedback(null);
      const res = await paymentsService.processPayment({ invoice_id: inv.id });

      if (res.status === 'succeeded') {
        setPaymentFeedback({
          type: 'success',
          message: `Payment successful! Transaction reference: ${res.transaction_reference}. Invoice ${inv.invoice_number} settled.`,
        });
      } else if (res.status === 'failed') {
        setPaymentFeedback({
          type: 'error',
          message: `Payment failed: ${res.failure_reason || 'Gateway simulation declined charge'}. Associated subscription updated to Past Due.`,
        });
      } else {
        setPaymentFeedback({
          type: 'info',
          message: `Payment recorded with status '${res.status}'. Reference: ${res.transaction_reference}.`,
        });
      }

      // Re-fetch fresh invoice data directly from backend
      await fetchInvoices();
    } catch (err) {
      setPaymentFeedback({
        type: 'error',
        message: err.message || 'Payment processing failed.',
      });
    } finally {
      setProcessingId(null);
    }
  };

  return (
    <div className="invoices-manager-container">
      {/* Module Title & Stats Header */}
      <div className="invoices-header">
        <div>
          <div className="status-pill online" style={{ marginBottom: '0.65rem' }}>
            <span className="status-dot"></span>
            Live PostgreSQL Invoicing Ledger
          </div>
          <h2 className="dashboard-title">Invoices Management</h2>
          <p className="dashboard-subtitle">
            Inspect automated recurring tax statements, search by sequence number, and download ReportLab PDF invoices.
          </p>
        </div>

        <div className="invoices-header-actions">
          <button className="btn-secondary" onClick={fetchInvoices} disabled={loading} title="Refresh table data">
            {loading ? 'Refreshing...' : '↻ Refresh Invoices'}
          </button>
        </div>
      </div>

      {/* Payment Processing Feedback Alert */}
      {paymentFeedback && (
        <div
          className={`auth-alert ${paymentFeedback.type === 'success' ? 'success' : 'error'}`}
          style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
            <span>{paymentFeedback.type === 'success' ? '✓' : '⚠️'}</span>
            <span>{paymentFeedback.message}</span>
          </div>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setPaymentFeedback(null)}
            style={{ padding: '0.2rem 0.6rem', fontSize: '0.75rem', marginLeft: '1rem' }}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Filter & Search Toolbar */}
      <div className="invoices-toolbar">
        <form onSubmit={handleSearchSubmit} className="invoices-search-form">
          <div className="search-input-wrapper">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="search-icon">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              className="toolbar-input"
              placeholder="Search invoice number (e.g. INV-2026)..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <button type="submit" className="btn-primary" style={{ padding: '0.55rem 1rem', fontSize: '0.85rem' }}>
            Search
          </button>
        </form>

        <div className="invoices-filter-group">
          {/* Status Filter */}
          <div className="filter-item">
            <label className="filter-label" htmlFor="filter-status">Status</label>
            <select
              id="filter-status"
              className="toolbar-select"
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="all">All Statuses</option>
              <option value="open">Open (Unpaid)</option>
              <option value="paid">Paid</option>
              <option value="void">Void</option>
              <option value="uncollectible">Uncollectible</option>
            </select>
          </div>

          {/* Date Range: From */}
          <div className="filter-item">
            <label className="filter-label" htmlFor="filter-date-from">From</label>
            <input
              id="filter-date-from"
              type="date"
              className="toolbar-input-date"
              value={dateFrom}
              onChange={(e) => {
                setDateFrom(e.target.value);
                setPage(1);
              }}
            />
          </div>

          {/* Date Range: To */}
          <div className="filter-item">
            <label className="filter-label" htmlFor="filter-date-to">To</label>
            <input
              id="filter-date-to"
              type="date"
              className="toolbar-input-date"
              value={dateTo}
              onChange={(e) => {
                setDateTo(e.target.value);
                setPage(1);
              }}
            />
          </div>

          {(query || statusFilter !== 'all' || dateFrom || dateTo) && (
            <button
              type="button"
              className="btn-secondary"
              onClick={handleResetFilters}
              style={{ alignSelf: 'flex-end', padding: '0.55rem 0.85rem', fontSize: '0.85rem' }}
              title="Clear all active filters"
            >
              Reset
            </button>
          )}
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="auth-alert error" style={{ marginBottom: '1.5rem' }}>
          <span>⚠️</span>
          <span>{error}</span>
          <button className="btn-secondary" onClick={fetchInvoices} style={{ marginLeft: 'auto', padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}>
            Retry
          </button>
        </div>
      )}

      {/* Table Data View */}
      <div className="table-card">
        <div className="table-responsive">
          <table className="data-table">
            <thead>
              <tr>
                <th>Invoice Number</th>
                <th>Customer</th>
                <th>Plan Details</th>
                <th>Issue Date</th>
                <th>Due Date</th>
                <th style={{ textAlign: 'right' }}>Total Amount</th>
                <th>Status</th>
                <th style={{ textAlign: 'center' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan="8" style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-secondary)' }}>
                    <div style={{ display: 'inline-block', marginBottom: '0.5rem', animation: 'spin 1s linear infinite' }}>⟳</div>
                    <div>Loading invoices from backend...</div>
                  </td>
                </tr>
              ) : invoices.length === 0 ? (
                <tr>
                  <td colSpan="8" style={{ textAlign: 'center', padding: '3.5rem 1rem' }}>
                    <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>🧾</div>
                    <div style={{ fontWeight: 600, fontSize: '1.05rem', color: 'var(--text-primary)' }}>No invoices found</div>
                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginTop: '0.25rem' }}>
                      {query || statusFilter !== 'all' || dateFrom || dateTo
                        ? 'No invoices match your search and filter parameters.'
                        : 'No invoice statements generated yet. Run automated billing cycles or trigger invoice generation.'}
                    </p>
                    {(query || statusFilter !== 'all' || dateFrom || dateTo) && (
                      <button className="btn-secondary" onClick={handleResetFilters} style={{ marginTop: '1rem' }}>
                        Clear Active Filters
                      </button>
                    )}
                  </td>
                </tr>
              ) : (
                invoices.map((inv) => {
                  const isPaid = (inv.status || '').toLowerCase() === 'paid';
                  const isDownloading = downloadingId === inv.id;

                  return (
                    <tr
                      key={inv.id}
                      className="table-row-hover"
                      onClick={() => handleViewDetails(inv)}
                      style={{ cursor: 'pointer' }}
                    >
                      <td style={{ fontWeight: 600, fontFamily: 'monospace', color: 'var(--primary-500)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                          <span>📄</span>
                          <span>{inv.invoice_number}</span>
                        </div>
                      </td>

                      <td>
                        <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                          {inv.customer_name || `Customer #${inv.customer_id}`}
                        </div>
                        <div style={{ fontSize: '0.785rem', color: 'var(--text-muted)' }}>
                          {inv.customer_email || 'No email registered'}
                        </div>
                      </td>

                      <td>
                        <div style={{ fontSize: '0.875rem', color: 'var(--text-primary)' }}>
                          {inv.plan_name || 'Recurring Plan'}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          {inv.plan_code ? `Code: ${inv.plan_code}` : `Sub #${inv.subscription_id || 'N/A'}`}
                        </div>
                      </td>

                      <td style={{ fontSize: '0.85rem' }}>
                        {inv.issue_date ? new Date(inv.issue_date).toLocaleDateString() : 'N/A'}
                      </td>

                      <td style={{ fontSize: '0.85rem' }}>
                        {inv.due_date ? new Date(inv.due_date).toLocaleDateString() : 'N/A'}
                      </td>

                      <td style={{ textAlign: 'right', fontWeight: 700, fontFamily: 'monospace' }}>
                        {inv.currency} {parseFloat(inv.total_amount).toFixed(2)}
                      </td>

                      <td>
                        <span className={`status-pill ${isPaid ? 'online' : 'offline'}`}>
                          <span className="status-dot"></span>
                          {(inv.status || 'open').toUpperCase()}
                        </span>
                      </td>

                      <td>
                        <div className="table-actions" onClick={(e) => e.stopPropagation()}>
                          {!isPaid && (
                            <button
                              type="button"
                              className="btn-primary table-btn"
                              style={{ background: '#10b981', borderColor: '#059669' }}
                              onClick={(e) => handleProcessPayment(inv, e)}
                              disabled={processingId === inv.id}
                              title="Process payment via Mock Payment Gateway"
                            >
                              {processingId === inv.id ? 'Paying...' : '💳 Pay'}
                            </button>
                          )}

                          <button
                            type="button"
                            className="btn-secondary table-btn"
                            onClick={() => handleViewDetails(inv)}
                            title="Inspect itemized line items"
                          >
                            View
                          </button>

                          <button
                            type="button"
                            className="btn-primary table-btn"
                            onClick={(e) => handleDownloadPdf(inv, e)}
                            disabled={isDownloading}
                            title="Download ReportLab PDF statement"
                          >
                            {isDownloading ? 'PDF...' : 'PDF ↓'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Footer */}
        {total > 0 && (
          <div className="table-pagination">
            <div className="pagination-info">
              Showing <strong>{invoices.length}</strong> of <strong>{total}</strong> invoice{total !== 1 ? 's' : ''}
              {pages > 1 && ` (Page ${page} of ${pages})`}
            </div>

            <div className="pagination-controls">
              <button
                type="button"
                className="btn-secondary pagination-btn"
                disabled={page <= 1 || loading}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                ← Previous
              </button>

              <button
                type="button"
                className="btn-secondary pagination-btn"
                disabled={page >= pages || loading}
                onClick={() => setPage((p) => p + 1)}
              >
                Next →
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Itemized Line Items & PDF Modal */}
      <InvoiceDetailModal
        invoice={selectedInvoice}
        isOpen={isDetailOpen}
        onClose={() => {
          setIsDetailOpen(false);
          setSelectedInvoice(null);
        }}
        onPaymentCompleted={async () => {
          await fetchInvoices();
        }}
      />
    </div>
  );
}
