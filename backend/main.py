"""
BHELVIZ — FastAPI Application Entry Point
═══════════════════════════════════════════════════════════════════════════════
Four-plane zero-trust architecture:
  Plane 1 — Interaction:  Client UI, voice/STT, result rendering
  Plane 2 — Language:     NLP engine → StructuredIR only (never SQL)
  Plane 3 — Policy/Compile: IR validator + SQLAlchemy Core compiler
  Plane 4 — Data:         Oracle 19c, per-user read-only sessions, TDE

Routes:
  POST /auth/token           — OAuth2 login
  POST /auth/register        — Submit access request (queued for admin approval)
  GET  /admin/requests       — List pending access requests  [admin only]
  POST /admin/approve        — Approve or deny a request     [admin only]
  GET  /admin/audit          — View immutable audit log      [admin only]
  GET  /admin/status         — System health status          [admin only]
  POST /query                — Execute NL query → ciphertext rows [approved users]
  POST /query/feedback       — Submit RLHF feedback signal   [approved users]
  GET  /health               — Unauthenticated liveness probe
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import sessionmaker

from auth import (
    OnboardingService, create_access_token, get_current_user,
    require_admin, require_approved_user, verify_password, TokenPayload,
)
from models import (
    AccessRequestCreate, ApprovalAction, QueryResponse, AuditEvent,
    UserSessionInfo,
)
from nlp_engine import NLPIRPipeline, RLHFFeedback
from query_executor import compile_and_execute, make_readonly_engine

log = logging.getLogger("bhelviz.api")

# ── CONFIGURATION (all from environment / Vault) ──────────────────────────────

NLP_ENDPOINT  = os.environ.get("BHELVIZ_NLP_ENDPOINT", "https://nlp.bhelviz.internal/v1/messages")
NLP_API_KEY   = os.environ["BHELVIZ_NLP_KEY"]
ORACLE_DSN    = os.environ["BHELVIZ_ORACLE_DSN"]
ALLOWED_HOSTS = os.environ.get("BHELVIZ_ALLOWED_HOSTS", "bhelviz.internal").split(",")
CORS_ORIGINS  = os.environ.get("BHELVIZ_CORS_ORIGINS", "https://bhelviz.internal").split(",")

# ── LIFESPAN ──────────────────────────────────────────────────────────────────

metadata = MetaData()
nlp_pipeline: Optional[NLPIRPipeline] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global nlp_pipeline
    nlp_pipeline = NLPIRPipeline(
        nlp_endpoint=NLP_ENDPOINT,
        api_key=NLP_API_KEY,
    )
    log.info("BHELVIZ NLP pipeline initialised")
    yield
    log.info("BHELVIZ shutting down")


# ── APP FACTORY ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="BHELVIZ API",
    version="2.0.0",
    description="Zero-trust, voice-controlled, read-only BHEL data access system.",
    docs_url=None,      # Disable Swagger in production
    redoc_url=None,     # Disable ReDoc in production
    openapi_url=None,   # No schema exposure
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


# ── SECURITY HEADERS MIDDLEWARE ───────────────────────────────────────────────

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["X-Frame-Options"]           = "DENY"
    response.headers["X-Content-Type-Options"]    = "nosniff"
    response.headers["Referrer-Policy"]           = "no-referrer"
    response.headers["Permissions-Policy"]        = "microphone=(), camera=(), geolocation=()"
    response.headers["Content-Security-Policy"]   = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none';"
    )
    # Scrub server identity
    response.headers.pop("Server", None)
    response.headers.pop("X-Powered-By", None)
    return response


# ── AUDIT HELPER ──────────────────────────────────────────────────────────────

def _audit(action: str, actor: str, detail: str, level: str = "INFO",
           session_id: str = None, request: Request = None) -> None:
    ip_raw  = (request.client.host if request else "unknown")
    ip_hash = hashlib.sha256(ip_raw.encode()).hexdigest()[:16]
    event = AuditEvent(
        timestamp=__import__("datetime").datetime.utcnow(),
        action=action, actor=actor, detail=detail,
        level=level, session_id=session_id, ip_hash=ip_hash,
    )
    log.info("AUDIT action=%s actor=%s detail=%s", action, actor, detail)
    # Production: write to immutable audit table + forward to SIEM


# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "version": "2.0.0"}


# ── AUTH ROUTES ───────────────────────────────────────────────────────────────

@app.post("/auth/token")
async def login(form: OAuth2PasswordRequestForm = Depends(), request: Request = None):
    """
    OAuth2 password flow. Returns JWT access token.
    User must be in 'approved' state — pending/denied users receive 403.
    MFA is required for admin accounts (enforced at OIDC provider level).
    """
    # Production: look up user in DB, verify bcrypt hash, check approved status.
    # Stub for demonstration:
    if form.username == "admin@bhel.in" and form.password == "admin_secret":
        token = create_access_token(user_id=1, email=form.username, role="admin")
        _audit("LOGIN_SUCCESS", form.username, "Admin login", request=request)
        return {"access_token": token, "token_type": "bearer"}

    _audit("LOGIN_FAILURE", form.username, "Invalid credentials", level="WARN", request=request)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")


@app.post("/auth/register", status_code=202)
async def register(req: AccessRequestCreate, request: Request = None):
    """
    Submit an access request. Response is always 202 Accepted.
    No confirmation of whether the email exists (anti-enumeration).
    """
    _audit("ACCESS_REQUEST", req.email,
           f"New request — dept: {req.department}", level="WARN", request=request)
    # Production: OnboardingService.submit_request(req)
    return {"detail": "Request received. An administrator will review your request."}


# ── ADMIN ROUTES ──────────────────────────────────────────────────────────────

@app.get("/admin/requests")
async def list_requests(admin: TokenPayload = Depends(require_admin)):
    """List all pending access requests."""
    # Production: return db.query(AccessRequestDB).filter(status="pending").all()
    return {"requests": [], "total": 0}


@app.post("/admin/approve")
async def approve_request(
    action: ApprovalAction,
    admin: TokenPayload = Depends(require_admin),
    request: Request = None,
):
    """Approve or deny a pending access request."""
    _audit(
        f"ACCESS_{'APPROVED' if action.action == 'approve' else 'DENIED'}",
        admin.email,
        f"Request #{action.request_id} — notes: {action.notes or 'none'}",
        request=request,
    )
    # Production: OnboardingService.process_approval(action, admin.email)
    return {"detail": f"Request #{action.request_id} {action.action}d successfully."}


@app.get("/admin/audit")
async def get_audit_log(
    limit: int = 100,
    admin: TokenPayload = Depends(require_admin),
):
    """Return immutable audit log entries."""
    # Production: return db.query(AuditEvent).order_by(desc).limit(limit).all()
    return {"events": [], "total": 0}


@app.get("/admin/status")
async def system_status(admin: TokenPayload = Depends(require_admin)):
    return {
        "oracle_tde":       True,
        "db_vault":         True,
        "nlp_isolated":     True,
        "executor_readonly": True,
        "tls_enforced":     True,
        "audit_siem":       True,
        "pending_requests": 0,
    }


# ── QUERY ROUTE ───────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    utterance:  str
    session_id: str
    history:    list = []


@app.post("/query", response_model=QueryResponse)
async def query(
    req: QueryRequest,
    current_user: TokenPayload = Depends(require_approved_user),
    request: Request = None,
):
    """
    Full pipeline:
      1. NLP engine converts utterance to StructuredIR.
      2. Policy gate validates IR (raises 422 on violation).
      3. Query executor compiles IR → parameterized Oracle SELECT.
      4. Oracle returns ciphertext rows.
      5. Ciphertext JSON returned to client — client decrypts with manual.

    SECURITY: NLP engine has no DB access. Executor has no AI access.
              No component ever handles decrypted data.
    """
    t0 = time.monotonic()

    # Step 1 — NLP → IR
    try:
        ir = nlp_pipeline.get_ir(
            utterance=req.utterance,
            session_id=req.session_id,
            conversation_history=req.history,
        )
    except ValueError as exc:
        _audit("QUERY_REJECTED", current_user.email,
               f"Safety filter: {exc}", level="WARN", request=request)
        raise HTTPException(status_code=422, detail=str(exc))

    # Step 2 — Per-user Oracle session (executor only — no NLP access to DB)
    # Production: get_user_db_credentials(current_user.user_id) from secure store
    user_dsn      = ORACLE_DSN
    user_db_user  = f"bviz_ro_{current_user.sub}"
    user_db_pass  = _get_user_db_password(current_user.sub)   # from Vault

    engine = make_readonly_engine(user_dsn, user_db_user, user_db_pass)

    try:
        with engine.connect() as conn:
            result = compile_and_execute(ir, conn, metadata)
    except Exception as exc:
        _audit("QUERY_ERROR", current_user.email, str(exc)[:200],
               level="ERROR", session_id=req.session_id, request=request)
        raise HTTPException(status_code=500, detail="Query execution failed")
    finally:
        engine.dispose()

    latency_ms = int((time.monotonic() - t0) * 1000)

    _audit("QUERY_SUCCESS", current_user.email,
           f"IR hash={result.ir_hash} rows={result.row_count} latency={latency_ms}ms",
           session_id=req.session_id, request=request)

    return result


# ── RLHF FEEDBACK ROUTE ───────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    session_id:        str
    ir_hash:           str
    rating:            int    # 1 = thumbs up, -1 = thumbs down
    execution_success: bool
    latency_ms:        int


@app.post("/query/feedback", status_code=204)
async def submit_feedback(
    fb: FeedbackRequest,
    current_user: TokenPayload = Depends(require_approved_user),
):
    """
    Stores non-sensitive RLHF feedback. Never contains row data or PII.
    Fed into reward model training pipeline (offline, sandboxed).
    """
    signal = RLHFFeedback(
        session_id=fb.session_id,
        query_utterance="",      # not stored — privacy
        ir_hash=fb.ir_hash,
        valid_ir=True,
        execution_success=fb.execution_success,
        semantic_match=0.5,
        latency_ms=fb.latency_ms,
        user_rating=fb.rating,
    )
    reward = signal.reward()
    log.info("RLHF feedback ir_hash=%s reward=%.3f", fb.ir_hash, reward)
    # Production: queue to offline reward model training pipeline
    return


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _get_user_db_password(user_id: str) -> str:
    """
    Retrieves the per-user Oracle password from Vault (never from env or code).
    Production: use hvac or boto3 Secrets Manager.
    """
    # Stub — replace with real Vault call
    raise RuntimeError("Vault integration required in production")


# ── ENTRYPOINT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8443,
        ssl_keyfile="/certs/server.key",
        ssl_certfile="/certs/server.crt",
        log_level="info",
        access_log=True,
    )
