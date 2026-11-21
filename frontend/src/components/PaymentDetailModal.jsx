import React from 'react';

export default function PaymentDetailModal({ payment, isOpen, onClose }) {
  if (!isOpen || !payment) return null;

  const status = (payment.status || 'pending').toLowerCase();
  const isSucceeded = status === 'succeeded';
  const isFailed = status === 'failed';
  const isRefunded = status === 'refunded';
  const isPending = status === 'pending';
  const isPartiallyRefunded =
    status === 'partially_refunded' ||
    (isSucceeded &&
      parseFloat(payment.refunded_amount || 0) > 0 &&
      parseFloat(payment.refunded_amount || 0) < parseFloat(payment.amount || 0));

  const getStatusPill = () => {
    if (isPartiallyRefunded) {
      return (
        <span className="status-pill refunded">
          <span className="status-dot"></span>
          PARTIALLY REFUNDED
        </span>
      );
    }
    if (isSucceeded) {
      return (
        <span className="status-pill online">
          <span className="status-dot"></span>
          SUCCEEDED
        </span>
      );
    }
    if (isFailed) {
      return (
        <span className="status-pill offline">
          <span className="status-dot"></span>
          FAILED
        </span>
      );
    }
    if (isRefunded) {
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

  const amountFormatted = parseFloat(payment.amount || 0).toFixed(2);
  const refundedFormatted = parseFloat(payment.refunded_amount || 0).toFixed(2);

  // Net Collected calculation:
  // - FAILED payment: Net Collected = 0.00 (transaction failed, no funds collected)
  // - SUCCEEDED payment: Net Collected = successful amount minus refunded amount
  // - PARTIALLY REFUNDED payment: Net Collected = amount actually collected minus refunded amount
  // - FULLY REFUNDED payment: Net Collected = 0.00
  // - PENDING payment: Net Collected = 0.00 (not settled yet)
  const calculateNetCollected = () => {
    if (isFailed || isPending || isRefunded) {
      return (0).toFixed(2);
    }
    if (isSucceeded || status === 'partially_refunded') {
      const net = Math.max(0, parseFloat(payment.amount || 0) - parseFloat(payment.refunded_amount || 0));
      return net.toFixed(2);
    }
    return (0).toFixed(2);
  };

  const netAmount = calculateNetCollected();

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-container invoice-modal-wide" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '780px' }}>
        {/* Header */}
        <div className="modal-header">
          <div className="modal-title-group">
            <div className="brand-icon" style={{ width: 36, height: 36, background: isFailed ? 'rgba(239, 68, 68, 0.15)' : 'rgba(59, 130, 246, 0.15)' }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={isFailed ? '#ef4444' : 'currentColor'} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <rect width="20" height="14" x="2" y="5" rx="2" />
                <line x1="2" x2="22" y1="10" y2="10" />
              </svg>
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem', flexWrap: 'wrap' }}>
                <h2 className="modal-title" style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '1.25rem' }}>
                  {payment.transaction_reference}
                </h2>
                {getStatusPill()}
              </div>
              <p className="modal-subtitle">
                Payment transaction record #{payment.id} · Settled via MockPaymentGateway
              </p>
            </div>
          </div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close modal">
            &times;
          </button>
        </div>

        {/* Modal Body */}
        <div className="invoice-modal-body" style={{ padding: '1.5rem', maxHeight: '72vh', overflowY: 'auto' }}>
          {/* Prominent Failure Callout if payment failed */}
          {isFailed && (
            <div
              className="auth-alert error"
              style={{
                marginBottom: '1.5rem',
                padding: '1rem 1.25rem',
                borderLeft: '4px solid #ef4444',
                background: 'rgba(239, 68, 68, 0.08)',
                display: 'flex',
                alignItems: 'flex-start',
                gap: '0.85rem',
              }}
            >
              <span style={{ fontSize: '1.35rem', lineHeight: 1 }}>❌</span>
              <div>
                <strong style={{ display: 'block', fontSize: '0.95rem', color: '#f87171', marginBottom: '0.25rem' }}>
                  Payment Transaction Failed
                </strong>
                <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                  {payment.failure_reason || 'Transaction declined by payment simulation engine.'}
                </p>
                <div style={{ marginTop: '0.45rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Associated subscription was automatically moved to <strong>Past Due</strong> status.
                </div>
              </div>
            </div>
          )}

          {/* Core Metadata Grid */}
          <div className="invoice-details-grid" style={{ marginBottom: '1.5rem' }}>
            {/* Box 1: INVOICE & CUSTOMER */}
            <div className="invoice-info-card">
              <span className="invoice-info-label">INVOICE & CUSTOMER</span>
              <div className="invoice-info-val-strong" style={{ color: 'var(--primary-500)', fontFamily: 'monospace' }}>
                {payment.invoice_number || `Invoice #${payment.invoice_id}`}
              </div>
              <div className="invoice-info-sub" style={{ marginTop: '0.35rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                {payment.customer_name || `Customer #${payment.customer_id}`}
              </div>
              <div className="invoice-info-sub">{payment.customer_email || 'No email registered'}</div>
              <div className="invoice-info-sub" style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                Customer ID: #{payment.customer_id} · Invoice ID: #{payment.invoice_id}
              </div>
            </div>

            {/* Box 2: FINANCIAL SETTLEMENT */}
            <div className="invoice-info-card">
              <span className="invoice-info-label">FINANCIAL SETTLEMENT</span>
              <div className="invoice-info-val-strong" style={{ fontSize: '1.35rem' }}>
                <span className="gradient-text">
                  {payment.currency} {amountFormatted}
                </span>
              </div>
              <div className="invoice-info-row" style={{ marginTop: '0.5rem' }}>
                <span>Refunded:</span>
                <strong style={{ color: parseFloat(refundedFormatted) > 0 ? '#c084fc' : 'var(--text-secondary)' }}>
                  {payment.currency} {refundedFormatted}
                </strong>
              </div>
              <div className="invoice-info-row">
                <span>Net Collected:</span>
                <strong>{payment.currency} {netAmount}</strong>
              </div>
            </div>

            {/* Box 3: TIMELINE & GATEWAY */}
            <div className="invoice-info-card">
              <span className="invoice-info-label">TIMELINE & METHOD</span>
              <div className="invoice-info-row">
                <span>Method:</span>
                <strong style={{ textTransform: 'capitalize' }}>
                  {payment.payment_method ? payment.payment_method.replace('_', ' ') : 'Simulated'}
                </strong>
              </div>
              <div className="invoice-info-row">
                <span>Settled At:</span>
                <strong>
                  {payment.payment_date ? new Date(payment.payment_date).toLocaleString() : 'Not settled'}
                </strong>
              </div>
              <div className="invoice-info-row">
                <span>Created At:</span>
                <strong>
                  {payment.created_at ? new Date(payment.created_at).toLocaleString() : 'N/A'}
                </strong>
              </div>
            </div>
          </div>

          {/* Idempotency & Diagnostics Section */}
          <div
            className="invoice-info-card"
            style={{
              marginBottom: '1.5rem',
              background: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid rgba(255, 255, 255, 0.06)',
            }}
          >
            <span className="invoice-info-label">GATEWAY METADATA & IDEMPOTENCY</span>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.85rem', marginTop: '0.5rem' }}>
              <div>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block' }}>IDEMPOTENCY KEY</span>
                <span style={{ fontFamily: 'monospace', fontSize: '0.85rem', color: payment.idempotency_key ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                  {payment.idempotency_key || 'None provided'}
                </span>
              </div>
              <div>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block' }}>GATEWAY SIMULATION</span>
                <span style={{ fontSize: '0.85rem', color: isSucceeded ? '#34d399' : isFailed ? '#f87171' : 'var(--text-secondary)' }}>
                  {isSucceeded ? 'Auto-simulated (Success 80% / Failure 20%)' : isFailed ? 'Failed simulation outcome' : 'Pending outcome'}
                </span>
              </div>
              <div>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block' }}>WEBHOOK AUDIT</span>
                <span style={{ fontSize: '0.85rem', color: '#38bdf8' }}>
                  HMAC-SHA256 Signed & Auto-dispatched
                </span>
              </div>
            </div>

            {/* Gateway response details if available */}
            {payment.gateway_response && (
              <div style={{ marginTop: '0.85rem', paddingTop: '0.85rem', borderTop: '1px solid rgba(255, 255, 255, 0.05)' }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.35rem' }}>
                  GATEWAY DIAGNOSTICS PAYLOAD
                </span>
                <pre
                  style={{
                    background: 'rgba(0, 0, 0, 0.35)',
                    padding: '0.75rem',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '0.75rem',
                    fontFamily: 'monospace',
                    color: 'var(--text-secondary)',
                    overflowX: 'auto',
                    margin: 0,
                  }}
                >
                  {JSON.stringify(payment.gateway_response, null, 2)}
                </pre>
              </div>
            )}
          </div>

          {/* Refund History Table (if any) */}
          {payment.refunds && payment.refunds.length > 0 && (
            <div className="invoice-items-section" style={{ marginTop: '1rem' }}>
              <div className="invoice-section-heading" style={{ color: '#c084fc' }}>
                Refund History ({payment.refunds.length})
              </div>
              <div className="invoice-table-wrapper">
                <table className="invoice-table">
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left' }}>Refund Reference</th>
                      <th style={{ textAlign: 'right' }}>Amount</th>
                      <th style={{ textAlign: 'center' }}>Status</th>
                      <th style={{ textAlign: 'left' }}>Reason</th>
                      <th style={{ textAlign: 'right' }}>Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {payment.refunds.map((ref) => (
                      <tr key={ref.id}>
                        <td style={{ fontFamily: 'monospace', color: 'var(--primary-500)', fontWeight: 600 }}>
                          {ref.refund_reference}
                        </td>
                        <td style={{ textAlign: 'right', fontFamily: 'monospace', fontWeight: 600 }}>
                          {ref.currency} {parseFloat(ref.amount).toFixed(2)}
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          <span className={`status-pill ${ref.status === 'succeeded' ? 'refunded' : 'offline'}`} style={{ fontSize: '0.75rem' }}>
                            <span className="status-dot"></span>
                            {(ref.status || 'succeeded').toUpperCase()}
                          </span>
                        </td>
                        <td style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                          {ref.reason || 'General refund'}
                        </td>
                        <td style={{ textAlign: 'right', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                          {ref.refunded_at ? new Date(ref.refunded_at).toLocaleDateString() : new Date(ref.created_at).toLocaleDateString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="modal-footer" style={{ padding: '1rem 1.5rem', display: 'flex', justifyContent: 'flex-end', borderTop: '1px solid var(--border-color)' }}>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
