import React from 'react';

export default function Footer() {
  return (
    <footer className="footer">
      <div className="footer-content">
        <div className="footer-badge">
          AutoSub Billing Platform
        </div>
        <div>
          <strong>Subscription Billing Automation System</strong>
        </div>
        <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
          Backend: Python &bull; FastAPI &bull; SQLAlchemy &bull; PostgreSQL &bull; Pydantic | Frontend: React &bull; Vite &bull; JavaScript &bull; CSS
        </div>
      </div>
    </footer>
  );
}

