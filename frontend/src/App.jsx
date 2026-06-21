import { useState, useRef, useEffect } from "react";
import './theme.css';
import Sidebar from './components/Sidebar';
import ChatWorkspace from './components/ChatWorkspace';
import RightSidebar from './components/RightSidebar';
import ErrorBoundary from './components/ErrorBoundary';
import OldDashboard from './components/OldDashboard';
import {
  BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, AreaChart, Area
} from "recharts";

/* ── THEME ──────────────────────────────────────────────────────── */
const C = {
  bg0: 'var(--surface-warm)',
  bg1: 'var(--surface-secondary)',
  bg2: 'var(--surface-primary)',
  bg3: 'var(--surface-glass)',
  border: 'var(--border-light)',
  borderHi: 'var(--border-medium)',
  gold: 'var(--peach)',
  goldBright: 'var(--peach)',
  goldDim: 'var(--amber-light)',
  blue: 'var(--surface-dark-header)',
  blueBright: 'var(--text-secondary)',
  text: 'var(--text-primary)',
  textBright: 'var(--text-on-dark)',
  textDim: 'var(--text-tertiary)',
  ok: 'var(--green-soft)',
  okBright: 'var(--green-soft)',
  err: 'var(--error-text)',
  errBright: 'var(--error-text)',
  warn: 'var(--amber-light)',
  warnBright: 'var(--amber-light)',
};
const CC = ['var(--peach)','var(--brown-warm)','var(--green-soft)','var(--amber-light)','var(--text-primary)','var(--text-secondary)','var(--surface-dark-header)','var(--text-tertiary)'];

/* ── MOCK DATABASE ──────────────────────────────────────────────── */
const DEPTS = ["POWER_SYSTEMS","TRANSMISSION","MANUFACTURING","HR","SECURITY","R&D","BOILER_DIV"];
const ROLES = ["EXECUTIVE","SUPERVISOR","WORKMAN"];
const SHIFTS = ["MORNING","AFTERNOON","NIGHT"];
const STATUSES = ["PRESENT","ABSENT","LATE","FALSE_PRESENT"];

const EMPLOYEES = Array.from({ length: 200 }, (_, i) => ({
  no: `BHEL${String(i + 1).padStart(5, "0")}`,
  no_enc: `AES-GCM::${btoa("E" + i).replace(/=/g, "").slice(0, 12)}…`,
  name: `${["A.K.","B.","C.","D.R.","E.","F.","G.","H.","I.","J.K."][i % 10]} ${["Sharma","Kumar","Patel","Singh","Nair","Reddy","Rao","Mehta","Joshi","Iyer"][i % 10]}`,
  name_enc: `AES-GCM::${btoa("Name" + i).replace(/=/g, "").slice(0, 20)}…`,
  dept: DEPTS[i % 7],
  role: i % 10 === 0 ? "EXECUTIVE" : i % 4 === 0 ? "SUPERVISOR" : "WORKMAN",
  shift: SHIFTS[i % 3],
  status: i % 10 < 7 ? "PRESENT" : i % 10 === 7 ? "ABSENT" : i % 10 === 8 ? "LATE" : "FALSE_PRESENT",
  hired: `${2008 + (i % 16)}-${String((i % 12) + 1).padStart(2, "0")}-01`,
  penalty: i % 10 === 9 ? -1 : null,
}));

const INIT_PENDING = [
  { id: 1, name: "Arjun Mehta", email: "a.mehta@bhel.in", dept: "Power Systems", reason: "Q2 attendance audit — division head requested analysis access", ts: "2026-05-12 09:14" },
  { id: 2, name: "Priya Subramaniam", email: "p.sub@bhel.in", dept: "HR", reason: "Employee relation and role history analysis for HR compliance", ts: "2026-05-12 11:30" },
  { id: 3, name: "Vikram Joshi", email: "v.joshi@bhel.in", dept: "Security", reason: "Night shift false-attendance detection and verification", ts: "2026-05-13 08:02" },
];

const INIT_LOG = [
  { ts: "2026-05-13 08:00:01", act: "SYSTEM_INIT", msg: "BHELVIZ v2.0 started. All security controls active.", lv: "INFO" },
  { ts: "2026-05-13 08:00:45", act: "TDE_VERIFIED", msg: "Oracle TDE: ACTIVE. HSM-backed master key confirmed. Rotation: 90 days.", lv: "INFO" },
  { ts: "2026-05-13 08:01:12", act: "VAULT_PING", msg: "Oracle Key Vault: CONNECTED. nShield Solo XC online.", lv: "INFO" },
  { ts: "2026-05-13 08:01:55", act: "DB_VAULT_INIT", msg: "Database Vault realms loaded. DBA bypass paths: BLOCKED.", lv: "INFO" },
  { ts: "2026-05-13 08:14:33", act: "ACCESS_REQUEST", msg: "Arjun Mehta <a.mehta@bhel.in> submitted access request.", lv: "WARN" },
  { ts: "2026-05-13 08:30:02", act: "ACCESS_REQUEST", msg: "Priya Subramaniam <p.sub@bhel.in> submitted access request.", lv: "WARN" },
  { ts: "2026-05-13 09:02:11", act: "ACCESS_REQUEST", msg: "Vikram Joshi <v.joshi@bhel.in> submitted access request.", lv: "WARN" },
];

