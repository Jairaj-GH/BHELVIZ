"""
BHELVIZ — Secure Query Executor & IR Compiler
═══════════════════════════════════════════════════════════════════════════════
SECURITY INVARIANTS (must hold at every code path):
  1. No raw SQL string assembly — all output is SQLAlchemy Core objects.
  2. Every literal value is a bind parameter — never interpolated.
  3. No DDL / DML generation path exists in this module.
  4. No decryption logic — executor returns ciphertext rows exactly as stored.
  5. Row limit is always clamped to MAX_LIMIT.
  6. Any unknown table, column, operator or intent causes PolicyError.
  7. The AI subsystem has no import or reference in this module.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    Date,
    and_,
    bindparam,
    create_engine,
    event,
    func,
    select,
)
import hashlib
import itertools
import logging
from typing import Any, Dict, Tuple

from sqlalchemy import MetaData, Table, and_, bindparam, create_engine, event, func, select
from sqlalchemy.engine import Connection, URL

from models import IntentEnum, QueryResponse, StructuredIR

log = logging.getLogger("bhelviz.executor")

# ── POLICY CONSTANTS ──────────────────────────────────────────────────────────

MAX_LIMIT = 1000
MIN_LIMIT = 1

ALLOWED_INTENTS = {e.value for e in IntentEnum}

# Only the two Oracle views the service account is allowed to read.
ALLOWED_TABLES = {
    "employee_attendance_v",
    "employee_leave_v",
}

# Logical column → physical Oracle column mapping.
# The executor only knows about these two views.
COLUMN_CATALOG: Dict[str, Dict[str, str]] = {
    "employee_attendance_v": {
    "emp_id": "EMP_ID",
    "full_name": "FULL_NAME",
    "dept_name": "DEPT_NAME",
    "att_date": "ATT_DATE",
    "status": "STATUS",
    "check_in": "CHECK_IN",
    "check_out": "CHECK_OUT",
},
    "employee_leave_v": {
        "request_id": "REQUEST_ID",
        "emp_id": "EMP_ID",
        "full_name": "FULL_NAME",
        "leave_type": "LEAVE_TYPE",
        "start_date": "START_DATE",
        "end_date": "END_DATE",
        "reason": "REASON",
        "status": "STATUS",
    },
}

ALLOWED_OPERATORS = {"eq", "neq", "in", "between", "lt", "lte", "gt", "gte"}

FORBIDDEN_PROJECTION_COLUMNS = {
    "password", "passwd", "secret", "token", "key", "salt",
    "private_key", "api_key", "credential",
}


class PolicyError(Exception):
    """Raised when an IR violates any security policy."""


# ── BIND NAME GENERATOR ───────────────────────────────────────────────────────

_counter = itertools.count(1)


def _new_bind(prefix: str = "p") -> str:
    return f"{prefix}_{next(_counter)}"


# ── SMALL HELPERS ────────────────────────────────────────────────────────────

def _resolve_physical_column(table_name: str, logical_column: str) -> str:
    catalog = COLUMN_CATALOG.get(table_name, {})
    if logical_column in catalog:
        return catalog[logical_column]

    # case-insensitive fallback for robustness
    normalized = logical_column.lower()
    for k, v in catalog.items():
        if k.lower() == normalized:
            return v

    raise PolicyError(f"Unknown column {logical_column!r} on table/view {table_name!r}")


def _resolve_table_column(tbl: Table, physical_name: str):
    """Return a reflected SQLAlchemy column with case-insensitive fallback."""
    if physical_name in tbl.c:
        return tbl.c[physical_name]

    upper = physical_name.upper()
    if upper in tbl.c:
        return tbl.c[upper]

    lower = physical_name.lower()
    if lower in tbl.c:
        return tbl.c[lower]

    for key in tbl.c.keys():
        if key.lower() == physical_name.lower():
            return tbl.c[key]

    raise PolicyError(f"Column {physical_name!r} not found in reflected view {tbl.name!r}")


# ── IR VALIDATOR ──────────────────────────────────────────────────────────────

def validate_ir(ir: StructuredIR) -> None:
    """
    Pure policy gate. Raises PolicyError on any violation.
    Called before compilation — nothing reaches Oracle if this raises.
    """
    # 1. Safety flags
    if not ir.safety.read_only:
        raise PolicyError("IR safety.read_only must be True")
    if ir.safety.allow_subquery:
        raise PolicyError("Subqueries are not permitted")
    if not ir.safety.no_sql:
        raise PolicyError("IR safety.no_sql must be True")
    if not ir.safety.no_ddl:
        raise PolicyError("IR safety.no_ddl must be True")

    # 2. Intent allowlist
    if ir.intent not in ALLOWED_INTENTS:
        raise PolicyError(f"Intent not in allowlist: {ir.intent!r}")

    # 3. Table/view allowlist
    if ir.table not in ALLOWED_TABLES:
        raise PolicyError(f"Unauthorized table/view: {ir.table!r}")

    # 4. Joins are completely disabled
    if ir.joins:
        raise PolicyError("Joins are disabled")

    # 5. Row limit
    if ir.safety.max_rows < MIN_LIMIT or ir.safety.max_rows > MAX_LIMIT:
        raise PolicyError(
            f"IR safety.max_rows must be between {MIN_LIMIT} and {MAX_LIMIT}"
        )
    if ir.limit < MIN_LIMIT:
        raise PolicyError(f"Requested limit {ir.limit} is below MIN_LIMIT {MIN_LIMIT}")
    if ir.limit > MAX_LIMIT:
        raise PolicyError(f"Requested limit {ir.limit} exceeds MAX_LIMIT {MAX_LIMIT}")
    if ir.limit > ir.safety.max_rows:
        raise PolicyError(
            f"Requested limit {ir.limit} exceeds IR safety.max_rows {ir.safety.max_rows}"
        )

    # 6. Select fields
    for field in (ir.select or []):
        if field.table != ir.table:
            raise PolicyError(
                f"Select field table {field.table!r} does not match IR table {ir.table!r}"
            )
        _resolve_physical_column(ir.table, field.column)
        if field.column.lower() in FORBIDDEN_PROJECTION_COLUMNS:
            raise PolicyError(f"Forbidden column in projection: {field.column!r}")

    # 7. Filters
    for f in ir.filters:
        prefix, col = (f.column.split(".", 1) + [None])[:2] if "." in f.column else (ir.table, f.column)
        if prefix != ir.table:
            raise PolicyError(
                f"Filter target table {prefix!r} does not match IR table {ir.table!r}"
            )
        _resolve_physical_column(ir.table, col)
        if f.op not in ALLOWED_OPERATORS:
            raise PolicyError(f"Operator not in allowlist: {f.op!r}")

    # 8. Group-by
    if ir.group_by_field:
        _resolve_physical_column(ir.table, ir.group_by_field)

    # 9. Order-by
    for o in (ir.order_by or []):
        if o.table != ir.table:
            raise PolicyError(
                f"Order-by table {o.table!r} does not match IR table {ir.table!r}"
            )
        _resolve_physical_column(ir.table, o.column)


# ── IR COMPILER ───────────────────────────────────────────────────────────────

def compile_ir(
    ir: StructuredIR,
    metadata: MetaData,
    conn: Connection,
) -> Tuple[Any, Dict[str, Any]]:
    """
    Converts a validated StructuredIR into a SQLAlchemy Core Select statement
    with bind parameters. Returns (stmt, bind_values).

    CONTRACT:
      - No string concatenation for SQL fragments.
      - No raw WHERE clauses.
      - No dynamic ORDER BY from free text.
      - Every user-supplied value is a bind parameter.
      - If this function raises, nothing has touched Oracle.
    """
    bind_values: Dict[str, Any] = {}

    primary_table_name = ir.table
    if primary_table_name not in ALLOWED_TABLES:
        raise PolicyError(f"Unauthorized table/view: {primary_table_name!r}")

    if primary_table_name == "employee_leave_v":

            tbl = Table(
                "EMPLOYEE_LEAVE_V",
                metadata,

                Column("REQUEST_ID", String),
                Column("EMP_ID", String),
                Column("FULL_NAME", String),
                Column("LEAVE_TYPE", String),
                Column("START_DATE", Date),
                Column("END_DATE", Date),
                Column("REASON", String),
                Column("STATUS", String),

                schema="SYSTEM",
                extend_existing=True,
        )

    elif primary_table_name == "employee_attendance_v":

            tbl = Table(
                "EMPLOYEE_ATTENDANCE_V",
                metadata,

                Column("EMP_ID", String),
                Column("FULL_NAME", String),
                Column("DEPT_NAME", String),
                Column("ATT_DATE", Date),
                Column("STATUS", String),
                Column("CHECK_IN", String),
                Column("CHECK_OUT", String),

                schema="SYSTEM",
                extend_existing=True,
            )

    else:
            raise PolicyError(
                f"Unauthorized table/view: {primary_table_name}"
            )

    # ── Build SELECT list ─────────────────────────────────────────────────────
    select_exprs = []
    if ir.select:
        for item in ir.select:
            if item.table != primary_table_name:
                raise PolicyError(
                    f"Select field table {item.table!r} does not match IR table {primary_table_name!r}"
                )
            physical_col = _resolve_physical_column(primary_table_name, item.column)
            col_obj = _resolve_table_column(tbl, physical_col)
            label = item.alias or item.column
            select_exprs.append(col_obj.label(label))
    else:
        # Default: all columns on the primary view
        select_exprs = [tbl]

    stmt = select(*select_exprs) if select_exprs else select(tbl)

    # ── WHERE predicates with bind parameters ────────────────────────────────
    predicates = []
    for f in ir.filters:
        col_logical = f.column.split(".")[-1]
        physical_col = _resolve_physical_column(primary_table_name, col_logical)
        col_obj = _resolve_table_column(tbl, physical_col)

        if f.op == "eq":
            bn = _new_bind("eq")
            predicates.append(col_obj == bindparam(bn))
            bind_values[bn] = f.value

        elif f.op == "neq":
            bn = _new_bind("neq")
            predicates.append(col_obj != bindparam(bn))
            bind_values[bn] = f.value

        elif f.op == "in":
            bn = _new_bind("in")
            predicates.append(col_obj.in_(bindparam(bn, expanding=True)))
            bind_values[bn] = f.values or []

        elif f.op == "between":
            b1, b2 = _new_bind("lo"), _new_bind("hi")
            predicates.append(col_obj.between(bindparam(b1), bindparam(b2)))
            bind_values[b1] = f.low
            bind_values[b2] = f.high

        elif f.op in ("lt", "lte", "gt", "gte"):
            bn = _new_bind(f.op)
            op_map = {
                "lt": col_obj.__lt__,
                "lte": col_obj.__le__,
                "gt": col_obj.__gt__,
                "gte": col_obj.__ge__,
            }
            predicates.append(op_map[f.op](bindparam(bn)))
            bind_values[bn] = f.value

        else:
            raise PolicyError(f"Operator not allowed: {f.op!r}")

    if predicates:
        stmt = stmt.where(and_(*predicates))

    # ── Time window ───────────────────────────────────────────────────────────
    if ir.time_window and primary_table_name in ("employee_attendance_v", "employee_leave_v"):
        if primary_table_name == "employee_attendance_v":
            ts_col = _resolve_table_column(tbl, _resolve_physical_column(primary_table_name, "att_date"))
        elif primary_table_name == "employee_leave_v":
            ts_col = _resolve_table_column(tbl, _resolve_physical_column(primary_table_name, "start_date"))
        else:
            ts_col = None

        if ts_col is not None and ir.time_window.type == "relative":
            rel_map = {
                "today": func.trunc(func.sysdate()),
                "yesterday": func.trunc(func.sysdate()) - 1,
                "last_week": func.trunc(func.sysdate()) - 7,
                "last_month": func.add_months(func.trunc(func.sysdate()), -1),
            }
            if ir.time_window.value in rel_map:
                stmt = stmt.where(ts_col >= rel_map[ir.time_window.value])

    # ── GROUP BY ──────────────────────────────────────────────────────────────
    if ir.group_by_field:
        physical = _resolve_physical_column(primary_table_name, ir.group_by_field)
        stmt = stmt.group_by(_resolve_table_column(tbl, physical))

    # ── ORDER BY ──────────────────────────────────────────────────────────────
    for o in (ir.order_by or []):
        if o.table != primary_table_name:
            raise PolicyError(
                f"Order-by table {o.table!r} does not match IR table {primary_table_name!r}"
            )
        physical_col = _resolve_physical_column(primary_table_name, o.column)
        col_obj = _resolve_table_column(tbl, physical_col)
        stmt = stmt.order_by(col_obj.desc() if o.direction == "desc" else col_obj.asc())

    # ── LIMIT (always clamped) ────────────────────────────────────────────────
    clamped_limit = max(MIN_LIMIT, min(ir.limit, ir.safety.max_rows, MAX_LIMIT))
    stmt = stmt.limit(clamped_limit)

    return stmt, bind_values


# ── EXECUTOR ──────────────────────────────────────────────────────────────────

def compile_and_execute(
    ir: StructuredIR,
    conn: Connection,
    metadata: MetaData,
) -> QueryResponse:
    """
    Full pipeline: validate → compile → execute → return ciphertext rows.

    SECURITY: conn must be a per-user read-only Oracle session.
              This function never decrypts anything.
              Rows are returned exactly as Oracle stores them.
    """
    validate_ir(ir)

    stmt, bind_values = compile_ir(ir, metadata, conn)

    ir_json = ir.model_dump_json()
    ir_hash = hashlib.sha256(ir_json.encode()).hexdigest()

    log.info("Executing IR hash=%s intent=%s limit=%d", ir_hash, ir.intent, ir.limit)
    try:
        log.info("Compiled SQL: %s", str(stmt))
        log.info(
            "Bind parameters: %s",
            {k: ("<redacted>" if len(str(v)) > 100 else v) for k, v in bind_values.items()},
        )
    except Exception:
        log.info("Compiled SQL/logging failed — proceeding to execute")

    result = conn.execute(stmt, bind_values)
    raw_rows = result.mappings().all()

    rows = [dict(r) for r in raw_rows]
    columns = list(rows[0].keys()) if rows else []

    return QueryResponse(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        intent=ir.intent,
        description=ir.description,
        chart_type=ir.chart_type,
        ir_hash=ir_hash,
    )


# ── READ-ONLY SESSION FACTORY ────────────────────────────────────────────────

def make_readonly_engine(dsn: str, username: str, password: str):
    """
    Returns a SQLAlchemy engine for a per-user Oracle session.
    Uses python-oracledb thin mode (no Oracle Client libraries required).
    """
    url = URL.create(
        "oracle+oracledb",
        username=username,
        password=password,
    )

    engine = create_engine(
        url,
        connect_args={"dsn": dsn},
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _):
        # The database account itself should be read-only; keep this hook minimal.
        # No session writes are performed here.
        return None

    return engine
