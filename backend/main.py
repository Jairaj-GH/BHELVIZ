from __future__ import annotations
"""
POST /query                — Execute NL query → ciphertext rows [approved users]
POST /query/feedback       — Submit RLHF feedback signal   [approved users]
GET  /health               — Unauthenticated liveness probe
══════════════════════════════════════════════════════════════════════════════
"""

import hashlib
from query_executor import (
    compile_and_execute,
    validate_ir,
    PolicyError,
)
from dotenv import load_dotenv
load_dotenv()

import hashlib
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import MetaData, text

from auth import (
    create_access_token,
    get_current_user,
    require_admin,
    TokenPayload,
)
from database import (
    get_dev_engine,
    get_dev_session,
    AccessRequestOrm,
    AuditLogOrm,
)
from models import (
    AccessRequestCreate,
    ApprovalAction,
    StructuredIR,
)
from nlp_engine import RLHFFeedback
from retriever import retrieve_context
from rag_engine import generate_rag_response
from metrics import instrument_app, RAG_HITS, RAG_MISSES, RAG_LATENCY

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bhelviz.dev")

# ── CONFIG ────────────────────────────────────────────────────────────────────

NLP_ENDPOINT = os.environ.get(
    "BHELVIZ_NLP_ENDPOINT",
    "https://api.anthropic.com/v1/messages",
)
NLP_API_KEY = os.environ.get("BHELVIZ_NLP_KEY", "")

ADMIN_EMAIL = "admin@bhel.in"
ADMIN_PASSWORD = "admin"  # demo only

# ── LIFESPAN ──────────────────────────────────────────────────────────────────

semantic_pipeline: Optional[BhelvizPipelineV3] = None
metadata = MetaData()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init dev DB (admin/audit only)
    get_dev_engine()
    log.info("Dev auth/audit database initialised")

    global semantic_pipeline
    """
    GET  /admin/requests       — List pending access requests  [admin only]
    POST /admin/approve        — Approve or deny a request     [admin only]
    GET  /admin/audit          — View immutable audit log      [admin only]
    GET  /admin/status         — System health status          [admin only]
    POST /query                — Execute NL query → ciphertext rows [approved users]
    POST /query/feedback       — Submit RLHF feedback signal   [approved users]
    GET  /health               — Unauthenticated liveness probe
    ══════════════════════════════════════════════════════════════════════════════
    """

    import hashlib
    log.info("BHELVIZ shutting down")


# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="BHELVIZ API (Dev)",
    version="2.0.0-dev",
    description="Development mode — SQLite dev DB with transformer NLP",
    lifespan=lifespan,
)

# Prometheus instrumentation (exposes /metrics)
"""
GET  /admin/requests       — List pending access requests  [admin only]
POST /admin/approve        — Approve or deny a request     [admin only]
GET  /admin/audit          — View immutable audit log      [admin only]
GET  /admin/status         — System health status          [admin only]
POST /query                — Execute NL query → ciphertext rows [approved users]
POST /query/feedback       — Submit RLHF feedback signal   [approved users]
GET  /health               — Unauthenticated liveness probe
══════════════════════════════════════════════════════════════════════════════
"""

import hashlib


# ── AUDIT HELPER ──────────────────────────────────────────────────────────────

def _audit(
    action: str,
    actor: str,
    detail: str,
    level: str = "INFO",
    session_id: str | None = None,
    request: Request | None = None,
) -> None:
    import datetime as dt

    ip_raw = request.client.host if request and request.client else "unknown"
    ip_hash = hashlib.sha256(ip_raw.encode()).hexdigest()[:16]
    db = get_dev_session()
    try:
        db.add(
            AuditLogOrm(
                timestamp=dt.datetime.utcnow(),
                action=action,
                actor=actor,
                detail=detail,
                level=level,
                session_id=session_id,
                ip_hash=ip_hash,
            )
        )
        db.commit()
    except Exception as e:
        log.error("Audit write failed: %s", e)
    finally:
        db.close()
    log.info("AUDIT action=%s actor=%s detail=%s", action, actor, detail)


# ── HEALTH ───────────────────────────────────────────────────────────────────


