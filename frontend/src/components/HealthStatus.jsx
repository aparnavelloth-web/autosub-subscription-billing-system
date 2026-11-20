import React, { useState, useEffect } from 'react';

export default function HealthStatus() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastChecked, setLastChecked] = useState(null);

  const fetchHealth = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/health');

      if (!response.ok) {
        throw new Error(`Server returned HTTP ${response.status}`);
      }
      const data = await response.json();
      setHealth(data);
      setError(null);
      setLastChecked(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err.message || 'Backend is currently offline or unreachable');
      setHealth(null);
      setLastChecked(new Date().toLocaleTimeString());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, 10000);
    return () => clearInterval(interval);
  }, []);

  const isOnline = health && health.status === 'healthy';

  return (
    <div id="health-status" className="health-panel">
      <div className="health-panel-header">
        <div>
          <h3 style={{ fontSize: '1.15rem', fontWeight: 600 }}>Backend Gateway & Database Telemetry</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
            Real-time ping from <code>FastAPI /api/v1/health</code>
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div className={`status-pill ${isOnline ? 'online' : 'offline'}`}>
            <span className="status-dot"></span>
            <span>{isOnline ? 'Core API Online' : 'Backend Offline'}</span>
          </div>
          <button 
            onClick={fetchHealth}
            style={{ 
              padding: '0.3rem 0.6rem', 
              background: 'rgba(255,255,255,0.05)', 
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-secondary)',
              fontSize: '0.8rem'
            }}
            title="Refresh Health"
          >
            {loading ? '⟳ Refreshing...' : '↻ Refresh'}
          </button>
        </div>
      </div>

      <div className="health-grid">
        <div className="health-item">
          <div className="health-label">API Service</div>
          <div className="health-val">{health?.app_name || (loading ? 'Probing...' : 'FastAPI Service')}</div>
        </div>

        <div className="health-item">
          <div className="health-label">Environment</div>
          <div className="health-val">{health?.environment || 'development'}</div>
        </div>

        <div className="health-item">
          <div className="health-label">Version</div>
          <div className="health-val">v{health?.version || '0.1.0'}</div>
        </div>

        <div className="health-item">
          <div className="health-label">PostgreSQL Database</div>
          <div className="health-val" style={{ 
            color: health?.database?.status === 'connected' ? '#34d399' : '#f59e0b' 
          }}>
            {health?.database?.status === 'connected' 
              ? `Connected (${health.database.dialect})` 
              : health?.database?.status === 'disconnected'
                ? `Ready / Awaiting DB (${health.database.dialect})`
                : 'Service Unavailable'}
          </div>
        </div>
      </div>

      {error && (
        <div style={{
          marginTop: '1rem',
          padding: '0.75rem 1rem',
          background: 'rgba(239, 68, 68, 0.08)',
          border: '1px solid rgba(239, 68, 68, 0.2)',
          borderRadius: 'var(--radius-sm)',
          fontSize: '0.85rem',
          color: '#fca5a5'
        }}>
          <strong>Notice:</strong> {error}. Start the backend server with <code>python -m uvicorn app.main:app --reload</code> to connect.
        </div>
      )}

      {lastChecked && (
        <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          Last probe: {lastChecked}
        </div>
      )}
    </div>
  );
}

