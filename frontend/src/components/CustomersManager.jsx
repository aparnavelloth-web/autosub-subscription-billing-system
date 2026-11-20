import React, { useState, useEffect, useCallback } from 'react';
import { customerService } from '../services/customers';
import CustomerModal from './CustomerModal';
import CustomerDetailModal from './CustomerDetailModal';

export default function CustomersManager() {
  const [customers, setCustomers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [successNotice, setSuccessNotice] = useState('');

  // Pagination & Filters
  const [page, setPage] = useState(1);
  const [pageSize] = useState(25);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all'); // 'all' | 'active' | 'inactive'
  const [includeDeleted, setIncludeDeleted] = useState(true);

  // Modal states
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedCustomerForEdit, setSelectedCustomerForEdit] = useState(null);
  const [viewingCustomer, setViewingCustomer] = useState(null);

  const fetchCustomers = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const data = await customerService.listCustomers({
        page,
        size: pageSize,
        include_deleted: includeDeleted,
        active_only: statusFilter === 'active',
      });
      setCustomers(data.items || []);
      setTotalPages(data.pages || 1);
      setTotalCount(data.total || 0);
    } catch (err) {
      setError(err.message || 'Failed to load customer directory.');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, includeDeleted, statusFilter]);

  useEffect(() => {
    fetchCustomers();
  }, [fetchCustomers]);

  const showSuccess = (msg) => {
    setSuccessNotice(msg);
    setTimeout(() => {
      setSuccessNotice('');
    }, 5000);
  };

  const handleOpenCreateModal = () => {
    setSelectedCustomerForEdit(null);
    setIsModalOpen(true);
  };

  const handleOpenEditModal = (cust) => {
    setSelectedCustomerForEdit(cust);
    setIsModalOpen(true);
  };

  const handleModalSuccess = (savedCust, actionType) => {
    showSuccess(
      actionType === 'created'
        ? `Customer '${savedCust.full_name}' (${savedCust.customer_code}) created successfully.`
        : `Customer '${savedCust.full_name}' updated successfully.`
    );
    fetchCustomers();
  };

  // Dashboard calculations
  const totalCustomersCount = totalCount;
  const activeCustomersCount = customers.filter((c) => c.is_active && !c.deleted_at).length;
  const thirtyDaysAgo = new Date();
  thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
  const recentCustomersCount = customers.filter(
    (c) => c.created_at && new Date(c.created_at) >= thirtyDaysAgo
  ).length;

  // Search filtering
  const filteredCustomers = customers.filter((c) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase().trim();
    return (
      c.full_name?.toLowerCase().includes(q) ||
      c.email?.toLowerCase().includes(q) ||
      c.company_name?.toLowerCase().includes(q) ||
      c.customer_code?.toLowerCase().includes(q)
    );
  });

  return (
    <div className="invoices-manager-container">
      {/* Header */}
      <div className="invoices-header">
        <div>
          <div className="status-pill online" style={{ marginBottom: '0.5rem' }}>
            <span className="status-dot"></span>
            Customer Directory & Profiles
          </div>
          <h1 className="invoices-title">Customers Management</h1>
          <p className="invoices-subtitle">
            Maintain subscriber organization accounts, contact details, currency preferences, and inspect associated active subscriptions and invoices.
          </p>
        </div>

        <div className="invoices-header-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={handleOpenCreateModal}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', fontWeight: 600 }}
          >
            <span>+</span> Add New Customer
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={fetchCustomers}
            disabled={loading}
            title="Refresh customer directory"
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

      {/* 1. Customer Dashboard Stats Overview */}
      <div className="dashboard-grid" style={{ marginBottom: '0.5rem' }}>
        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-tag">TOTAL SUBSCRIBERS</span>
          </div>
          <h3 className="dash-card-value">{totalCustomersCount}</h3>
          <p className="dash-card-desc">Total customer records registered in database</p>
          <div className="meta-row">
            <span className="meta-label">Total in Page:</span>
            <span className="meta-val">{customers.length}</span>
          </div>
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-tag">ACTIVE & BILLABLE</span>
          </div>
          <h3 className="dash-card-value" style={{ color: '#10b981' }}>{activeCustomersCount}</h3>
          <p className="dash-card-desc">Customers eligible for new subscriptions & invoices</p>
          <div className="meta-row">
            <span className="meta-label">Active Ratio:</span>
            <span className="meta-badge active">
              {customers.length > 0
                ? `${Math.round((activeCustomersCount / customers.length) * 100)}%`
                : '100%'}
            </span>
          </div>
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-tag">RECENTLY ADDED</span>
          </div>
          <h3 className="dash-card-value" style={{ color: '#60a5fa' }}>{recentCustomersCount}</h3>
          <p className="dash-card-desc">New accounts registered in the last 30 days</p>
          <div className="meta-row">
            <span className="meta-label">Onboarding Velocity:</span>
            <span className="meta-val">Healthy</span>
          </div>
        </div>
      </div>

      {/* Controls & Filter Toolbar */}
      <div className="plans-controls-bar">
        {/* Status Filter Buttons */}
        <div className="subscription-status-tabs">
          {[
            { id: 'all', label: 'ALL CUSTOMERS' },
            { id: 'active', label: 'ACTIVE' },
            { id: 'inactive', label: 'INACTIVE / DELETED' },
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
              <span className={`filter-status-indicator indicator-${tab.id === 'inactive' ? 'cancelled' : tab.id}`}></span>
              {tab.label}
            </button>
          ))}
        </div>

        {/* Right Search & Controls */}
        <div className="plans-controls-right">
          <div className="search-input-wrapper" style={{ minWidth: '260px' }}>
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
              placeholder="Search by name, email, or company..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <label className="checkbox-label" style={{ fontSize: '0.8rem', userSelect: 'none', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(e) => {
                setIncludeDeleted(e.target.checked);
                setPage(1);
              }}
            />
            <span>Show Deleted</span>
          </label>
        </div>
      </div>

      {/* 2. Customer List Table */}
      <div className="table-card">
        {loading ? (
          <div className="table-loading" style={{ padding: '4rem 1rem', textAlign: 'center' }}>
            <div className="spinner" style={{ margin: '0 auto 1rem' }}></div>
            <p style={{ color: 'var(--text-secondary)' }}>Loading customer directory...</p>
          </div>
        ) : filteredCustomers.length === 0 ? (
          <div className="empty-state" style={{ padding: '4rem 2rem' }}>
            <div className="empty-icon" style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>👥</div>
            <h3>No Customer Records Found</h3>
            <p style={{ maxWidth: '440px', margin: '0.5rem auto 1.5rem', color: 'var(--text-secondary)' }}>
              {searchQuery
                ? `No customers match your search query "${searchQuery}". Clear your search or adjust status filters.`
                : 'There are currently no customers registered in your database. Add your first customer to get started.'}
            </p>
            <button
              type="button"
              className="btn-primary"
              onClick={handleOpenCreateModal}
            >
              + Add First Customer
            </button>
          </div>
        ) : (
          <div className="table-responsive">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Customer Code</th>
                  <th>Customer Name</th>
                  <th>Email Address</th>
                  <th>Company</th>
                  <th>Currency</th>
                  <th>Created Date</th>
                  <th>Status</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredCustomers.map((cust) => {
                  const isDeleted = Boolean(cust.deleted_at);
                  return (
                    <tr key={cust.id} className={isDeleted ? 'row-archived' : ''}>
                      <td>
                        <span className="mono-code" style={{ fontSize: '0.78rem' }}>{cust.customer_code}</span>
                      </td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                          <div className="customer-avatar-small">
                            {cust.full_name?.charAt(0).toUpperCase() || 'C'}
                          </div>
                          <div>
                            <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                              {cust.full_name}
                            </div>
                            {cust.phone && (
                              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                                {cust.phone}
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                      <td>
                        <span style={{ color: 'var(--text-primary)', fontSize: '0.85rem' }}>{cust.email}</span>
                      </td>
                      <td>
                        {cust.company_name ? (
                          <span style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                            {cust.company_name}
                          </span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>—</span>
                        )}
                      </td>
                      <td>
                        <span className="plan-badge-tag">{cust.currency}</span>
                      </td>
                      <td>
                        <span style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                          {cust.created_at ? new Date(cust.created_at).toLocaleDateString() : '—'}
                        </span>
                      </td>
                      <td>
                        {isDeleted ? (
                          <span className="status-badge status-cancelled">Soft-Deleted</span>
                        ) : cust.is_active ? (
                          <span className="status-badge status-active">Active</span>
                        ) : (
                          <span className="status-badge status-past_due">Inactive</span>
                        )}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '0.4rem' }}>
                          <button
                            type="button"
                            className="btn-primary table-btn"
                            style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}
                            onClick={() => setViewingCustomer(cust)}
                            title="View customer profile, subscriptions, and invoices"
                          >
                            View Profile
                          </button>
                          {!isDeleted && (
                            <button
                              type="button"
                              className="btn-secondary table-btn"
                              style={{ padding: '0.35rem 0.65rem', fontSize: '0.8rem' }}
                              onClick={() => handleOpenEditModal(cust)}
                              title="Edit customer details"
                            >
                              Edit
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
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="table-pagination">
            <span className="pagination-info">
              Page {page} of {totalPages} ({totalCount} total customers)
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

      {/* Customer Create / Edit Modal */}
      {isModalOpen && (
        <CustomerModal
          isOpen={isModalOpen}
          customer={selectedCustomerForEdit}
          onClose={() => setIsModalOpen(false)}
          onSuccess={handleModalSuccess}
        />
      )}

      {/* Customer Detail & Profiles Modal */}
      {viewingCustomer && (
        <CustomerDetailModal
          customer={viewingCustomer}
          onClose={() => setViewingCustomer(null)}
          onEditCustomer={(cust) => {
            setViewingCustomer(null);
            handleOpenEditModal(cust);
          }}
        />
      )}
    </div>
  );
}
