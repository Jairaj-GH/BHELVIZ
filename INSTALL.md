# BHELVIZ — Installation & Running Guide

> **Zero-Trust · Voice-Controlled · Read-Only · Admin-Gated · AES-256-GCM**

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Prerequisites](#2-prerequisites)
3. [Quick Start — Development (5 minutes)](#3-quick-start--development-5-minutes)
4. [Configuration Reference](#4-configuration-reference)
5. [Running in Production (Oracle 19c)](#5-running-in-production-oracle-19c)
6. [Docker Compose Deployment](#6-docker-compose-deployment)
7. [Feature Walkthrough](#7-feature-walkthrough)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Project Structure

```
bhelviz/
├── backend/
│   ├── main.py              ← Production FastAPI app (Oracle + Vault)
│   ├── dev_main.py          ← Development FastAPI app (SQLite, no Oracle)
│   ├── auth.py              ← JWT auth, admin-gated onboarding
│   ├── models.py            ← Pydantic models (StructuredIR, etc.)
│   ├── nlp_engine.py        ← NLP→IR pipeline, safety filters, RLHF
│   ├── query_executor.py    ← IR validator + SQLAlchemy Core compiler
│   ├── database.py          ← Dev SQLite schema + seed data (200 employees)
│   ├── schema.sql           ← Oracle 19c DDL (production)
│   ├── requirements.txt     ← Production deps (Oracle, PyTorch, etc.)
│   ├── dev_requirements.txt ← Dev deps (SQLite only, fast install)
│   └── Dockerfile           ← API container image
│
├── frontend/
│   ├── src/
│   │   ├── App.jsx          ← Full BHELVIZ React UI
│   │   └── main.jsx         ← React entry point
│   ├── public/
│   │   └── favicon.svg
│   ├── index.html
│   ├── vite.config.js       ← Vite + proxy to backend
│   ├── package.json
│   └── .env.example
│
├── monitoring/
│   └── prometheus.yml
├── scripts/
│   ├── setup_dev.sh         ← One-shot dev environment setup
│   └── gen_certs.sh         ← Generate self-signed TLS certs
├── docker-compose.yml
├── .env.example
└── INSTALL.md               ← This file
```

---

## 2. Prerequisites

| Tool | Minimum Version | Install |
|------|----------------|---------|
| Python | 3.11+ | https://python.org |
| Node.js | 18+ | https://nodejs.org |
| npm | 9+ | (comes with Node) |
| Git | Any | https://git-scm.com |
| Docker + Compose | 24+ | https://docs.docker.com/get-docker/ *(optional)* |

**Production-only:**
- Oracle 19c database with TDE
- HashiCorp Vault or Oracle Key Vault
- nShield HSM (for master key)

---

## 3. Quick Start — Development (5 minutes)

Development mode uses **SQLite** with 200 mock employees and 7 days of attendance data. No Oracle, no Vault, no Docker required.

### Step 1: Clone / Unzip

```bash
# If you received a zip archive:
unzip bhelviz.zip -d bhelviz
cd bhelviz

# Or if using git:
git clone https://github.com/your-org/bhelviz.git
cd bhelviz
```

### Step 2: Run the one-shot setup script

```bash
bash scripts/setup_dev.sh
```

This will:
- Check Python 3.11+ and Node 18+
- Create `backend/.venv` and install Python deps
- Install frontend npm packages
- Create `frontend/.env` from the template

### Step 3: Start the backend

Open **Terminal 1**:

```bash
cd backend
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

export BHELVIZ_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

# Optional: set your Anthropic API key for real NLP (otherwise rule-based fallback)
# export BHELVIZ_NLP_KEY="sk-ant-your-key-here"

uvicorn dev_main:app --reload --port 8000
```

Expected output:
```
INFO:     BHELVIZ v2.0 started
INFO:     Dev SQLite database initialised
INFO:     200 employees seeded
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Step 4: Start the frontend

Open **Terminal 2**:

```bash
cd frontend
npm run dev
```

Expected output:
```
  VITE v5.x  ready in 300ms
  ➜  Local:   http://localhost:5173/
```

### Step 5: Open the app

Open **http://localhost:5173** in your browser.

**Demo credentials:**

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin@bhel.in` | `admin` |
| User | `any@bhel.in` | any 6+ character string (simulates manual password) |

---

## 4. Configuration Reference

### Backend environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BHELVIZ_JWT_SECRET` | **Yes** | — | JWT signing secret. Generate: `openssl rand -hex 32` |
| `BHELVIZ_NLP_KEY` | No | — | Anthropic API key. Without it, rule-based fallback IR is used. |
| `BHELVIZ_NLP_ENDPOINT` | No | Anthropic API | NLP endpoint URL (self-hosted FLAN-T5 in production) |
| `BHELVIZ_ORACLE_DSN` | Prod only | — | Oracle DSN e.g. `host:1521/BHELVIZ` |
| `BHELVIZ_ALLOWED_HOSTS` | No | `*` | Comma-separated allowed hostnames |
| `BHELVIZ_CORS_ORIGINS` | No | `*` | Comma-separated allowed CORS origins |
| `BHELVIZ_DEV_DB` | No | `bhelviz_dev.db` | SQLite database file path (dev mode only) |
| `BHELVIZ_DEV_MODE` | No | `true` | Set to `false` to force Oracle mode |

### Frontend environment variables (`frontend/.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_BASE_URL` | *(Vite proxy)* | Backend URL. Leave blank in dev (Vite proxies automatically). |
| `VITE_ANTHROPIC_KEY` | — | Anthropic key for client-side NLP. **Not recommended for production.** |
| `VITE_USE_BACKEND_NLP` | `false` | Route NLP through backend instead of calling Anthropic directly. |

---

## 5. Running in Production (Oracle 19c)

### 5.1 Database setup

```bash
# Connect as DBA and run the full schema:
sqlplus bhelviz_dba@BHELVIZ @backend/schema.sql
```

The schema creates:
- TDE-encrypted tablespace `bhelviz_data`
- Tables: `department`, `role_lu`, `shift`, `employee`, `attendance`, etc.
- Read-only views: `employee_attendance_v`, `employee_dept_role_v`
- Oracle DB Vault realms (blocks DBA bypass)
- Immutable audit table

### 5.2 Vault configuration

```bash
# HashiCorp Vault — store per-user DB passwords:
vault kv put secret/bhelviz/db/user_1 password="$(openssl rand -hex 16)"

# Oracle Key Vault — rotate TDE master key every 90 days (automated via policy)
```

### 5.3 Production backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt    # full deps including oracledb + torch

export BHELVIZ_JWT_SECRET="$(vault kv get -field=value secret/bhelviz/jwt_secret)"
export BHELVIZ_NLP_KEY="$(vault kv get -field=value secret/bhelviz/nlp_key)"
export BHELVIZ_ORACLE_DSN="oracle-host:1521/BHELVIZ"
export BHELVIZ_ALLOWED_HOSTS="bhelviz.internal"
export BHELVIZ_CORS_ORIGINS="https://bhelviz.internal"
export BHELVIZ_DEV_MODE="false"

# Generate TLS certificate (or use your CA-signed cert):
bash scripts/gen_certs.sh /certs

# Start production server (TLS, port 8443):
uvicorn main:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile /certs/server.key \
  --ssl-certfile /certs/server.crt \
  --log-level info
```

### 5.4 Production frontend build

```bash
cd frontend
cp .env.example .env
# Edit .env: set VITE_API_BASE_URL=https://bhelviz.internal:8443
#            set VITE_USE_BACKEND_NLP=true (no API key client-side)
npm install
npm run build
# Serve dist/ with nginx / caddy / any static host
```

---

## 6. Docker Compose Deployment

```bash
# 1. Set secrets
cp .env.example .env
# Edit .env: fill in BHELVIZ_JWT_SECRET, BHELVIZ_NLP_KEY, BHELVIZ_ORACLE_DSN

# 2. Generate TLS certs
bash scripts/gen_certs.sh ./certs

# 3. Build and start all services
docker compose up --build

# Services started:
#   api           → https://localhost:8443
#   nlp           → internal only (no external port)
#   admin_console → https://localhost:9443
#   prometheus    → http://localhost:9090
```

**Note:** The Docker Compose file is configured for staging/production with Oracle. For local dev without Docker, use the Quick Start guide above (Step 3).

---

## 7. Feature Walkthrough

### Login Screen
- **Admin login**: `admin@bhel.in` / `admin` → opens Admin Console
- **User login**: any `@bhel.in` email + 6+ char password → opens Dashboard
- **Access request**: "Request Access" button → queued for admin approval

### Admin Console
- **Pending requests** tab: approve or deny access requests
- **Audit log** tab: immutable event log (logins, queries, approvals)
- **System status** tab: TDE, Vault, executor, NLP health indicators

### Dashboard (User)
1. **Unlock Decoding Manual**: enter your manual password (demo: any 6+ chars)
   - Simulates WebCrypto AES-256-GCM decryption of employee PII
   - After unlock: encrypted column values are shown as plaintext
2. **Query bar**: type a natural language question, e.g.:
   - *"Show absent employees in Power Systems today"*
   - *"Compare attendance across roles as a pie chart"*
   - *"Find false attendance records in the night shift"*
   - *"How many supervisors were late last week?"*
3. **Voice input**: click the microphone icon to speak a query (WebSpeech API)
4. **Results**: displayed as table, bar chart, pie chart, or area chart
5. **IR Viewer** (right panel): shows the StructuredIR produced by NLP — never SQL
6. **RLHF feedback**: thumbs up/down on each result → feeds reward model

### Security indicators (status bar)
Shows live: Oracle TDE status, AES-256-GCM, Executor READ-ONLY, AI: IR-only, active sessions.

---

## 8. Troubleshooting

### Backend won't start: `KeyError: 'BHELVIZ_JWT_SECRET'`
```bash
export BHELVIZ_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

### Frontend proxy error: `connect ECONNREFUSED 127.0.0.1:8000`
Make sure the backend is running on port 8000 before starting the frontend.

### NLP returns fallback IR (no real query understanding)
Set `BHELVIZ_NLP_KEY` to your Anthropic API key. Without it, a simple keyword-based IR is used.

### SQLite database is empty / missing seed data
Delete `backend/bhelviz_dev.db` and restart the backend — it will reseed automatically.

### Voice input not working
- Voice input requires HTTPS or `localhost`. It works on `http://localhost:5173` in Chrome.
- On Firefox, enable `dom.webSpeech.enabled` in `about:config`.
- On mobile, grant microphone permissions.

### `bcrypt` install fails on Windows
```bash
pip install bcrypt --only-binary :all:
```

### Oracle connection errors (production)
- Verify `BHELVIZ_ORACLE_DSN` format: `host:port/service_name`
- `oracledb` in thin mode requires no Oracle Client libraries — just Python 3.11+
- Check firewall allows TCP to Oracle port 1521

### CORS errors in browser
In dev mode, Vite proxies all `/auth`, `/query`, `/admin` requests to `localhost:8000` — no CORS issue. If you changed the port, update `vite.config.js`.

---

## Architecture Security Summary

```
Browser (React)
     │  utterance only (voice → STT → text)
     ▼
NLP Engine (Anthropic / FLAN-T5)
     │  StructuredIR JSON only — never SQL, never row data
     ▼
IR Validator + SQLAlchemy Core Compiler
     │  parameterized SELECT only — all values are bind params
     ▼
Oracle 19c (TDE + AES-256-GCM)
     │  ciphertext rows returned — server never decrypts
     ▼
Browser (WebCrypto AES-256-GCM)
     │  decrypted with user's Decoding Manual password
     ▼
React UI — plaintext rendered only in the user's browser
```

**The AI never sees real data. The database never receives raw SQL.
The server never holds the decryption key. Only the user's browser decrypts.**
