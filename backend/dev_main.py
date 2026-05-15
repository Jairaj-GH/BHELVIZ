"""
BHELVIZ — Development FastAPI Entry Point (SQLite, no Oracle required)
════════════════════════════════════════════════════════════════════════
This is the DEVELOPMENT version of main.py.
  - Uses SQLite (via database.py) instead of Oracle 19c
  - Auth uses simple demo credentials (no MFA, no Vault)
  - NLP calls the real Anthropic API (set BHELVIZ_NLP_KEY=your-key)
  - All other security code paths are exercised identically to production

DEMO CREDENTIALS
  Admin : admin@bhel.in    / admin
  User  : any @bhel.in email, any 6+ char "manual password"

Run:
  uvicorn dev_main:app --reload --port 8000
"""

from __future__ import annotations

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
    get_oracle_session,
    AccessRequestOrm,
    AuditLogOrm,
    EmployeeOrm,
    AttendanceOrm,
    DepartmentOrm,
    RoleLuOrm,
    ShiftOrm,
)
from models import (
    AccessRequestCreate,
    ApprovalAction,
    StructuredIR,
)
from nlp_engine import NLPIRPipeline, RLHFFeedback
print("DEV_MAIN RUNNING")

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

nlp_pipeline: Optional[NLPIRPipeline] = None
metadata = MetaData()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global nlp_pipeline

    # Init dev DB
    get_dev_engine()
    log.info("Dev SQLite database initialised")

    if NLP_API_KEY:
        nlp_pipeline = NLPIRPipeline(nlp_endpoint=NLP_ENDPOINT, api_key=NLP_API_KEY)
        log.info("NLP pipeline initialised (endpoint: %s)", NLP_ENDPOINT)
    else:
        log.warning("BHELVIZ_NLP_KEY not set — NLP will return fallback IR")

    yield
    log.info("BHELVIZ shutting down")


# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="BHELVIZ API (Dev)",
    version="2.0.0-dev",
    description="Development mode — SQLite backend, Anthropic NLP",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev only — restrict in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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


# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=True)
async def health():
    return {"status": "ok", "version": "2.0.0-dev", "mode": "sqlite"}


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
        # In production: verify DB user, check approved_status, verify bcrypt hash
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
        "oracle_tde": False,  # dev mode: SQLite
        "db_vault": False,
        "nlp_isolated": True,
        "executor_readonly": True,
        "tls_enforced": False,  # dev mode
        "audit_siem": False,
        "pending_requests": pending,
        "mode": "development",
    }


# ── QUERY ROUTE ───────────────────────────────────────────────────────────────

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

    # Step 1: NLP → IR
    ir_dict: dict | None = None
    if nlp_pipeline and NLP_API_KEY:
        try:
            ir = nlp_pipeline.get_ir(
                utterance=req.utterance,
                session_id=req.session_id,
                conversation_history=req.history,
            )
            ir_dict = ir.model_dump()
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
            log.error("NLP failed: %s", exc)
            ir_dict = None

    if ir_dict is None:
        ir_dict = _fallback_ir(req.utterance)

    # Ensure structured IR always exists, even when falling back
    structured_ir = StructuredIR(**ir_dict)

    # Step 2: Execute against REAL Oracle DB

    db = get_oracle_session()

    try:
        validate_ir(structured_ir)
        print("REAL ORACLE EXECUTOR IS RUNNING")
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

    # ── Attendance queries ─────────────────────────────────

    if any(w in u for w in ["absent", "absence"]):

        return {
            "intent": "attendance_summary",
            "table": "employee_attendance_v",
            "description": "Absent employees today",
            "chart_type": "table",

            "select": [
                {
                    "table": "employee_attendance_v",
                    "column": "emp_id",
                    "alias": "emp_id",
                },
                {
                    "table": "employee_attendance_v",
                    "column": "full_name",
                    "alias": "full_name",
                },
                {
                    "table": "employee_attendance_v",
                    "column": "dept_name",
                    "alias": "dept_name",
                },
                {
                    "table": "employee_attendance_v",
                    "column": "status",
                    "alias": "status",
                },
                {
                    "table": "employee_attendance_v",
                    "column": "att_date",
                    "alias": "att_date",
                },
            ],

            "filters": [
                {
                    "column": "status",
                    "op": "eq",
                    "value": "ABSENT",
                }
            ],

            "order_by": [
                {
                    "table": "employee_attendance_v",
                    "column": "att_date",
                    "direction": "desc",
                }
            ],

            "limit": 100,

            "safety": {
                "read_only": True,
                "allow_subquery": False,
                "no_sql": True,
                "no_ddl": True,
                "max_rows": 500,
            },
        }

    # ── Leave queries ──────────────────────────────────────

    if any(w in u for w in ["leave", "approved leave"]):

        return {
            "intent": "leave_summary",
            "table": "employee_leave_v",
            "description": "Approved employee leaves",
            "chart_type": "table",

            "select": [
                {
                    "table": "employee_leave_v",
                    "column": "request_id",
                    "alias": "request_id",
                },
                {
                    "table": "employee_leave_v",
                    "column": "emp_id",
                    "alias": "emp_id",
                },
                {
                    "table": "employee_leave_v",
                    "column": "full_name",
                    "alias": "full_name",
                },
                {
                    "table": "employee_leave_v",
                    "column": "leave_type",
                    "alias": "leave_type",
                },
                {
                    "table": "employee_leave_v",
                    "column": "status",
                    "alias": "status",
                },
            ],

            "filters": [
                {
                    "column": "status",
                    "op": "eq",
                    "value": "APPROVED",
                }
            ],

            "order_by": [
                {
                    "table": "employee_leave_v",
                    "column": "start_date",
                    "direction": "desc",
                }
            ],

            "limit": 50,

            "safety": {
                "read_only": True,
                "allow_subquery": False,
                "no_sql": True,
                "no_ddl": True,
                "max_rows": 500,
            },
        }

    # ── Default fallback ───────────────────────────────────

    return {
        "intent": "attendance_summary",
        "table": "employee_attendance_v",
        "description": "Attendance summary",
        "chart_type": "table",

        "select": [
            {
                "table": "employee_attendance_v",
                "column": "emp_id",
                "alias": "emp_id",
            },
            {
                "table": "employee_attendance_v",
                "column": "full_name",
                "alias": "full_name",
            },
            {
                "table": "employee_attendance_v",
                "column": "status",
                "alias": "status",
            },
        ],

        "filters": [],

        "order_by": [
            {
                "table": "employee_attendance_v",
                "column": "att_date",
                "direction": "desc",
            }
        ],

        "limit": 100,

        "safety": {
            "read_only": True,
            "allow_subquery": False,
            "no_sql": True,
            "no_ddl": True,
            "max_rows": 500,
        },
    }