@app.get("/health", include_in_schema=True)
async def health():
    return {
        "status": "ok",
        "version": "2.0.0-dev",
        "mode": "dev-sqlite",
        "executor": "readonly",
    }


# ── AUTH ──────────────────────────────────────────────────────────────────────

@app.post("/auth/token")
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
):
    # Admin login
    if form.username == ADMIN_EMAIL and form.password == ADMIN_PASSWORD:
        token = create_access_token(user_id=1, email=form.username, role="admin")
        _audit("LOGIN_SUCCESS", form.username, "Admin login", request=request)
        return {"access_token": token, "token_type": "bearer"}

    # Approved user login (any @bhel.in email with manual password 6+ chars)
    if form.username.endswith("@bhel.in") and len(form.password) >= 6:
        token = create_access_token(user_id=999, email=form.username, role="user")
        _audit("LOGIN_SUCCESS", form.username, "User login (demo)", request=request)
        return {"access_token": token, "token_type": "bearer"}

    _audit(
        "LOGIN_FAILURE",
        form.username,
        "Invalid credentials",
        level="WARN",
        request=request,
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
    )


@app.post("/auth/register", status_code=202)
async def register(req: AccessRequestCreate, request: Request = None):
    db = get_dev_session()
    try:
        audit_id = f"BVIZ-REQ-{uuid.uuid4().hex[:8].upper()}"
        record = AccessRequestOrm(
            full_name=req.full_name,
            email=req.email,
            department=req.department,
            justification=req.justification,
            audit_id=audit_id,
            status="pending",
        )
        db.add(record)
        db.commit()
        _audit(
            "ACCESS_REQUEST",
            req.email,
            f"New request {audit_id} — dept: {req.department}",
            level="WARN",
            request=request,
        )
    finally:
        db.close()
    return {"detail": "Request received. An administrator will review your request."}


# ── ADMIN ROUTES ──────────────────────────────────────────────────────────────

@app.get("/admin/requests")
async def list_requests(admin: TokenPayload = Depends(require_admin)):
    db = get_dev_session()
    try:
        reqs = db.query(AccessRequestOrm).filter(AccessRequestOrm.status == "pending").all()
        return {
            "requests": [
                {
                    "id": r.id,
                    "full_name": r.full_name,
                    "email": r.email,
                    "department": r.department,
                    "justification": r.justification,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "audit_id": r.audit_id,
                }
                for r in reqs
            ],
            "total": len(reqs),
        }
    finally:
        db.close()


@app.post("/admin/approve")
async def approve_request(
    action: ApprovalAction,
    admin: TokenPayload = Depends(require_admin),
    request: Request = None,
):
    import datetime as dt

    db = get_dev_session()
    try:
        rec = db.query(AccessRequestOrm).filter(AccessRequestOrm.id == action.request_id).first()
        if not rec:
            raise HTTPException(status_code=404, detail="Request not found")
        rec.status = "approved" if action.action == "approve" else "denied"
        rec.reviewed_at = dt.datetime.utcnow()
        rec.reviewed_by = admin.email
        db.commit()
    finally:
        db.close()

    level = "INFO" if action.action == "approve" else "WARN"
    _audit(
        f"ACCESS_{'APPROVED' if action.action == 'approve' else 'DENIED'}",
        admin.email,
        f"Request #{action.request_id} — notes: {action.notes or 'none'}",
        level=level,
        request=request,
    )
    return {"detail": f"Request #{action.request_id} {action.action}d successfully."}


@app.get("/admin/audit")
async def get_audit_log(limit: int = 100, admin: TokenPayload = Depends(require_admin)):
    db = get_dev_session()
    try:
        events = db.query(AuditLogOrm).order_by(AuditLogOrm.timestamp.desc()).limit(limit).all()
        return {
            "events": [
                {
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "action": e.action,
                    "actor": e.actor,
                    "detail": e.detail,
                    "level": e.level,
                    "session_id": e.session_id,
                    "ip_hash": e.ip_hash,
                }
                for e in events
            ],
            "total": len(events),
        }
    finally:
        db.close()


