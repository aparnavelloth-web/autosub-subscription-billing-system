import React from 'react';

const features = [
  {
    title: 'Recurring Billing Engine',
    desc: 'Automates subscription cycles, renewal invoices, and trial periods with predictable billing dates and calendar adjustments.',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.3"/>
      </svg>
    ),
  },
  {
    title: 'Flexible Plan Architecture',
    desc: 'Supports tiered, flat-rate, usage-based, and freemium subscription models with fine-grained feature entitlement management.',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect width="18" height="18" x="3" y="3" rx="2"/><path d="m9 12 2 2 4-4"/>
      </svg>
    ),
  },
  {
    title: 'Automated Invoice Generation',
    desc: 'Generates compliant, itemized PDF tax invoices with dynamic payment links, downloadable customer receipts, and line-item breakdowns.',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" x2="8" y1="13" y2="13"/><line x1="16" x2="8" y1="17" y2="17"/>
      </svg>
    ),
  },
  {
    title: 'Revenue & Churn Analytics',
    desc: 'Tracks key SaaS financial metrics including Monthly Recurring Revenue (MRR), Annual Run Rate (ARR), customer churn, and cohort retention.',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" x2="18" y1="20" y2="10"/><line x1="12" x2="12" y1="20" y2="4"/><line x1="6" x2="6" y1="20" y2="14"/>
      </svg>
    ),
  },
];

export default function Features() {
  return (
    <section id="features" className="features-section">
      <div className="section-header">
        <h2 className="section-title">Core System Capabilities</h2>
        <p className="section-subtitle">
          Designed to automate end-to-end subscription lifecycle operations
        </p>
      </div>

      <div className="features-grid">
        {features.map((f, i) => (
          <div key={i} className="feature-card">
            <div className="feature-icon-wrapper">{f.icon}</div>
            <h4 className="feature-title">{f.title}</h4>
            <p className="feature-description">{f.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