/* ── NLP → IR via Anthropic API ─────────────────────────────────── */
async function fetchIR(query, history) {
  const apiKey = import.meta.env.VITE_ANTHROPIC_KEY;
  if (!apiKey) {
    // fallback IR when no key set
    return { intent: "attendance_summary", description: query,
             chart_type: "bar", group_by_field: "dept",
             filters: [], limit: 100,
             safety: { read_only: true, no_sql: true } };
  }
  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,                  // ← add this line
      "anthropic-version": "2023-06-01",    // ← add this line
      "anthropic-dangerous-direct-browser-access": "true",  // ← required for browser calls
    },
    body: JSON.stringify({
      model: "claude-sonnet-4-20250514",
      max_tokens: 500,
      system: sys,
      messages: [...history.slice(-12), { role: "user", content: query }],
    }),
  });
  const d = await resp.json();
  const txt = (d.content || []).map(b => b.text || "").join("").replace(/```\w*\n?|```/g, "").trim();
  try { return JSON.parse(txt); }
  catch {
    return { intent: "employee_lookup", description: "General employee lookup", chart_type: "table", group_by_field: "null", filters: [], limit: 50, safety: { read_only: true, no_sql: true } };
  }
}

/* ── MOCK QUERY ENGINE ──────────────────────────────────────────── */
function runQuery(ir, isDecrypted) {
  let rows = [...EMPLOYEES];
  (ir.filters || []).forEach(f => {
    const v = (f.value || "").toUpperCase();
    if (f.column === "dept") rows = rows.filter(e => e.dept.replace(/_/g, " ").includes(v.replace(/_/g, " ")) || e.dept.includes(v));
    if (f.column === "role") rows = rows.filter(e => e.role === v);
    if (f.column === "shift") rows = rows.filter(e => e.shift === v);
    if (f.column === "status") rows = rows.filter(e => e.status === v);
  });
  rows = rows.slice(0, ir.limit || 50);

  const display = rows.map(r => ({
    "Emp No": isDecrypted ? r.no : r.no_enc,
    "Name": isDecrypted ? r.name : r.name_enc,
    "Department": r.dept,
    "Role": r.role,
    "Shift": r.shift,
    "Status": r.status,
    "Hired": r.hired,
    "Penalty": r.penalty !== null ? r.penalty : "—",
  }));

  const gbf = ir.group_by_field && ir.group_by_field !== "null" ? ir.group_by_field : null;
  let chart = null;
  if (gbf) {
    const map = {};
    rows.forEach(r => {
      const k = r[gbf === "dept" ? "dept" : gbf === "role" ? "role" : gbf === "shift" ? "shift" : "status"] || "UNKNOWN";
      map[k] = (map[k] || 0) + 1;
    });
    chart = Object.entries(map).map(([name, value]) => ({ name, value }));
  } else {
    const map = {};
    rows.forEach(r => { map[r.status] = (map[r.status] || 0) + 1; });
    chart = Object.entries(map).map(([name, value]) => ({ name, value }));
  }
  return { display, chart, rawCount: rows.length };
}

/* ── MICRO COMPONENTS ───────────────────────────────────────────── */
const Spinner = () => (
  <div style={{ width: 14, height: 14, border: `2px solid ${C.border}`, borderTop: `2px solid ${C.goldBright}`, borderRadius: "50%", animation: "spin 0.7s linear infinite", flexShrink: 0 }} />
);

const Badge = ({ children, bg = C.bg3, fg = C.textBright }) => (
  <span className="badge" style={{ background: bg, color: fg, fontSize: 11, padding: "6px 12px", borderRadius: 100 }}>{children}</span>
);

const Dot = ({ ok }) => (
  <span style={{ width: 6, height: 6, borderRadius: "50%", flexShrink: 0, display: "inline-block", background: ok ? C.okBright : C.errBright, boxShadow: `0 0 5px ${ok ? C.okBright : C.errBright}`, animation: "pulse 2s infinite" }} />
);

const Panel = ({ children, style = {} }) => (
  <div className="card" style={{ ...style }}>{children}</div>
);

const Btn = ({ children, onClick, variant = "gold", style = {}, disabled = false, title }) => {
  const vs = { gold: { bg: C.gold, fg: "var(--surface-dark)", br: "none" }, ghost: { bg: "transparent", fg: C.textBright, br: `1px solid ${C.borderHi}` }, ok: { bg: C.ok, fg: "var(--surface-primary)", br: "none" }, err: { bg: C.err, fg: "var(--surface-primary)", br: "none" }, blue: { bg: C.blue, fg: "var(--surface-primary)", br: "none" } }[variant] || { bg: C.bg3, fg: C.textBright, br: "none" };
  const cls = variant === 'gold' || variant === 'goldBright' ? 'primary' : variant === 'ghost' ? 'ghost' : '';
  return (
    <button className={cls} onClick={disabled ? undefined : onClick} title={title} disabled={disabled}
      style={{ background: vs.bg, color: vs.fg, border: vs.br, borderRadius: 8, padding: "8px 14px", fontFamily: "'Inter',sans-serif", fontSize: 12, fontWeight: 600, letterSpacing: 1, opacity: disabled ? 0.6 : 1, display: "flex", alignItems: "center", gap: 8, flexShrink: 0, ...style }}>
      {children}
    </button>
  );
};

const Inp = ({ value, onChange, placeholder, type = "text", style = {}, onKeyDown }) => (
  <input className="input-card" value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} type={type} onKeyDown={onKeyDown}
    style={{ color: C.text, fontFamily: "'JetBrains Mono',monospace", fontSize: 12, width: "100%", caretColor: C.goldBright, ...style }} />
);

