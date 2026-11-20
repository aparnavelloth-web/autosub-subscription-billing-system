import React, { useState, useEffect, useCallback } from 'react';
import { planService } from '../services/plans';
import PlanModal from './PlanModal';

export default function PlansManager() {
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [successNotice, setSuccessNotice] = useState('');

  // Filters & Controls
  const [intervalFilter, setIntervalFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [includeArchived, setIncludeArchived] = useState(true);
  const [viewMode, setViewMode] = useState('cards'); // 'cards' | 'table'

  // Modal states
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedPlanForEdit, setSelectedPlanForEdit] = useState(null);

  // Action states for Archive / Delete confirmation
  const [confirmAction, setConfirmAction] = useState(null); // { type: 'archive'|'delete', plan: obj }
  const [actionLoading, setActionLoading] = useState(false);

  const fetchPlans = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const data = await planService.listPlans({
        page: 1,
        size: 100,
        include_archived: includeArchived,
        active_only: false,
        billing_interval: intervalFilter === 'all' ? null : intervalFilter,
      });
      setPlans(data.items || []);
    } catch (err) {
      setError(err.message || 'Failed to load subscription plans.');
    } finally {
      setLoading(false);
    }
  }, [includeArchived, intervalFilter]);

  useEffect(() => {
    fetchPlans();
  }, [fetchPlans]);

  // Flash notice helper
  const showSuccess = (msg) => {
    setSuccessNotice(msg);
    setTimeout(() => {
      setSuccessNotice('');
    }, 5000);
  };

  const handleOpenCreateModal = () => {
    setSelectedPlanForEdit(null);
    setIsModalOpen(true);
  };

  const handleOpenEditModal = (plan) => {
    setSelectedPlanForEdit(plan);
    setIsModalOpen(true);
  };

  const handleModalSuccess = (savedPlan, actionType) => {
    showSuccess(
      actionType === 'created'
        ? `Plan '${savedPlan.name}' (${savedPlan.code}) created successfully.`
        : `Plan '${savedPlan.name}' updated successfully.`
    );
    fetchPlans();
  };

  const handleExecuteArchive = async () => {
    if (!confirmAction?.plan) return;
    const { plan } = confirmAction;
    try {
      setActionLoading(true);
      await planService.archivePlan(plan.id);
      showSuccess(`Plan '${plan.name}' (${plan.code}) has been archived.`);
      setConfirmAction(null);
      fetchPlans();
    } catch (err) {
      setError(err.message || `Failed to archive plan #${plan.id}.`);
      setConfirmAction(null);
    } finally {
      setActionLoading(false);
    }
  };

  const handleExecuteDelete = async () => {
    if (!confirmAction?.plan) return;
    const { plan } = confirmAction;
    try {
      setActionLoading(true);
      await planService.deletePlan(plan.id);
      showSuccess(`Plan '${plan.name}' (${plan.code}) has been soft-deleted.`);
      setConfirmAction(null);
      fetchPlans();
    } catch (err) {
      setError(err.message || `Failed to delete plan #${plan.id}.`);
      setConfirmAction(null);
    } finally {
      setActionLoading(false);
    }
  };

  // Filter plans locally by search query
  const filteredPlans = plans.filter((p) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase().trim();
    return (
      p.name?.toLowerCase().includes(q) ||
      p.code?.toLowerCase().includes(q) ||
      p.description?.toLowerCase().includes(q)
    );
  });

  const getCurrencySymbol = (code) => {
    switch (code?.toUpperCase()) {
      case 'USD':
        return '$';
      case 'EUR':
        return '€';
      case 'GBP':
        return '£';
      case 'INR':
        return '₹';
      case 'CAD':
        return 'CA$';
      case 'AUD':
        return 'AU$';
      default:
        return code || '$';
    }
  };

  const renderFeaturesList = (planFeatures) => {
    let items = [];
    if (Array.isArray(planFeatures)) {
      items = planFeatures;
    } else if (planFeatures && typeof planFeatures === 'object') {
      items = Object.entries(planFeatures).map(([k, v]) => `${k}: ${v}`);
    }

    if (items.length === 0) {
      return <div className="no-features-text">Standard recurring subscription entitlements</div>;
    }

    return (
      <ul className="plan-card-features">
        {items.map((feat, idx) => (
          <li key={idx}>
            <span className="feature-check-icon">✓</span>
            <span className="feature-text">{feat}</span>
          </li>
        ))}
      </ul>
    );
  };

  return (
    <div className="invoices-manager-container">
      {/* Header */}
      <div className="invoices-header">
        <div>
          <div className="status-pill online" style={{ marginBottom: '0.5rem' }}>
            <span className="status-dot"></span>
            Recurring Plan Catalog
          </div>
          <h1 className="invoices-title">Subscription Plans Management</h1>
          <p className="invoices-subtitle">
            Configure pricing tiers, billing cadences, trial periods, and feature entitlements. Active plans are immediately available for new customer subscriptions.
          </p>
        </div>

        <div className="invoices-header-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={handleOpenCreateModal}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', fontWeight: 600 }}
          >
            <span>+</span> Create New Plan
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={fetchPlans}
            disabled={loading}
            title="Refresh plan catalog"
          >
            {loading ? 'Refreshing...' : '⟳ Refresh'}
          </button>
        </div>
      </div>

      {/* Success Banner */}
      {successNotice && (
        <div className="auth-alert success" style={{ marginBottom: '0.5rem' }}>
          <span>✓</span>
          <span>{successNotice}</span>
        </div>
      )}

      {/* Error Alert */}
      {error && (
        <div className="auth-alert error" style={{ marginBottom: '0.5rem' }}>
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}

      {/* Controls & Filter Toolbar */}
      <div className="plans-controls-bar">
        {/* Interval Filter Tabs */}
        <div className="subscription-status-tabs">
          {[
            { id: 'all', label: 'ALL INTERVALS' },
            { id: 'monthly', label: 'MONTHLY' },
            { id: 'quarterly', label: 'QUARTERLY' },
            { id: 'yearly', label: 'YEARLY' },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`subscription-filter-btn ${intervalFilter === tab.id ? 'active' : ''}`}
              onClick={() => setIntervalFilter(tab.id)}
            >
              <span className={`filter-status-indicator indicator-${tab.id}`}></span>
              {tab.label}
            </button>
          ))}
        </div>

        {/* Right Side Controls */}
        <div className="plans-controls-right">
          {/* Search Box */}
          <div className="search-input-wrapper" style={{ minWidth: '220px' }}>
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="search-icon"
            >
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              className="toolbar-input"
              style={{ padding: '0.45rem 0.85rem 0.45rem 2.2rem', fontSize: '0.8rem' }}
              placeholder="Filter by name or code..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          {/* Archived Toggle */}
          <label className="checkbox-label" style={{ fontSize: '0.8rem', userSelect: 'none', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
            />
            <span>Show Archived</span>
          </label>

          {/* View Mode Toggle */}
          <div className="view-mode-toggle">
            <button
              type="button"
              className={`view-toggle-btn ${viewMode === 'cards' ? 'active' : ''}`}
              onClick={() => setViewMode('cards')}
              title="Cards Grid View"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect width="7" height="7" x="3" y="3" rx="1" />
                <rect width="7" height="7" x="14" y="3" rx="1" />
                <rect width="7" height="7" x="14" y="14" rx="1" />
                <rect width="7" height="7" x="3" y="14" rx="1" />
              </svg>
              <span>Cards</span>
            </button>
            <button
              type="button"
              className={`view-toggle-btn ${viewMode === 'table' ? 'active' : ''}`}
              onClick={() => setViewMode('table')}
              title="Table View"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="8" x2="21" y1="6" y2="6" />
                <line x1="8" x2="21" y1="12" y2="12" />
                <line x1="8" x2="21" y1="18" y2="18" />
                <line x1="3" x2="3.01" y1="6" y2="6" />
                <line x1="3" x2="3.01" y1="12" y2="12" />
                <line x1="3" x2="3.01" y1="18" y2="18" />
              </svg>
              <span>Table</span>
            </button>
          </div>
        </div>
      </div>

      {/* Main Content Area */}
      {loading ? (
        <div className="table-card" style={{ padding: '4rem 1rem', textAlign: 'center' }}>
          <div className="spinner" style={{ margin: '0 auto 1rem' }}></div>
          <p style={{ color: 'var(--text-secondary)' }}>Loading subscription plans...</p>
        </div>
      ) : filteredPlans.length === 0 ? (
        <div className="table-card empty-state" style={{ padding: '4rem 2rem' }}>
          <div className="empty-icon" style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>📦</div>
          <h3>No Subscription Plans Found</h3>
          <p style={{ maxWidth: '460px', margin: '0.5rem auto 1.5rem', color: 'var(--text-secondary)' }}>
            {searchQuery
              ? `No plans matching "${searchQuery}". Clear your search or adjust the interval filter.`
              : 'There are currently no subscription tiers defined in your catalog. Create your first plan to start enrolling subscribers.'}
          </p>
          <button
            type="button"
            className="btn-primary"
            onClick={handleOpenCreateModal}
          >
            + Create First Plan
          </button>
        </div>
      ) : viewMode === 'cards' ? (
        /* CARDS GRID VIEW */
        <div className="plans-grid">
          {filteredPlans.map((plan) => {
            const isArchived = Boolean(plan.deleted_at || !plan.is_active);
            const symbol = getCurrencySymbol(plan.currency);
            return (
              <div
                key={plan.id}
                className={`plan-card-item ${isArchived ? 'plan-archived' : ''}`}
              >
                {/* Plan Card Top Bar */}
                <div className="plan-card-header">
                  <div>
                    <div className="plan-code-badge mono-code">{plan.code}</div>
                    <h3 className="plan-card-name">{plan.name}</h3>
                  </div>
                  <div>
                    {isArchived ? (
                      <span className="status-badge status-cancelled">Archived</span>
                    ) : (
                      <span className="status-badge status-active">Active</span>
                    )}
                  </div>
                </div>

                {/* Pricing Display */}
                <div className="plan-card-pricing">
                  <div className="plan-price-large">
                    <span className="price-currency">{symbol}</span>
                    <span className="price-amount">{plan.price}</span>
                    <span className="price-cadence">/ {plan.billing_interval}</span>
                  </div>
                  <div className="plan-trial-pill">
                    {plan.trial_days > 0 ? (
                      <span>⚡ {plan.trial_days} Days Free Trial</span>
                    ) : (
                      <span>Immediate Billing (No Trial)</span>
                    )}
                  </div>
                </div>

                {/* Description */}
                {plan.description && (
                  <p className="plan-card-desc">{plan.description}</p>
                )}

                {/* Features Section */}
                <div className="plan-card-features-container">
                  <div className="features-label">INCLUDED ENTITLEMENTS</div>
                  {renderFeaturesList(plan.features)}
                </div>

                {/* Card Actions */}
                <div className="plan-card-footer">
                  <div className="plan-card-actions">
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '0.4rem 0.85rem', fontSize: '0.8rem' }}
                      onClick={() => handleOpenEditModal(plan)}
                      disabled={isArchived}
                      title={isArchived ? 'Archived plans cannot be edited' : 'Edit Plan'}
                    >
                      Edit Plan
                    </button>

                    {!isArchived ? (
                      <button
                        type="button"
                        className="btn-secondary btn-archive"
                        style={{ padding: '0.4rem 0.75rem', fontSize: '0.8rem' }}
                        onClick={() => setConfirmAction({ type: 'archive', plan })}
                        title="Archive this plan (prevents new subscriptions)"
                      >
                        Archive
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn-secondary"
                        style={{ padding: '0.4rem 0.75rem', fontSize: '0.8rem', opacity: 0.5, cursor: 'not-allowed' }}
                        disabled
                      >
                        Archived
                      </button>
                    )}

                    {!isArchived && (
                      <button
                        type="button"
                        className="btn-secondary btn-delete"
                        style={{ padding: '0.4rem 0.65rem', fontSize: '0.8rem', color: '#ef4444' }}
                        onClick={() => setConfirmAction({ type: 'delete', plan })}
                        title="Soft-delete plan"
                      >
                        Delete
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* TABLE VIEW */
        <div className="table-card">
          <div className="table-responsive">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Plan Code</th>
                  <th>Name</th>
                  <th>Interval</th>
                  <th>Recurring Price</th>
                  <th>Trial Period</th>
                  <th>Features</th>
                  <th>Status</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredPlans.map((plan) => {
                  const isArchived = Boolean(plan.deleted_at || !plan.is_active);
                  const symbol = getCurrencySymbol(plan.currency);
                  const featureCount = Array.isArray(plan.features)
                    ? plan.features.length
                    : plan.features
                    ? Object.keys(plan.features).length
                    : 0;

                  return (
                    <tr key={plan.id} className={isArchived ? 'row-archived' : ''}>
                      <td>
                        <span className="mono-code">{plan.code}</span>
                      </td>
                      <td>
                        <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{plan.name}</div>
                        {plan.description && (
                          <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', maxWidth: '280px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {plan.description}
                          </div>
                        )}
                      </td>
                      <td>
                        <span className="plan-badge-tag">{plan.billing_interval?.toUpperCase()}</span>
                      </td>
                      <td>
                        <span className="amount-col" style={{ fontWeight: 700 }}>
                          {symbol} {plan.price}
                        </span>
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: '0.25rem' }}>
                          {plan.currency}
                        </span>
                      </td>
                      <td>
                        {plan.trial_days > 0 ? (
                          <span style={{ color: '#60a5fa', fontSize: '0.82rem' }}>{plan.trial_days} days</span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)', fontSize: '0.82rem' }}>None</span>
                        )}
                      </td>
                      <td>
                        <span style={{ fontSize: '0.82rem' }}>{featureCount} feature{featureCount !== 1 ? 's' : ''}</span>
                      </td>
                      <td>
                        {isArchived ? (
                          <span className="status-badge status-cancelled">Archived</span>
                        ) : (
                          <span className="status-badge status-active">Active</span>
                        )}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '0.4rem' }}>
                          <button
                            type="button"
                            className="btn-secondary table-btn"
                            onClick={() => handleOpenEditModal(plan)}
                            disabled={isArchived}
                            title="Edit plan"
                          >
                            Edit
                          </button>
                          {!isArchived && (
                            <button
                              type="button"
                              className="btn-secondary table-btn"
                              onClick={() => setConfirmAction({ type: 'archive', plan })}
                              title="Archive plan"
                            >
                              Archive
                            </button>
                          )}
                          {!isArchived && (
                            <button
                              type="button"
                              className="btn-secondary table-btn"
                              style={{ color: '#ef4444' }}
                              onClick={() => setConfirmAction({ type: 'delete', plan })}
                              title="Delete plan"
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Plan Create / Edit Modal */}
      {isModalOpen && (
        <PlanModal
          isOpen={isModalOpen}
          plan={selectedPlanForEdit}
          onClose={() => setIsModalOpen(false)}
          onSuccess={handleModalSuccess}
        />
      )}

      {/* Confirmation Dialog for Archive / Delete */}
      {confirmAction && (
        <div className="modal-backdrop" onClick={() => setConfirmAction(null)}>
          <div
            className="modal-container"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: '480px' }}
          >
            <div className="modal-header">
              <div>
                <h3 className="modal-title" style={{ fontSize: '1.15rem' }}>
                  {confirmAction.type === 'archive' ? 'Archive Subscription Plan' : 'Delete Subscription Plan'}
                </h3>
                <p className="modal-subtitle">
                  Plan: <strong style={{ color: 'var(--text-primary)' }}>{confirmAction.plan.name}</strong> ({confirmAction.plan.code})
                </p>
              </div>
              <button
                className="modal-close-btn"
                onClick={() => setConfirmAction(null)}
              >
                &times;
              </button>
            </div>

            <div style={{ margin: '1.25rem 0', color: 'var(--text-secondary)', fontSize: '0.875rem', lineHeight: '1.5' }}>
              {confirmAction.type === 'archive' ? (
                <p>
                  Archiving this plan will deactivate it from the catalog so that <strong>no new subscriptions</strong> can reference it. Existing active subscriptions will safely retain their historical pricing and billing schedules.
                </p>
              ) : (
                <p>
                  Soft-deleting this plan will mark it as deleted in the database. Historical subscriptions, invoices, and audit trails will remain preserved in compliance with billing idempotency.
                </p>
              )}
            </div>

            <div className="modal-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConfirmAction(null)}
                disabled={actionLoading}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                style={{
                  background: confirmAction.type === 'delete' ? '#dc2626' : 'var(--primary-600)',
                }}
                onClick={confirmAction.type === 'archive' ? handleExecuteArchive : handleExecuteDelete}
                disabled={actionLoading}
              >
                {actionLoading
                  ? 'Processing...'
                  : confirmAction.type === 'archive'
                  ? 'Confirm Archive'
                  : 'Confirm Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
