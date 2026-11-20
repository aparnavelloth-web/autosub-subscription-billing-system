import React from 'react';

export default function Navbar({
  user,
  currentView,
  onNavigate,
  onOpenLogin,
  onOpenSignup,
  onLogout,
}) {
  return (
    <nav className="navbar">
      <div className="navbar-inner">
        <div className="brand" onClick={() => onNavigate(user ? 'dashboard' : 'landing')} style={{ cursor: 'pointer' }}>
          <div className="brand-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <rect width="20" height="14" x="2" y="5" rx="2" />
              <line x1="2" x2="22" y1="10" y2="10" />
            </svg>
          </div>
          <span>AutoSub</span>
        </div>

        <div className="nav-links">
          {user ? (
            <>
              <button
                className={`nav-link-btn ${currentView === 'dashboard' ? 'active' : ''}`}
                onClick={() => onNavigate('dashboard')}
              >
                Dashboard
              </button>
              <button
                className={`nav-link-btn ${currentView === 'landing' ? 'active' : ''}`}
                onClick={() => onNavigate('landing')}
              >
                Home Overview
              </button>
            </>
          ) : (
            <>
              <a href="#features" onClick={() => onNavigate('landing')}>Features</a>
              <a href="#architecture" onClick={() => onNavigate('landing')}>Architecture</a>
            </>
          )}

          {user ? (
            <div className="user-profile-menu">
              <div className="user-badge" title={user.email}>
                <div className="user-avatar">
                  {user.full_name ? user.full_name.charAt(0).toUpperCase() : 'U'}
                </div>
                <span className="user-name">{user.full_name}</span>
              </div>
              <button className="btn-secondary" style={{ padding: '0.4rem 0.85rem', fontSize: '0.85rem' }} onClick={onLogout}>
                Sign Out
              </button>
            </div>
          ) : (
            <div className="auth-buttons-group">
              <button className="btn-secondary" style={{ padding: '0.45rem 1rem', fontSize: '0.875rem' }} onClick={onOpenLogin}>
                Sign In
              </button>
              <button className="btn-primary" style={{ padding: '0.45rem 1rem', fontSize: '0.875rem' }} onClick={onOpenSignup}>
                Get Started
              </button>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}
