import React, { useState, useEffect } from 'react';
import { customerService } from '../services/customers';
import { invoiceService } from '../services/invoices';

export default function CustomerDetailModal({ customer, onClose, onEditCustomer }) {
  const [activeTab, setActiveTab] = useState('profile'); // 'profile' | 'subscriptions' | 'invoices'
  const [subscriptions, setSubscriptions] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [loadingData, setLoadingData] = useState(false);
  const [errorData, setErrorData] = useState('');

  useEffect(() => {
    if (!customer?.id) return;

    const loadRelatedData = async () => {
      try {
        setLoadingData(true);
        setErrorData('');
        const [subRes, invRes] = await Promise.all([
          customerService.getCustomerSubscriptions(customer.id, { size: 50 }).catch(() => ({ items: [] })),
          customerService.getCustomerInvoices(customer.id, { size: 50 }).catch(() => ({ items: [] })),
        ]);
        setSubscriptions(subRes.items || []);
        setInvoices(invRes.items || []);
      } catch (err) {
        setErrorData(err.message || 'Failed to load customer subscriptions or invoices.');
      } finally {
        setLoadingData(false);
      }
    };

    loadRelatedData();
  }, [customer?.id]);

  if (!customer) return null;

  const isDeleted = Boolean(customer.deleted_at);

  const getStatusBadge = (status) => {
    switch (status) {
      case 'active':
        return <span className="status-badge status-active">Active</span>;
      case 'trial':
        return <span className="status-badge status-trial">Trial</span>;
      case 'past_due':
        return <span className="status-badge status-past_due">Past Due</span>;
      case 'cancelled':
        return <span className="status-badge status-cancelled">Cancelled</span>;
      case 'paid':
        return <span className="status-badge status-active">Paid</span>;
      case 'open':
        return <span className="status-badge status-past_due">Open / Unpaid</span>;
      default:
        return <span className="status-badge">{status}</span>;
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-container"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '720px' }}
      >
        {/* Header */}
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.85rem' }}>
            <div className="customer-avatar-large">
              {customer.full_name?.charAt(0).toUpperCase() || 'C'}
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.2rem' }}>
                <span className="mono-code" style={{ fontSize: '0.78rem' }}>{customer.customer_code}</span>
                {isDeleted ? (
                  <span className="status-badge status-cancelled">Soft-Deleted</span>
                ) : customer.is_active ? (
                  <span className="status-badge status-active">Active</span>
                ) : (
                  <span className="status-badge status-past_due">Inactive</span>
                )}
              </div>
              <h2 className="modal-title" style={{ fontSize: '1.25rem', marginBottom: '0.15rem' }}>
                {customer.full_name}
              </h2>
              <p className="modal-subtitle">
                {customer.company_name ? `${customer.company_name} · ` : ''}{customer.email}
              </p>
            </div>
          </div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close modal">
            &times;
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="customer-modal-tabs">
          <button
            type="button"
            className={`customer-modal-tab-btn ${activeTab === 'profile' ? 'active' : ''}`}
            onClick={() => setActiveTab('profile')}
          >
            Profile Information
          </button>
          <button
            type="button"
            className={`customer-modal-tab-btn ${activeTab === 'subscriptions' ? 'active' : ''}`}
            onClick={() => setActiveTab('subscriptions')}
          >
            Subscriptions ({subscriptions.length})
          </button>
          <button
            type="button"
            className={`customer-modal-tab-btn ${activeTab === 'invoices' ? 'active' : ''}`}
            onClick={() => setActiveTab('invoices')}
          >
            Invoice History ({invoices.length})
          </button>

          {!isDeleted && (
            <button
              type="button"
              className="btn-secondary"
              style={{ marginLeft: 'auto', padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}
              onClick={() => {
                onClose();
                onEditCustomer(customer);
              }}
            >
              Edit Profile ✎
            </button>
          )}
        </div>

        {errorData && (
          <div className="auth-alert error" style={{ margin: '1rem 0' }}>
            <span>⚠️</span>
            <span>{errorData}</span>
          </div>
        )}

        {/* Content Body */}
        <div className="customer-modal-content">
          {/* TAB 1: Profile Information */}
          {activeTab === 'profile' && (
            <div className="customer-profile-grid">
              <div className="profile-detail-box">
                <span className="detail-box-label">CONTACT INFORMATION</span>
                <div className="detail-row">
                  <span className="detail-key">Full Name:</span>
                  <span className="detail-val">{customer.full_name}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Email:</span>
                  <span className="detail-val">{customer.email}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Phone:</span>
                  <span className="detail-val">{customer.phone || '—'}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Company:</span>
                  <span className="detail-val">{customer.company_name || '—'}</span>
                </div>
              </div>

              <div className="profile-detail-box">
                <span className="detail-box-label">BILLING & TAX CONFIGURATION</span>
                <div className="detail-row">
                  <span className="detail-key">Currency:</span>
                  <span className="detail-val">{customer.currency}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Tax / GST ID:</span>
                  <span className="detail-val mono-code" style={{ fontSize: '0.82rem' }}>
                    {customer.tax_id || '—'}
                  </span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Account Status:</span>
                  <span className="detail-val">
                    {customer.is_active ? 'Active & Billable' : 'Inactive'}
                  </span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Registered:</span>
                  <span className="detail-val">
                    {customer.created_at ? new Date(customer.created_at).toLocaleDateString() : '—'}
                  </span>
                </div>
              </div>

              <div className="profile-detail-box full-width">
                <span className="detail-box-label">REGISTERED BILLING ADDRESS</span>
                <p style={{ margin: '0.35rem 0 0', color: 'var(--text-primary)', fontSize: '0.875rem', lineHeight: '1.5' }}>
                  {customer.billing_address || 'No billing address provided for this customer.'}
                </p>
              </div>
            </div>
          )}

          {/* TAB 2: Subscriptions */}
          {activeTab === 'subscriptions' && (
            <div>
              {loadingData ? (
                <div style={{ padding: '2.5rem', textAlign: 'center' }}>
                  <div className="spinner" style={{ margin: '0 auto 0.75rem' }}></div>
                  <p style={{ color: 'var(--text-secondary)' }}>Loading customer subscriptions...</p>
                </div>
              ) : subscriptions.length === 0 ? (
                <div className="empty-state" style={{ padding: '2.5rem 1rem' }}>
                  <div className="empty-icon" style={{ fontSize: '2rem' }}>🔁</div>
                  <h4 style={{ margin: '0.5rem 0' }}>No Active Subscriptions</h4>
                  <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                    This customer currently has no subscriptions enrolled.
                  </p>
                </div>
              ) : (
                <div className="table-responsive">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Code</th>
                        <th>Plan</th>
                        <th>Status</th>
                        <th>Interval</th>
                        <th>Price</th>
                        <th>Billing Window</th>
                      </tr>
                    </thead>
                    <tbody>
                      {subscriptions.map((sub) => (
                        <tr key={sub.id}>
                          <td>
                            <span className="mono-code" style={{ fontSize: '0.75rem' }}>{sub.subscription_code}</span>
                          </td>
                          <td>
                            <span style={{ fontWeight: 600 }}>Plan #{sub.plan_id}</span>
                          </td>
                          <td>{getStatusBadge(sub.status)}</td>
                          <td>
                            <span className="plan-badge-tag">{sub.billing_interval?.toUpperCase()}</span>
                          </td>
                          <td>
                            <span className="amount-col">{sub.currency} {sub.unit_price}</span>
                          </td>
                          <td>
                            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                              {sub.current_period_start?.slice(0, 10)} to {sub.current_period_end?.slice(0, 10)}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* TAB 3: Invoice History */}
          {activeTab === 'invoices' && (
            <div>
              {loadingData ? (
                <div style={{ padding: '2.5rem', textAlign: 'center' }}>
                  <div className="spinner" style={{ margin: '0 auto 0.75rem' }}></div>
                  <p style={{ color: 'var(--text-secondary)' }}>Loading customer invoices...</p>
                </div>
              ) : invoices.length === 0 ? (
                <div className="empty-state" style={{ padding: '2.5rem 1rem' }}>
                  <div className="empty-icon" style={{ fontSize: '2rem' }}>🧾</div>
                  <h4 style={{ margin: '0.5rem 0' }}>No Invoices Generated</h4>
                  <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                    No recurring cycle invoices have been generated for this customer yet.
                  </p>
                </div>
              ) : (
                <div className="table-responsive">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Invoice #</th>
                        <th>Issue Date</th>
                        <th>Due Date</th>
                        <th>Total</th>
                        <th>Status</th>
                        <th style={{ textAlign: 'right' }}>Receipt</th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoices.map((inv) => (
                        <tr key={inv.id}>
                          <td>
                            <span className="mono-code" style={{ fontSize: '0.78rem', fontWeight: 600 }}>
                              {inv.invoice_number}
                            </span>
                          </td>
                          <td>
                            <span style={{ fontSize: '0.82rem' }}>{inv.issue_date?.slice(0, 10)}</span>
                          </td>
                          <td>
                            <span style={{ fontSize: '0.82rem' }}>{inv.due_date?.slice(0, 10)}</span>
                          </td>
                          <td>
                            <span className="amount-col" style={{ fontWeight: 700 }}>
                              {inv.currency} {inv.total_amount}
                            </span>
                          </td>
                          <td>{getStatusBadge(inv.status)}</td>
                          <td style={{ textAlign: 'right' }}>
                            <button
                              type="button"
                              className="btn-secondary table-btn"
                              onClick={() => invoiceService.downloadInvoicePdf(inv.id, inv.invoice_number)}
                              title="Download PDF"
                            >
                              PDF ↓
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="modal-actions" style={{ marginTop: '1.5rem' }}>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
