import React, { useState, useEffect, useCallback } from 'react';
import { subscriptionService } from '../services/subscriptions';
import PlanChangeModal from './PlanChangeModal';
import SubscriptionHistoryModal from './SubscriptionHistoryModal';

export default function SubscriptionsManager() {
  const [subscriptions, setSubscriptions] = useState([]);
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  // Modal states
  const [changingSubscription, setChangingSubscription] = useState(null);
  const [historySubscription, setHistorySubscription] = useState(null);

  const fetchPlans = async () => {
    try {
      const planItems = await subscriptionService.listActivePlans();
      setPlans(planItems);
    } catch (err) {
      console.error('Failed to load plans:', err);
    }
  };

  const loadSubscriptions = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const data = await subscriptionService.listSubscriptions({
        page,
        size: 15,
        status: statusFilter === 'all' ? '' : statusFilter,
      });
      setSubscriptions(data.items || []);
      setTotalPages(data.pages || 1);
      setTotalCount(data.total || 0);
    } catch (err) {
      setError(err.message || 'Failed to load subscriptions.');
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    fetchPlans();
  }, []);

  useEffect(() => {
    loadSubscriptions();
  }, [loadSubscriptions]);

  const handlePlanChangeSuccess = () => {
    loadSubscriptions();
  };

  const getPlanDetails = (planId) => {
    return plans.find((p) => p.id === planId);
  };

  const getStatusBadge = (sub) => {
    if (sub.is_paused) {
      return <span className="status-badge status-paused">Paused</span>;
    }
    switch (sub.status) {
      case 'active':
        return <span className="status-badge status-active">Active</span>;
      case 'trial':
        return <span className="status-badge status-trial">Trial</span>;
      case 'past_due':
        return <span className="status-badge status-past_due">Past Due</span>;
      case 'cancelled':
        return <span className="status-badge status-cancelled">Cancelled</span>;
      default:
        return <span className="status-badge">{sub.status}</span>;
    }
  };

  return (
    <div className="invoices-manager-container">
      {/* Header */}
      <div className="invoices-header">
        <div>
          <div className="status-pill online" style={{ marginBottom: '0.5rem' }}>
            <span className="status-dot"></span>
            Subscription Management
          </div>
          <h1 className="invoices-title">Subscription Management</h1>
          <p className="invoices-subtitle">
            Manage customer subscription tiers, preview live proration adjustments, execute upgrades/downgrades, and inspect audit histories.
          </p>
        </div>

        <div className="invoices-header-actions">
          <button type="button" className="btn-secondary" onClick={loadSubscriptions} disabled={loading}>
            {loading ? 'Refreshing...' : '⟳ Refresh Subscriptions'}
          </button>
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="subscription-controls-card">
        <div className="subscription-filter-group">
          <span className="filter-group-label">Filter Status:</span>
          <div className="subscription-status-tabs">
            {[
              { id: 'all', label: 'ALL' },
              { id: 'active', label: 'ACTIVE' },
              { id: 'trial', label: 'TRIAL' },
              { id: 'past_due', label: 'PAST DUE' },
              { id: 'cancelled', label: 'CANCELLED' },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={`subscription-filter-btn ${statusFilter === tab.id ? 'active' : ''}`}
                onClick={() => {
                  setStatusFilter(tab.id);
                  setPage(1);
                }}
              >
                <span className={`filter-status-indicator indicator-${tab.id}`}></span>
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && (
        <div className="auth-alert error" style={{ marginBottom: '1.5rem' }}>
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}

      {/* Table */}
      <div className="table-card">
        {loading ? (
          <div className="table-loading">
            <div className="spinner"></div>
            <p>Loading subscriptions...</p>
          </div>
        ) : subscriptions.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">🔁</div>
            <h3>No Subscriptions Found</h3>
            <p>No subscriptions match the selected criteria.</p>
          </div>
        ) : (
          <div className="table-responsive">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Subscription Code</th>
                  <th>Status</th>
                  <th>Assigned Plan</th>
                  <th>Contract Price</th>
                  <th>Quantity</th>
                  <th>Billing Window</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {subscriptions.map((sub) => {
                  const plan = getPlanDetails(sub.plan_id);
                  const isChangeable = sub.status !== 'cancelled' && sub.status !== 'past_due' && !sub.is_paused;
                  return (
                    <tr key={sub.id}>
                      <td>
                        <span className="invoice-number mono-code">{sub.subscription_code}</span>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          Cust ID: #{sub.customer_id}
                        </div>
                      </td>
                      <td>{getStatusBadge(sub)}</td>
                      <td>
                        <div style={{ fontWeight: 600 }}>{plan?.name || `Plan #${sub.plan_id}`}</div>
                        <span className="plan-badge-tag">{sub.billing_interval?.toUpperCase()}</span>
                      </td>
                      <td>
                        <span className="amount-col">
                          {sub.currency} {sub.unit_price}
                        </span>
                      </td>
                      <td>{sub.quantity}</td>
                      <td>
                        <div style={{ fontSize: '0.82rem' }}>
                          {sub.current_period_start?.slice(0, 10)} to {sub.current_period_end?.slice(0, 10)}
                        </div>
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '0.5rem' }}>
                          <button
                            type="button"
                            className="btn-primary"
                            style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}
                            onClick={() => setChangingSubscription(sub)}
                            disabled={!isChangeable}
                            title={isChangeable ? 'Change Plan' : 'Plan change unavailable for current state'}
                          >
                            Change Plan →
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            style={{ padding: '0.35rem 0.65rem', fontSize: '0.8rem' }}
                            onClick={() => setHistorySubscription(sub)}
                          >
                            History
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="table-pagination">
            <span className="pagination-info">
              Page {page} of {totalPages} ({totalCount} subscriptions total)
            </span>
            <div className="pagination-buttons">
              <button
                type="button"
                className="btn-secondary"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                ← Prev
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Next →
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Plan Change Modal */}
      {changingSubscription && (
        <PlanChangeModal
          subscription={changingSubscription}
          plans={plans}
          onClose={() => setChangingSubscription(null)}
          onSuccess={handlePlanChangeSuccess}
        />
      )}

      {/* Subscription History Modal */}
      {historySubscription && (
        <SubscriptionHistoryModal
          subscription={historySubscription}
          onClose={() => setHistorySubscription(null)}
        />
      )}
    </div>
  );
}
