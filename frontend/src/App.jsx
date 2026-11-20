import React, { useState, useEffect } from 'react';
import Navbar from './components/Navbar';
import Hero from './components/Hero';
import HealthStatus from './components/HealthStatus';
import Features from './components/Features';
import ArchitecturePreview from './components/ArchitecturePreview';
import Footer from './components/Footer';
import LoginModal from './components/LoginModal';
import SignupModal from './components/SignupModal';
import Dashboard from './components/Dashboard';
import { authService } from './services/auth';
import './App.css';

export default function App() {
  const [user, setUser] = useState(() => authService.getUser());
  const [currentView, setCurrentView] = useState(() => (authService.isAuthenticated() ? 'dashboard' : 'landing'));
  const [isLoginOpen, setIsLoginOpen] = useState(false);
  const [isSignupOpen, setIsSignupOpen] = useState(false);
  const [authNotice, setAuthNotice] = useState('');
  const [showDevConsole, setShowDevConsole] = useState(false);

  // Validate session against backend on load
  useEffect(() => {
    if (authService.isAuthenticated()) {
      authService.getMe()
        .then((freshUser) => {
          setUser(freshUser);
        })
        .catch(() => {
          setUser(null);
          setCurrentView('landing');
        });
    }
  }, []);

  const handleNavigate = (targetView) => {
    setAuthNotice('');
    if (targetView === 'dashboard') {
      if (!authService.isAuthenticated()) {
        setAuthNotice('Please sign in to access your protected dashboard.');
        setIsLoginOpen(true);
        return;
      }
      setCurrentView('dashboard');
    } else {
      setCurrentView('landing');
    }
  };

  const handleLoginSuccess = (authenticatedUser) => {
    setUser(authenticatedUser);
    setCurrentView('dashboard');
    setAuthNotice('');
  };

  const handleSignupSuccess = (newUser) => {
    setUser(newUser);
    setCurrentView('dashboard');
    setAuthNotice('');
  };

  const handleLogout = () => {
    authService.clearSession();
    setUser(null);
    setCurrentView('landing');
    setAuthNotice('');
  };

  return (
    <div className="app-container">
      <Navbar
        user={user}
        currentView={currentView}
        onNavigate={handleNavigate}
        onOpenLogin={() => {
          setAuthNotice('');
          setIsLoginOpen(true);
        }}
        onOpenSignup={() => {
          setAuthNotice('');
          setIsSignupOpen(true);
        }}
        onLogout={handleLogout}
      />

      <main className="content-wrapper">
        {authNotice && (
          <div className="auth-alert error" style={{ margin: '1.5rem 0' }}>
            <span>⚠️</span>
            <span>{authNotice}</span>
          </div>
        )}

        {currentView === 'dashboard' && user ? (
          <Dashboard user={user} onLogout={handleLogout} />
        ) : (
          <>
            <Hero
              user={user}
              onGetStarted={() => {
                setAuthNotice('');
                setIsSignupOpen(true);
              }}
              onAccessWorkspace={() => handleNavigate('dashboard')}
            />
            <Features />
            <ArchitecturePreview />

            {/* Separate Developer-Only Section */}
            <section className="developer-only-section">
              <div className="developer-section-header">
                <button
                  type="button"
                  className="developer-toggle-btn"
                  onClick={() => setShowDevConsole(!showDevConsole)}
                >
                  <span>⚙️ Developer Tools & API Telemetry</span>
                  <span className="toggle-badge">{showDevConsole ? '▲ Collapse' : '▼ View Diagnostics'}</span>
                </button>
              </div>

              {showDevConsole && (
                <div className="developer-section-body">
                  <div className="developer-links-bar">
                    <span className="dev-links-label">Developer Interfaces:</span>
                    <a href="/api/v1/docs" target="_blank" rel="noreferrer" className="dev-link">
                      OpenAPI Specification (Swagger) ↗
                    </a>
                    <a href="/api/v1/health" target="_blank" rel="noreferrer" className="dev-link">
                      Service Health Status (JSON) ↗
                    </a>
                  </div>
                  <HealthStatus />
                </div>
              )}
            </section>
          </>
        )}
      </main>

      <Footer />

      {/* Auth Modals */}
      <LoginModal
        isOpen={isLoginOpen}
        onClose={() => setIsLoginOpen(false)}
        onSwitchToSignup={() => {
          setIsLoginOpen(false);
          setIsSignupOpen(true);
        }}
        onLoginSuccess={handleLoginSuccess}
      />

      <SignupModal
        isOpen={isSignupOpen}
        onClose={() => setIsSignupOpen(false)}
        onSwitchToLogin={() => {
          setIsSignupOpen(false);
          setIsLoginOpen(true);
        }}
        onSignupSuccess={handleSignupSuccess}
      />
    </div>
  );
}
