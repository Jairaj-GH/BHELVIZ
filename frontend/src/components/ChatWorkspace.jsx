import React, { useRef, useEffect, useState } from 'react';
import {
  BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, AreaChart, Area,
} from 'recharts';

const PALETTE = [
  '#BFA77A', '#2C4E3D', '#8B7355', '#4A7C59', '#D4B896',
  '#6B5D4F', '#3D6B52', '#C9A96E', '#5A8C6A', '#B8956A',
];

const STATUS_COLOR = {
  PRESENT: '#4A7C59', ABSENT: '#C0392B', LATE: '#E67E22',
  FALSE_PRESENT: '#8E44AD', APPROVED: '#4A7C59', PENDING: '#E67E22',
};

/* ── Chart type toggle per-message ─────────────────────────────── */
function ChartToggle({ value, onChange }) {
  return (
    <div style={{ display: 'flex', gap: 4, marginTop: 10, flexWrap: 'wrap' }}>
      {['table', 'bar', 'pie', 'area'].map(t => (
        <button
          key={t}
          onClick={() => onChange(t)}
          style={{
            padding: '3px 10px', borderRadius: 4, border: 'none', cursor: 'pointer',
            fontSize: 10, fontFamily: "'JetBrains Mono', monospace", fontWeight: 600,
            letterSpacing: 1, textTransform: 'uppercase',
            background: value === t ? 'var(--accent-gold)' : 'rgba(191,167,122,0.15)',
            color: value === t ? 'var(--primary-green)' : 'var(--accent-gold)',
            transition: 'all 0.15s',
          }}
        >{t}</button>
      ))}
    </div>
  );
}

