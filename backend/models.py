"""
BHELVIZ — Pydantic models for IR, user onboarding, audit, and query response.
All IR fields are strictly typed — the executor accepts nothing outside these types.
"""
from __future__ import annotations
from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import Literal, Optional, List
from datetime import datetime
from enum import Enum


# ── ENUMERATIONS ─────────────────────────────────────────────────────────────

class IntentEnum(str, Enum):
    attendance_summary = "attendance_summary"
    employee_lookup    = "employee_lookup"
    hierarchy_lookup   = "hierarchy_lookup"
    role_comparison    = "role_comparison"
    anomaly_detection  = "anomaly_detection"
    leave_summary = "leave_summary"

class OperatorEnum(str, Enum):
    eq      = "eq"
    neq     = "neq"
    in_     = "in"
    between = "between"
    lt      = "lt"
    lte     = "lte"
    gt      = "gt"
    gte     = "gte"

class ChartTypeEnum(str, Enum):
    table = "table"
    bar   = "bar"
    pie   = "pie"
    line  = "line"
    area  = "area"


# ── IR COMPONENTS ─────────────────────────────────────────────────────────────

class IRSafety(BaseModel):
    read_only:      bool = True
    allow_subquery: bool = False
    max_rows:       int  = Field(default=500, le=1000, ge=1)
    no_sql:         bool = True
    no_ddl:         bool = True

    @field_validator("read_only")
    @classmethod
    def must_be_read_only(cls, v):
        if not v:
            raise ValueError("IR safety.read_only must always be True")
        return v


class IRFilter(BaseModel):
    column: str
    op:     OperatorEnum
    value:  Optional[str]  = None   # for eq / neq / lt / lte / gt / gte
    values: Optional[List[str]] = None  # for in
    low:    Optional[str]  = None   # for between
    high:   Optional[str]  = None   # for between

    @field_validator("column")
    @classmethod
    def no_raw_sql_in_column(cls, v):
        forbidden = ["'", '"', ";", "--", "/*", "*/", "xp_", "exec", "drop", "delete", "insert", "update"]
        for f in forbidden:
            if f in v.lower():
                raise ValueError(f"Forbidden fragment in column name: {f}")
        return v


class IRSelectField(BaseModel):
    table:  str
    column: str
    alias:  Optional[str] = None


class IRJoin(BaseModel):
    type:         Literal["inner", "left"]
    left_table:   str
    left_column:  str
    right_table:  str
    right_column: str


class IROrderBy(BaseModel):
    table:     str
    column:    str
    direction: Literal["asc", "desc"] = "asc"


class IRTimeWindow(BaseModel):
    type:  Literal["relative", "absolute"]
    value: str   # "today", "last_week", "last_month", or ISO date string


class StructuredIR(BaseModel):
    """
    The only object the query executor will accept.
    Produced by the NLP engine; validated by the policy gate before compilation.
    """
    intent:        IntentEnum
    table: str = "employee_attendance_v"
    description:   str = Field(max_length=300)
    chart_type:    ChartTypeEnum = ChartTypeEnum.table
    group_by_field: Optional[str] = None
    select:        Optional[List[IRSelectField]] = None
    joins: Optional[List[IRJoin]] = Field(default_factory=list)
    filters:       List[IRFilter]                = Field(default_factory=list)
    order_by:      Optional[List[IROrderBy]]     = None
    time_window:   Optional[IRTimeWindow]        = None
    limit:         int = Field(default=100, le=1000, ge=1)
    safety:        IRSafety = Field(default_factory=IRSafety)
    model_config = {
    "extra": "forbid"
}
    @field_validator("table")
    @classmethod
    def validate_table(cls, v):
        allowed = {
            "employee_attendance_v",
            "employee_leave_v",
        }

        if v not in allowed:
            raise ValueError(f"Unauthorized table/view: {v}")

        return v

# ── REQUEST / RESPONSE ────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    utterance:  str  = Field(min_length=1, max_length=1000)
    session_id: str
    context:    Optional[dict] = None


class QueryResponse(BaseModel):
    columns:    List[str]
    rows:       List[dict]          # ciphertext values — decrypted client-side
    row_count:  int
    intent:     IntentEnum
    description: str
    chart_type: ChartTypeEnum
    ir_hash:    str                 # SHA-256 of compiled IR for audit


# ── USER MANAGEMENT ───────────────────────────────────────────────────────────

class AccessRequestCreate(BaseModel):
    full_name:   str  = Field(min_length=2, max_length=200)
    email:       EmailStr
    department:  str  = Field(min_length=1, max_length=100)
    justification: str = Field(min_length=10, max_length=1000)


class AccessRequestDB(AccessRequestCreate):
    id:          int
    status:      Literal["pending", "approved", "denied"] = "pending"
    created_at:  datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str]      = None
    audit_id:    str                # immutable BVIZ-REQ-<uuid>


class ApprovalAction(BaseModel):
    request_id: int
    action:     Literal["approve", "deny"]
    notes:      Optional[str] = None


class UserSessionInfo(BaseModel):
    user_id:     int
    email:       EmailStr
    role:        Literal["admin", "user"]
    approved_at: datetime
    session_exp: datetime


# ── AUDIT ─────────────────────────────────────────────────────────────────────

class AuditEvent(BaseModel):
    timestamp:   datetime
    action:      str
    actor:       str
    detail:      str
    level:       Literal["INFO", "WARN", "ERROR"]
    session_id:  Optional[str] = None
    ip_hash:     Optional[str] = None   # hashed, never raw IP