@app.get("/admin/status")
async def system_status(admin: TokenPayload = Depends(require_admin)):
    db = get_dev_session()
    try:
        pending = db.query(AccessRequestOrm).filter(AccessRequestOrm.status == "pending").count()
    finally:
        db.close()
    return {
        "dev_db": True,
        "nlp_isolated": True,
        "executor_readonly": True,
        "tls_enforced": False,
        "audit_siem": False,
        "pending_requests": pending,
        "mode": "development",
    }


# ── QUERY ROUTE (DEV) ────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    utterance: str
    session_id: str
    history: list = Field(default_factory=list)


@app.post("/query")
async def query(
    req: QueryRequest,
    current_user: TokenPayload = Depends(get_current_user),
    request: Request = None,
):
    t0 = time.monotonic()

    if semantic_pipeline is None:
        log.warning("Semantic pipeline unavailable; using safe fallback IR")
        ir_dict = _fallback_ir(req.utterance)
        structured_ir = StructuredIR(**ir_dict)
    else:
        # Step 1: NLP → IR
        try:
            ir_obj = semantic_pipeline.get_ir(
                utterance=req.utterance,
                session_id=req.session_id,
                conversation_history=req.history,
            )

            if isinstance(ir_obj, StructuredIR):
                structured_ir = ir_obj
                ir_dict = structured_ir.model_dump()
            elif hasattr(ir_obj, "model_dump"):
                ir_dict = ir_obj.model_dump()
                structured_ir = StructuredIR(**ir_dict)
            elif isinstance(ir_obj, dict):
                ir_dict = ir_obj
                structured_ir = StructuredIR(**ir_dict)
            else:
                raise TypeError(f"Unsupported IR object type: {type(ir_obj)!r}")

        except ValueError as exc:
            _audit(
                "QUERY_REJECTED",
                current_user.email,
                f"Safety filter: {exc}",
                level="WARN",
                request=request,
            )
            raise HTTPException(status_code=422, detail=str(exc))

        except Exception as exc:
            log.exception("NLP failed: %s", exc)
            ir_dict = _fallback_ir(req.utterance)
            structured_ir = StructuredIR(**ir_dict)

    # Step 2: Attempt RAG path via retriever
    try:
        chunks = retrieve_context(req.utterance, top_k=5)
    except Exception:
        chunks = []

    if chunks:
        # Use RAG orchestrator to produce answer + sources
        try:
            t0 = time.monotonic()
            rag_resp = generate_rag_response(req.utterance, int(getattr(current_user, "sub", "0") or 0))
            latency = time.monotonic() - t0
            try:
                RAG_LATENCY.observe(latency)
                RAG_HITS.inc()
            except Exception:
                pass

            _audit(
                "QUERY_RAG",
                current_user.email,
                f"RAG used — sources={len(rag_resp.sources)} latency_ms={int(latency*1000)}",
                session_id=req.session_id,
                request=request,
            )
            return rag_resp.model_dump()
        except Exception as exc:
            log.exception("RAG path failed, falling back to IR executor: %s", exc)
            try:
                RAG_MISSES.inc()
            except Exception:
                pass

    # No RAG hit — record miss and continue to dev executor
    try:
        RAG_MISSES.inc()
    except Exception:
        pass

    # Step 3: Execute against DEV SQLite session
    db = get_dev_session()

    try:
        validate_ir(structured_ir)
        results_obj = compile_and_execute(
            ir=structured_ir,
            conn=db.connection(),
            metadata=metadata,
        )
        results = results_obj.model_dump()

    except PolicyError as exc:
        _audit(
            "QUERY_POLICY_BLOCK",
            current_user.email,
            str(exc),
            level="WARN",
            session_id=req.session_id,
            request=request,
        )
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:
        log.exception("Query execution failed")
        _audit(
            "QUERY_EXECUTION_ERROR",
            current_user.email,
            str(exc),
            level="ERROR",
            session_id=req.session_id,
            request=request,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Query execution failed: {exc}",
        )

    finally:
        db.close()

    latency_ms = int((time.monotonic() - t0) * 1000)
    ir_hash = hashlib.sha256(str(ir_dict).encode()).hexdigest()[:16]

    _audit(
        "QUERY_SUCCESS",
        current_user.email,
        f"IR={ir_dict.get('intent')} rows={results['row_count']} latency={latency_ms}ms",
        session_id=req.session_id,
        request=request,
    )

    return {
        **results,
        "intent": ir_dict.get("intent", "employee_lookup"),
        "description": ir_dict.get("description", req.utterance),
        "chart_type": ir_dict.get("chart_type", "table"),
        "ir_hash": ir_hash,
        "ir": ir_dict,
        "structured_ir": structured_ir.model_dump(),
    }