def _execute_dev_query(ir: dict) -> dict:
    """Execute IR against the dev SQLite database. Returns mock-encrypted rows."""
    db = get_dev_session()
    try:
        filters = ir.get("filters", [])
        limit = min(int(ir.get("limit", 100)), 500)

        # Build base query: join employee + attendance + department + role
        q = (
            db.query(
                EmployeeOrm.employee_id,
                EmployeeOrm.employee_no_enc,
                EmployeeOrm.full_name_enc,
                EmployeeOrm.current_role_code,
                EmployeeOrm.active_flag,
                DepartmentOrm.dept_code,
                AttendanceOrm.att_date,
                AttendanceOrm.status_code,
                AttendanceOrm.attendance_penalty,
                ShiftOrm.shift_code,
            )
            .join(AttendanceOrm, AttendanceOrm.employee_id == EmployeeOrm.employee_id)
            .join(DepartmentOrm, DepartmentOrm.department_id == EmployeeOrm.department_id)
            .join(ShiftOrm, ShiftOrm.shift_id == AttendanceOrm.shift_id)
        )

        # Apply filters from IR
        for f in filters:
            col = f.get("column", "")
            value = f.get("value", "")
            if col in ("status", "status_code"):
                q = q.filter(AttendanceOrm.status_code == value)
            elif col in ("dept", "dept_code"):
                q = q.filter(DepartmentOrm.dept_code == value)
            elif col in ("role", "current_role_code"):
                q = q.filter(EmployeeOrm.current_role_code == value)
            elif col in ("shift", "shift_code"):
                q = q.filter(ShiftOrm.shift_code == value)

        rows_raw = q.limit(limit).all()

        columns = [
            "employee_no",
            "full_name",
            "dept",
            "role",
            "shift",
            "status",
            "att_date",
            "penalty",
        ]
        rows = []
        for r in rows_raw:
            rows.append(
                {
                    "employee_no": r.employee_no_enc,
                    "full_name": r.full_name_enc,
                    "dept": r.dept_code,
                    "role": r.current_role_code,
                    "shift": r.shift_code,
                    "status": r.status_code,
                    "att_date": r.att_date.isoformat() if r.att_date else None,
                    "penalty": r.attendance_penalty,
                }
            )

        return {"columns": columns, "rows": rows, "row_count": len(rows)}

    finally:
        db.close()


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
    db = get_oracle_session()
    try:
        result = db.execute(text("SELECT USER FROM dual"))
        row = result.fetchone()
        return {
            "connected": True,
            "db_user": row[0],
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

    uvicorn.run("dev_main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
