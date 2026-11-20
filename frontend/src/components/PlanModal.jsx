import React, { useState, useEffect } from 'react';
import { planService } from '../services/plans';

const CURRENCIES = [
  { code: 'INR', symbol: '₹', label: 'INR (₹) - Indian Rupee' },
  { code: 'USD', symbol: '$', label: 'USD ($) - US Dollar' },
  { code: 'EUR', symbol: '€', label: 'EUR (€) - Euro' },
  { code: 'GBP', symbol: '£', label: 'GBP (£) - British Pound' },
  { code: 'CAD', symbol: 'CA$', label: 'CAD ($) - Canadian Dollar' },
  { code: 'AUD', symbol: 'AU$', label: 'AUD ($) - Australian Dollar' },
];

const BILLING_INTERVALS = [
  { value: 'monthly', label: 'Monthly (every 1 month)' },
  { value: 'quarterly', label: 'Quarterly (every 3 months)' },
  { value: 'yearly', label: 'Yearly (every 12 months)' },
];

export default function PlanModal({ isOpen, plan, onClose, onSuccess }) {
  const isEdit = Boolean(plan && plan.id);

  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [codeManuallyEdited, setCodeManuallyEdited] = useState(false);
  const [description, setDescription] = useState('');
  const [price, setPrice] = useState('29.00');
  const [currency, setCurrency] = useState('INR');
  const [billingInterval, setBillingInterval] = useState('monthly');
  const [trialDays, setTrialDays] = useState(0);
  const [features, setFeatures] = useState([]);
  const [newFeatureText, setNewFeatureText] = useState('');
  const [isActive, setIsActive] = useState(true);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (plan) {
      setName(plan.name || '');
      setCode(plan.code || '');
      setCodeManuallyEdited(true);
      setDescription(plan.description || '');
      setPrice(plan.price !== undefined ? String(plan.price) : '0.00');
      setCurrency(plan.currency || 'INR');
      setBillingInterval(plan.billing_interval || 'monthly');
      setTrialDays(plan.trial_days !== undefined ? plan.trial_days : 0);
      setIsActive(plan.is_active !== undefined ? plan.is_active : true);

      // Parse features list
      if (Array.isArray(plan.features)) {
        setFeatures(plan.features);
      } else if (plan.features && typeof plan.features === 'object') {
        setFeatures(Object.entries(plan.features).map(([k, v]) => `${k}: ${v}`));
      } else {
        setFeatures([]);
      }
    } else {
      setName('');
      setCode('');
      setCodeManuallyEdited(false);
      setDescription('');
      setPrice('29.00');
      setCurrency('INR');
      setBillingInterval('monthly');
      setTrialDays(0);
      setFeatures(['Automated Recurring Billing', 'Standard Invoice PDF Export']);
      setIsActive(true);
    }
    setError('');
  }, [plan, isOpen]);

  if (!isOpen) return null;

  const handleNameChange = (val) => {
    setName(val);
    if (!isEdit && !codeManuallyEdited) {
      // Auto-generate plan code from name
      const autoCode = val
        .toUpperCase()
        .replace(/[^A-Z0-9\s-_]/g, '')
        .trim()
        .replace(/\s+/g, '-');
      setCode(autoCode);
    }
  };

  const handleCodeChange = (val) => {
    setCode(val.toUpperCase().replace(/[^A-Z0-9-_]/g, ''));
    setCodeManuallyEdited(true);
  };

  const handleAddFeature = (e) => {
    e?.preventDefault();
    const trimmed = newFeatureText.trim();
    if (trimmed) {
      if (!features.includes(trimmed)) {
        setFeatures([...features, trimmed]);
      }
      setNewFeatureText('');
    }
  };

  const handleRemoveFeature = (indexToRemove) => {
    setFeatures(features.filter((_, idx) => idx !== indexToRemove));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    // Validations
    if (!name || name.trim().length < 2) {
      setError('Plan name must be at least 2 characters.');
      return;
    }
    if (!code || code.trim().length < 2) {
      setError('Plan code must be at least 2 characters (alphanumeric, hyphens, underscores).');
      return;
    }
    const numPrice = parseFloat(price);
    if (isNaN(numPrice) || numPrice < 0) {
      setError('Price must be a valid non-negative number.');
      return;
    }
    const numTrial = parseInt(trialDays, 10);
    if (isNaN(numTrial) || numTrial < 0) {
      setError('Trial days must be 0 or greater.');
      return;
    }

    try {
      setLoading(true);

      const payload = {
        name: name.trim(),
        code: code.trim().toUpperCase(),
        description: description.trim() || null,
        price: numPrice.toFixed(2),
        currency: currency.trim().toUpperCase(),
        billing_interval: billingInterval,
        trial_days: numTrial,
        features: features,
        is_active: isActive,
      };

      let result;
      if (isEdit) {
        result = await planService.updatePlan(plan.id, payload);
      } else {
        result = await planService.createPlan(payload);
      }

      setLoading(false);
      onSuccess(result, isEdit ? 'updated' : 'created');
      onClose();
    } catch (err) {
      setLoading(false);
      setError(err.message || 'Failed to save plan. Please check inputs.');
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-container"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '640px' }}
      >
        <div className="modal-header">
          <div>
            <div className="status-pill online" style={{ marginBottom: '0.4rem', fontSize: '0.75rem' }}>
              <span className="status-dot"></span>
              {isEdit ? 'Update Existing Plan' : 'Plan Catalog Configuration'}
            </div>
            <h2 className="modal-title">{isEdit ? `Edit Plan: ${plan.name}` : 'Create New Subscription Plan'}</h2>
            <p className="modal-subtitle">
              {isEdit
                ? 'Update pricing, billing interval, and entitlements. Subscriptions preserve historical snapshots.'
                : 'Define pricing tiers, trial period, billing cadence, and feature entitlements.'}
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

        <form onSubmit={handleSubmit} className="plan-form">
          <div className="form-grid-2">
            <div className="form-group">
              <label className="form-label" htmlFor="plan-name">
                Plan Name <span className="required-mark">*</span>
              </label>
              <input
                id="plan-name"
                type="text"
                className="form-input"
                placeholder="e.g. Pro Monthly, Enterprise Annual"
                value={name}
                onChange={(e) => handleNameChange(e.target.value)}
                required
                minLength={2}
                maxLength={100}
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="plan-code">
                Plan Code (Unique ID) <span className="required-mark">*</span>
              </label>
              <input
                id="plan-code"
                type="text"
                className="form-input mono-input"
                placeholder="e.g. PRO-MO, ENT-YEARLY"
                value={code}
                onChange={(e) => handleCodeChange(e.target.value)}
                required
                minLength={2}
                maxLength={50}
              />
              <span className="input-hint">Used by API and subscriptions. Uppercase, alphanumeric, hyphens.</span>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="plan-desc">
              Description
            </label>
            <textarea
              id="plan-desc"
              className="form-input form-textarea"
              rows={2}
              placeholder="Brief description of this tier and target audience..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={2000}
            />
          </div>

          <div className="form-grid-3">
            <div className="form-group">
              <label className="form-label" htmlFor="plan-price">
                Recurring Price <span className="required-mark">*</span>
              </label>
              <input
                id="plan-price"
                type="number"
                step="0.01"
                min="0"
                className="form-input"
                placeholder="29.00"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="plan-currency">
                Currency <span className="required-mark">*</span>
              </label>
              <select
                id="plan-currency"
                className="form-input"
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
              >
                {CURRENCIES.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="plan-interval">
                Billing Interval <span className="required-mark">*</span>
              </label>
              <select
                id="plan-interval"
                className="form-input"
                value={billingInterval}
                onChange={(e) => setBillingInterval(e.target.value)}
              >
                {BILLING_INTERVALS.map((bi) => (
                  <option key={bi.value} value={bi.value}>
                    {bi.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="form-grid-2">
            <div className="form-group">
              <label className="form-label" htmlFor="plan-trial">
                Trial Period (Days)
              </label>
              <input
                id="plan-trial"
                type="number"
                min="0"
                max="365"
                className="form-input"
                placeholder="0 for no trial"
                value={trialDays}
                onChange={(e) => setTrialDays(e.target.value)}
              />
              <span className="input-hint">0 = active immediately; &gt; 0 = starts in trial status.</span>
            </div>

            <div className="form-group" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
              <label className="form-label" style={{ marginBottom: '0.5rem' }}>
                Catalog Status
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                />
                <span>Active (Available for new subscriptions)</span>
              </label>
            </div>
          </div>

          {/* Interactive Features List */}
          <div className="form-group" style={{ marginTop: '0.5rem' }}>
            <label className="form-label">
              Feature Entitlements
            </label>
            <div className="feature-input-row">
              <input
                type="text"
                className="form-input"
                placeholder="Add a plan feature (e.g. Up to 100 Subscribers, Webhooks)..."
                value={newFeatureText}
                onChange={(e) => setNewFeatureText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleAddFeature();
                  }
                }}
              />
              <button
                type="button"
                className="btn-secondary"
                onClick={handleAddFeature}
                style={{ flexShrink: 0, padding: '0.5rem 1rem' }}
              >
                + Add
              </button>
            </div>

            {features.length > 0 && (
              <div className="features-chip-list">
                {features.map((feat, idx) => (
                  <span key={idx} className="feature-chip">
                    <span className="chip-check">✓</span>
                    <span className="chip-text">{feat}</span>
                    <button
                      type="button"
                      className="chip-remove"
                      onClick={() => handleRemoveFeature(idx)}
                      title="Remove feature"
                    >
                      &times;
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="modal-actions" style={{ marginTop: '1.75rem' }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={onClose}
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={loading}
            >
              {loading ? (isEdit ? 'Updating Plan...' : 'Creating Plan...') : (isEdit ? 'Save Changes' : 'Create Plan')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
