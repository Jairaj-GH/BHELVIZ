import React, { useRef, useEffect, useState, useMemo } from 'react';
import {
  BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, AreaChart, Area,
} from 'recharts';

/* ── PALETTES ────────────────────────────────────────────────────── */
const CHART_PALETTE = ['#3A5E47','#C9A84C','#567A62','#E2C97E','#1A3028','#8B6E2E','#6B9E7A','#D6CBAF'];
const STATUS_COLORS = {
  PRESENT:'#3B7A52', ABSENT:'#A63228', LATE:'#B07020', FALSE_PRESENT:'#6B42A0',
  APPROVED:'#3B7A52', PENDING:'#B07020',
};
const STATUS_BG = {
  PRESENT:'rgba(59,122,82,0.1)', ABSENT:'rgba(166,50,40,0.1)',
  LATE:'rgba(176,112,32,0.1)', FALSE_PRESENT:'rgba(107,66,160,0.1)',
};

/* ── QUERY-AWARE CHART ENGINE ────────────────────────────────────── */
function deriveChartData(rows, intent, groupByField) {
  if (!rows || rows.length === 0) return { chartData: [], suggestedType: 'bar', hasDeptBreakdown: false };

  const findKey = (...names) => Object.keys(rows[0]).find(k =>
    names.some(n => k.toLowerCase() === n.toLowerCase() ||
      k.toLowerCase().replace(/[_\s]/g,'') === n.toLowerCase().replace(/[_\s]/g,''))
  );

  const deptKey   = findKey('dept_name','department','Department','dept_code');
  const statusKey = findKey('status','attendance_status','Status');
  const roleKey   = findKey('role','current_role_code','Role');
  const shiftKey  = findKey('shift','shift_code','Shift');

  // Case 1: Dept × Status breakdown (compare attendance by department)
  if (deptKey && statusKey && rows.length > 1) {
    const map = {};
    rows.forEach(r => {
      const dept = r[deptKey] || 'Unknown';
      const st   = (r[statusKey] || 'UNKNOWN').toUpperCase();
      if (!map[dept]) map[dept] = { name: dept, PRESENT:0, ABSENT:0, LATE:0, OTHER:0 };
      if (st==='PRESENT') map[dept].PRESENT++;
      else if (st==='ABSENT') map[dept].ABSENT++;
      else if (st==='LATE') map[dept].LATE++;
      else map[dept].OTHER++;
    });
    const chartData = Object.values(map).map(d => ({
      ...d, name: d.name.length > 18 ? d.name.slice(0,16)+'…' : d.name,
    }));
    return { chartData, suggestedType:'stackedBar', hasDeptBreakdown: true };
  }

  // Case 2: explicit groupBy
  if (groupByField && groupByField !== 'null') {
    const gKey = findKey(groupByField) || groupByField;
    if (rows[0] && rows[0][gKey] !== undefined) {
      const map = {};
      rows.forEach(r => { const k=r[gKey]||'Unknown'; map[k]=(map[k]||0)+1; });
      return { chartData: Object.entries(map).map(([n,v])=>({name:n,value:v})), suggestedType:'bar', hasDeptBreakdown:false };
    }
  }

  // Case 3: Role pie
  if (roleKey && !statusKey) {
    const map = {};
    rows.forEach(r => { const k=r[roleKey]||'Unknown'; map[k]=(map[k]||0)+1; });
    return { chartData: Object.entries(map).map(([n,v])=>({name:n,value:v})), suggestedType:'pie', hasDeptBreakdown:false };
  }

  // Case 4: Shift pie
  if (shiftKey && !statusKey) {
    const map = {};
    rows.forEach(r => { const k=r[shiftKey]||'Unknown'; map[k]=(map[k]||0)+1; });
    return { chartData: Object.entries(map).map(([n,v])=>({name:n,value:v})), suggestedType:'pie', hasDeptBreakdown:false };
  }

  // Case 5: Status distribution
  if (statusKey) {
    const map = {};
    rows.forEach(r => { const k=(r[statusKey]||'Unknown').toUpperCase(); map[k]=(map[k]||0)+1; });
    const chartData = Object.entries(map).map(([n,v])=>({name:n,value:v}));
    return { chartData, suggestedType: chartData.length<=5 ? 'pie' : 'bar', hasDeptBreakdown:false };
  }

  // Case 6: Dept count only
  if (deptKey) {
    const map = {};
    rows.forEach(r => { const k=r[deptKey]||'Unknown'; map[k]=(map[k]||0)+1; });
    return { chartData: Object.entries(map).map(([n,v])=>({name:n,value:v})), suggestedType:'bar', hasDeptBreakdown:false };
  }

  return { chartData:[], suggestedType:'bar', hasDeptBreakdown:false };
}

