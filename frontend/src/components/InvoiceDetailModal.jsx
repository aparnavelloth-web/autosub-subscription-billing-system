import React, { useState } from 'react';
import { invoiceService } from '../services/invoices';

export default function InvoiceDetailModal({ invoice, isOpen, onClose }) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');

  if (!isOpen || !invoice) return null;

  const handleDownloadPdf = async () => {
    try {
      setDownloading(true);
      setDownloadError('');
      await invoiceService.downloadInvoicePdf(invoice.id, invoice.invoice_number);
    } catch (err) {
      setDownloadError(err.message || 'Failed to download PDF statement.');
    } finally {
      setDownloading(false);
    }
  };

  const statusStr = (invoice.status || 'open').toUpperCase();
  const isPaid = statusStr === 'PAID';
  const subtotal = parseFloat(invoice.subtotal || 0).toFixed(2);
  const tax = parseFloat(invoice.tax_amount || 0).toFixed(2);
  const discount = parseFloat(invoice.discount_amount || 0).toFixed(2);
  const total = parseFloat(invoice.total_amount || 0).toFixed(2);
  const amountPaid = parseFloat(invoice.amount_paid || 0).toFixed(2);
  const balance = Math.max(0, parseFloat(invoice.total_amount || 0) - parseFloat(invoice.amount_paid || 0)).toFixed(2);

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
                  {invoice.invoice_number}
                </h2>
                <span className={`status-pill ${isPaid ? 'online' : 'offline'}`}>
                  <span className="status-dot"></span>
                  {statusStr}
                </span>
              </div>
              <p className="modal-subtitle">Itemized tax billing statement details</p>
            </div>
          </div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close modal">
            &times;
          </button>
        </div>

        {downloadError && (
          <div className="auth-alert error" style={{ margin: '0.75rem 1.5rem' }}>
            <span>⚠️</span>
            <span>{downloadError}</span>
          </div>
        )}

        {/* Modal Body */}
        <div className="invoice-modal-body">
          {/* Metadata Columns */}
          <div className="invoice-details-grid">
            <div className="invoice-info-card">
              <span className="invoice-info-label">BILLED CUSTOMER</span>
              <div className="invoice-info-val-strong">{invoice.customer_name || 'Customer'}</div>
              <div className="invoice-info-sub">{invoice.customer_email || 'N/A'}</div>
              <div className="invoice-info-sub">Customer ID: #{invoice.customer_id}</div>
            </div>

            <div className="invoice-info-card">
              <span className="invoice-info-label">SUBSCRIPTION PLAN</span>
              <div className="invoice-info-val-strong">{invoice.plan_name || 'Standard Tier'}</div>
              <div className="invoice-info-sub">Plan Code: {invoice.plan_code || 'N/A'}</div>
              <div className="invoice-info-sub">Subscription ID: #{invoice.subscription_id || 'Direct'}</div>
            </div>

            <div className="invoice-info-card">
              <span className="invoice-info-label">DATES & TIMELINE</span>
              <div className="invoice-info-row">
                <span>Issue Date:</span>
                <strong>{invoice.issue_date ? new Date(invoice.issue_date).toLocaleDateString() : 'N/A'}</strong>
              </div>
              <div className="invoice-info-row">
                <span>Due Date:</span>
                <strong>{invoice.due_date ? new Date(invoice.due_date).toLocaleDateString() : 'N/A'}</strong>
              </div>
              <div className="invoice-info-row">
                <span>Settled At:</span>
                <strong>{invoice.paid_at ? new Date(invoice.paid_at).toLocaleString() : 'Unpaid'}</strong>
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
                  {(invoice.items && invoice.items.length > 0) ? (
                    invoice.items.map((item, idx) => (
                      <tr key={item.id || idx}>
                        <td style={{ fontWeight: 500 }}>{item.description}</td>
                        <td>
                          <span className="invoice-type-pill">{item.item_type}</span>
                        </td>
                        <td style={{ textAlign: 'center' }}>{item.quantity}</td>
                        <td style={{ textAlign: 'right', fontFamily: 'monospace' }}>
                          {invoice.currency} {parseFloat(item.unit_price).toFixed(2)}
                        </td>
                        <td style={{ textAlign: 'right', fontWeight: 600, fontFamily: 'monospace' }}>
                          {invoice.currency} {parseFloat(item.amount).toFixed(2)}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan="5" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '1.25rem' }}>
                        Base recurring charge ({invoice.currency} {subtotal})
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
              <p>{invoice.notes || 'Payment terms: Due on specified due date. Automated recurring billing managed via AutoSub engine.'}</p>
            </div>

            <div className="invoice-totals-box">
              <div className="invoice-tot-row">
                <span>Subtotal:</span>
                <span>{invoice.currency} {subtotal}</span>
              </div>
              {parseFloat(discount) > 0 && (
                <div className="invoice-tot-row" style={{ color: '#10b981' }}>
                  <span>Discount / Credits:</span>
                  <span>-{invoice.currency} {discount}</span>
                </div>
              )}
              <div className="invoice-tot-row">
                <span>Tax:</span>
                <span>{invoice.currency} {tax}</span>
              </div>
              <div className="invoice-tot-row grand-total">
                <span>Total Amount:</span>
                <span className="gradient-text">{invoice.currency} {total}</span>
              </div>
              <div className="invoice-tot-row">
                <span>Amount Paid:</span>
                <span>{invoice.currency} {amountPaid}</span>
              </div>
              <div className="invoice-tot-row balance-due">
                <span>Outstanding Balance:</span>
                <span style={{ color: parseFloat(balance) > 0 ? '#ef4444' : '#34d399' }}>
                  {invoice.currency} {balance}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Modal Footer Actions */}
        <div className="modal-footer" style={{ padding: '1rem 1.5rem', display: 'flex', justifyContent: 'space-between', borderTop: '1px solid var(--border-color)' }}>
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

          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
