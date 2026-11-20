import React, { useState, useEffect } from 'react';
import { subscriptionService } from '../services/subscriptions';

export default function SubscriptionHistoryModal({ subscription, onClose }) {
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchHistory = async () => {
      try {
        setLoading(true);
        setError('');
        const data = await subscriptionService.getSubscriptionHistory(subscription.id);
        setTimeline(data);
      } catch (err) {
        setError(err.message || 'Failed to fetch subscription history.');
      } finally {
        setLoading(false);
      }
    };

    if (subscription?.id) {
      fetchHistory();
    }
  }, [subscription?.id]);

  const getEventBadgeClass = (eventType) => {
    switch (eventType) {
      case 'plan_changed':
        return 'badge-plan-change';
      case 'proration_invoice':
        return 'badge-proration-inv';
      case 'invoice_generated':
        return 'badge-invoice';
      case 'status_changed':
        return 'badge-status';
      case 'paused':
      case 'resumed':
        return 'badge-lifecycle';
      default:
        return 'badge-default';
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-container history-modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '680px' }}
      >
        <div className="modal-header">
          <div>
            <div className="status-pill online" style={{ marginBottom: '0.4rem', fontSize: '0.75rem' }}>
              <span className="status-dot"></span>
              Live Database Audit Trail & Invoices
            </div>
            <h2 className="modal-title">Subscription Lifecycle History</h2>
            <p className="modal-subtitle">
              Subscription <span className="mono-code">{subscription?.subscription_code}</span> · {subscription?.status?.toUpperCase()}
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

        {loading ? (
          <div className="table-loading" style={{ padding: '3rem 1rem' }}>
            <div className="spinner"></div>
            <p>Loading chronological subscription events...</p>
          </div>
        ) : timeline.length === 0 ? (
          <div className="empty-state" style={{ padding: '3rem 1rem' }}>
            <p>No history records found for this subscription.</p>
          </div>
        ) : (
          <div className="history-timeline-container">
            <div className="timeline-track">
              {timeline.map((event, idx) => (
                <div key={event.id || idx} className="timeline-item">
                  <div className="timeline-dot-wrapper">
                    <div className="timeline-dot"></div>
                    {idx < timeline.length - 1 && <div className="timeline-line"></div>}
                  </div>

                  <div className="timeline-content">
                    <div className="timeline-header-row">
                      <div className="timeline-title-group">
                        <span className={`timeline-badge ${getEventBadgeClass(event.event_type)}`}>
                          {event.event_type?.replace('_', ' ').toUpperCase()}
                        </span>
                        <h4 className="timeline-event-title">{event.title}</h4>
                      </div>
                      <span className="timeline-timestamp">
                        {new Date(event.timestamp).toLocaleString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          year: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    </div>

                    <p className="timeline-desc">{event.description}</p>

                    {(event.amount !== null && event.amount !== undefined) || event.invoice_number ? (
                      <div className="timeline-meta-bar">
                        {event.amount !== null && event.amount !== undefined && (
                          <div className="meta-item">
                            <span className="meta-k">Amount:</span>
                            <span className="meta-v">
                              {event.currency} {event.amount}
                            </span>
                          </div>
                        )}
                        {event.invoice_number && (
                          <div className="meta-item">
                            <span className="meta-k">Invoice Ref:</span>
                            <span className="meta-v highlight">{event.invoice_number}</span>
                          </div>
                        )}
                        {event.details?.status && (
                          <div className="meta-item">
                            <span className="meta-k">Status:</span>
                            <span className="meta-v">{event.details.status.toUpperCase()}</span>
                          </div>
                        )}
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="modal-actions" style={{ marginTop: '1.5rem', justifyContent: 'flex-end' }}>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close History
          </button>
        </div>
      </div>
    </div>
  );
}