/* ── SMART TABLE: detects query type and renders best columns ─────── */
function SmartTable({ display, intent, onCopy }) {
  const [copied, setCopied] = useState(false);
  if (!display || display.length === 0) return <div className="small-muted">No records returned.</div>;

  const allCols = Object.keys(display[0]);

  // Filter out columns that are all empty/dash
  const activeCols = allCols.filter(c =>
    display.some(r => r[c] !== null && r[c] !== undefined && r[c] !== '—' && r[c] !== '')
  );

  // Detect if this is a "compare by dept" query → compute attendance count per dept inline
  const isDeptCompare = intent && (
    intent.toLowerCase().includes('attendance_summary') ||
    intent.toLowerCase().includes('compare')
  );

  // For dept-compare: build aggregated view with count columns
  const isDeptCol = col => ['department','dept_name','dept_code','Department'].some(
    n => col.toLowerCase() === n.toLowerCase() || col.toLowerCase().replace(/[_\s]/g,'') === n.toLowerCase().replace(/[_\s]/g,'')
  );
  const isStatusCol = col => ['status','attendance_status','Status'].some(
    n => col.toLowerCase() === n.toLowerCase()
  );

  const hasDeptCol   = activeCols.some(isDeptCol);
  const hasStatusCol = activeCols.some(isStatusCol);
  const deptColName  = activeCols.find(isDeptCol);
  const statusColName = activeCols.find(isStatusCol);

  // If we have both dept and status, build a dept-aggregated view
  if (hasDeptCol && hasStatusCol && display.length > 1) {
    return <DeptAttendanceTable display={display} deptKey={deptColName} statusKey={statusColName} />;
  }

  // Standard table
  const maxBarValue = (() => {
    // find any numeric col for bar visualization
    const numCol = activeCols.find(c => typeof display[0][c] === 'number' && display[0][c] > 0);
    if (!numCol) return null;
    return { col: numCol, max: Math.max(...display.map(r => Number(r[numCol]) || 0)) };
  })();

  const doCopy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(display, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {}
  };

  return (
    <div className="smart-table-wrap">
      <div className="smart-table-scroll">
        <table className="smart-table">
          <thead>
            <tr>
              {activeCols.map(col => <th key={col}>{col}</th>)}
            </tr>
          </thead>
          <tbody>
            {display.map((row, i) => (
              <tr key={i}>
                {activeCols.map(col => {
                  const val = row[col] ?? '—';
                  const statusUpper = isStatusCol(col) && String(val).toUpperCase();
                  return (
                    <td key={col}>
                      {statusUpper && STATUS_COLORS[statusUpper] ? (
                        <span className={`status-badge sb-${statusUpper}`}>{val}</span>
                      ) : (
                        val
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-footer">
        <span>{display.length} record{display.length !== 1 ? 's' : ''}</span>
        <button className="copy-btn" onClick={doCopy}>
          {copied ? '✓ Copied' : 'Copy data'}
        </button>
      </div>
    </div>
  );
}

/* ── DEPT ATTENDANCE TABLE: the smart aggregated view ─────────────── */
function DeptAttendanceTable({ display, deptKey, statusKey }) {
  const [copied, setCopied] = useState(false);

  // Aggregate by dept
  const aggMap = {};
  display.forEach(r => {
    const dept   = r[deptKey] || 'Unknown';
    const status = (r[statusKey] || 'UNKNOWN').toUpperCase();
    if (!aggMap[dept]) aggMap[dept] = { dept, total:0, PRESENT:0, ABSENT:0, LATE:0, FALSE_PRESENT:0 };
    aggMap[dept].total++;
    if (aggMap[dept][status] !== undefined) aggMap[dept][status]++;
    else aggMap[dept].total; // just count total
  });

  const depts = Object.values(aggMap).sort((a,b) => b.total - a.total);
  const maxTotal = Math.max(...depts.map(d => d.total), 1);

  const doCopy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(depts, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {}
  };

  const hasLate  = depts.some(d => d.LATE > 0);
  const hasFalse = depts.some(d => d.FALSE_PRESENT > 0);

  return (
    <div className="smart-table-wrap">
      <div className="smart-table-scroll">
        <table className="smart-table">
          <thead>
            <tr>
              <th>Department</th>
              <th>Attendance</th>
              <th style={{ color:'rgba(59,122,82,0.8)' }}>Present</th>
              <th style={{ color:'rgba(166,50,40,0.8)' }}>Absent</th>
              {hasLate  && <th style={{ color:'rgba(176,112,32,0.8)' }}>Late</th>}
              {hasFalse && <th style={{ color:'rgba(107,66,160,0.8)' }}>False</th>}
              <th>Rate</th>
            </tr>
          </thead>
          <tbody>
            {depts.map((d, i) => {
              const rate = d.total > 0 ? Math.round((d.PRESENT / d.total) * 100) : 0;
              return (
                <tr key={d.dept}>
                  <td style={{ fontWeight: 500, maxWidth: 200, overflow:'hidden', textOverflow:'ellipsis' }}>
                    {d.dept}
                  </td>
                  <td>
                    <div className="count-cell">
                      <div className="count-bar-wrap">
                        <div className="count-bar" style={{
                          width: `${(d.total / maxTotal) * 100}%`,
                          background: 'var(--forest-sage)',
                        }} />
                      </div>
                      <span className="count-num">{d.total}</span>
                    </div>
                  </td>
                  <td>
                    <span className="status-badge sb-PRESENT">{d.PRESENT}</span>
                  </td>
                  <td>
                    <span className="status-badge sb-ABSENT">{d.ABSENT}</span>
                  </td>
                  {hasLate  && <td><span className="status-badge sb-LATE">{d.LATE}</span></td>}
                  {hasFalse && <td><span className="status-badge sb-FALSE_PRESENT">{d.FALSE_PRESENT}</span></td>}
                  <td>
                    <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                      <div style={{ flex:1, height:4, background:'rgba(14,31,24,0.08)', borderRadius:2, minWidth:50, overflow:'hidden' }}>
                        <div style={{
                          height:'100%', borderRadius:2,
                          width:`${rate}%`,
                          background: rate >= 80 ? 'var(--status-present)' : rate >= 60 ? 'var(--status-late)' : 'var(--status-absent)',
                          transition:'width 0.5s ease',
                        }}/>
                      </div>
                      <span style={{ fontFamily:'var(--ff-mono)', fontSize:11, fontWeight:500, color:'var(--ink-muted)', minWidth:32, textAlign:'right' }}>
                        {rate}%
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="table-footer">
        <span>{depts.length} departments · {display.length} records</span>
        <button className="copy-btn" onClick={doCopy}>{copied ? '✓ Copied' : 'Copy data'}</button>
      </div>
    </div>
  );
}

/* ── SUMMARY PILLS ───────────────────────────────────────────────── */
function SummaryPills({ rows }) {
  if (!rows || rows.length === 0) return null;
  const statusKey = Object.keys(rows[0]).find(k =>
    ['status','attendance_status','Status'].includes(k)
  );
  if (!statusKey) return null;
  const counts = {};
  rows.forEach(r => { const s=(r[statusKey]||'UNKNOWN').toUpperCase(); counts[s]=(counts[s]||0)+1; });
  const pillClass = { PRESENT:'pill-present', ABSENT:'pill-absent', LATE:'pill-late', FALSE_PRESENT:'pill-false' };

  return (
    <div className="summary-pills">
      <span className="pill pill-total">{rows.length} total</span>
      {Object.entries(counts).map(([s, n]) => (
        <span key={s} className={`pill ${pillClass[s]||'pill-other'}`}>{s}: {n}</span>
      ))}
    </div>
  );
}

/* ── RECHARTS: STACKED BAR ───────────────────────────────────────── */
const TT_STYLE = {
  background:'rgba(14,31,24,0.95)', border:'1px solid rgba(201,168,76,0.25)',
  borderRadius:8, fontFamily:"'JetBrains Mono',monospace", fontSize:11,
  color:'var(--parchment)',
};

function StackedBarViz({ data }) {
  if (!data || data.length === 0) return null;
  const keys = ['PRESENT','ABSENT','LATE','OTHER'].filter(k => data.some(d => (d[k]||0) > 0));
  const keyColors = { PRESENT:'#3B7A52', ABSENT:'#A63228', LATE:'#B07020', OTHER:'#6B42A0' };
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top:8, right:16, bottom:64, left:0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(14,31,24,0.07)" />
        <XAxis dataKey="name" tick={{ fill:'var(--ink-faint)', fontSize:9, fontFamily:"'JetBrains Mono',monospace" }}
          angle={-30} textAnchor="end" interval={0} />
        <YAxis tick={{ fill:'var(--ink-faint)', fontSize:9 }} />
        <Tooltip contentStyle={TT_STYLE} />
        <Legend wrapperStyle={{ fontSize:10, fontFamily:"'JetBrains Mono',monospace", color:'var(--ink-faint)', paddingTop:8 }} />
        {keys.map(k => (
          <Bar key={k} dataKey={k} stackId="a" fill={keyColors[k]} name={k}
            radius={k===keys[keys.length-1] ? [3,3,0,0] : [0,0,0,0]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function BarViz({ data }) {
  if (!data || data.length === 0) return null;
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top:8, right:16, bottom:60, left:0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(14,31,24,0.07)" />
        <XAxis dataKey="name" tick={{ fill:'var(--ink-faint)', fontSize:9, fontFamily:"'JetBrains Mono',monospace" }}
          angle={-28} textAnchor="end" interval={0} />
        <YAxis tick={{ fill:'var(--ink-faint)', fontSize:9 }} />
        <Tooltip contentStyle={TT_STYLE} />
        <Bar dataKey="value" radius={[3,3,0,0]}>
          {data.map((_,i) => <Cell key={i} fill={CHART_PALETTE[i%CHART_PALETTE.length]} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function PieViz({ data }) {
  if (!data || data.length === 0) return null;
  const RADIAN = Math.PI / 180;
  const renderLabel = ({ cx,cy,midAngle,innerRadius,outerRadius,percent,name }) => {
    if (percent < 0.05) return null;
    const r = innerRadius + (outerRadius - innerRadius) * 0.55;
    const x = cx + r * Math.cos(-midAngle * RADIAN);
    const y = cy + r * Math.sin(-midAngle * RADIAN);
    return <text x={x} y={y} fill="white" textAnchor="middle" dominantBaseline="central"
      fontSize={10} fontFamily="'JetBrains Mono',monospace">{`${(percent*100).toFixed(0)}%`}</text>;
  };
  return (
    <ResponsiveContainer width="100%" height={260}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={105}
          labelLine={false} label={renderLabel}>
          {data.map((e,i) => (
            <Cell key={i} fill={STATUS_COLORS[e.name] || CHART_PALETTE[i%CHART_PALETTE.length]} />
          ))}
        </Pie>
        <Tooltip contentStyle={TT_STYLE} />
        <Legend wrapperStyle={{ fontSize:10, fontFamily:"'JetBrains Mono',monospace", color:'var(--ink-faint)' }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

/* ── VIEW TOGGLE ─────────────────────────────────────────────────── */
function ViewToggle({ value, onChange, options }) {
  return (
    <div className="chart-toggle">
      {options.map(opt => (
        <button key={opt} onClick={() => onChange(opt)}
          className={`chart-toggle-btn ${value===opt ? 'active' : ''}`}>
          {opt}
        </button>
      ))}
    </div>
  );
}

/* ── STRUCTURED RESULT BLOCK ─────────────────────────────────────── */
function StructuredBlock({ structured }) {
  const display     = structured?.data?.display || [];
  const intent      = structured?.intent || '';
  const groupByField = structured?.data?.groupByField || null;

  const { chartData, suggestedType, hasDeptBreakdown } = useMemo(
    () => deriveChartData(display, intent, groupByField),
    [display, intent, groupByField]
  );

  const hasUsefulChart = chartData.length > 0;

  // Smart default: show the most meaningful view first
  const defaultView = hasDeptBreakdown && chartData.length > 1 ? 'table' // our dept table IS the smart view
    : suggestedType === 'pie' && chartData.length <= 6 ? 'pie'
    : display.length > 30 ? 'bar'
    : 'table';

  const [view, setView] = useState(defaultView);
  const toggleOptions = ['table', ...(hasUsefulChart ? ['bar', 'pie'] : [])];

  return (
    <div className="result-block">
      {structured.description && (
        <div className="result-description">{structured.description}</div>
      )}

      <SummaryPills rows={display} />
      <ViewToggle value={view} onChange={setView} options={toggleOptions} />

      <div style={{ marginTop: 0 }}>
        {view === 'table' && (
          <SmartTable display={display} intent={intent} />
        )}
        {view === 'bar' && (
          <div className="chart-area">
            {hasDeptBreakdown ? <StackedBarViz data={chartData} /> : <BarViz data={chartData} />}
          </div>
        )}
        {view === 'pie' && (
          <div className="chart-area">
            <PieViz data={chartData.map(d => ({
              name: d.name,
              value: d.value ?? ((d.PRESENT||0) + (d.ABSENT||0) + (d.LATE||0) + (d.OTHER||0))
            }))} />
          </div>
        )}
      </div>

      {hasUsefulChart && view !== 'table' && (
        <div className="chart-insight">
          {hasDeptBreakdown
            ? `Attendance across ${chartData.length} departments · ${display.length} records`
            : `${chartData.length} groups · ${display.length} records total`}
        </div>
      )}
    </div>
  );
}

/* ── DOCUMENT BLOCK ──────────────────────────────────────────────── */
function DocumentBlock({ m }) {
  const answer    = m.document?.answer || m.content || '';
  const citations = m.citations || [];
  return (
    <div style={{ marginTop: 8 }}>
      {answer && <div className="document-answer">{answer}</div>}
      {citations.length > 0 && (
        <div className="small-muted" style={{ marginTop:10, borderTop:'1px solid var(--border-light)', paddingTop:8 }}>
          <strong>Sources:</strong>{' '}
          {citations.map((c,i) => (
            <span key={i} style={{ marginRight:8 }}>{c.filename||c.source||c.title||`[${i+1}]`}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── MAIN CHAT WORKSPACE ─────────────────────────────────────────── */
const SAMPLES = [
  'Compare attendance by department',
  'Show all absent executives today',
  'Show approved leaves',
  'Show employees in HR department',
  'Show false attendance policy',
  'Show salary, allowances and benefits policies',
];

export default function ChatWorkspace({ messages, onSend, inputValue, setInputValue, loading }) {
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleKey = e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (inputValue?.trim()) onSend(inputValue);
    }
  };

  return (
    <div className="fc-theme-workspace" role="region" aria-label="Chat workspace">
      <div className="chat-card">

        {/* ── Messages ── */}
        <div ref={scrollRef} className="chat-history">
          {messages.length === 0 ? (
            <div className="empty-state">
              <svg className="empty-icon" viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.2">
                <circle cx="24" cy="24" r="20"/>
                <path d="M16 24h16M24 16v16" opacity="0.3"/>
                <circle cx="24" cy="24" r="6" opacity="0.4"/>
              </svg>
              <div className="empty-title">Ready to query</div>
              <div className="empty-sub">
                Ask anything about attendance, departments, or employee data. Results include smart tables and context-aware charts.
              </div>
              <div className="sample-queries">
                {SAMPLES.map(s => (
                  <button key={s} className="sample-query-chip" onClick={() => onSend(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, idx) => (
              <div key={idx}
                className={m.role === 'user' ? 'user-bubble' : 'assistant-bubble'}
                aria-live={m.role === 'assistant' ? 'polite' : 'off'}
              >
                {m.content && (
                  <div style={{ whiteSpace:'pre-wrap', lineHeight:1.6 }}>{m.content}</div>
                )}

                {(m.type === 'structured' || m.type === 'hybrid') && m.structured && (
                  <StructuredBlock structured={m.structured} />
                )}

                {m.type === 'document' && <DocumentBlock m={m} />}

                {m.type === 'hybrid' && (m.document || m.citations?.length > 0) && (
                  <div style={{ marginTop:16, paddingTop:12, borderTop:'1px solid var(--border-light)' }}>
                    <div className="small-muted" style={{ marginBottom:6, fontWeight:600 }}>
                      Document context
                    </div>
                    <DocumentBlock m={m} />
                  </div>
                )}

                <div className="message-meta">{m.ts || ''}</div>
              </div>
            ))
          )}
        </div>

        {/* ── Input ── */}
        <div className="chat-input-bar">
          <div className="chat-input">
            <textarea
              value={inputValue}
              onChange={e => setInputValue(e.target.value)}
              onKeyDown={handleKey}
              rows={1}
              placeholder="Ask about attendance, departments, employees…"
              className="chat-textarea"
              aria-label="Query input"
            />
            <button
              className="send-btn focus-outline"
              onClick={() => { if (inputValue?.trim()) onSend(inputValue); }}
              disabled={loading}
              aria-label="Send query"
              title="Send"
            >
              {loading ? (
                <span className="typing-dots" aria-hidden>
                  <span /><span /><span />
                </span>
              ) : (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/>
                </svg>
              )}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}