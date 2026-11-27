import React, { useState, useEffect } from 'react';
import { invoiceService } from '../services/invoices';
import { paymentsService } from '../services/payments';

export default function InvoiceDetailModal({ invoice, isOpen, onClose, onPaymentCompleted }) {
  const [currentInvoice, setCurrentInvoice] = useState(invoice);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const [processingPayment, setProcessingPayment] = useState(false);
  const [paymentFeedback, setPaymentFeedback] = useState(null);

  useEffect(() => {
    setCurrentInvoice(invoice);
    setPaymentFeedback(null);
    setDownloadError('');
  }, [invoice]);

  if (!isOpen || !currentInvoice) return null;

  const handleDownloadPdf = async () => {
    try {
      setDownloading(true);
      setDownloadError('');
      await invoiceService.downloadInvoicePdf(currentInvoice.id, currentInvoice.invoice_number);
    } catch (err) {
      setDownloadError(err.message || 'Failed to download PDF statement.');
    } finally {
      setDownloading(false);
    }
  };

  const handleProcessPayment = async () => {
    try {
      setProcessingPayment(true);
      setPaymentFeedback(null);
      const res = await paymentsService.processPayment({ invoice_id: currentInvoice.id });

      if (res.status === 'succeeded') {
        setPaymentFeedback({
          type: 'success',
          message: `Payment successful! Transaction reference: ${res.transaction_reference}. Invoice settled.`,
        });
      } else if (res.status === 'failed') {
        setPaymentFeedback({
          type: 'error',
          message: `Payment failed: ${res.failure_reason || 'Card declined by simulation engine'}. Associated subscription moved to Past Due.`,
        });
      } else {
        setPaymentFeedback({
          type: 'info',
          message: `Payment status: ${res.status}. Reference: ${res.transaction_reference}.`,
        });
      }

      // Re-fetch fresh invoice record from backend
      const fresh = await invoiceService.getInvoice(currentInvoice.id);
      setCurrentInvoice(fresh);

      if (onPaymentCompleted) {
        onPaymentCompleted(fresh);
      }
    } catch (err) {
      setPaymentFeedback({
        type: 'error',
        message: err.message || 'Payment processing failed.',
      });
    } finally {
      setProcessingPayment(false);
    }
  };

  const statusStr = (currentInvoice.status || 'open').toUpperCase();
  const isPaid = statusStr === 'PAID';
  const subtotal = parseFloat(currentInvoice.subtotal || 0).toFixed(2);
  const tax = parseFloat(currentInvoice.tax_amount || 0).toFixed(2);
  const discount = parseFloat(currentInvoice.discount_amount || 0).toFixed(2);
  const total = parseFloat(currentInvoice.total_amount || 0).toFixed(2);
  const amountPaid = parseFloat(currentInvoice.amount_paid || 0).toFixed(2);
  const balance = Math.max(0, parseFloat(currentInvoice.total_amount || 0) - parseFloat(currentInvoice.amount_paid || 0)).toFixed(2);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-container invoice-modal-wide" onClick={(e) => e.stopPropagation()}>
        {/* Modal Header */}
        <div className="modal-header">
          <div className="modal-title-group">
            <div className="brand-icon" style={{ width: 34, height: 34 }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" x2="8" y1="13" y2="13" />
                <line x1="16" x2="8" y1="17" y2="17" />
              </svg>
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                <h2 className="modal-title" style={{ fontFamily: 'var(--font-mono, monospace)' }}>
                  {currentInvoice.invoice_number}
                </h2>
                <span className={`status-pill ${isPaid ? 'online' : 'offline'}`}>
                  <span className="status-dot"></span>
                  {statusStr}
                </span>
              </div>
              <p className="modal-subtitle">Itemized tax billing statement details</p>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <button
              type="button"
              className="btn-primary"
              style={{ padding: '0.4rem 0.85rem', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}
              onClick={handleDownloadPdf}
              disabled={downloading}
              title="Download official PDF invoice"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" x2="12" y1="15" y2="3" />
              </svg>
              {downloading ? 'Downloading...' : 'Download PDF ↓'}
            </button>
            <button className="modal-close-btn" onClick={onClose} aria-label="Close modal">
              &times;
            </button>
          </div>
        </div>

        {downloadError && (
          <div className="auth-alert error" style={{ margin: '0.75rem 1.5rem' }}>
            <span>⚠️</span>
            <span>{downloadError}</span>
          </div>
        )}

        {paymentFeedback && (
          <div
            className={`auth-alert ${paymentFeedback.type === 'success' ? 'success' : 'error'}`}
            style={{ margin: '0.75rem 1.5rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
              <span>{paymentFeedback.type === 'success' ? '✓' : '⚠️'}</span>
              <span>{paymentFeedback.message}</span>
            </div>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setPaymentFeedback(null)}
              style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Modal Body */}
        <div className="invoice-modal-body">
          {/* Metadata Columns */}
          <div className="invoice-details-grid">
            <div className="invoice-info-card">
              <span className="invoice-info-label">BILLED CUSTOMER</span>
              <div className="invoice-info-val-strong">{currentInvoice.customer_name || 'Customer'}</div>
              <div className="invoice-info-sub">{currentInvoice.customer_email || 'N/A'}</div>
              <div className="invoice-info-sub">Customer ID: #{currentInvoice.customer_id}</div>
            </div>

            <div className="invoice-info-card">
              <span className="invoice-info-label">SUBSCRIPTION PLAN</span>
              <div className="invoice-info-val-strong">{currentInvoice.plan_name || 'Standard Tier'}</div>
              <div className="invoice-info-sub">Plan Code: {currentInvoice.plan_code || 'N/A'}</div>
              <div className="invoice-info-sub">Subscription ID: #{currentInvoice.subscription_id || 'Direct'}</div>
            </div>

            <div className="invoice-info-card">
              <span className="invoice-info-label">DATES & TIMELINE</span>
              <div className="invoice-info-row">
                <span>Issue Date:</span>
                <strong>{currentInvoice.issue_date ? new Date(currentInvoice.issue_date).toLocaleDateString() : 'N/A'}</strong>
              </div>
              <div className="invoice-info-row">
                <span>Due Date:</span>
                <strong>{currentInvoice.due_date ? new Date(currentInvoice.due_date).toLocaleDateString() : 'N/A'}</strong>
              </div>
              <div className="invoice-info-row">
                <span>Settled At:</span>
                <strong>{currentInvoice.paid_at ? new Date(currentInvoice.paid_at).toLocaleString() : 'Unpaid'}</strong>
              </div>
            </div>
          </div>

          {/* Itemized Line Items Table */}
          <div className="invoice-items-section">
            <div className="invoice-section-heading">Itemized Charges Breakdown</div>
            <div className="invoice-table-wrapper">
              <table className="invoice-table">
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left' }}>Item & Description</th>
                    <th style={{ textAlign: 'left' }}>Type</th>
                    <th style={{ textAlign: 'center' }}>Qty</th>
                    <th style={{ textAlign: 'right' }}>Unit Price</th>
                    <th style={{ textAlign: 'right' }}>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {(currentInvoice.items && currentInvoice.items.length > 0) ? (
                    currentInvoice.items.map((item, idx) => (
                      <tr key={item.id || idx}>
                        <td style={{ fontWeight: 500 }}>{item.description}</td>
                        <td>
                          <span className="invoice-type-pill">{item.item_type}</span>
                        </td>
                        <td style={{ textAlign: 'center' }}>{item.quantity}</td>
                        <td style={{ textAlign: 'right', fontFamily: 'monospace' }}>
                          {currentInvoice.currency} {parseFloat(item.unit_price).toFixed(2)}
                        </td>
                        <td style={{ textAlign: 'right', fontWeight: 600, fontFamily: 'monospace' }}>
                          {currentInvoice.currency} {parseFloat(item.amount).toFixed(2)}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan="5" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '1.25rem' }}>
                        Base recurring charge ({currentInvoice.currency} {subtotal})
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Totals & Financial Accounting */}
          <div className="invoice-financial-summary">
            <div className="invoice-notes-box">
              <span className="invoice-info-label">INVOICE NOTES & TERMS</span>
              <p>{currentInvoice.notes || 'Payment terms: Due on specified due date. Automated recurring billing managed via AutoSub engine.'}</p>
            </div>

            <div className="invoice-totals-box">
              <div className="invoice-tot-row">
                <span>Subtotal:</span>
                <span>{currentInvoice.currency} {subtotal}</span>
              </div>
              {parseFloat(discount) > 0 && (
                <div className="invoice-tot-row" style={{ color: '#10b981' }}>
                  <span>Discount / Credits:</span>
                  <span>-{currentInvoice.currency} {discount}</span>
                </div>
              )}
              <div className="invoice-tot-row">
                <span>GST (18%):</span>
                <span>{currentInvoice.currency} {tax}</span>
              </div>
              <div className="invoice-tot-row grand-total">
                <span>Total Amount:</span>
                <span className="gradient-text">{currentInvoice.currency} {total}</span>
              </div>
              <div className="invoice-tot-row">
                <span>Amount Paid:</span>
                <span>{currentInvoice.currency} {amountPaid}</span>
              </div>
              <div className="invoice-tot-row balance-due">
                <span>Outstanding Balance:</span>
                <span style={{ color: parseFloat(balance) > 0 ? '#ef4444' : '#34d399' }}>
                  {currentInvoice.currency} {balance}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Modal Footer Actions */}
        <div className="modal-footer" style={{ padding: '1rem 1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--border-color)' }}>
          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
            {!isPaid && (
              <button
                type="button"
                className="btn-primary"
                style={{ background: '#10b981', borderColor: '#059669' }}
                onClick={handleProcessPayment}
                disabled={processingPayment}
              >
                {processingPayment ? 'Processing Payment...' : '💳 Process Payment'}
              </button>
            )}

            <button
              type="button"
              className="btn-primary"
              onClick={handleDownloadPdf}
              disabled={downloading}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: '0.45rem' }}>
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" x2="12" y1="15" y2="3" />
              </svg>
              {downloading ? 'Generating PDF...' : 'Download PDF Statement'}
            </button>
          </div>

          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
