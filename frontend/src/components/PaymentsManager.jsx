import React, { useState, useEffect, useCallback } from 'react';
import { paymentsService } from '../services/payments';
import PaymentDetailModal from './PaymentDetailModal';

export default function PaymentsManager() {
  const [payments, setPayments] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const [pages, setPages] = useState(0);

  // Filters state
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');

  // UI state
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedPayment, setSelectedPayment] = useState(null);
  const [isDetailOpen, setIsDetailOpen] = useState(false);

  const fetchPayments = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await paymentsService.listPayments({
        page,
        size,
        status: statusFilter,
      });
      setPayments(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 0);
    } catch (err) {
      setError(err.message || 'Failed to retrieve payment records.');
      setPayments([]);
    } finally {
      setLoading(false);
    }
  }, [page, size, statusFilter]);

  useEffect(() => {
    fetchPayments();
  }, [fetchPayments]);

  const handleSearchSubmit = (e) => {
    e.preventDefault();
    // Filter locally or re-fetch
    setPage(1);
    fetchPayments();
  };

  const handleResetFilters = () => {
    setSearchQuery('');
    setStatusFilter('all');
    setPage(1);
  };

  const handleOpenDetails = (payment) => {
    setSelectedPayment(payment);
    setIsDetailOpen(true);
  };

  // Filter payments by client search query if entered (matching transaction_reference, invoice_number, or customer)
  const displayedPayments = payments.filter((p) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase().trim();
    const refMatch = (p.transaction_reference || '').toLowerCase().includes(q);
    const invMatch = (p.invoice_number || '').toLowerCase().includes(q) || String(p.invoice_id).includes(q);
    const custMatch = (p.customer_name || '').toLowerCase().includes(q) || (p.customer_email || '').toLowerCase().includes(q);
    return refMatch || invMatch || custMatch;
  });

  // Calculate summary metrics
  const succeededCount = payments.filter((p) => (p.status || '').toLowerCase() === 'succeeded').length;
  const failedCount = payments.filter((p) => (p.status || '').toLowerCase() === 'failed').length;
  const totalSettledAmount = payments
    .filter((p) => (p.status || '').toLowerCase() === 'succeeded')
    .reduce((sum, p) => sum + parseFloat(p.amount || 0), 0);

  const renderStatusPill = (statusStr) => {
    const s = (statusStr || 'pending').toLowerCase();
    if (s === 'succeeded') {
      return (
        <span className="status-pill online">
          <span className="status-dot"></span>
          SUCCEEDED
        </span>
      );
    }
    if (s === 'failed') {
      return (
        <span className="status-pill offline">
          <span className="status-dot"></span>
          FAILED
        </span>
      );
    }
    if (s === 'refunded') {
      return (
        <span className="status-pill refunded">
          <span className="status-dot"></span>
          REFUNDED
        </span>
      );
    }
    return (
      <span className="status-pill pending">
        <span className="status-dot"></span>
        PENDING
      </span>
    );
  };

  return (
    <div className="invoices-manager-container">
      {/* Module Title & Stats Header */}
      <div className="invoices-header">
        <div>
          <div className="status-pill online" style={{ marginBottom: '0.65rem' }}>
            <span className="status-dot"></span>
            Mock Payment Gateway & Webhook Ledger
          </div>
          <h2 className="dashboard-title">Payment Transactions Dashboard</h2>
          <p className="dashboard-subtitle">
            Inspect simulated charges, 80/20 outcome distributions, HMAC-SHA256 webhook deliveries, and refund audit trails.
          </p>
        </div>

        <div className="invoices-header-actions">
          <button className="btn-secondary" onClick={fetchPayments} disabled={loading} title="Refresh table data">
            {loading ? 'Refreshing...' : '↻ Refresh Payments'}
          </button>
        </div>
      </div>

      {/* KPI Metric Summary Cards */}
      <div className="dashboard-grid" style={{ marginBottom: '1.5rem', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-tag">TOTAL TRANSACTIONS</span>
          </div>
          <h3 className="dash-card-value">{total}</h3>
          <p className="dash-card-desc">Recorded payment transactions</p>
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-tag" style={{ color: '#34d399' }}>SUCCEEDED CHARGES</span>
          </div>
          <h3 className="dash-card-value" style={{ color: '#34d399' }}>{succeededCount}</h3>
          <p className="dash-card-desc">Settled & invoices cleared</p>
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-tag" style={{ color: '#f87171' }}>FAILED ATTEMPTS</span>
          </div>
          <h3 className="dash-card-value" style={{ color: '#f87171' }}>{failedCount}</h3>
          <p className="dash-card-desc">Subscriptions marked Past Due</p>
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-tag">SETTLED VOLUME</span>
          </div>
          <h3 className="dash-card-value">
            <span className="gradient-text">₹ {totalSettledAmount.toFixed(2)}</span>
          </h3>
          <p className="dash-card-desc">Current page successful total</p>
        </div>
      </div>

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
              placeholder="Search reference (TXN-...) or invoice..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <button type="submit" className="btn-primary" style={{ padding: '0.55rem 1rem', fontSize: '0.85rem' }}>
            Filter
          </button>
        </form>

        <div className="invoices-filter-group">
          {/* Status Filter */}
          <div className="filter-item">
            <label className="filter-label" htmlFor="filter-payment-status">Status</label>
            <select
              id="filter-payment-status"
              className="toolbar-select"
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="all">All Statuses</option>
              <option value="succeeded">Succeeded</option>
              <option value="failed">Failed</option>
              <option value="pending">Pending</option>
              <option value="refunded">Refunded</option>
            </select>
          </div>

          {(searchQuery || statusFilter !== 'all') && (
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
          <button className="btn-secondary" onClick={fetchPayments} style={{ marginLeft: 'auto', padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}>
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
                <th>Transaction Reference</th>
                <th>Target Invoice</th>
                <th>Customer</th>
                <th style={{ textAlign: 'right' }}>Amount</th>
                <th>Method</th>
                <th>Status</th>
                <th>Payment Date</th>
                <th style={{ textAlign: 'center' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan="8" style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-secondary)' }}>
                    <div style={{ display: 'inline-block', marginBottom: '0.5rem', animation: 'spin 1s linear infinite' }}>⟳</div>
                    <div>Loading payments from gateway ledger...</div>
                  </td>
                </tr>
              ) : displayedPayments.length === 0 ? (
                <tr>
                  <td colSpan="8" style={{ textAlign: 'center', padding: '3.5rem 1rem' }}>
                    <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>💳</div>
                    <div style={{ fontWeight: 600, fontSize: '1.05rem', color: 'var(--text-primary)' }}>No payment transactions found</div>
                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginTop: '0.25rem' }}>
                      {searchQuery || statusFilter !== 'all'
                        ? 'No payment transactions match your search and filter parameters.'
                        : 'No payments processed yet. Use the "Pay" button on open invoices to process simulated charges.'}
                    </p>
                    {(searchQuery || statusFilter !== 'all') && (
                      <button className="btn-secondary" onClick={handleResetFilters} style={{ marginTop: '1rem' }}>
                        Clear Active Filters
                      </button>
                    )}
                  </td>
                </tr>
              ) : (
                displayedPayments.map((p) => {
                  const paymentDate = p.payment_date || p.created_at;
                  return (
                    <tr
                      key={p.id}
                      className="table-row-hover"
                      onClick={() => handleOpenDetails(p)}
                      style={{ cursor: 'pointer' }}
                    >
                      <td style={{ fontWeight: 600, fontFamily: 'monospace', color: 'var(--primary-500)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                          <span>💳</span>
                          <span>{p.transaction_reference}</span>
                        </div>
                      </td>

                      <td>
                        <div style={{ fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                          {p.invoice_number || `Invoice #${p.invoice_id}`}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          ID: #{p.invoice_id}
                        </div>
                      </td>

                      <td>
                        <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                          {p.customer_name || `Customer #${p.customer_id}`}
                        </div>
                        <div style={{ fontSize: '0.785rem', color: 'var(--text-muted)' }}>
                          {p.customer_email || 'No email registered'}
                        </div>
                      </td>

                      <td style={{ textAlign: 'right', fontWeight: 700, fontFamily: 'monospace' }}>
                        {p.currency} {parseFloat(p.amount).toFixed(2)}
                      </td>

                      <td style={{ fontSize: '0.85rem', textTransform: 'capitalize' }}>
                        {p.payment_method ? p.payment_method.replace('_', ' ') : 'Simulated'}
                      </td>

                      <td>
                        {renderStatusPill(p.status)}
                      </td>

                      <td style={{ fontSize: '0.85rem' }}>
                        {paymentDate ? new Date(paymentDate).toLocaleString() : 'N/A'}
                      </td>

                      <td>
                        <div className="table-actions" onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            className="btn-secondary table-btn"
                            onClick={() => handleOpenDetails(p)}
                            title="Inspect payment details and refund history"
                          >
                            Details
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
              Showing <strong>{displayedPayments.length}</strong> of <strong>{total}</strong> payment{total !== 1 ? 's' : ''}
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

      {/* Payment Details Modal */}
      <PaymentDetailModal
        payment={selectedPayment}
        isOpen={isDetailOpen}
        onClose={() => {
          setIsDetailOpen(false);
          setSelectedPayment(null);
        }}
      />
    </div>
  );
}