/* ── Data table ─────────────────────────────────────────────────── */
function DataTable({ display }) {
  if (!display || display.length === 0) return <div className="small-muted">No rows returned.</div>;
  const cols = Object.keys(display[0]);
  return (
    <div className="fc-table-wrap">
      <table className="fc-table">
        <thead>
          <tr>{cols.map(h => <th key={h}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {display.map((row, i) => (
            <tr key={i}>
              {cols.map(c => (
                <td key={c} style={{ color: c === 'Status' ? (STATUS_COLOR[row[c]] || 'inherit') : 'inherit' }}>
                  {row[c] ?? '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Recharts wrappers ──────────────────────────────────────────── */
function BarViz({ data }) {
  if (!data || data.length === 0) return <div className="small-muted">No chart data.</div>;
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 50, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(191,167,122,0.2)" />
        <XAxis dataKey="name" tick={{ fill: 'var(--muted-text)', fontSize: 9, fontFamily: "'JetBrains Mono',monospace" }} angle={-20} textAnchor="end" interval={0} />
        <YAxis tick={{ fill: 'var(--muted-text)', fontSize: 9 }} />
        <Tooltip contentStyle={{ background: 'var(--surface-glass)', border: '1px solid var(--border-light)', borderRadius: 6, fontFamily: "'JetBrains Mono',monospace", fontSize: 11 }} />
        <Bar dataKey="value" radius={[4, 4, 0, 0]}>
          {data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function PieViz({ data }) {
  if (!data || data.length === 0) return <div className="small-muted">No chart data.</div>;
  return (
    <ResponsiveContainer width="100%" height={260}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={95}
          label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
          labelLine={{ stroke: 'var(--muted-text)', strokeWidth: 1 }}>
          {data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
        </Pie>
        <Tooltip contentStyle={{ background: 'var(--surface-glass)', border: '1px solid var(--border-light)', borderRadius: 6 }} />
        <Legend wrapperStyle={{ fontSize: 10, fontFamily: "'JetBrains Mono',monospace", color: 'var(--muted-text)' }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

function AreaViz({ data }) {
  if (!data || data.length === 0) return <div className="small-muted">No chart data.</div>;
  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 50, left: 0 }}>
        <defs>
          <linearGradient id="agrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#BFA77A" stopOpacity={0.35} />
            <stop offset="95%" stopColor="#BFA77A" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(191,167,122,0.2)" />
        <XAxis dataKey="name" tick={{ fill: 'var(--muted-text)', fontSize: 9 }} angle={-20} textAnchor="end" interval={0} />
        <YAxis tick={{ fill: 'var(--muted-text)', fontSize: 9 }} />
        <Tooltip contentStyle={{ background: 'var(--surface-glass)', border: '1px solid var(--border-light)', borderRadius: 6 }} />
        <Area type="monotone" dataKey="value" stroke="#BFA77A" fill="url(#agrad)" strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ── Structured message block ───────────────────────────────────── */
function StructuredBlock({ structured }) {
  const defaultType = structured.chartType || 'table';
  const [chartType, setChartType] = useState(defaultType);
  const display = structured?.data?.display || [];
  const chart   = structured?.data?.chart   || [];

  return (
    <div style={{ marginTop: 8 }}>
      {structured.description && (
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--accent-gold)', fontFamily: "'Playfair Display', serif", letterSpacing: 1 }}>
          {structured.description}
        </div>
      )}

      <ChartToggle value={chartType} onChange={setChartType} />

      <div style={{ marginTop: 12 }}>
        {chartType === 'table' && <DataTable display={display} />}
        {chartType === 'bar'   && <BarViz  data={chart} />}
        {chartType === 'pie'   && <PieViz  data={chart} />}
        {chartType === 'area'  && <AreaViz data={chart} />}
      </div>

      <div className="small-muted" style={{ marginTop: 6 }}>
        {display.length} rows · {structured.intent || ''}
      </div>
    </div>
  );
}

/* ── Document / RAG block ───────────────────────────────────────── */
function DocumentBlock({ m }) {
  const answer = m.document?.answer || m.content || '';
  const citations = m.citations || [];
  return (
    <div style={{ marginTop: 8 }}>
      {answer && (
        <div className="document-answer" style={{ lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>
          {answer}
        </div>
      )}
      {citations.length > 0 && (
        <div className="small-muted" style={{ marginTop: 10, borderTop: '1px solid var(--border-light)', paddingTop: 8 }}>
          <span style={{ fontWeight: 600 }}>Sources: </span>
          {citations.map((c, i) => (
            <span key={i} style={{ marginRight: 8 }}>
              {c.filename || c.source || c.title || `[${i + 1}]`}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Main ChatWorkspace ─────────────────────────────────────────── */
export default function ChatWorkspace({ messages, onSend, inputValue, setInputValue, loading }) {
  const scrollRef = useRef(null);
  const [copied, setCopied] = useState(false);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (inputValue && inputValue.trim()) onSend(inputValue);
    }
  };

  const doCopy = async (text) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1400); } catch {}
  };

  return (
    <div className="fc-theme-workspace" role="region" aria-label="Chat workspace">
      <div className="chat-card">

        {/* ── Message history ── */}
        <div ref={scrollRef} className="chat-history">
          {messages.length === 0 && (
            <div className="small-muted" style={{ textAlign: 'center', padding: 40, opacity: 0.6 }}>
              <div style={{ fontSize: 40, marginBottom: 12, opacity: 0.3 }}>◈</div>
              <div style={{ fontFamily: "'Playfair Display', serif", fontSize: 16, letterSpacing: 3, marginBottom: 6 }}>READY</div>
              Ask a question — answers may include tables, charts or document citations.
            </div>
          )}

          {messages.map((m, idx) => (
            <div
              key={idx}
              className={m.role === 'user' ? 'user-bubble' : 'assistant-bubble'}
              aria-live={m.role === 'assistant' ? 'polite' : 'off'}
            >
              {/* Plain text content (user messages and description) */}
              {m.content && (
                <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{m.content}</div>
              )}

              {/* Structured data: table + charts */}
              {(m.type === 'structured' || m.type === 'hybrid') && m.structured && (
                <StructuredBlock structured={m.structured} />
              )}

              {/* Document (RAG) answer */}
              {m.type === 'document' && <DocumentBlock m={m} />}

              {/* Hybrid: also show document part below structured */}
              {m.type === 'hybrid' && (m.document || m.citations?.length > 0) && (
                <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--border-light)' }}>
                  <div className="small-muted" style={{ marginBottom: 6, fontWeight: 600 }}>Document context</div>
                  <DocumentBlock m={m} />
                </div>
              )}

              {/* Copy button for structured */}
              {(m.type === 'structured' || m.type === 'hybrid') && m.structured?.data?.display?.length > 0 && (
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 6 }}>
                  <button
                    className="send-btn"
                    onClick={() => doCopy(JSON.stringify(m.structured.data.display, null, 2))}
                    aria-label="Copy table data"
                    style={{ padding: '4px 12px', fontSize: 11 }}
                  >
                    {copied ? 'Copied ✓' : 'Copy CSV'}
                  </button>
                </div>
              )}

              <div className="message-meta">{m.ts || ''}</div>
            </div>
          ))}
        </div>

        {/* ── Input ── */}
        <div className="chat-input glass" role="form" aria-label="Message input">
          <textarea
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            onKeyDown={handleKey}
            rows={1}
            placeholder="Type your question… (Enter to send, Shift+Enter for newline)"
            className="chat-textarea"
            aria-label="Message text"
          />
          <button
            className="send-btn focus-outline"
            onClick={() => { if (inputValue && inputValue.trim()) onSend(inputValue); }}
            disabled={loading}
            title="Send"
            aria-label="Send message"
          >
            {loading ? (
              <span className="typing-dots" aria-hidden><span /><span /><span /></span>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/>
              </svg>
            )}
          </button>
        </div>

      </div>
    </div>
  );
}