const Notif = ({ msg, type }) => {
  const fc = { info: C.blueBright, success: C.okBright, error: C.errBright, warning: C.warnBright };
  return (
    <div style={{ position: "fixed", top: 14, right: 18, zIndex: 9999, background: C.bg2, border: `1px solid ${fc[type] || C.borderHi}`, borderLeft: `3px solid ${fc[type] || C.borderHi}`, color: C.textBright, padding: "10px 16px", borderRadius: 3, fontSize: 11, fontFamily: "'Inter',sans-serif", maxWidth: 360, animation: "fadein 0.2s ease", boxShadow: "0 4px 20px rgba(0,0,0,0.6)" }}>
      {msg}
    </div>
  );
};

/* ── LOGIN SCREEN ───────────────────────────────────────────────── */
function LoginScreen({ onLogin, notify }) {
  const [email, setEmail] = useState("admin@bhel.in");
  const [pwd, setPwd] = useState("");
  const [tab, setTab] = useState("login");
  const [form, setForm] = useState({ name: "", dept: "", reason: "" });
  const [submitted, setSubmitted] = useState(false);

  const doLogin = async () => {
    if (!pwd.trim()) { notify("Enter a passphrase", "error"); return; }

    // Attempt backend token exchange for demo credentials; fallback to local demo behavior
    try {
      const form = new URLSearchParams();
      form.append('username', email);
      form.append('password', pwd);
      const resp = await fetch('/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form.toString(),
      });
      if (resp.ok) {
        const data = await resp.json();
        const role = (email === 'admin@bhel.in') ? 'admin' : 'user';
        onLogin(role, data.access_token);
        return;
      }
    } catch (e) {
      // ignore and fall back to local demo
    }

    // Fallback local demo auth
    if (email === "admin@bhel.in" && pwd === "admin") { onLogin("admin", null); return; }
    if (email.endsWith("@bhel.in") && pwd.length >= 4) { onLogin("user", null); return; }
    notify("Invalid credentials. Demo: admin@bhel.in / admin", "error");
  };

  if (submitted) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", background: C.bg0 }}>
      <Panel style={{ maxWidth: 440, width: "90%", padding: 40, textAlign: "center", animation: "fadein 0.4s ease" }}>
        <div style={{ fontSize: 36, marginBottom: 16 }}>📨</div>
        <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 20, fontWeight: 700, color: C.goldBright, letterSpacing: 2, marginBottom: 12 }}>REQUEST SUBMITTED</div>
        <div style={{ color: C.text, fontSize: 12, lineHeight: 1.9, marginBottom: 20 }}>
          Your request has been forwarded to the BHELVIZ Security Administrator via S/MIME encrypted email.<br /><br />
          Upon approval you will receive:<br />
          <span style={{ color: C.textBright }}>① Encrypted Decoding Manual (email)</span><br />
          <span style={{ color: C.textBright }}>② Manual password (separate SMS)</span><br />
          <span style={{ color: C.textBright }}>③ DB credentials (time-limited secure link)</span>
        </div>
        <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: C.textDim }}>AUDIT-ID: BVIZ-{Date.now().toString(36).toUpperCase().slice(0, 8)}</div>
        <div style={{ marginTop: 20, display: "flex", justifyContent: "center" }}>
          <Btn onClick={() => setSubmitted(false)} variant="ghost">← Back to login</Btn>
        </div>
      </Panel>
    </div>
  );

  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", background: C.bg0, backgroundImage: `radial-gradient(ellipse at 25% 50%, rgba(21,88,200,0.07) 0%,transparent 55%), radial-gradient(ellipse at 75% 50%, rgba(196,138,8,0.05) 0%,transparent 55%)` }}>
      <div style={{ position: "fixed", inset: 0, backgroundImage: `linear-gradient(${C.border} 1px,transparent 1px),linear-gradient(90deg,${C.border} 1px,transparent 1px)`, backgroundSize: "48px 48px", opacity: 0.22, pointerEvents: "none" }} />
      <div style={{ width: "100%", maxWidth: 400, padding: 20, animation: "fadein 0.5s ease", position: "relative", zIndex: 1 }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 50, fontWeight: 700, letterSpacing: 6, color: C.goldBright, lineHeight: 1, animation: "glow-g 4s ease infinite" }}>BHELVIZ</div>
          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: C.textDim, letterSpacing: 4, marginTop: 6 }}>SECURE · READ-ONLY · VOICE-ENABLED · ZERO-TRUST</div>
          <div style={{ display: "flex", justifyContent: "center", gap: 5, marginTop: 10, flexWrap: "wrap" }}>
            {["ADMIN-GATED", "AES-256-GCM", "ORACLE TDE", "IR-ONLY NLP", "4-PLANE ARCH"].map(t => <Badge key={t} bg={C.bg3} fg={C.textDim}>{t}</Badge>)}
          </div>
        </div>
        <Panel style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ display: "flex", borderBottom: `1px solid ${C.border}` }}>
            {["login", "register"].map(t => (
              <div key={t} onClick={() => setTab(t)} style={{ flex: 1, padding: "11px", textAlign: "center", cursor: "pointer", fontFamily: "'Playfair Display',serif", fontSize: 12, fontWeight: 600, letterSpacing: 2, color: tab === t ? C.goldBright : C.textDim, borderBottom: `2px solid ${tab === t ? C.goldBright : "transparent"}`, background: tab === t ? C.bg3 : C.bg2, transition: "all 0.2s", textTransform: "uppercase" }}>
                {t === "login" ? "AUTHENTICATE" : "REQUEST ACCESS"}
              </div>
            ))}
          </div>
          <div style={{ padding: 24 }}>
            {tab === "login" ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div>
                  <div style={{ fontSize: 9, color: C.textDim, letterSpacing: 2, marginBottom: 5, fontFamily: "'JetBrains Mono',monospace" }}>IDENTITY</div>
                  <Inp value={email} onChange={setEmail} placeholder="user@bhel.in" onKeyDown={e => e.key === "Enter" && doLogin()} />
                </div>
                <div>
                  <div style={{ fontSize: 9, color: C.textDim, letterSpacing: 2, marginBottom: 5, fontFamily: "'JetBrains Mono',monospace" }}>PASSPHRASE</div>
                  <Inp value={pwd} onChange={setPwd} type="password" placeholder="••••••••" onKeyDown={e => e.key === "Enter" && doLogin()} />
                </div>
                <Btn onClick={doLogin} style={{ width: "100%", justifyContent: "center", padding: "10px", letterSpacing: 2, marginTop: 4 }}>AUTHENTICATE</Btn>
                <div style={{ textAlign: "center", fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: C.textDim }}>
                  Demo admin: <span style={{ color: C.goldBright }}>admin@bhel.in</span> / <span style={{ color: C.goldBright }}>admin</span><br />
                  User demo: any @bhel.in email + ≥4 char password
                </div>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <Inp value={form.name} onChange={v => setForm(f => ({ ...f, name: v }))} placeholder="Full name" />
                <Inp value={email} onChange={setEmail} placeholder="email@bhel.in" />
                <Inp value={form.dept} onChange={v => setForm(f => ({ ...f, dept: v }))} placeholder="Department" />
                <textarea className="input-card" value={form.reason} onChange={e => setForm(f => ({ ...f, reason: e.target.value }))} placeholder="Business justification for access…"
                  style={{ height: 96, fontFamily: "'JetBrains Mono',monospace", fontSize: 13 }} />
                <Btn onClick={() => { if (!form.name || !email || !form.reason) { notify("Fill all fields", "error"); return; } if (!email.endsWith("@bhel.in")) { notify("Must be a @bhel.in address", "error"); return; } setSubmitted(true); }} style={{ width: "100%", justifyContent: "center", letterSpacing: 2 }}>
                  SUBMIT REQUEST
                </Btn>
              </div>
            )}
          </div>
        </Panel>
        <div style={{ textAlign: "center", marginTop: 14, fontFamily: "'JetBrains Mono',monospace", fontSize: 8, color: C.textDim, letterSpacing: 1 }}>
          ALL ACCESS LOGGED · TLS 1.3 · ORACLE 19c · ZERO-TRUST ARCHITECTURE
        </div>
      </div>
    </div>
  );
}

