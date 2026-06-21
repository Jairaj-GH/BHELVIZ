import React, { useState } from 'react';

export default function RightSidebar({ ir, qHistory }){
  const [open, setOpen] = useState(true);
  if(!open) return (
    <aside style={{width:44,display:'flex',alignItems:'center',justifyContent:'center'}}>
      <button aria-label="Open context" onClick={()=>setOpen(true)} className="focus-outline">▶</button>
    </aside>
  );

  return (
    <aside className="fc-theme-right glass" aria-label="Context sidebar">
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
        <div style={{fontWeight:700}}>Context</div>
        <button onClick={()=>setOpen(false)} aria-label="Close context" className="focus-outline">✕</button>
      </div>
      <div className="fc-card glass" style={{marginTop:10}}>
        <div className="small-muted" style={{marginBottom:6}}>IR Intent</div>
        <div style={{fontWeight:600}}>{ir?.intent || '—'}</div>
        <div className="small-muted" style={{marginTop:8}}>Chart type: {ir?.chart_type || 'table'}</div>
      </div>

      <div style={{height:12}} />

      <div className="fc-card glass">
        <div style={{fontWeight:700}}>Recent Queries</div>
        <div style={{marginTop:8,display:'flex',flexDirection:'column',gap:8}}>
          {qHistory && qHistory.length>0 ? qHistory.slice(0,6).map((h,i)=> (
            <div key={i} className="small-muted" style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
              <div style={{maxWidth:140,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{h.q}</div>
              <div style={{fontSize:11,color:'var(--muted-text)'}}>{h.ts}</div>
            </div>
          )) : <div className="small-muted">No recent queries</div>}
        </div>
      </div>
    </aside>
  );
}
