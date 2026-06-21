import React from 'react';

function Icon({ name }){
  // small set of inline icons
  if(name==='dashboard') return (<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 13h8V3H3v10zM13 21h8V11h-8v10zM13 3v6h8V3h-8zM3 21h8v-6H3v6z"/></svg>);
  if(name==='analytics') return (<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 3v18h18"/><rect x="7" y="7" width="3" height="10" rx="1"/><rect x="12" y="4" width="3" height="13" rx="1"/><rect x="17" y="10" width="3" height="7" rx="1"/></svg>);
  if(name==='reports') return (<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 7h18M3 12h18M3 17h18"/></svg>);
  if(name==='settings') return (<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 15.5A3.5 3.5 0 1 0 12 8.5a3.5 3.5 0 0 0 0 7z"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06A2 2 0 0 1 2.3 16.88l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09c.67 0 1.22-.41 1.51-1a1.65 1.65 0 0 0-.33-1.82L4.3 2.3A2 2 0 0 1 7.13.47l.06.06c.5.5 1.02.74 1.82.33.79-.4 1.51-1 1.51-1V3a2 2 0 0 1 4 0v.09c0 .67.41 1.22 1 1.51.8.41 1.32.17 1.82-.33l.06-.06A2 2 0 0 1 21.7 7.13l-.06.06c-.4.79-.74 1.02-.33 1.82.3.79 1 1.51 1 1.51H21a2 2 0 0 1 0 4h-.09c-.67 0-1.22.41-1.51 1z"/></svg>);
  return null;
}

export default function Sidebar({ onNav }){
  return (
    <aside className="fc-theme-sidebar" aria-label="Main navigation">
      <div>
        <div className="fc-theme-logo">BHELVIZ</div>
        <div className="small-muted">Secure AI · Read‑Only</div>
      </div>
      <nav className="fc-theme-nav" role="navigation" aria-label="Sidebar">
        <button onClick={() => onNav && onNav('dashboard')} className="focus-outline" aria-label="Dashboard"><Icon name="dashboard" />&nbsp; Dashboard</button>
        <button onClick={() => onNav && onNav('analytics')} className="focus-outline" aria-label="Analytics"><Icon name="analytics" />&nbsp; Analytics</button>
        <button onClick={() => onNav && onNav('reports')} className="focus-outline" aria-label="Reports"><Icon name="reports" />&nbsp; Reports</button>
        <button onClick={() => onNav && onNav('settings')} className="focus-outline" aria-label="Settings"><Icon name="settings" />&nbsp; Settings</button>
      </nav>
      <div style={{marginTop:12}}>
        <button onClick={() => onNav && onNav('new_chat')} className="send-btn" aria-label="New chat">+ New Chat</button>
      </div>
      <div className="fc-theme-profile">Signed in as <strong>demo@bhel.in</strong></div>
    </aside>
  );
}