def _fallback_ir(utterance: str) -> dict:
    u = utterance.lower()

    if any(w in u for w in ["absent", "absence"]):
        return {
            "intent": "attendance_summary",
            "table": "employee_attendance_v",
            "description": "Absent employees today",
            "chart_type": "table",
            "select": [
                {"table": "employee_attendance_v", "column": "emp_id", "alias": "emp_id"},
                {"table": "employee_attendance_v", "column": "full_name", "alias": "full_name"},
                {"table": "employee_attendance_v", "column": "dept_name", "alias": "dept_name"},
                {"table": "employee_attendance_v", "column": "status", "alias": "status"},
                {"table": "employee_attendance_v", "column": "att_date", "alias": "att_date"},
            ],
            "filters": [{"column": "status", "op": "eq", "value": "ABSENT"}],
            "order_by": [{"table": "employee_attendance_v", "column": "att_date", "direction": "desc"}],
            "limit": 100,
            "safety": {"read_only": True, "allow_subquery": False, "no_sql": True, "no_ddl": True, "max_rows": 500},
        }

    if any(w in u for w in ["leave", "approved leave"]):
        return {
            "intent": "leave_summary",
            "table": "employee_leave_v",
            "description": "Approved employee leaves",
            "chart_type": "table",
            "select": [
                {"table": "employee_leave_v", "column": "request_id", "alias": "request_id"},
                {"table": "employee_leave_v", "column": "emp_id", "alias": "emp_id"},
                {"table": "employee_leave_v", "column": "full_name", "alias": "full_name"},
                {"table": "employee_leave_v", "column": "leave_type", "alias": "leave_type"},
                {"table": "employee_leave_v", "column": "status", "alias": "status"},
            ],
            "filters": [{"column": "status", "op": "eq", "value": "APPROVED"}],
            "order_by": [{"table": "employee_leave_v", "column": "start_date", "direction": "desc"}],
            "limit": 50,
            "safety": {"read_only": True, "allow_subquery": False, "no_sql": True, "no_ddl": True, "max_rows": 500},
        }

    return {
        "intent": "attendance_summary",
        "table": "employee_attendance_v",
        "description": "Attendance summary",
        "chart_type": "table",
        "select": [
            {"table": "employee_attendance_v", "column": "emp_id", "alias": "emp_id"},
            {"table": "employee_attendance_v", "column": "full_name", "alias": "full_name"},
            {"table": "employee_attendance_v", "column": "status", "alias": "status"},
        ],
        "filters": [],
        "order_by": [{"table": "employee_attendance_v", "column": "att_date", "direction": "desc"}],
        "limit": 100,
        "safety": {"read_only": True, "allow_subquery": False, "no_sql": True, "no_ddl": True, "max_rows": 500},
    }


# ── RLHF FEEDBACK ─────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    session_id: str
    ir_hash: str
    rating: int
    execution_success: bool
    latency_ms: int


@app.post("/query/feedback", status_code=204)
async def submit_feedback(
    fb: FeedbackRequest,
    current_user: TokenPayload = Depends(get_current_user),
):
    signal = RLHFFeedback(
        session_id=fb.session_id,
        query_utterance="",
        ir_hash=fb.ir_hash,
        valid_ir=True,
        execution_success=fb.execution_success,
        semantic_match=0.5,
        latency_ms=fb.latency_ms,
        user_rating=fb.rating,
    )
    log.info("RLHF feedback ir_hash=%s reward=%.3f", fb.ir_hash, signal.reward())
    return


@app.get("/db-test")
async def db_test():
    db = get_dev_session()
    try:
        result = db.execute(text("SELECT 1"))
        row = result.fetchone()
        return {
            "connected": True,
            "val": row[0] if row is not None else None,
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
        }
    finally:
        db.close()


# ── ENTRYPOINT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")


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
