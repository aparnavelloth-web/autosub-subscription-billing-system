import React, { useState, useEffect } from 'react';
import { subscriptionService } from '../services/subscriptions';

export default function PlanChangeModal({
  subscription,
  plans = [],
  onClose,
  onSuccess,
}) {
  const [selectedPlanId, setSelectedPlanId] = useState('');
  const [quantity, setQuantity] = useState(subscription?.quantity || 1);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [preview, setPreview] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState('');
  const [successResult, setSuccessResult] = useState(null);

  // Filter out the current plan from selectable options if available
  const availablePlans = plans.filter(
    (p) => p.id !== subscription?.plan_id && p.is_active && !p.is_archived
  );

  useEffect(() => {
    if (availablePlans.length > 0 && !selectedPlanId) {
      setSelectedPlanId(String(availablePlans[0].id));
    }
  }, [availablePlans, selectedPlanId]);

  // Reset preview when plan or quantity changes
  const handlePlanChange = (planId) => {
    setSelectedPlanId(planId);
    setPreview(null);
    setError('');
  };

  const handleQuantityChange = (newQty) => {
    const val = Math.max(1, parseInt(newQty, 10) || 1);
    setQuantity(val);
    setPreview(null);
    setError('');
  };

  const handleFetchPreview = async () => {
    if (!selectedPlanId) {
      setError('Please select a target plan to preview.');
      return;
    }
    try {
      setLoadingPreview(true);
      setError('');
      const data = await subscriptionService.previewProration(subscription.id, {
        newPlanId: selectedPlanId,
        quantity,
      });
      setPreview(data);
    } catch (err) {
      setError(err.message || 'Failed to calculate proration preview.');
      setPreview(null);
    } finally {
      setLoadingPreview(false);
    }
  };

  const handleConfirmChange = async () => {
    if (!selectedPlanId || !preview) {
      setError('Please preview the proration breakdown first.');
      return;
    }
    try {
      setConfirming(true);
      setError('');
      const result = await subscriptionService.changePlan(subscription.id, {
        newPlanId: selectedPlanId,
        quantity,
      });
      setSuccessResult(result);
      if (onSuccess) {
        onSuccess(result);
      }
    } catch (err) {
      setError(err.message || 'Failed to execute plan change.');
    } finally {
      setConfirming(false);
    }
  };

  const currentPlan = plans.find((p) => p.id === subscription?.plan_id);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-container proration-modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '640px' }}
      >
        <div className="modal-header">
          <div>
            <div className="status-pill online" style={{ marginBottom: '0.4rem', fontSize: '0.75rem' }}>
              <span className="status-dot"></span>
              Plan Upgrade & Proration
            </div>
            <h2 className="modal-title">Change Subscription Plan</h2>
            <p className="modal-subtitle">
              Subscription <span className="mono-code">{subscription?.subscription_code}</span>
            </p>
          </div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close modal">
            &times;
          </button>
        </div>

        {error && (
          <div className="auth-alert error" style={{ margin: '1rem 0' }}>
            <span>⚠️</span>
            <span>{error}</span>
          </div>
        )}

        {successResult ? (
          <div className="proration-success-card">
            <div className="success-icon-badge">✓</div>
            <h3>Plan Change Executed Successfully</h3>
            <p className="success-desc">
              Subscription contracted tier has been upgraded/updated. Historical pricing was cleanly grandfathered and a formal adjustment statement was issued.
            </p>

            <div className="success-meta-grid">
              <div className="meta-box">
                <span className="meta-box-label">New Contract Price</span>
                <span className="meta-box-value">
                  {subscription?.currency} {successResult.unit_price}
                </span>
              </div>
              <div className="meta-box">
                <span className="meta-box-label">Adjustment Invoice</span>
                <span className="meta-box-value highlight">
                  {successResult.invoice_number || 'INV-Generated'}
                </span>
              </div>
              {successResult.proration_net_amount !== null && (
                <div className="meta-box">
                  <span className="meta-box-label">Net Adjustment</span>
                  <span className="meta-box-value">
                    {subscription?.currency} {successResult.proration_net_amount}
                  </span>
                </div>
              )}
            </div>

            <div className="modal-actions" style={{ marginTop: '1.5rem', justifyContent: 'flex-end' }}>
              <button type="button" className="btn-primary" onClick={onClose}>
                Done & Return to Workspace
              </button>
            </div>
          </div>
        ) : (
          <div className="proration-form">
            {/* Current Contract Plan Card */}
            <div className="plan-summary-card current-plan">
              <div className="card-sub-header">
                <span className="badge-tag current">CURRENT CONTRACT</span>
                <span className="mono-code">{subscription?.billing_interval?.toUpperCase()}</span>
              </div>
              <div className="plan-row">
                <div>
                  <h4 className="plan-name-display">{currentPlan?.name || `Plan #${subscription?.plan_id}`}</h4>
                  <p className="plan-cycle-dates">
                    Period: {subscription?.current_period_start?.slice(0, 10)} to {subscription?.current_period_end?.slice(0, 10)}
                  </p>
                </div>
                <div className="plan-price-display">
                  <span className="currency">{subscription?.currency}</span>
                  <span className="amount">{subscription?.unit_price}</span>
                  <span className="unit">/ {subscription?.billing_interval}</span>
                </div>
              </div>
            </div>

            {/* Target Plan Selector */}
            <div className="form-group" style={{ marginTop: '1.25rem' }}>
              <label className="form-label" htmlFor="new-plan-select">
                Select New Target Plan
              </label>
              <select
                id="new-plan-select"
                className="form-input"
                value={selectedPlanId}
                onChange={(e) => handlePlanChange(e.target.value)}
              >
                {availablePlans.length === 0 ? (
                  <option value="">No other plans available</option>
                ) : (
                  availablePlans.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} — {p.currency} {p.price} / {p.billing_interval}
                    </option>
                  ))
                )}
              </select>
            </div>

            {/* Seat/Unit Quantity */}
            <div className="form-group">
              <label className="form-label" htmlFor="plan-quantity">
                Unit Quantity / Seats
              </label>
              <input
                id="plan-quantity"
                type="number"
                min="1"
                max="1000"
                className="form-input"
                value={quantity}
                onChange={(e) => handleQuantityChange(e.target.value)}
              />
            </div>

            {/* Preview Action Button */}
            <div style={{ marginTop: '1rem', marginBottom: '1.25rem' }}>
              <button
                type="button"
                className="btn-secondary"
                style={{ width: '100%', justifyContent: 'center' }}
                onClick={handleFetchPreview}
                disabled={loadingPreview || !selectedPlanId}
              >
                {loadingPreview ? 'Calculating Proration Engine...' : '⟳ Calculate Proration Preview'}
              </button>
            </div>

            {/* Proration Preview Breakdown Card */}
            {preview && (
              <div className="proration-breakdown-card">
                <div className="breakdown-header">
                  <span className={`change-badge ${preview.change_type}`}>
                    {preview.change_type?.toUpperCase()}
                  </span>
                  <span className="breakdown-days">
                    {preview.remaining_billing_days} of {preview.total_billing_days} billing days remaining
                  </span>
                </div>

                <div className="breakdown-items-table">
                  <div className="breakdown-line">
                    <span className="line-label">
                      Unused Credit ({preview.current_plan_name})
                    </span>
                    <span className="line-val credit">
                      -{preview.currency} {preview.unused_credit}
                    </span>
                  </div>

                  <div className="breakdown-line">
                    <span className="line-label">
                      Prorated Charge ({preview.new_plan_name})
                    </span>
                    <span className="line-val debit">
                      +{preview.currency} {preview.prorated_charge}
                    </span>
                  </div>

                  {Number(preview.tax_amount) > 0 && (
                    <div className="breakdown-line">
                      <span className="line-label">Estimated Tax</span>
                      <span className="line-val">
                        +{preview.currency} {preview.tax_amount}
                      </span>
                    </div>
                  )}

                  <div className="breakdown-divider"></div>

                  <div className="breakdown-line total-line">
                    <span className="total-title">Final Amount Due Immediately</span>
                    <span className="total-amount-highlight">
                      {preview.currency} {preview.total_due}
                    </span>
                  </div>

                  {Number(preview.credit_carried_forward) > 0 && (
                    <div className="credit-surplus-note">
                      ℹ️ Downgrade generates surplus credit of{' '}
                      <strong>
                        {preview.currency} {preview.credit_carried_forward}
                      </strong>{' '}
                      recorded to customer ledger.
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Modal Footer Actions */}
            <div className="modal-actions" style={{ marginTop: '1.5rem' }}>
              <button type="button" className="btn-secondary" onClick={onClose} disabled={confirming}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={handleConfirmChange}
                disabled={!preview || confirming || loadingPreview}
              >
                {confirming ? 'Executing Change & Invoicing...' : '✓ Confirm & Execute Plan Change'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
