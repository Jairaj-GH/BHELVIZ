import React, { useState } from 'react';

export default function RightSidebar({ ir, qHistory }) {
  const [open, setOpen] = useState(true);

  if (!open) return (
    <aside style={{ width: 40, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: 24 }}>
      <button
        onClick={() => setOpen(true)}
        aria-label="Open context panel"
        style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-faint)', padding: 4 }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
      </button>
    </aside>
  );

  return (
    <aside className="fc-theme-right" aria-label="Context panel">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className="sidebar-section-label">Context</span>
        <button
          onClick={() => setOpen(false)}
          aria-label="Close context panel"
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-faint)', lineHeight: 1 }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <polyline points="9 18 15 12 9 6"/>
          </svg>
        </button>
      </div>

      <div className="context-card">
        <div className="context-meta" style={{ marginBottom: 6 }}>IR Intent</div>
        <div className="context-intent">{ir?.intent || '—'}</div>
        {ir?.chart_type && (
          <div style={{ marginTop: 10, display: 'flex', gap: 6, alignItems: 'center' }}>
            <span className="badge badge-forest" style={{ fontSize: 9 }}>{ir.chart_type.toUpperCase()}</span>
            {ir?.group_by_field && ir.group_by_field !== 'null' && (
              <span className="badge badge-gold" style={{ fontSize: 9 }}>BY {ir.group_by_field.toUpperCase()}</span>
            )}
          </div>
        )}
      </div>

      {/* System status */}
      <div>
        <div className="sidebar-section-label">System</div>
        {[
          { label: 'Oracle 19c', ok: true },
          { label: 'AES-256-GCM', ok: true },
          { label: 'TDE Active', ok: true },
          { label: 'Read-Only', ok: true },
        ].map(s => (
          <div key={s.label} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '7px 0', borderBottom: '1px solid var(--border-light)',
          }}>
            <span style={{ fontSize: 11, color: 'var(--ink-muted)' }}>{s.label}</span>
            <span style={{
              fontSize: 9, fontFamily: 'var(--ff-mono)', fontWeight: 500,
              color: s.ok ? 'var(--status-present)' : 'var(--status-absent)',
              letterSpacing: '0.06em',
            }}>
              {s.ok ? '● ON' : '● OFF'}
            </span>
          </div>
        ))}
      </div>

      {/* Recent queries */}
      <div>
        <div className="sidebar-section-label">Recent queries</div>
        {qHistory && qHistory.length > 0 ? (
          qHistory.slice(0, 6).map((h, i) => (
            <div key={i} className="query-history-item">
              <span className="query-text">{h.q}</span>
              <span className="query-time">{h.ts}</span>
            </div>
          ))
        ) : (
          <div className="small-muted" style={{ fontStyle: 'italic', padding: '8px 0' }}>
            No queries yet
          </div>
        )}
      </div>
    </aside>
  );
}