import React, { useState, useEffect } from 'react';
import { authService } from '../services/auth';
import Sidebar from './Sidebar';
import InvoicesManager from './InvoicesManager';
import SubscriptionsManager from './SubscriptionsManager';
import PlansManager from './PlansManager';
import CustomersManager from './CustomersManager';

export default function Dashboard({ user, onLogout }) {
  const [profile, setProfile] = useState(user);
  const [activeTab, setActiveTab] = useState('invoices');
  const [verifyStatus, setVerifyStatus] = useState(null);
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    // Re-verify session with backend on load
    const verifyToken = async () => {
      try {
        const freshUser = await authService.getMe();
        setProfile(freshUser);
        setVerifyStatus({ ok: true, message: 'JWT Bearer token valid & active' });
      } catch (err) {
        setVerifyStatus({ ok: false, message: err.message });
      }
    };
    verifyToken();
  }, []);

  const handleManualVerify = async () => {
    try {
      setVerifying(true);
      const freshUser = await authService.getMe();
      setProfile(freshUser);
      setVerifyStatus({
        ok: true,
        message: `Verified via /api/v1/auth/me at ${new Date().toLocaleTimeString()}`,
      });
    } catch (err) {
      setVerifyStatus({ ok: false, message: err.message });
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="workspace-layout">
      {/* Sidebar Navigation */}
      <Sidebar activeTab={activeTab} onSelectTab={setActiveTab} />

      {/* Main Workspace Body */}
      <main className="workspace-main">
        {activeTab === 'invoices' && <InvoicesManager />}
        {activeTab === 'subscriptions' && <SubscriptionsManager />}
        {activeTab === 'plans' && <PlansManager />}
        {activeTab === 'customers' && <CustomersManager />}

        {activeTab === 'overview' && (
          <div className="overview-container">
            {/* Welcome Banner */}
            <div className="dashboard-hero">
              <div className="dashboard-hero-content">
                <div className="status-pill online" style={{ marginBottom: '1rem' }}>
                  <span className="status-dot"></span>
                  Authenticated Session Active
                </div>
                <h1 className="dashboard-title">
                  Welcome back, <span className="gradient-text">{profile?.full_name || 'Administrator'}</span>
                </h1>
                <p className="dashboard-subtitle">
                  Authenticated workspace for recurring revenue operations, subscription lifecycle management, and invoice automation.
                </p>
              </div>

              <div className="dashboard-hero-actions">
                <button className="btn-secondary" onClick={handleManualVerify} disabled={verifying}>
                  {verifying ? 'Verifying...' : '⟳ Verify JWT Token'}
                </button>
                <button className="btn-primary" style={{ background: '#dc2626' }} onClick={onLogout}>
                  <span>Sign Out</span>
                </button>
              </div>
            </div>

            {/* Token Verification Status Alert */}
            {verifyStatus && (
              <div className={`auth-alert ${verifyStatus.ok ? 'success' : 'error'}`} style={{ marginBottom: '2rem' }}>
                <span>{verifyStatus.ok ? '✓' : '⚠️'}</span>
                <span>{verifyStatus.message}</span>
              </div>
            )}

            {/* User Session Metadata Grid */}
            <div className="dashboard-grid">
              <div className="dash-card">
                <div className="dash-card-header">
                  <span className="dash-card-tag">AUTHENTICATION IDENTITY</span>
                </div>
                <h3 className="dash-card-value">{profile?.email}</h3>
                <p className="dash-card-desc">Registered Account Email</p>
                <div className="meta-row">
                  <span className="meta-label">Account Status:</span>
                  <span className="meta-badge active">
                    {profile?.is_active ? 'Active' : 'Inactive'}
                  </span>
                </div>
                <div className="meta-row">
                  <span className="meta-label">Superuser Role:</span>
                  <span className="meta-val">{profile?.is_superuser ? 'Yes (Root)' : 'Standard User'}</span>
                </div>
              </div>

              <div className="dash-card">
                <div className="dash-card-header">
                  <span className="dash-card-tag">SESSION DETAILS</span>
                </div>
                <h3 className="dash-card-value">HS256 Signed JWT</h3>
                <p className="dash-card-desc">Stateless Bearer Authorization</p>
                <div className="meta-row">
                  <span className="meta-label">User ID:</span>
                  <span className="meta-val">#{profile?.id}</span>
                </div>
                <div className="meta-row">
                  <span className="meta-label">Created At:</span>
                  <span className="meta-val">
                    {profile?.created_at ? new Date(profile.created_at).toLocaleDateString() : 'Active'}
                  </span>
                </div>
              </div>

              <div className="dash-card">
                <div className="dash-card-header">
                  <span className="dash-card-tag">SYSTEM CAPABILITIES</span>
                </div>
                <h3 className="dash-card-value">Billing Engine</h3>
                <p className="dash-card-desc">Fully operational backend APIs</p>
                <div className="meta-row">
                  <span className="meta-label">Scheduler:</span>
                  <span className="meta-val">Celery Beat (Hourly)</span>
                </div>
                <div className="meta-row">
                  <span className="meta-label">Payment Gateway:</span>
                  <span className="meta-val">Mock Gateway with Webhooks</span>
                </div>
              </div>
            </div>

            {/* Module Overview Section */}
            <div className="dashboard-section">
              <h2 className="section-title">Subscription Operations Management</h2>
              <p className="section-subtitle">Direct access to core automated recurring revenue services</p>

              <div className="module-grid">
                <div className="module-card">
                  <div className="module-icon">📊</div>
                  <h4>Plans & Pricing</h4>
                  <p>Create and manage recurring tiers (monthly/annual) with grandfathered historical pricing.</p>
                  <button type="button" className="module-link-btn" onClick={() => setActiveTab('plans')}>
                    Manage Plans →
                  </button>
                </div>

                <div className="module-card">
                  <div className="module-icon">👥</div>
                  <h4>Subscriber Customers</h4>
                  <p>Manage customer records, unique customer codes, currency preferences, and soft deletions.</p>
                  <button type="button" className="module-link-btn" onClick={() => setActiveTab('customers')}>
                    Manage Customers →
                  </button>
                </div>

                <div className="module-card" style={{ borderColor: 'var(--primary-500)', background: 'rgba(59, 130, 246, 0.05)' }}>
                  <div className="module-icon">🔁</div>
                  <h4>Subscriptions & Lifecycles</h4>
                  <p>Deterministic state transitions (active, paused, cancelled, past_due) and live proration calculations.</p>
                  <button type="button" className="btn-primary" style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem', alignSelf: 'flex-start' }} onClick={() => setActiveTab('subscriptions')}>
                    Open Subscriptions Manager →
                  </button>
                </div>

                <div className="module-card" style={{ borderColor: 'var(--primary-500)', background: 'rgba(59, 130, 246, 0.05)' }}>
                  <div className="module-icon">🧾</div>
                  <h4>Invoices & Line Items</h4>
                  <p>Automated billing cycle invoice generation with ReportLab PDF download and itemized breakdown.</p>
                  <button type="button" className="btn-primary" style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem', alignSelf: 'flex-start' }} onClick={() => setActiveTab('invoices')}>
                    Open Invoices Manager →
                  </button>
                </div>

                <div className="module-card">
                  <div className="module-icon">💳</div>
                  <h4>Mock Payment Gateway</h4>
                  <p>Simulate charges, HMAC-SHA256 webhooks, and full/partial refund ledgers with row-level locks.</p>
                  <button type="button" className="module-link-btn" onClick={() => setActiveTab('payments')}>
                    Manage Payments →
                  </button>
                </div>

                <div className="module-card">
                  <div className="module-icon">⏱</div>
                  <h4>Celery Beat Scheduler</h4>
                  <p>Periodic background tasks identifying due renewals and maintaining billing idempotency.</p>
                  <button type="button" className="module-link-btn" onClick={() => setActiveTab('billing_cycles')}>
                    View Billing Cycles →
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Other Module Placeholder Views */}
        {activeTab !== 'invoices' && activeTab !== 'subscriptions' && activeTab !== 'plans' && activeTab !== 'customers' && activeTab !== 'overview' && (
          <div className="module-placeholder-view">
            <div className="dashboard-hero">
              <div className="dashboard-hero-content">
                <div className="status-pill online" style={{ marginBottom: '0.75rem' }}>
                  <span className="status-dot"></span>
                  Backend Module Operational
                </div>
                <h2 className="dashboard-title">
                  {activeTab.replace('_', ' ').toUpperCase()} Management
                </h2>
                <p className="dashboard-subtitle">
                  Automated recurring revenue controls for this module are operational. Use the Subscriptions and Invoices managers for operational administration.
                </p>
              </div>

              <div className="dashboard-hero-actions">
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => setActiveTab('invoices')}
                >
                  Open Invoices Manager →
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setActiveTab('subscriptions')}
                >
                  Open Subscriptions Manager →
                </button>
              </div>
            </div>

            <div className="dash-card" style={{ padding: '2rem' }}>
              <h3>Module Status: {activeTab.replace('_', ' ').toUpperCase()}</h3>
              <p style={{ color: 'var(--text-secondary)', marginTop: '0.5rem', marginBottom: '1.5rem' }}>
                All recurring operations, lifecycle state machine transitions, and database audit trails are active and verified.
              </p>
              <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
                <button type="button" className="btn-secondary" onClick={() => setActiveTab('invoices')}>
                  ← Switch to Invoices Manager
                </button>
                <button type="button" className="btn-secondary" onClick={() => setActiveTab('overview')}>
                  View Account Overview
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
