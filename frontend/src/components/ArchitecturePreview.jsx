import React from 'react';

const pipeline = [
  {
    step: '01',
    title: 'Customer Onboarding',
    desc: 'Customer registers, selects a tiered subscription plan, and configures payment authorization.',
  },
  {
    step: '02',
    title: 'Cycle Scheduling',
    desc: 'Celery Beat dispatches recurrent billing tasks based on active subscription renewal epochs.',
  },
  {
    step: '03',
    title: 'Invoice & Payment',
    desc: 'FastAPI generates invoice records in PostgreSQL and invokes payment gateway transactions.',
  },
  {
    step: '04',
    title: 'PDF & Notification',
    desc: 'ReportLab renders downloadable PDF statements dispatched with renewal status updates.',
  },
];

export default function ArchitecturePreview() {
  return (
    <section id="architecture" className="architecture-section">
      <div className="section-header" style={{ textAlign: 'left', marginBottom: '1rem' }}>
        <h3 className="section-title">Automated Recurring Workflow</h3>
        <p className="section-subtitle">
          End-to-end transaction pipeline from subscription activation to automated reconciliation
        </p>
      </div>

      <div className="pipeline-steps">
        {pipeline.map((p, i) => (
          <div key={i} className="step-card">
            <div className="step-number">{p.step} // PIPELINE</div>
            <div className="step-title">{p.title}</div>
            <div className="step-desc">{p.desc}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

