import React, { useState, useEffect } from 'react';
import { customerService } from '../services/customers';

const CURRENCIES = [
  { code: 'INR', symbol: '₹', label: 'INR (₹) - Indian Rupee' },
  { code: 'USD', symbol: '$', label: 'USD ($) - US Dollar' },
  { code: 'EUR', symbol: '€', label: 'EUR (€) - Euro' },
  { code: 'GBP', symbol: '£', label: 'GBP (£) - British Pound' },
  { code: 'CAD', symbol: 'CA$', label: 'CAD ($) - Canadian Dollar' },
  { code: 'AUD', symbol: 'AU$', label: 'AUD ($) - Australian Dollar' },
];

export default function CustomerModal({ isOpen, customer, onClose, onSuccess }) {
  const isEdit = Boolean(customer && customer.id);

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [companyName, setCompanyName] = useState('');
  const [phone, setPhone] = useState('');
  const [billingAddress, setBillingAddress] = useState('');
  const [taxId, setTaxId] = useState('');
  const [currency, setCurrency] = useState('INR');
  const [isActive, setIsActive] = useState(true);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (customer) {
      setFullName(customer.full_name || '');
      setEmail(customer.email || '');
      setCompanyName(customer.company_name || '');
      setPhone(customer.phone || '');
      setBillingAddress(customer.billing_address || '');
      setTaxId(customer.tax_id || '');
      setCurrency(customer.currency || 'INR');
      setIsActive(customer.is_active !== undefined ? customer.is_active : true);
    } else {
      setFullName('');
      setEmail('');
      setCompanyName('');
      setPhone('');
      setBillingAddress('');
      setTaxId('');
      setCurrency('INR');
      setIsActive(true);
    }
    setError('');
  }, [customer, isOpen]);

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    // Validations
    if (!fullName || fullName.trim().length < 2) {
      setError('Customer full name must be at least 2 characters.');
      return;
    }
    if (!email || !email.includes('@')) {
      setError('A valid email address is required.');
      return;
    }

    try {
      setLoading(true);

      const payload = {
        full_name: fullName.trim(),
        email: email.trim().toLowerCase(),
        company_name: companyName.trim() || null,
        phone: phone.trim() || null,
        billing_address: billingAddress.trim() || null,
        tax_id: taxId.trim() || null,
        currency: currency.trim().toUpperCase(),
        is_active: isActive,
      };

      let result;
      if (isEdit) {
        result = await customerService.updateCustomer(customer.id, payload);
      } else {
        result = await customerService.createCustomer(payload);
      }

      setLoading(false);
      onSuccess(result, isEdit ? 'updated' : 'created');
      onClose();
    } catch (err) {
      setLoading(false);
      setError(err.message || 'Failed to save customer record. Please check inputs.');
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-container"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '600px' }}
      >
        <div className="modal-header">
          <div>
            <div className="status-pill online" style={{ marginBottom: '0.4rem', fontSize: '0.75rem' }}>
              <span className="status-dot"></span>
              {isEdit ? 'Update Customer Profile' : 'New Customer Registration'}
            </div>
            <h2 className="modal-title">
              {isEdit ? `Edit Customer: ${customer.full_name}` : 'Add New Billing Customer'}
            </h2>
            <p className="modal-subtitle">
              {isEdit
                ? `Customer Code: ${customer.customer_code} · Update contact, company, or billing address.`
                : 'Register an individual or enterprise subscriber account for recurring billing.'}
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
              <label className="form-label" htmlFor="cust-name">
                Full Name <span className="required-mark">*</span>
              </label>
              <input
                id="cust-name"
                type="text"
                className="form-input"
                placeholder="e.g. Maya Sharma, Rohan Verma"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
                minLength={2}
                maxLength={150}
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="cust-email">
                Email Address <span className="required-mark">*</span>
              </label>
              <input
                id="cust-email"
                type="email"
                className="form-input"
                placeholder="e.g. maya@acme.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                maxLength={255}
              />
            </div>
          </div>

          <div className="form-grid-2">
            <div className="form-group">
              <label className="form-label" htmlFor="cust-company">
                Company Name
              </label>
              <input
                id="cust-company"
                type="text"
                className="form-input"
                placeholder="e.g. Acme Technologies Inc."
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                maxLength={150}
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="cust-phone">
                Phone Number
              </label>
              <input
                id="cust-phone"
                type="tel"
                className="form-input"
                placeholder="e.g. +1-555-0199"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                maxLength={30}
              />
            </div>
          </div>

          <div className="form-grid-2">
            <div className="form-group">
              <label className="form-label" htmlFor="cust-currency">
                Billing Currency <span className="required-mark">*</span>
              </label>
              <select
                id="cust-currency"
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
              <label className="form-label" htmlFor="cust-tax-id">
                Tax ID / GST / VAT Number
              </label>
              <input
                id="cust-tax-id"
                type="text"
                className="form-input mono-input"
                placeholder="e.g. US-123456789, 27AAAAA0000A1Z5"
                value={taxId}
                onChange={(e) => setTaxId(e.target.value)}
                maxLength={50}
              />
            </div>
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="cust-address">
              Billing Address
            </label>
            <textarea
              id="cust-address"
              className="form-input form-textarea"
              rows={2}
              placeholder="e.g. 100 Innovation Way, Suite 400, San Francisco, CA 94105"
              value={billingAddress}
              onChange={(e) => setBillingAddress(e.target.value)}
              maxLength={1000}
            />
          </div>

          <div className="form-group">
            <label className="checkbox-label" style={{ marginTop: '0.25rem' }}>
              <input
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
              />
              <span>Account Active (Eligible for subscription billing)</span>
            </label>
          </div>

          <div className="modal-actions" style={{ marginTop: '1.5rem' }}>
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
              {loading
                ? isEdit
                  ? 'Saving Changes...'
                  : 'Registering Customer...'
                : isEdit
                ? 'Save Changes'
                : 'Add Customer'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
