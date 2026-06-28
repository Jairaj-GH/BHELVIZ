"""
BHELVIZ — Inference Pipeline v3  (bhelviz_pipeline_v3.py)
═══════════════════════════════════════════════════════════════════════════════
What changed from v2 → v3:

  LOCAL MODEL FIRST — every utterance is first processed by the local
  DistilBERT + LoRA model (bhelviz_model.py). If the model's confidence
  exceeds LOCAL_CONF_THRESHOLD for both intent and select_mode, the pipeline
  short-circuits and never calls the external NLP endpoint.

  Decision cascade (in order):
    1. SafetyFilter            — blocks injection / credential patterns
    2. ConversationSlotsV2     — pronoun + carry-forward resolution
    3. FastPathMatcher         — zero-latency regex path
    4. ★ LocalModel inference  — DistilBERT transformer, runs on CPU
         if intent_conf > threshold AND mode_conf > threshold:
             → assemble IR directly from local predictions + slot tags
         else:
             → fall through to external NLP
    5. External NLP endpoint   — remote NLP model
    6. SelectModeEnforcer      — rewrites SELECT clause
    7. IRConfidenceScorer      — quality gate
    8. Pydantic StructuredIR   — validation
    9. Belt-and-suspenders     — safety assertions
   10. Slot memory update

  Backpropagation (training time, not inference):
    The local model's weights were updated via loss.backward() in
    bhelviz_trainer.py. At inference time, torch.no_grad() is active —
    no gradient computation happens, keeping CPU overhead minimal.

Security contract (unchanged):
  • Local model: receives tokenised utterance only
  • External NLP: receives utterance + schema + glossary only
  • Neither path receives Oracle credentials or row data
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

import torch

from NLP.nlp_engine import (
    ConversationSlotsV2,
    FastPathMatcher,
    FastPathResult,
    IRConfidenceScorer,
    NLPIRPipeline as RemoteNLPPipeline,
    SelectMode,
    is_safe_utterance,
)

from NLP.bhelviz_model_impl import BhelvizNLPModel, build_model

# ---------------------------------------------------------------------------
# Dynamic department list loaded at application startup (from Oracle)
# ---------------------------------------------------------------------------
try:
    from core.metadata import KNOWN_DEPARTMENTS
except ImportError:
    KNOWN_DEPARTMENTS = set()

try:
    from core.models import StructuredIR
except ImportError:
    StructuredIR = dict  # type: ignore

log = logging.getLogger("bhelviz.pipeline_v3")

# ──────────────────────────────────────────────────────────────────────────────
# LOCAL SAFE CATALOGS / TEMPLATES
# ──────────────────────────────────────────────────────────────────────────────

PIPELINE_ALLOWED_VIEWS = {"employee_attendance_v", "employee_leave_v"}

PIPELINE_SCHEMA_CATALOG = {
    "views": ["employee_attendance_v", "employee_leave_v"],
    "view_columns": {
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
    },
}

PIPELINE_BUSINESS_GLOSSARY = {
    "workmen": "WORKMAN",
    "workmans": "WORKMAN",
    "executives": "EXECUTIVE",
    "supervisors": "SUPERVISOR",
    "false attendance": "FALSE_PRESENT",
    "proxy": "FALSE_PRESENT",
    "absent": "ABSENT",
    "present": "PRESENT",
    "late": "LATE",
    "morning shift": "MORNING",
    "afternoon shift": "AFTERNOON",
    "night shift": "NIGHT",
    "leave": "employee_leave_v",
    "attendance": "employee_attendance_v",
}

PIPELINE_ALLOWED_INTENTS = {
    "attendance_summary",
    "leave_summary",
    "employee_lookup",
    "hierarchy_lookup",
    "role_comparison",
    "anomaly_detection",
}

PIPELINE_ALLOWED_CHART_TYPES = {"table", "bar", "pie", "line", "area"}
PIPELINE_ALLOWED_FILTER_OPS = {"eq", "neq", "in", "between", "lt", "lte", "gt", "gte"}

PIPELINE_SELECT_TEMPLATES = {
    "employee_attendance_v": {
        SelectMode.LIST: ["emp_id", "full_name", "dept_name", "status", "att_date"],
        SelectMode.COUNT: ["emp_id"],
        SelectMode.AGGREGATE: ["dept_name", "status"],
        SelectMode.DETAIL: ["emp_id", "full_name", "dept_name", "att_date", "status", "check_in", "check_out"],
        SelectMode.TREND: ["att_date", "status"],
        SelectMode.NAMES_ONLY: ["full_name"],
    },
    "employee_leave_v": {
        SelectMode.LIST: ["request_id", "emp_id", "full_name", "leave_type", "start_date", "end_date", "status"],
        SelectMode.COUNT: ["request_id"],
        SelectMode.AGGREGATE: ["leave_type", "status"],
        SelectMode.DETAIL: ["request_id", "emp_id", "full_name", "leave_type", "start_date", "end_date", "reason", "status"],
        SelectMode.TREND: ["start_date", "status"],
        SelectMode.NAMES_ONLY: ["full_name"],
    },
}

LOCAL_CONF_THRESHOLD = 0.80
SLOT_TAG_CONF_FLOOR = 0.85


# ──────────────────────────────────────────────────────────────────────────────
# SMALL HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _is_leave_utterance(utterance: str) -> bool:
    return bool(re.search(r"(?i)\b(leave|leaves|vacation)\b", utterance))


def _table_for_utterance(utterance: str) -> str:
    return "employee_leave_v" if _is_leave_utterance(utterance) else "employee_attendance_v"


def _allowed_columns_for(table: str) -> set[str]:
    return set(PIPELINE_SCHEMA_CATALOG["view_columns"].get(table, {}).keys())


def _normalize_select_mode(value: Any) -> SelectMode:
    if isinstance(value, SelectMode):
        return value
    if value is None:
        return SelectMode.LIST
    try:
        return SelectMode(str(value).upper())
    except Exception:
        return SelectMode.LIST


def _normalize_intent(value: Any, utterance: str, table: str) -> str:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in PIPELINE_ALLOWED_INTENTS:
            return v
        if v in {"count_query", "names_query", "trend_analysis", "department_summary", "shift_summary"}:
            return "leave_summary" if table == "employee_leave_v" else "attendance_summary"

    u = utterance.lower()
    if any(k in u for k in ["false attendance", "proxy", "anomaly"]):
        return "anomaly_detection"
    if any(k in u for k in ["manager", "hierarchy", "reports to", "reporting line"]):
        return "hierarchy_lookup"
    if any(k in u for k in ["role", "executive", "supervisor", "workman"]):
        return "role_comparison"
    if table == "employee_leave_v":
        return "leave_summary"
    return "attendance_summary"


def _normalize_chart_type(value: Any, select_mode: SelectMode) -> str:
    if isinstance(value, str) and value.lower() in PIPELINE_ALLOWED_CHART_TYPES:
        return value.lower()

    if select_mode == SelectMode.TREND:
        return "line"
    if select_mode == SelectMode.AGGREGATE:
        return "bar"
    return "table"


def _select_columns_for_mode(table: str, mode: SelectMode) -> List[str]:
    tpl = PIPELINE_SELECT_TEMPLATES.get(table, PIPELINE_SELECT_TEMPLATES["employee_attendance_v"])
    return tpl.get(mode, tpl[SelectMode.LIST])


def _build_select_items(table: str, columns: List[str]) -> List[Dict[str, str]]:
    allowed = _allowed_columns_for(table)
    items: List[Dict[str, str]] = []
    for col in columns:
        if col in allowed:
            items.append({"table": table, "column": col, "alias": col})
    return items


def _infer_group_by_field(table: str, mode: SelectMode, utterance: str) -> Optional[str]:
    if mode in (SelectMode.COUNT, SelectMode.AGGREGATE):
        return "leave_type" if table == "employee_leave_v" else "dept_name"
    if mode == SelectMode.TREND:
        return "start_date" if table == "employee_leave_v" else "att_date"
    return None


def _default_ir(utterance: str) -> dict:
    table = _table_for_utterance(utterance)
    mode = SelectMode.LIST

    intent = _normalize_intent(None, utterance, table)
    chart_type = _normalize_chart_type(None, mode)
    select_items = _build_select_items(table, _select_columns_for_mode(table, mode))
    group_by_field = None

    filters: List[Dict[str, Any]] = []
    u = utterance.lower()

    if table == "employee_attendance_v":
        if any(w in u for w in ["absent", "absence"]):
            filters.append({"column": "status", "op": "eq", "value": "ABSENT"})
        elif "present" in u:
            filters.append({"column": "status", "op": "eq", "value": "PRESENT"})
        elif "late" in u:
            filters.append({"column": "status", "op": "eq", "value": "LATE"})
    else:
        if "approved" in u:
            filters.append({"column": "status", "op": "eq", "value": "APPROVED"})
        elif "pending" in u:
            filters.append({"column": "status", "op": "eq", "value": "PENDING"})
        elif "rejected" in u:
            filters.append({"column": "status", "op": "eq", "value": "REJECTED"})

    time_window = None
    if "today" in u:
        time_window = {"type": "relative", "value": "today"}
    elif "yesterday" in u:
        time_window = {"type": "relative", "value": "yesterday"}
    elif "last week" in u:
        time_window = {"type": "relative", "value": "last_week"}
    elif "last month" in u:
        time_window = {"type": "relative", "value": "last_month"}

    return {
        "intent": intent,
        "table": table,
        "description": "Employee leave summary" if table == "employee_leave_v" else "Attendance summary",
        "chart_type": chart_type,
        "group_by_field": group_by_field,
        "select": select_items,
        "filters": filters,
        "order_by": [
            {
                "table": table,
                "column": "start_date" if table == "employee_leave_v" else "att_date",
                "direction": "desc",
            }
        ],
        "time_window": time_window,
        "limit": 50,
        "safety": {
            "read_only": True,
            "allow_subquery": False,
            "no_sql": True,
            "no_ddl": True,
        },
    }


def _extract_slots_from_tags(
    tokens: List[str],
    slot_tags: List[str],
    utterance: str,
) -> Dict[str, Optional[str]]:
    """
    Converts BIO token-level tags into slot values.
    Returns dict with keys: dept, status, time_hint, role.
    """
    slots: Dict[str, Optional[str]] = {"dept": None, "status": None, "time_hint": None, "role": None}
    TAG_MAP = {"DEPT": "dept", "STATUS": "status", "TIME": "time_hint", "ROLE": "role"}

    current_tag: Optional[str] = None
    current_toks: List[str] = []

    def flush() -> None:
        nonlocal current_tag, current_toks
        if current_tag and current_toks:
            slot_key = TAG_MAP.get(current_tag)
            if slot_key:
                span = " ".join(
                    t.lstrip("#") for t in current_toks if t not in ("[CLS]", "[SEP]", "[PAD]")
                )
                if slot_key in ("status", "role", "dept"):
                    slots[slot_key] = span.strip().upper()
                else:
                    slots[slot_key] = span.strip()

    for token, tag in zip(tokens, slot_tags):
        if tag.startswith("B-"):
            flush()
            current_tag = tag[2:]
            current_toks = [token]
        elif tag.startswith("I-") and current_tag == tag[2:]:
            current_toks.append(token)
        else:
            flush()
            current_tag = None
            current_toks = []

    flush()

    if slots["time_hint"]:
        t = slots["time_hint"].lower()
        if "yesterday" in t:
            slots["time_hint"] = "yesterday"
        elif "last week" in t:
            slots["time_hint"] = "last_week"
        elif "last month" in t:
            slots["time_hint"] = "last_month"
        elif "today" in t:
            slots["time_hint"] = "today"

    return slots

#     return filters
def _assemble_ir_from_local(
    preds: Dict[str, Any],       # Full prediction dict from model.predict()
    slots: Dict[str, Optional[str]],
    utterance: str,
) -> Dict[str, Any]:
    """
    Builds a raw IR dict from the local model's semantic predictions.
    Uses a systematic dimensions / metrics planner for analytical queries.
    """
    is_leave = _is_leave_utterance(utterance)
    table = "employee_leave_v" if is_leave else "employee_attendance_v"

    # Basic fields from predictions
    intent = preds.get("intent", "")
    tokens = preds.get("tokens", [])
    slot_tags = preds.get("slot_tags", [])
    select_mode = preds.get("select_mode", "LIST")
    sm = _normalize_select_mode(select_mode)
    intent_name = _normalize_intent(intent, utterance, table)

    # ── 1. DETECT RETRIEVAL QUERIES (vs. analytics) ─────────────────────────
    utterance_lower = utterance.lower()
    is_retrieval = any(x in utterance_lower for x in [
        "employees of", "employee names", "show employees",
        "list employees", "give me employees", "only employees",
        "employee list",
    ])

    # Semantic flags from transformer output
    aggregation_type = preds.get("aggregation", "NONE")
    groupby_field = preds.get("groupby", "NONE")
    ranking = preds.get("ranking", "NONE")
    trend = preds.get("trend", "NONE")

    # ── Planner flags ────────────────────────────────────────────────────────
    is_trend = (trend == "TIME_SERIES")
    is_grouped = (groupby_field != "NONE")
    is_count = (sm == SelectMode.COUNT)

    # ── 2. OVERRIDE TRANSFORMER ANALYTICS IF RETRIEVAL ───────────────────────
    if is_retrieval:
        aggregation_type = "NONE"
        groupby_field = "NONE"
        is_grouped = False
        is_count = False
        is_trend = False

    # ── Dimensions (GROUP BY columns) ────────────────────────────────────────
    dimensions: List[str] = []
    if is_grouped:
        if groupby_field in _allowed_columns_for(table):
            dimensions.append(groupby_field)

    # ── Metrics (aggregations) ───────────────────────────────────────────────
    metrics: List[Dict[str, Any]] = []
    if not is_retrieval:
        if aggregation_type == "COUNT":
            metrics.append({
                "function": "count",
                "column": "emp_id" if table == "employee_attendance_v" else "request_id",
                "alias": "count"
            })
        elif aggregation_type == "AVG":
            metrics.append({
                "function": "avg",
                "column": "penalty",
                "alias": "avg_value"
            })
        elif aggregation_type == "SUM":
            metrics.append({
                "function": "sum",
                "column": "penalty",
                "alias": "total"
            })

    # ── Automatic COUNT repair for grouped/trend queries without aggregation ─
    if not is_retrieval and (is_grouped or is_trend) and not metrics:
        metrics.append({
            "function": "count",
            "column": "emp_id" if table == "employee_attendance_v" else "request_id",
            "alias": "count"
        })

    # ── Build SELECT list ────────────────────────────────────────────────────
    select_items: List[Dict[str, str]] = []

    if is_retrieval:
        if "names" in utterance_lower:
            select_items.append({"table": table, "column": "full_name", "alias": "full_name"})
        else:
            select_items = [
                {"table": table, "column": "emp_id", "alias": "emp_id"},
                {"table": table, "column": "full_name", "alias": "full_name"},
            ]
            if table == "employee_attendance_v":
                select_items.append({"table": table, "column": "dept_name", "alias": "dept_name"})
    elif metrics or dimensions:
        for d in dimensions:
            if d in _allowed_columns_for(table):
                select_items.append({"table": table, "column": d, "alias": d})
    else:
        select_items = _build_select_items(table, _select_columns_for_mode(table, sm))

    # ── GROUP BY handling ────────────────────────────────────────────────────
    group_by = list(dimensions)
    if is_retrieval:
        group_by = []
    elif is_count and not is_grouped:
        group_by = []
    if is_trend:
        date_col = "att_date" if table == "employee_attendance_v" else "start_date"
        if date_col not in group_by:
            group_by.append(date_col)
            if not any(s["column"] == date_col for s in select_items):
                select_items.append({"table": table, "column": date_col, "alias": date_col})

    # ── ORDER BY ─────────────────────────────────────────────────────────────
    sort_dir = "desc"
    if ranking == "BOTTOM":
        sort_dir = "asc"

    order_by: List[Dict[str, Any]] = []
    if is_trend:
        date_col = "att_date" if table == "employee_attendance_v" else "start_date"
        order_by.append({"table": table, "column": date_col, "direction": "asc"})
    elif metrics and not is_retrieval:
        order_by.append({
            "table": table,
            "column": metrics[0]["alias"],
            "direction": sort_dir,
        })
    else:
        date_col = "start_date" if table == "employee_leave_v" else "att_date"
        order_by.append({"table": table, "column": date_col, "direction": "desc"})

    # ── Chart type ───────────────────────────────────────────────────────────
    chart_type = _normalize_chart_type(None, sm)
    if is_trend:
        chart_type = "line"
    
    filters: List[Dict[str, Any]] = []
    
    STATUS_NORMALIZATION = {

        "HALF DAY": "HALF_DAY",
        "HALF DAYS": "HALF_DAY",
        "HALFDAY": "HALF_DAY",
        "HD": "HALF_DAY",

        "ABSENT": "ABSENT",
        "PRESENT": "PRESENT",
        "LATE": "LATE",

        "APPROVED": "APPROVED",
        "PENDING": "PENDING",
        "REJECTED": "REJECTED",

        "FALSE ATTENDANCE": "FALSE_PRESENT",
        "PROXY": "FALSE_PRESENT",
    }
    DEPT_NORMALIZATION = {

    "HR": "Human Resources",
    "HUMAN RESOURCE": "Human Resources",
    "HUMAN RESOURCES": "Human Resources",

    "IT": "Internship and Training",
    "INFORMATION TECHNOLOGY": "Internship and Training",

    "FINANCE": "Finance",

    "ADMIN": "Administration",

    "SECURITY": "Security",

    "MANAGEMENT": "Management",

    "TRANSFORMER": "Transformer Plant",
    "TRANSFORMER PLANT": "Transformer Plant",

    "NUCLEAR": "Nuclear Plant",

    "R&D": "Research and Development",
    "RESEARCH": "Research and Development",

    "PUBLIC RELATIONS": "Public Relations",

    "STEEL": "Steel Plates Plant",

    "DIGITAL": "DTG – Digital Trans. Group",

    "HEAVY ELECTRICAL": "Heavy Electrical Equip. Plant",
}

        # fallback status keyword detection
    utterance_upper = utterance.upper()

    if not slots.get("status"):

        if "APPROVED" in utterance_upper:
            slots["status"] = "APPROVED"

        elif "PENDING" in utterance_upper:
            slots["status"] = "PENDING"

        elif "REJECTED" in utterance_upper:
            slots["status"] = "REJECTED"

        elif "ABSENT" in utterance_upper:
            slots["status"] = "ABSENT"

        elif "HALF DAY" in utterance_upper or "HALF DAYS" in utterance_upper:
            slots["status"] = "HALF_DAY"
    if slots.get("status"):

        status_value = slots["status"].strip().upper()

        status_value = STATUS_NORMALIZATION.get(
            status_value,
            status_value
        )

        filters.append({
            "column": "status",
            "op": "eq",
            "value": status_value
        })

    if slots.get("dept") and table == "employee_attendance_v":

        dept_value = slots["dept"].strip().upper()

        dept_value = DEPT_NORMALIZATION.get(
            dept_value,
            dept_value
        )

        filters.append({
            "column": "dept_name",
            "op": "eq",
            "value": dept_value
        })

    time_val = slots.get("time_hint")

    time_window = {
        "type": "relative",
        "value": time_val
    } if time_val else None

    group_by_field_legacy = (
        group_by[0]
        if group_by
        else _infer_group_by_field(table, sm, utterance)
    )

    return {
        "intent": intent_name,
        "table": table,
        "description": f"[LocalModel] {utterance[:80]}",
        "chart_type": chart_type,
        "select_mode": sm.value,
        "group_by_field": group_by_field_legacy,
        "group_by": group_by,
        "select": select_items,
        "aggregations": metrics,
        "filters": filters,
        "order_by": order_by,
        "time_window": time_window,
        "limit": 50,
        "safety": {
            "read_only": True,
            "allow_subquery": False,
            "no_sql": True,
            "no_ddl": True,
        },
    }


def _sanitize_raw_ir(
    raw_ir: Any,
    utterance: str,
    select_mode: SelectMode,
) -> dict:
    """
    Sanitise any IR dict coming from the local model, fast path, or external NLP
    so it fits StructuredIR and current executor constraints.
    """
    if not isinstance(raw_ir, dict):
        raw_ir = {}

    clean = dict(raw_ir)

    table = clean.get("table")
    if table not in PIPELINE_ALLOWED_VIEWS:
        table = _table_for_utterance(utterance)
    clean["table"] = table

    clean["intent"] = _normalize_intent(clean.get("intent"), utterance, table)
    clean["description"] = str(clean.get("description") or utterance[:300])[:300]
    clean["chart_type"] = _normalize_chart_type(clean.get("chart_type"), select_mode)

    clean["joins"] = []
    clean["safety"] = {
        "read_only": True,
        "allow_subquery": False,
        "no_sql": True,
        "no_ddl": True,
        "max_rows": 500,
    }

    allowed_cols = _allowed_columns_for(table)

    # SELECT
    select_items: List[Dict[str, str]] = []
    for item in clean.get("select") or []:
        if not isinstance(item, dict):
            continue
        col = str(item.get("column") or "").split(".")[-1].strip()
        if col and col in allowed_cols:
            select_items.append(
                {
                    "table": table,
                    "column": col,
                    "alias": str(item.get("alias") or col),
                }
            )

    if not select_items:
        select_items = _build_select_items(table, _select_columns_for_mode(table, select_mode))
    clean["select"] = select_items

    # FILTERS
    filters: List[Dict[str, Any]] = []
    for f in clean.get("filters") or []:
        if not isinstance(f, dict):
            continue

        col = str(f.get("column") or "").split(".")[-1].strip()
        op = str(f.get("op") or "").strip().lower()

        if col not in allowed_cols or op not in PIPELINE_ALLOWED_FILTER_OPS:
            continue

        if table == "employee_leave_v" and col == "dept_name":
            continue

        item: Dict[str, Any] = {"column": col, "op": op}

        if op == "in":
            vals = f.get("values")
            if vals is None:
                v = f.get("value")
                if isinstance(v, list):
                    vals = v
                elif v is None:
                    vals = []
                else:
                    vals = [v]
            elif not isinstance(vals, list):
                vals = [vals]
            item["values"] = [str(v) for v in vals if v is not None]

        elif op == "between":
            low = f.get("low", f.get("value"))
            high = f.get("high")
            item["low"] = low
            item["high"] = high

        else:
            item["value"] = f.get("value")

        filters.append(item)

    clean["filters"] = filters

    # ORDER BY
    order_by: List[Dict[str, Any]] = []
    for o in clean.get("order_by") or []:
        if not isinstance(o, dict):
            continue

        col = str(o.get("column") or "").split(".")[-1].strip()
        agg_aliases = {
            a["alias"]
            for a in clean.get("aggregations", [])
            if isinstance(a, dict) and a.get("alias")
        }

        if col not in allowed_cols and col not in agg_aliases:
            continue

        direction = str(o.get("direction") or "asc").lower()
        order_by.append(
            {
                "table": table,
                "column": col,
                "direction": "desc" if direction == "desc" else "asc",
            }
        )

    clean["order_by"] = order_by

    # GROUP BY
    group_by: List[str] = []
    for gb in clean.get("group_by") or []:
        if isinstance(gb, str) and gb in allowed_cols:
            group_by.append(gb)

    gb_field = clean.get("group_by_field")
    if isinstance(gb_field, str) and gb_field in allowed_cols and gb_field not in group_by:
        group_by.append(gb_field)
        clean["group_by_field"] = gb_field
    else:
        clean["group_by_field"] = gb_field if isinstance(gb_field, str) and gb_field in allowed_cols else None

    clean["group_by"] = group_by

    # AGGREGATIONS
    aggregations: List[Dict[str, Any]] = []
    for agg in clean.get("aggregations") or []:
        if not isinstance(agg, dict):
            continue

        func = str(agg.get("function") or "").strip().lower()
        if func not in {"count", "sum", "avg", "min", "max"}:
            continue

        col = str(agg.get("column") or "").strip().split(".")[-1]
        if not col or col == "*" or col not in allowed_cols:
            col = "request_id" if table == "employee_leave_v" else "emp_id"

        alias = str(agg.get("alias") or f"{func}_{col}")
        aggregations.append({"function": func, "column": col, "alias": alias})

    clean["aggregations"] = aggregations

    # TIME WINDOW
    tw = clean.get("time_window")
    if isinstance(tw, dict) and tw.get("type") in {"relative", "absolute"}:
        clean["time_window"] = {
            "type": tw.get("type"),
            "value": str(tw.get("value") or ""),
        }
    else:
        clean["time_window"] = None

    # LIMIT
    try:
        limit = int(clean.get("limit", 50))
    except Exception:
        limit = 50
    clean["limit"] = max(1, min(limit, 1000))

    clean.pop("select_mode", None)

    return clean


# ──────────────────────────────────────────────────────────────────────────────
# PIPELINE V3
# ──────────────────────────────────────────────────────────────────────────────

class BhelvizPipelineV3:
    """
    Extended inference pipeline that adds a local DistilBERT model as an
    intermediate stage between FastPath and the external NLP endpoint.
    """

    def __init__(
        self,
        nlp_endpoint: str,
        api_key: str,
        checkpoint: Optional[str] = None,
        pretrained_name: str = "distilbert-base-uncased",
        local_conf_threshold: float = LOCAL_CONF_THRESHOLD,
        confidence_threshold: float = 0.55,
        timeout: float = 12.0,
        schema_catalog: Dict[str, Any] = PIPELINE_SCHEMA_CATALOG,
        glossary: Dict[str, str] = PIPELINE_BUSINESS_GLOSSARY,
    ):
        self._local_threshold = local_conf_threshold
        self._slots: Dict[str, ConversationSlotsV2] = {}
        self._fast_path = FastPathMatcher()
        self._confidence_scorer = IRConfidenceScorer(threshold=confidence_threshold)
        self.catalog = schema_catalog
        self.glossary = glossary

        self._v2 = RemoteNLPPipeline(
            nlp_endpoint=nlp_endpoint,
            api_key=api_key,
            schema_catalog=schema_catalog,
            glossary=glossary,
            timeout=timeout,
        )

        self.local_model = None
        self.local_tokenizer = None
        try:
            self.local_model, self.local_tokenizer = build_model(
                pretrained_name=pretrained_name,
                checkpoint=checkpoint,
            )
            self.local_model.eval()
            log.info(
                "PipelineV3 ready — local model trainable params=%d  threshold=%.2f",
                self.local_model.trainable_params(),
                local_conf_threshold,
            )
        except Exception as exc:
            log.warning("Local model unavailable; falling back to remote NLP only: %s", exc)

    def _get_slots(self, session_id: str) -> ConversationSlotsV2:
        if session_id not in self._slots or self._slots[session_id].is_expired():
            self._slots[session_id] = ConversationSlotsV2(session_id=session_id)
        return self._slots[session_id]

    def _run_local_model(self, utterance: str) -> Dict[str, Any]:
        if self.local_model is None or self.local_tokenizer is None:
            raise RuntimeError("Local model not available")

        enc = self.local_tokenizer.encode(utterance)
        tokens = self.local_tokenizer.tokens(utterance)

        with torch.no_grad():
            preds = self.local_model.predict(
                enc["input_ids"],
                enc["attention_mask"]
            )

        print("TRANSFORMER INFERENCE EXECUTED")
        print("MODEL OUTPUT =", preds)

        preds["tokens"] = tokens
        return preds

    def _build_fast_path_ir(self, fp: FastPathResult, utterance: str, slots: ConversationSlotsV2) -> Dict[str, Any]:
        table = _table_for_utterance(utterance)
        select_mode = _normalize_select_mode(fp.select_mode)
        intent = _normalize_intent(fp.intent, utterance, table)

        select_items = _build_select_items(table, _select_columns_for_mode(table, select_mode))
        group_by_field = _infer_group_by_field(table, select_mode, utterance)
        if select_mode == SelectMode.AGGREGATE:
            select_items = [
                {
                    "table": table,
                    "column": "dept_name",
                    "alias": "department"
                }
            ]

        filters: List[Dict[str, Any]] = []
        u = utterance.lower()

        if any(k in u for k in ["proxy", "false attendance", "anomaly"]):
            filters.append({
                "column": "status",
                "op": "eq",
                "value": "FALSE_PRESENT"
            })

        elif "absent" in u:
            filters.append({
                "column": "status",
                "op": "eq",
                "value": "ABSENT"
            })

        elif "late" in u:
            filters.append({
                "column": "status",
                "op": "eq",
                "value": "LATE"
            })

        elif "present" in u:
            filters.append({
                "column": "status",
                "op": "eq",
                "value": "PRESENT"
            })
        if slots.last_status and table == "employee_attendance_v":
            filters.append({"column": "status", "op": "eq", "value": slots.last_status})
        if slots.last_dept and table == "employee_attendance_v":
            filters.append({"column": "dept_name", "op": "eq", "value": slots.last_dept})

        time_window = None
        if slots.last_time_window:
            time_window = {"type": "relative", "value": slots.last_time_window}

        chart_map = {
            SelectMode.COUNT: "table",
            SelectMode.LIST: "table",
            SelectMode.AGGREGATE: "bar",
            SelectMode.DETAIL: "table",
            SelectMode.TREND: "line",
            SelectMode.NAMES_ONLY: "table",
        }

        return {
            "intent": intent,
            "table": table,
            "description": f"FastPath: {utterance[:120]}",
            "chart_type": chart_map.get(select_mode, "table"),
            "select_mode": select_mode.value,
            "group_by_field": group_by_field,
            "select": select_items,
            "filters": filters,
            "order_by": [
                {
                    "table": table,
                    "column": "start_date" if table == "employee_leave_v" else "att_date",
                    "direction": "desc",
                }
            ],
            "time_window": time_window,
            "limit": 50,
            "safety": {
                "read_only": True,
                "allow_subquery": False,
                "no_sql": True,
                "no_ddl": True,
            },
        }

    def _select_mode_from_ir(self, raw_ir: Dict[str, Any]) -> SelectMode:
        sm = raw_ir.get("select_mode") or raw_ir.get("mode") or raw_ir.get("chart_type")
        if isinstance(sm, SelectMode):
            return sm
        if isinstance(sm, str):
            try:
                return SelectMode(sm.upper())
            except Exception:
                pass

        chart = str(raw_ir.get("chart_type") or "").lower()
        if chart == "line":
            return SelectMode.TREND
        if chart == "bar":
            return SelectMode.AGGREGATE
        return SelectMode.LIST

    def _enforce_select_mode(self, raw_ir: Dict[str, Any], select_mode: SelectMode) -> Dict[str, Any]:
        table = raw_ir.get("table") if raw_ir.get("table") in PIPELINE_ALLOWED_VIEWS else _table_for_utterance("")
        if table not in PIPELINE_ALLOWED_VIEWS:
            table = "employee_attendance_v"

        template_cols = _select_columns_for_mode(table, select_mode)
        allowed = _allowed_columns_for(table)

        cleaned_select = []
        for item in raw_ir.get("select") or []:
            if not isinstance(item, dict):
                continue
            col = str(item.get("column") or "").split(".")[-1].strip()
            if col in allowed:
                cleaned_select.append({"table": table, "column": col, "alias": str(item.get("alias") or col)})

        if not cleaned_select:
            cleaned_select = _build_select_items(table, template_cols)

        raw_ir["table"] = table
        raw_ir["select"] = cleaned_select

        if not raw_ir.get("group_by_field"):
            raw_ir["group_by_field"] = _infer_group_by_field(table, select_mode, "")
        return raw_ir

    # ── main entry point ──────────────────────────────────────────────────────

    def get_ir(
        self,
        utterance: str,
        session_id: str,
        conversation_history: Optional[List[Dict]] = None,
    ) -> Any:
        """
        V3 pipeline — adds local model stage between FastPath and external NLP.
        """
        t_start = time.perf_counter()

        # 1. Safety
        safe, reason = is_safe_utterance(utterance)
        if not safe:
            log.warning("Unsafe utterance session=%s: %s", session_id, reason)
            raise ValueError(f"Query rejected: {reason}")

        # 2. Slot resolution
        slots = self._get_slots(session_id)
        utterance = slots.resolve_pronouns(utterance)
        log.info("Resolved utterance: %r", utterance)

        source = "unknown"
        raw_ir: Optional[Dict[str, Any]] = None
        select_mode = SelectMode.LIST

        # 3. FastPath
        fp = self._fast_path.match(utterance)
        if fp.matched:
            log.info("FastPath hit: mode=%s", fp.select_mode)
            raw_ir = self._build_fast_path_ir(fp, utterance, slots)
            select_mode = _normalize_select_mode(fp.select_mode)
            source = "fast_path"

        # 4. Local model (only if FastPath missed)
        if raw_ir is None:
            try:
                preds = self._run_local_model(utterance)
                intent_pred = preds.get("intent")
                mode_pred = preds.get("select_mode")

                intent_conf = float(preds.get("intent_conf", 0.0))
                mode_conf = float(preds.get("mode_conf", 0.0))

                log.info(
                    "LocalModel: intent=%s(%.2f) mode=%s(%.2f)",
                    intent_pred, intent_conf, mode_pred, mode_conf,
                )

                if intent_conf >= self._local_threshold and mode_conf >= self._local_threshold:
                    extracted_slots = _extract_slots_from_tags(
                        preds["tokens"], preds["slot_tags"], utterance
                    )

                    # Carry forward session slots
                    extracted_slots["dept"] = extracted_slots["dept"] or slots.last_dept
                    extracted_slots["status"] = extracted_slots["status"] or slots.last_status
                    extracted_slots["time_hint"] = extracted_slots["time_hint"] or slots.last_time_window

                    raw_ir = _assemble_ir_from_local(
                        preds,
                        extracted_slots,
                        utterance,
                    )
                    select_mode = _normalize_select_mode(mode_pred)
                    source = "local_model"
                    log.info("LocalModel hit — skipping external NLP")
                else:
                    log.info(
                        "LocalModel confidence too low (%.2f / %.2f) — falling through to NLP endpoint",
                        intent_conf,
                        mode_conf,
                    )
            except Exception as exc:
                log.warning("Local model stage failed: %s", exc)

        # 5. External NLP (only if FastPath and local model missed)
        if raw_ir is None:
            log.info("Calling external NLP endpoint")
            try:
                system = self._v2.NLP_SYSTEM_PROMPT.format(
                    schema_summary=self._v2._schema_summary(),
                    glossary=self._v2._glossary_summary(),
                    context_block="none",
                )
                raw_ir = self._v2._call_nlp(system, utterance, conversation_history or [])
            except Exception as exc:
                log.warning("External NLP failed, using default IR: %s", exc)
                raw_ir = _default_ir(utterance)
            select_mode = self._select_mode_from_ir(raw_ir)
            raw_ir = self._enforce_select_mode(raw_ir, select_mode)
            source = "external_nlp"

        # 7. Hard guardrails (always applied regardless of source)
        raw_ir = _sanitize_raw_ir(raw_ir, utterance, select_mode)

        # 8. Confidence gate
        score = self._confidence_scorer.score(raw_ir, select_mode)
        log.info("IRConfidence [%s]: %s", source, score)
        if not score.passed:
            log.warning("Low confidence IR (%.3f) — falling back to default", score.total)
            raw_ir = _sanitize_raw_ir(_default_ir(utterance), utterance, SelectMode.LIST)
            select_mode = SelectMode.LIST
            source = "default_fallback"

        # 9. Pydantic validation
        raw_ir.pop("select_mode", None)
        try:
            ir = StructuredIR.model_validate(raw_ir)
        except Exception as exc:
            log.warning("StructuredIR validation failed (%s) — falling back to default IR", exc)
            raw_ir = _sanitize_raw_ir(_default_ir(utterance), utterance, SelectMode.LIST)
            raw_ir.pop("select_mode", None)
            ir = StructuredIR.model_validate(raw_ir)

        # 10. Belt-and-suspenders
        if not ir.safety.read_only or ir.safety.allow_subquery:
            raise ValueError("IR safety invariant violated")
        if getattr(ir, "joins", None):
            raise ValueError("Joins are disabled")
        if getattr(ir, "table", None) not in PIPELINE_ALLOWED_VIEWS:
            raise ValueError(f"Invalid view: {getattr(ir, 'table', None)!r}")

        # 11. Slot update
        slots.update_from_ir(ir, select_mode)

        elapsed_ms = int((time.perf_counter() - t_start) * 1000)
        log.info("get_ir complete [source=%s] in %d ms", source, elapsed_ms)

        return ir


# ──────────────────────────────────────────────────────────────────────────────
# FACTORY
# ──────────────────────────────────────────────────────────────────────────────

def build_pipeline_v3(
    nlp_endpoint: str,
    api_key: str,
    checkpoint: Optional[str] = None,
    pretrained_name: str = "distilbert-base-uncased",
    local_conf_threshold: float = LOCAL_CONF_THRESHOLD,
    confidence_threshold: float = 0.55,
    timeout: float = 12.0,
) -> BhelvizPipelineV3:
    """
    Factory — creates a ready-to-use BhelvizPipelineV3.
    """
    return BhelvizPipelineV3(
        nlp_endpoint=nlp_endpoint,
        api_key=api_key,
        checkpoint=checkpoint,
        pretrained_name=pretrained_name,
        local_conf_threshold=local_conf_threshold,
        confidence_threshold=confidence_threshold,
        timeout=timeout,
    )


# ──────────────────────────────────────────────────────────────────────────────
# QUICK SMOKE TEST
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("Loading local model (untrained — random weights for smoke test) …")
    model, tokenizer = build_model()

    TEST_UTTERANCES = [
        "how many absentees today in HR",
        "show late employees in Finance yesterday",
        "attendance breakdown by department last week",
        "who are the latecomers today",
        "attendance trend over last month",
        "full details of false attendance yesterday",
        "show approved leaves last month",
        "count pending leaves",
    ]

    print("\n{:<50} {:<22} {:<14} {:>7} {:>7}".format(
        "Utterance", "Intent", "Mode", "I-conf", "M-conf"
    ))
    print("-" * 105)

    for utt in TEST_UTTERANCES:
        enc = tokenizer.encode(utt)
        preds = model.predict(enc["input_ids"], enc["attention_mask"])
        print("{:<50} {:<22} {:<14} {:>6.2f} {:>7.2f}".format(
            utt[:49],
            preds["intent"],
            preds["select_mode"],
            preds["intent_conf"],
            preds["mode_conf"],
        ))

    print("\n(Confidences above are from an untrained model — run bhelviz_trainer.py first.)")
    print("\nSlot tag example for 'show absentees in HR today':")
    enc = tokenizer.encode("show absentees in HR today")
    tokens = tokenizer.tokens("show absentees in HR today")
    preds = model.predict(enc["input_ids"], enc["attention_mask"])
    for tok, tag in zip(tokens, preds["slot_tags"]):
        if tok not in ("[PAD]",):
            print(f"  {tok:<20} {tag}")