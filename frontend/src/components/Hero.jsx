import React from 'react';

export default function Hero({
  user,
  onGetStarted,
  onAccessWorkspace,
}) {
  return (
    <section className="hero-section">
      <div className="hero-badge">
        <span style={{ fontSize: '1.1em' }}>⚡</span> Automated Recurring Billing & Revenue Engine
      </div>

      <h1 className="hero-title">
        Subscription Billing Automation <br />
        <span className="gradient-text">Recurring Revenue Engine</span>
      </h1>

      <p className="hero-description">
        Enterprise-grade recurring billing lifecycle automation powered by modern backend services, 
        PostgreSQL persistence, scheduled renewal tasks, and real-time React analytics. Built for deterministic, 
        fault-tolerant subscription operations with secure JWT authentication.
      </p>

      <div className="hero-actions">
        {user ? (
          <>
            <button className="btn-primary" onClick={onAccessWorkspace}>
              <span>Access Workspace</span>
              <span>→</span>
            </button>
            <a href="#features" className="btn-secondary">
              <span>Explore Features</span>
            </a>
          </>
        ) : (
          <>
            <button className="btn-primary" onClick={onGetStarted}>
              <span>Get Started</span>
              <span>→</span>
            </button>
            <button className="btn-secondary" onClick={onAccessWorkspace}>
              <span>Access Workspace</span>
              <span>→</span>
            </button>
            <a href="#features" className="btn-secondary">
              <span>Explore Features</span>
            </a>
          </>
        )}
      </div>
    </section>
  );
}