/* ── ADMIN CONSOLE ──────────────────────────────────────────────── */
function AdminScreen({ onLogout, notify }) {
  const [pending, setPending] = useState(INIT_PENDING);
  const [log, setLog] = useState(INIT_LOG);
  const [tab, setTab] = useState("pending");
  const [sel, setSel] = useState(null);

  const addLog = (act, msg, lv = "INFO") =>
    setLog(l => [{ ts: new Date().toISOString().replace("T", " ").slice(0, 19), act, msg, lv }, ...l]);

  const approve = u => {
    setPending(p => p.filter(x => x.id !== u.id));
    setSel(null);
    addLog("USER_APPROVED", `${u.name} <${u.email}> approved. DB role created. Decoding Manual issued via encrypted email. Password via SMS.`);
    notify(`✓ ${u.name} approved — credentials and Decoding Manual issued.`, "success");
  };
  const deny = u => {
    setPending(p => p.filter(x => x.id !== u.id));
    setSel(null);
    addLog("ACCESS_DENIED", `${u.name} <${u.email}> denied access.`, "WARN");
    notify(`${u.name} denied.`, "warning");
  };

  const SYS = [
    { l: "Oracle 19c Enterprise", ok: true, d: "Session: CONNECTED · TDE: ACTIVE" },
    { l: "App-Level AES-256-GCM", ok: true, d: "Client-side only · Server never decrypts" },
    { l: "NLP Engine (FLAN-T5)", ok: true, d: "IR output only · No DB route · Isolated" },
    { l: "Query Executor", ok: true, d: "READ-ONLY · No internet · Hardened container" },
    { l: "Oracle Key Vault (HSM)", ok: true, d: "nShield Solo XC · Auto-rotation: 90d" },
    { l: "Admin Console", ok: true, d: "mTLS · MFA: ENFORCED · Isolated VPC" },
    { l: "Audit SIEM (Splunk)", ok: true, d: "Immutable log · 0 critical alerts" },
    { l: "Oracle Data Redaction", ok: true, d: "7 column policies active" },
    { l: "Database Vault", ok: true, d: "Realm protection: ACTIVE · DBA bypass: BLOCKED" },
    { l: "TLS 1.3", ok: true, d: "All service-to-service channels secured" },
  ];

  return (
    <div style={{ background: C.bg0, height: "100vh", color: C.text, fontFamily: "'Inter',sans-serif", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{ background: C.bg1, borderBottom: `1px solid ${C.border}`, padding: "10px 20px", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 22, fontWeight: 700, color: C.goldBright, letterSpacing: 3 }}>BHELVIZ</div>
          <div style={{ fontSize: 9, fontFamily: "'JetBrains Mono',monospace", color: C.textDim, letterSpacing: 2, borderLeft: `1px solid ${C.border}`, paddingLeft: 14 }}>ADMIN CONSOLE</div>
          <Badge bg={'var(--surface-dark)'} fg={C.goldBright}>● ADMINISTRATOR</Badge>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {pending.length > 0 && <Badge bg={C.warn} fg={'var(--surface-primary)'}>{pending.length} PENDING</Badge>}
          <Dot ok={true} /><span style={{ fontSize: 9, color: C.textDim, fontFamily: "'JetBrains Mono',monospace", marginLeft: 4 }}>ALL SYSTEMS GO</span>
          <Btn onClick={onLogout} variant="ghost" style={{ fontSize: 10, padding: "4px 12px" }}>LOGOUT</Btn>
        </div>
      </div>

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <div style={{ width: 165, background: C.bg1, borderRight: `1px solid ${C.border}`, padding: 12, flexShrink: 0, display: "flex", flexDirection: "column" }}>
          {["pending", "audit", "status"].map(t => (
            <div key={t} onClick={() => setTab(t)} style={{ padding: "9px 10px", borderRadius: 3, cursor: "pointer", marginBottom: 4, background: tab === t ? C.bg3 : "transparent", borderLeft: `2px solid ${tab === t ? C.goldBright : "transparent"}`, color: tab === t ? C.textBright : C.textDim, fontSize: 11, fontWeight: 600, letterSpacing: 1, display: "flex", alignItems: "center", justifyContent: "space-between", textTransform: "uppercase" }}>
              {t} {t === "pending" && pending.length > 0 && <Badge bg={C.warn} fg={'var(--surface-primary)'}>{pending.length}</Badge>}
            </div>
          ))}
          <div style={{ marginTop: "auto", paddingTop: 16, borderTop: `1px solid ${C.border}`, fontSize: 9, color: C.textDim, fontFamily: "'JetBrains Mono',monospace" }}>
            <div style={{ marginBottom: 4 }}>Oracle TDE <span style={{ color: C.okBright }}>ON</span></div>
            <div style={{ marginBottom: 4 }}>DB Vault <span style={{ color: C.okBright }}>ON</span></div>
            <div>Sessions <span style={{ color: C.okBright }}>3</span></div>
          </div>
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: 20 }}>
          {tab === "pending" && (
            <div style={{ display: "grid", gridTemplateColumns: sel ? "1fr 380px" : "1fr", gap: 16, animation: "fadein 0.2s ease" }}>
              <div>
                <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 15, fontWeight: 700, color: C.textBright, letterSpacing: 2, marginBottom: 14 }}>
                  PENDING ACCESS REQUESTS {pending.length > 0 && <Badge bg={C.warn} fg={'var(--surface-primary)'}>{pending.length}</Badge>}
                </div>
                {pending.length === 0
                  ? <Panel style={{ padding: 32, textAlign: "center", color: C.textDim, fontSize: 12 }}>No pending requests. Queue is clear.</Panel>
                  : pending.map(u => (
                    <Panel key={u.id} style={{ padding: 16, marginBottom: 10, cursor: "pointer", borderColor: sel?.id === u.id ? C.goldBright : C.border, animation: "fadein 0.2s ease", transition: "border-color 0.2s" }} onClick={() => setSel(s => s?.id === u.id ? null : u)}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", marginBottom: 8 }}>
                        <div>
                          <div style={{ color: C.textBright, fontSize: 13, fontWeight: 600 }}>{u.name}</div>
                          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, color: C.textDim, marginTop: 2 }}>{u.email}</div>
                        </div>
                        <Badge bg={C.warn} fg={'var(--surface-primary)'}>PENDING</Badge>
                      </div>
                      <div style={{ fontSize: 10, color: C.text, marginBottom: 8 }}>{u.dept} · {u.ts}</div>
                      <div style={{ fontSize: 11, color: C.textDim, fontStyle: "italic", borderTop: `1px solid ${C.border}`, paddingTop: 8 }}>"{u.reason}"</div>
                      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                        <Btn onClick={e => { e.stopPropagation(); approve(u); }} variant="ok" style={{ fontSize: 10, padding: "5px 12px" }}>✓ APPROVE</Btn>
                        <Btn onClick={e => { e.stopPropagation(); deny(u); }} variant="err" style={{ fontSize: 10, padding: "5px 12px" }}>✗ DENY</Btn>
                      </div>
                    </Panel>
                  ))
                }
              </div>
              {sel && (
                <Panel style={{ padding: 18, animation: "fadein 0.2s ease", alignSelf: "start" }}>
                  <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 13, fontWeight: 700, color: C.goldBright, letterSpacing: 2, marginBottom: 14 }}>APPROVAL WORKFLOW</div>
                  {[["Name", sel.name], ["Email", sel.email], ["Department", sel.dept], ["Requested", sel.ts], ["DB Role", `bviz_ro_${sel.email.split("@")[0]}`], ["Access Level", "SELECT on ciphertext views only"], ["Manual Format", "AES-256-GCM · Argon2id KDF"], ["Manual Delivery", "S/MIME encrypted email (one-time link)"], ["Password Delivery", "OTP via separate SMS channel"], ["Audit Trail", "Immutable · SIEM-forwarded"]].map(([k, v]) => (
                    <div key={k} style={{ display: "flex", gap: 8, padding: "4px 0", borderBottom: `1px solid ${C.bg3}` }}>
                      <span style={{ color: C.textDim, width: 120, flexShrink: 0, fontFamily: "'JetBrains Mono',monospace", fontSize: 9 }}>{k}</span>
                      <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9, color: C.textBright }}>{v}</span>
                    </div>
                  ))}
                  <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
                    <Btn onClick={() => approve(sel)} variant="ok">✓ APPROVE & ISSUE</Btn>
                    <Btn onClick={() => deny(sel)} variant="err">✗ DENY</Btn>
                  </div>
                </Panel>
              )}
            </div>
          )}

          {tab === "audit" && (
            <div style={{ animation: "fadein 0.2s ease" }}>
              <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 15, fontWeight: 700, color: C.textBright, letterSpacing: 2, marginBottom: 14 }}>CRYPTOGRAPHIC AUDIT LOG</div>
              <Panel style={{ padding: 0, overflow: "hidden" }}>
                <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 10, maxHeight: "65vh", overflow: "auto" }}>
                  {log.map((l, i) => (
                    <div key={i} style={{ display: "flex", gap: 12, padding: "7px 14px", borderBottom: `1px solid ${C.border}`, background: i % 2 === 0 ? C.bg1 : C.bg2, animation: "fadein 0.2s ease" }}>
                      <span style={{ color: C.textDim, flexShrink: 0, width: 160 }}>{l.ts}</span>
                      <span style={{ color: l.lv === "WARN" ? C.goldBright : l.lv === "ERR" ? C.errBright : C.blueBright, flexShrink: 0, width: 170 }}>[{l.act}]</span>
                      <span style={{ color: C.text }}>{l.msg}</span>
                    </div>
                  ))}
                </div>
              </Panel>
            </div>
          )}

          {tab === "status" && (
            <div style={{ animation: "fadein 0.2s ease" }}>
              <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 15, fontWeight: 700, color: C.textBright, letterSpacing: 2, marginBottom: 14 }}>SYSTEM HEALTH STATUS</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {SYS.map(s => (
                  <Panel key={s.l} style={{ padding: 14, display: "flex", gap: 12, alignItems: "center" }}>
                    <Dot ok={s.ok} />
                    <div>
                      <div style={{ fontSize: 12, color: C.textBright, fontWeight: 600 }}>{s.l}</div>
                      <div style={{ fontSize: 10, color: C.textDim, fontFamily: "'JetBrains Mono',monospace", marginTop: 2 }}>{s.d}</div>
                    </div>
                  </Panel>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── MAIN DASHBOARD ─────────────────────────────────────────────── */
function Dashboard({ userRole, onLogout, notify, token }) {
  const USE_NEW_UI = typeof import.meta !== 'undefined' && import.meta.env && (import.meta.env.VITE_USE_NEW_UI === 'true' || import.meta.env.REACT_APP_USE_NEW_UI !== 'false');
  const [q, setQ] = useState("");
  const [messages, setMessages] = useState([]);
  const [listening, setListening] = useState(false);
  const [loading, setLoading] = useState(false);
  const [decryptAnim, setDecryptAnim] = useState(false);
  const [results, setResults] = useState(null);
  const [ir, setIR] = useState(null);
  const [history, setHistory] = useState([]);
  const [chartType, setChartType] = useState("table");
  const [manualPwd, setManualPwd] = useState("");
  const [decrypted, setDecrypted] = useState(userRole === "admin");
  const [showManualBar, setShowManualBar] = useState(userRole !== "admin");
  const [unlockingManual, setUnlockingManual] = useState(false);
  const [qHistory, setQHistory] = useState([]);
  const [feedback, setFeedback] = useState(null);
  const [currentNav, setCurrentNav] = useState("dashboard");
  const recogRef = useRef(null);

  const unlockManual = () => {
    if (manualPwd.length < 6) { notify("Manual password must be ≥6 characters", "error"); return; }
    setUnlockingManual(true);
    setTimeout(() => {
      setUnlockingManual(false); setDecrypted(true); setShowManualBar(false);
      notify("🔓 Decoding Manual unlocked. AES-256-GCM keys loaded into WebCrypto API. Volatile memory only.", "success");
    }, 2000);
  };

  const toggleVoice = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { notify("Voice input requires Chrome/Edge browser.", "error"); return; }
    if (listening) { recogRef.current?.stop(); setListening(false); return; }
    const r = new SR(); r.lang = "en-IN"; r.continuous = false; r.interimResults = true;
    r.onresult = e => setQ(Array.from(e.results).map(x => x[0].transcript).join(""));
    r.onend = () => setListening(false);
    r.onerror = () => { setListening(false); notify("Voice error — use text input.", "error"); };
    r.start(); recogRef.current = r; setListening(true);
    notify("🎤 Listening… speak your BHELVIZ query.", "info");
  };

  const execute = async (queryText) => {
    const utterance = (typeof queryText === 'string' && queryText.trim().length > 0) ? queryText.trim() : q.trim();
    if (!utterance) { notify("Enter a query first", "error"); return; }
    if (!decrypted) { notify("Unlock the Decoding Manual before querying", "warning"); return; }
    // append user message to conversation
    const userMsg = { role: 'user', content: utterance, ts: new Date().toLocaleTimeString("en-IN") };
    setMessages(m => [...m, userMsg]);
    setLoading(true); setFeedback(null);

    let irRes = {};
    let rows = [];
    let display = [];
    let chart = [];
    let rawCount = 0;

    try {
      if (token) {
        const sessionId = `web-${Date.now()}`;
        const resp = await fetch('/query', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
          },
          body: JSON.stringify({ utterance, session_id: sessionId, history }),
        });

        if (!resp.ok) {
          const errBody = await resp.json().catch(() => ({}));
          throw new Error(`Server query failed: ${resp.status} — ${errBody.detail || resp.statusText}`);
        }
        const envelope = await resp.json();

        // ── Handle document (pure RAG) response ─────────────────────────
        if (envelope.type === 'document') {
          const botMsg = {
            role: 'assistant',
            type: 'document',
            content: envelope.answer || '',
            document: { answer: envelope.answer || '' },
            citations: envelope.citations || [],
            ts: new Date().toLocaleTimeString("en-IN"),
          };
          setMessages(m => [...m, botMsg]);
          setQHistory(h => [{ q: utterance, ts: new Date().toLocaleTimeString("en-IN"), n: 0 }, ...h.slice(0, 7)]);
          notify('✓ Document answer retrieved via RAG', 'success');
          setLoading(false);
          return;
        }

        // ── Unwrap structured / hybrid envelope ──────────────────────────
        // dev_main returns { type: "structured", data: { rows, ir, intent, ... } }
        // or              { type: "hybrid",     structured: { ... }, document: { ... } }
        const structuredPayload = envelope.type === 'hybrid'
          ? (envelope.structured || {})
          : (envelope.data || envelope); // fallback: old flat shape

        irRes = structuredPayload.ir || structuredPayload.structured_ir || {};
        rows  = structuredPayload.rows || [];

        display = rows.map(r => ({
          'Emp No':     r.employee_no  || r.emp_id      || '',
          'Name':       r.full_name    || r.employee_name || '',
          'Department': r.dept_name    || r.dept_code   || r.department || '',
          'Role':       r.current_role_code || r.role   || '',
          'Shift':      r.shift        || r.shift_code  || r.shift_name || '',
          'Status':     r.status       || r.attendance_status || '',
          'Att. Date':  r.att_date     || r.attendance_ts || null,
          'Penalty':    (r.penalty !== undefined && r.penalty !== null) ? r.penalty
                        : (r.attendance_penalty !== undefined ? r.attendance_penalty : '—'),
        }));

        const map = {};
        rows.forEach(r => {
          const k = r.status || r.attendance_status || r.attendance_status_code || 'UNKNOWN';
          map[k] = (map[k] || 0) + 1;
        });
        chart = Object.entries(map).map(([name, value]) => ({ name, value }));
        rawCount = rows.length;

        setIR(irRes);
        setDecryptAnim(true);
        await new Promise(r => setTimeout(r, 600));
        setDecryptAnim(false);
        setResults({
          display, chart, rawCount,
          intent: structuredPayload.intent || irRes.intent,
          desc:   structuredPayload.description || irRes.description,
        });

        const botMsg = {
          role: 'assistant',
          type: envelope.type === 'hybrid' ? 'hybrid' : 'structured',
          content: structuredPayload.description || '',
          structured: {
            data: { display, chart },
            chartType: structuredPayload.chart_type || irRes.chart_type || 'table',
            intent: structuredPayload.intent,
            description: structuredPayload.description,
          },
          // for hybrid: attach the document part so ChatWorkspace can render it
          ...(envelope.type === 'hybrid' && envelope.document
            ? { document: envelope.document, citations: envelope.document.citations || [] }
            : {}),
          ts: new Date().toLocaleTimeString("en-IN"),
        };
        setMessages(m => [...m, botMsg]);
      } else {
        irRes = await fetchIR(utterance, history);
        setIR(irRes);
        setDecryptAnim(true);
        await new Promise(r => setTimeout(r, 800));
        setDecryptAnim(false);
        const qres = runQuery(irRes, decrypted);
        display = qres.display; chart = qres.chart; rawCount = qres.rawCount;
        setResults({ display, chart, rawCount, intent: irRes.intent, desc: irRes.description });
        const botMsg = { role: 'assistant', type: 'structured', structured: { data: { display, chart }, chartType: irRes.chart_type || 'table', intent: irRes.intent, description: irRes.description }, ts: new Date().toLocaleTimeString("en-IN") };
        setMessages(m => [...m, botMsg]);
      }

      setChartType((irRes && irRes.chart_type) ? irRes.chart_type : "table");
      setHistory(h => [...h.slice(-18), { role: "user", content: utterance }, { role: "assistant", content: JSON.stringify(irRes) }]);
      setQHistory(h => [{ q: utterance, ts: new Date().toLocaleTimeString("en-IN"), n: rawCount }, ...h.slice(0, 7)]);
      notify(`✓ ${rawCount} records retrieved — client-side decryption complete`, "success");
    } catch (e) { notify("Query failed: " + e.message, "error"); }
    finally { setLoading(false); }
  };

  const SAMPLES = [
    "Show all absent executives today",
    "Compare attendance by department",
    "List false attendance cases in night shift",
    "How many supervisors in Power Systems?",
    "Employees in R&D hired after 2015",
    "Anomaly detection — penalty cases this week",
  ];

  const handleNavigation = (navItem) => {
    setCurrentNav(navItem);
    if (navItem === "new_chat") {
      setMessages([]);
      setResults(null);
      setQ("");
      setCurrentNav("dashboard");
      notify("New chat started", "info");
    }
  };

  // Render different views based on navigation
  const renderContent = () => {
    switch(currentNav) {
      case "dashboard":
        return (
          <div style={{flex:1,display:'flex',flexDirection:'column'}}>
            {/* Top header */}
            <div style={{ padding: 12, borderBottom: `1px solid ${C.border}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 20, color: C.goldBright }}>BHELVIZ Dashboard</div>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <Dot ok={true} />
                <span className="small-muted">READ-ONLY · ORACLE 19c</span>
              </div>
            </div>

            {showManualBar && (
              <div style={{ padding: 12 }}>
                <div className="manual-bar glass" style={{ display: 'flex', gap: 12, alignItems: 'center', padding: 12 }}>
                  <div style={{ fontSize: 20 }}>🔐</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700 }}>Decoding Manual Required</div>
                    <div className="small-muted">Enter your manual password to unlock client-side AES-256-GCM decryption.</div>
                  </div>
                  <input value={manualPwd} onChange={e => setManualPwd(e.target.value)} placeholder="Manual password" type="password" onKeyDown={e => e.key === 'Enter' && unlockManual()} className="manual-input" aria-label="Manual password" />
                  <button className="unlock-btn" onClick={unlockManual} disabled={unlockingManual} aria-label="Unlock manual">{unlockingManual ? 'Unlocking…' : 'Unlock'}</button>
                </div>
              </div>
            )}

            <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
              <ErrorBoundary>
                <ChatWorkspace messages={messages} onSend={(txt)=>{ setQ(txt); execute(txt); }} inputValue={q} setInputValue={setQ} loading={loading} />
              </ErrorBoundary>
              <RightSidebar ir={ir} qHistory={qHistory} />
            </div>
          </div>
        );
      
      case "analytics":
        return (
          <div style={{flex:1,display:'flex',flexDirection:'column', padding: 20, overflow: 'auto'}}>
            <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 24, color: C.goldBright, marginBottom: 20 }}>Analytics</div>
            <Panel style={{ padding: 20, marginBottom: 20 }}>
              <div style={{ color: C.textDim, fontSize: 14 }}>
                <div style={{ marginBottom: 10 }}>📊 Advanced analytics features coming soon.</div>
                <div>Monitor key metrics, trends, and system performance data with detailed visualizations.</div>
              </div>
            </Panel>
          </div>
        );
      
      case "reports":
        return (
          <div style={{flex:1,display:'flex',flexDirection:'column', padding: 20, overflow: 'auto'}}>
            <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 24, color: C.goldBright, marginBottom: 20 }}>Reports</div>
            <Panel style={{ padding: 20, marginBottom: 20 }}>
              <div style={{ color: C.textDim, fontSize: 14 }}>
                <div style={{ marginBottom: 10 }}>📋 Generate and manage detailed reports.</div>
                <div>Create compliance reports, audit trails, and export data in multiple formats.</div>
              </div>
            </Panel>
          </div>
        );
      
      case "settings":
        return (
          <div style={{flex:1,display:'flex',flexDirection:'column', padding: 20, overflow: 'auto'}}>
            <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 24, color: C.goldBright, marginBottom: 20 }}>Settings</div>
            <Panel style={{ padding: 20, marginBottom: 20 }}>
              <div style={{ marginBottom: 15 }}>
                <div style={{ fontWeight: 600, marginBottom: 10 }}>System Settings</div>
                <div style={{ color: C.textDim, fontSize: 13 }}>User Role: <span style={{ color: C.textBright }}>{userRole?.toUpperCase() || 'USER'}</span></div>
              </div>
              <div style={{ marginTop: 20, paddingTop: 20, borderTop: `1px solid ${C.border}` }}>
                <Btn onClick={onLogout} variant="err" style={{ fontSize: 12 }}>LOGOUT</Btn>
              </div>
            </Panel>
          </div>
        );
      
      default:
        return null;
    }
  };

  return (
    <div className="fc-theme-app">
      <Sidebar onNav={handleNavigation} />
      {renderContent()}
    </div>
  );
}

/* ── ROOT APP ────────────────────────────────────────────────────── */
export default function App() {
  const USE_NEW_UI = typeof import.meta !== 'undefined' && import.meta.env && (import.meta.env.VITE_USE_NEW_UI === 'true' || import.meta.env.REACT_APP_USE_NEW_UI !== 'false');
  const [screen, setScreen] = useState("login");
  const [role, setRole] = useState(null);
  const [notif, setNotif] = useState(null);
  const [token, setToken] = useState(null);

  const notify = (msg, type = "info") => {
    setNotif({ msg, type });
    setTimeout(() => setNotif(null), 3800);
  };

  const login = (r, tok) => {
    setRole(r); setScreen(r === "admin" ? "admin" : "dashboard");
    setToken(tok || null);
    notify(r === "admin" ? "Welcome, Administrator. MFA verified. Admin console loaded." : "Session started. Unlock your Decoding Manual to begin querying.", "success");
  };
  const logout = () => { setScreen("login"); setRole(null); notify("Session terminated. All keys cleared from memory.", "info"); };

  return (
    <>
      {notif && <Notif msg={notif.msg} type={notif.type} />}
      {screen === "login" && <LoginScreen notify={notify} onLogin={login} setToken={setToken} />}
      {screen === "admin" && <AdminScreen notify={notify} onLogout={logout} />}
      {screen === "dashboard" && (
        USE_NEW_UI ? <Dashboard userRole={role} token={token} notify={notify} onLogout={logout} /> : <OldDashboard userRole={role} token={token} notify={notify} onLogout={logout} />
      )}
    </>
  );
}
