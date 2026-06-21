
"""
BHELVIZ — NLP Engine Client & IR Pipeline

This module provides:
- schema catalog and glossary
- safety filtering
- slot memory
- fast-path rule matching
- IR confidence scoring
- an NLP→IR pipeline
- a compatibility TransformerIRPipeline wrapper
- simple GA / PSO optimizers used by the trainer

The module stays read-only and never touches Oracle.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from models import StructuredIR

log = logging.getLogger("bhelviz.nlp")

# ── VIEW CATALOG (exposed to NLP — no ciphertext, no data) ────────────────────

SCHEMA_CATALOG: Dict[str, Any] = {
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

BUSINESS_GLOSSARY: Dict[str, str] = {
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

ALLOWED_VIEWS = {"employee_attendance_v", "employee_leave_v"}

# ── Compatibility / v2 shims -------------------------------------------------

class SelectMode(str, Enum):
    LIST = "LIST"
    COUNT = "COUNT"
    AGGREGATE = "AGGREGATE"
    DETAIL = "DETAIL"
    TREND = "TREND"
    NAMES_ONLY = "NAMES_ONLY"


class ChartHint(str, Enum):
    TABLE = "table"
    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    AREA = "area"


@dataclass
class QueryIntent:
    name: str
    score: float = 0.0


@dataclass
class FastPathResult:
    matched: bool = False
    select_mode: SelectMode = SelectMode.LIST
    intent: Optional[str] = None
    score: float = 0.0
class FastPathMatcher:
    """
    Lightweight regex/rule matcher for ultra-fast intent routing.
    Runs before transformer inference.
    """

    def match(self, utterance: str) -> FastPathResult:
        u = utterance.lower().strip()

        # # COUNT queries
        # if any(x in u for x in ["how many", "count", "total number"]):
        #     return FastPathResult(
        #         matched=True,
        #         select_mode=SelectMode.COUNT,
        #         intent=self._infer_intent(u),
        #         score=0.95,
        #     )

        # # DETAIL queries
        # if any(x in u for x in ["full details", "details of", "show details"]):
        #     return FastPathResult(
        #         matched=True,
        #         select_mode=SelectMode.DETAIL,
        #         intent=self._infer_intent(u),
        #         score=0.92,
        #     )

        # TREND queries
        # if any(x in u for x in ["trend", "over time", "last month", "last week"]):
        #     return FastPathResult(
        #         matched=True,
        #         select_mode=SelectMode.TREND,
        #         intent=self._infer_intent(u),
        #         score=0.90,
        #     )

        # # AGGREGATE queries
        # if any(x in u for x in ["breakdown", "by department", "summary by"]):
        #     return FastPathResult(
        #         matched=True,
        #         select_mode=SelectMode.AGGREGATE,
        #         intent=self._infer_intent(u),
        #         score=0.88,
        #     )
    #     if any(x in u for x in [
    #     "departments where employees are absent",
    #     "departments with absentees",
    #     "absent departments",
    #     "which departments have absentees"
    # ]):
    #         return FastPathResult(
    #             matched=True,
    #             select_mode=SelectMode.AGGREGATE,
    #             intent="attendance_summary",
    #             score=0.95,
    #         )
        # LIST queries
        # if any(x in u for x in ["show", "list", "who are"]):
        #     return FastPathResult(
        #         matched=True,
        #         select_mode=SelectMode.LIST,
        #         intent=self._infer_intent(u),
        #         score=0.85,
        #     )

        return FastPathResult(
            matched=False,
            select_mode=SelectMode.LIST,
            intent=None,
            score=0.0,
        )

    def _infer_intent(self, u: str) -> str:
        if any(k in u for k in ["proxy", "false attendance", "anomaly"]):
            return "anomaly_detection"

        if any(k in u for k in ["leave", "vacation"]):
            return "leave_summary"

        if any(k in u for k in ["executive", "supervisor", "workman"]):
            return "role_comparison"

        if any(k in u for k in ["manager", "hierarchy", "reports to"]):
            return "hierarchy_lookup"

        return "attendance_summary"

# Select templates used by the fast-path / local assembly code.
# These are intentionally conservative and avoid unsupported columns.
SELECT_TEMPLATES: Dict[str, Dict[SelectMode, List[str]]] = {
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


@dataclass
class IRScore:
    total: float
    passed: bool
    details: Dict[str, float]


class IRConfidenceScorer:
    def __init__(self, threshold: float = 0.30) -> None:   # lowered for analytics debugging
        self.threshold = float(threshold)

    def score(self, ir: Dict[str, Any], select_mode: Optional[SelectMode] = None) -> IRScore:
        sel = ir.get("select") or []
        filters = ir.get("filters") or []
        time_w = ir.get("time_window")

        comp_select = min(1.0, 0.10 + 0.18 * len(sel))
        comp_filters = 0.20 if filters else 0.0
        comp_time = 0.15 if time_w else 0.0
        comp_mode = 0.10 if select_mode is not None else 0.0

        # Boost for analytical queries (aggregations + group by)
        has_aggs = bool(ir.get("aggregations"))
        has_group = bool(ir.get("group_by"))
        if has_aggs:
            comp_select += 0.35
        if has_group:
            comp_select += 0.20
        comp_select = min(comp_select, 1.0)

        total = min(1.0, comp_select + comp_filters + comp_time + comp_mode)
        passed = total >= self.threshold
        details = {
            "select": comp_select,
            "filters": comp_filters,
            "time": comp_time,
            "mode": comp_mode,
        }
        return IRScore(total=total, passed=passed, details=details)

# ── CONVERSATION SLOT MEMORY ──────────────────────────────────────────────────

@dataclass
class ConversationSlots:
    """
    Minimal slot-based context memory. Per-session.
    SECURITY: Never stores credentials, row data, full transcripts, or audio.
    TTL: slots are evicted after SESSION_TTL seconds of inactivity.
    """

    session_id: str
    last_intent: Optional[str] = None
    last_view: Optional[str] = None
    last_dept: Optional[str] = None
    last_role: Optional[str] = None
    last_shift: Optional[str] = None
    last_status: Optional[str] = None
    last_metric: Optional[str] = None
    last_time_window: Optional[str] = None
    last_chart_type: Optional[str] = None
    last_active_at: float = field(default_factory=time.time)

    SESSION_TTL: int = 1800  # 30 minutes

    def is_expired(self) -> bool:
        return (time.time() - self.last_active_at) > self.SESSION_TTL

    def update(self, ir: StructuredIR) -> None:
        self.last_intent = getattr(getattr(ir, "intent", None), "value", getattr(ir, "intent", None))
        self.last_view = getattr(ir, "table", None)
        self.last_chart_type = getattr(getattr(ir, "chart_type", None), "value", getattr(ir, "chart_type", None))
        if getattr(ir, "time_window", None):
            self.last_time_window = getattr(ir.time_window, "value", None)

        for f in getattr(ir, "filters", []) or []:
            col = getattr(f, "column", "").lower()
            val = getattr(f, "value", None)
            if col in ("dept_code", "dept_name", "dept_name_enc"):
                self.last_dept = str(val)
            if col in ("current_role_code", "role_name"):
                self.last_role = str(val)
            if col in ("shift_code", "shift_name"):
                self.last_shift = str(val)
            if col in ("status_code", "status"):
                self.last_status = str(val)

        self.last_active_at = time.time()

    def update_from_ir(self, ir: StructuredIR, select_mode: Optional[SelectMode] = None) -> None:
        """Compatibility helper used by pipeline v3."""
        try:
            self.update(ir)
        except Exception:
            log.debug("slots.update failed", exc_info=True)
        if select_mode is not None:
            self.last_metric = select_mode.value
            self.last_chart_type = getattr(getattr(ir, "chart_type", None), "value", getattr(ir, "chart_type", None))

    def to_context_block(self) -> str:
        """Return a small text block summarising recent slots for system prompt."""
        parts: List[str] = []
        if self.last_view:
            parts.append(f"view={self.last_view}")
        if self.last_dept:
            parts.append(f"dept={self.last_dept}")
        if self.last_role:
            parts.append(f"role={self.last_role}")
        if self.last_status:
            parts.append(f"status={self.last_status}")
        if self.last_time_window:
            parts.append(f"time={self.last_time_window}")
        return "; ".join(parts) if parts else "none"

    def resolve_pronouns(self, utterance: str) -> str:
        u = utterance.lower()
        if "same department" in u and self.last_dept:
            utterance = utterance.replace("same department", self.last_dept)
        if "same role" in u and self.last_role:
            utterance = utterance.replace("same role", self.last_role)
        if "same shift" in u and self.last_shift:
            utterance = utterance.replace("same shift", self.last_shift)
        if "same status" in u and self.last_status:
            utterance = utterance.replace("same status", self.last_status)
        if "same view" in u and self.last_view:
            utterance = utterance.replace("same view", self.last_view)
        return utterance


class ConversationSlotsV2(ConversationSlots):
    """Backward-compatible wrapper expected by pipeline_v3."""
    pass


# ── SAFETY / MODERATION FILTER ────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    r"(?i)(ignore|forget|disregard).{0,20}(instruction|prompt|system)",
    r"(?i)(select|insert|update|delete|drop|create|alter|exec|execute)\s+",
    r"(?i)(union\s+select|or\s+1\s*=\s*1|and\s+1\s*=\s*1)",
    r"(?i)(--\s*$|/\*.*\*/)",
    r"(?i)(xp_|sp_|cmdshell|openrowset)",
    r"(?i)\b(exec|eval|subprocess)\s*\(",
    r"(?i)(jailbreak|dan mode|developer mode|bypass)",
]

_COMPILED_PATTERNS = [re.compile(p) for p in _INJECTION_PATTERNS]

_PASSWORD_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|manual.?key|decode.?key|unlock.?key)"
    r".{0,30}[A-Za-z0-9!@#$%^&*]{8,}"
)


def is_safe_utterance(utterance: str) -> Tuple[bool, str]:
    """
    Returns (is_safe, reason).
    If unsafe, the utterance must NOT be forwarded to the NLP model.
    """
    for p in _COMPILED_PATTERNS:
        if p.search(utterance):
            return False, f"Injection pattern detected: {p.pattern[:40]}"

    if _PASSWORD_PATTERN.search(utterance):
        return False, "Utterance appears to contain a password or key — session purged"

    if len(utterance) > 1000:
        return False, "Utterance exceeds maximum length"

    return True, ""


# ── NLP → IR PIPELINE ─────────────────────────────────────────────────────────

class NLPIRPipeline:
    """
    Converts a natural language utterance into a StructuredIR via an NLP service.

    The NLP service is isolated:
      - no DB credentials
      - no Oracle network route
      - receives only utterance text + view catalog + glossary
      - returns only typed JSON IR
    """

    NLP_SYSTEM_PROMPT = """You are the BHELVIZ NLP Engine.
Your ONLY job is converting natural language HR / leave / attendance queries into structured JSON IR.
You NEVER emit SQL.
You NEVER see real data.

You may target ONLY these Oracle views:
- employee_attendance_v
- employee_leave_v

Rules:
- Always set the `table` field to exactly one allowed view name.
- Never output joins.
- Never output raw SQL.
- Never invent tables, columns, or operators.
- Use only columns listed in the view catalog.
- For attendance queries, use `employee_attendance_v`.
- For leave queries, use `employee_leave_v`.

View catalog:
{schema_summary}

Business glossary:
{glossary}

Conversation context:
{context_block}

Output ONLY valid JSON matching this exact schema (no markdown, no preamble):
{{
  "intent": "attendance_summary|leave_summary|employee_lookup|role_comparison|anomaly_detection",
  "table": "employee_attendance_v|employee_leave_v",
  "description": "concise description",
  "chart_type": "table|bar|pie|line|area",
  "select_mode": "LIST|COUNT|AGGREGATE|DETAIL|TREND|NAMES_ONLY",
  "group_by_field": "dept_name|status|null",
  "select": [{{"table":"employee_attendance_v","column":"att_date","alias":"att_date"}}],
  "filters": [{{"column":"status","op":"eq|neq|in|between|lt|lte|gt|gte","value":"UPPERCASE_VALUE"}}],
  "order_by": [{{"table":"employee_attendance_v","column":"att_date","direction":"asc|desc"}}],
  "time_window": {{"type":"relative","value":"today|yesterday|last_week|last_month"}},
  "limit": 100,
  "safety": {{"read_only":true,"allow_subquery":false,"no_sql":true,"no_ddl":true}}
}}"""

    def __init__(
        self,
        nlp_endpoint: str,
        api_key: str,
        schema_catalog: dict = SCHEMA_CATALOG,
        glossary: dict = BUSINESS_GLOSSARY,
        timeout: float = 10.0,
    ):
        self.endpoint = nlp_endpoint
        self.api_key = api_key
        self.catalog = schema_catalog
        self.glossary = glossary
        self.timeout = timeout
        self._slots: Dict[str, ConversationSlots] = {}

    def _get_slots(self, session_id: str) -> ConversationSlots:
        if session_id not in self._slots or self._slots[session_id].is_expired():
            self._slots[session_id] = ConversationSlots(session_id=session_id)
        return self._slots[session_id]

    def _schema_summary(self) -> str:
        lines = []
        view_columns = self.catalog.get("view_columns", {})
        for view_name in self.catalog.get("views", []):
            cols = view_columns.get(view_name, {})
            if isinstance(cols, dict):
                cols_list = list(cols.keys())
            else:
                cols_list = list(cols)
            lines.append(f"{view_name}({', '.join(cols_list)})")
        return "\n".join(lines)

    def _glossary_summary(self) -> str:
        return ", ".join(f"{k}→{v}" for k, v in self.glossary.items())

    def _external_context_block(self, context_chunks: Optional[List[str]] = None) -> str:
        """Return a concise context block built from retrieved document chunks."""
        if not context_chunks:
            return "none"
        # Keep it short: include first 3 chunks, truncated
        parts = []
        for i, c in enumerate(context_chunks[:3]):
            snippet = (c[:400] + "…") if len(c) > 400 else c
            parts.append(f"[{i+1}] {snippet}")
        return "\n".join(parts)
    def _infer_intent_from_text(self, utterance: str) -> str:
        u = utterance.lower()
        if any(k in u for k in ["leave", "vacation", "approved leave", "pending leave", "rejected leave"]):
            return "leave_summary"
        if any(k in u for k in ["absent", "lateness", "late", "present", "attendance", "proxy", "false attendance"]):
            if any(k in u for k in ["who", "which employee", "names", "list names", "details", "full details"]):
                return "employee_lookup"
            if "false attendance" in u or "proxy" in u:
                return "anomaly_detection"
            return "attendance_summary"
        if any(k in u for k in ["executive", "supervisor", "workman"]):
            return "role_comparison"
        if any(k in u for k in ["who is", "who are", "which employee", "names of", "list names", "details", "show me"]):
            return "employee_lookup"
        return "attendance_summary"

    def _default_ir(self, utterance: str) -> dict:
        """Safe fallback IR that still targets only approved views."""
        u = utterance.lower()

        if any(w in u for w in ["leave", "vacation", "approved leave", "pending leave", "rejected leave"]):
            return {
                "intent": "leave_summary",
                "table": "employee_leave_v",
                "description": "Employee leave summary",
                "chart_type": "table",
                "select_mode": "LIST",
                "select": [
                    {"table": "employee_leave_v", "column": "request_id", "alias": "request_id"},
                    {"table": "employee_leave_v", "column": "emp_id", "alias": "emp_id"},
                    {"table": "employee_leave_v", "column": "full_name", "alias": "full_name"},
                    {"table": "employee_leave_v", "column": "leave_type", "alias": "leave_type"},
                    {"table": "employee_leave_v", "column": "start_date", "alias": "start_date"},
                    {"table": "employee_leave_v", "column": "end_date", "alias": "end_date"},
                    {"table": "employee_leave_v", "column": "status", "alias": "status"},
                ],
                "filters": [{"column": "status", "op": "eq", "value": "APPROVED"}],
                "order_by": [{"table": "employee_leave_v", "column": "start_date", "direction": "desc"}],
                "time_window": None,
                "limit": 50,
                "safety": {"read_only": True, "allow_subquery": False, "no_sql": True, "no_ddl": True},
            }

        return {
            "intent": "attendance_summary",
            "table": "employee_attendance_v",
            "description": "Attendance summary",
            "chart_type": "table",
            "select_mode": "LIST",
            "select": [
                {"table": "employee_attendance_v", "column": "emp_id", "alias": "emp_id"},
                {"table": "employee_attendance_v", "column": "full_name", "alias": "full_name"},
                {"table": "employee_attendance_v", "column": "dept_name", "alias": "dept_name"},
                {"table": "employee_attendance_v", "column": "att_date", "alias": "att_date"},
                {"table": "employee_attendance_v", "column": "status", "alias": "status"},
            ],
            "filters": [],
            "order_by": [{"table": "employee_attendance_v", "column": "att_date", "direction": "desc"}],
            "time_window": {"type": "relative", "value": "today"},
            "limit": 50,
            "safety": {"read_only": True, "allow_subquery": False, "no_sql": True, "no_ddl": True},
        }

    def _build_fast_path_ir(self, fp: FastPathResult, utterance: str, slots: ConversationSlots) -> dict:
        """Build a conservative IR directly from a fast-path hit."""
        is_leave = bool(re.search(r"(?i)\b(leave|leaves|vacation)\b", utterance))
        table = "employee_leave_v" if is_leave else "employee_attendance_v"

        sm = fp.select_mode if isinstance(fp.select_mode, SelectMode) else SelectMode.LIST
        template = SELECT_TEMPLATES.get(table, SELECT_TEMPLATES["employee_attendance_v"])
        col_list = template.get(sm, template[SelectMode.LIST])

        # Keep fast-path outputs simple and valid. No group_by by default.
        select_items = [{"table": table, "column": c, "alias": c} for c in col_list]

        filters: List[Dict[str, Any]] = []
        if slots.last_status:
            filters.append({"column": "status", "op": "eq", "value": slots.last_status})
        if slots.last_dept and table == "employee_attendance_v":
            filters.append({"column": "dept_name", "op": "eq", "value": slots.last_dept})

        time_val = slots.last_time_window
        time_window = {"type": "relative", "value": time_val} if time_val else None

        chart_map = {
            SelectMode.COUNT: "table",
            SelectMode.LIST: "table",
            SelectMode.AGGREGATE: "bar",
            SelectMode.DETAIL: "table",
            SelectMode.TREND: "line",
            SelectMode.NAMES_ONLY: "table",
        }

        intent = self._infer_intent_from_text(utterance)

        return {
            "intent": intent,
            "table": table,
            "description": f"FastPath: {utterance[:80]}",
            "chart_type": chart_map.get(sm, "table"),
            "select_mode": sm.value,
            "group_by_field": None,
            "select": select_items,
            "filters": filters,
            "order_by": [{"table": table, "column": "att_date" if table == "employee_attendance_v" else "start_date", "direction": "desc"}],
            "time_window": time_window,
            "limit": 100 if sm != SelectMode.COUNT else 1,
            "safety": {"read_only": True, "allow_subquery": False, "no_sql": True, "no_ddl": True},
        }

    def _select_mode_from_ir(self, raw_ir: dict) -> SelectMode:
        sm = raw_ir.get("select_mode") or raw_ir.get("mode") or raw_ir.get("chart_type")
        if isinstance(sm, SelectMode):
            return sm
        if isinstance(sm, str):
            try:
                return SelectMode(sm.upper())
            except Exception:
                if raw_ir.get("chart_type") == "line":
                    return SelectMode.TREND
                if raw_ir.get("chart_type") == "bar":
                    return SelectMode.AGGREGATE
        intent = str(raw_ir.get("intent", "")).lower()
        if intent in {"count_query"}:
            return SelectMode.COUNT
        if intent in {"trend_analysis"}:
            return SelectMode.TREND
        return SelectMode.LIST

    def _enforce_select_mode(self, raw_ir: dict, select_mode: SelectMode) -> dict:
        """Ensure select/list structure is compatible with the current select mode."""
        table = raw_ir.get("table", "employee_attendance_v")
        template = SELECT_TEMPLATES.get(table, SELECT_TEMPLATES["employee_attendance_v"])

        if not raw_ir.get("select"):
            raw_ir["select"] = [
                {"table": table, "column": c, "alias": c}
                for c in template.get(select_mode, template[SelectMode.LIST])
            ]

        raw_ir["select_mode"] = select_mode.value
        raw_ir.setdefault("group_by_field", None)
        raw_ir.setdefault("filters", [])
        raw_ir.setdefault("order_by", [])
        raw_ir.setdefault("limit", 50)
        return raw_ir

    def _call_nlp(self, system: str, utterance: str, history: List[Dict]) -> dict:
        """Calls the NLP endpoint. Returns parsed dict. Falls back on safe IR if needed."""
        if not self.endpoint or not self.api_key:
            return self._default_ir(utterance)

        try:
            import httpx
        except Exception as exc:  # pragma: no cover
            log.warning("httpx unavailable; falling back to default IR: %s", exc)
            return self._default_ir(utterance)

        messages = [*history[-10:], {"role": "user", "content": utterance}]

        with httpx.Client(timeout=self.timeout) as client:
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            log.info(
                "Calling NLP endpoint %s (x-api-key present=%s) messages=%d",
                self.endpoint,
                bool(self.api_key),
                len(messages),
            )
            resp = client.post(
                self.endpoint,
                headers=headers,
                json={"system": system, "messages": messages, "max_tokens": 600},
            )
            resp.raise_for_status()
            data = resp.json()

        text = "".join(b.get("text", "") for b in data.get("content", []))
        text = re.sub(r"```\w*\n?|```", "", text).strip()

        try:
            log.info("NLP endpoint returned %d bytes", len(text))
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("NLP output must be a JSON object")
            return parsed
        except Exception:
            log.error("NLP returned invalid JSON (first 200 chars): %s…", text[:200])
            return self._default_ir(utterance)

    def get_ir(
        self,
        utterance: str,
        session_id: str,
        conversation_history: Optional[List[Dict]] = None,
        retrieved_context: Optional[List[str]] = None,
    ) -> StructuredIR:
        """
        Full pipeline:
          1. Safety / moderation check
          2. Slot resolution
          3. NLP inference → raw JSON
          4. Pydantic validation
          5. Slot memory update
        """
        safe, reason = is_safe_utterance(utterance)
        if not safe:
            log.warning("Unsafe utterance from session %s: %s", session_id, reason)
            raise ValueError(f"Query rejected: {reason}")

        slots = self._get_slots(session_id)
        utterance = slots.resolve_pronouns(utterance)

        system = self.NLP_SYSTEM_PROMPT.format(
            schema_summary=self._schema_summary(),
            glossary=self._glossary_summary(),
            context_block=slots.to_context_block(),
            external_context=self._external_context_block(retrieved_context),
        )

        raw_ir = self._call_nlp(system, utterance, conversation_history or [])

        raw_ir["table"] = raw_ir.get("table") or (
            "employee_leave_v" if any(k in utterance.lower() for k in ["leave", "vacation"]) else "employee_attendance_v"
        )
        if raw_ir["table"] not in ALLOWED_VIEWS:
            raw_ir["table"] = "employee_attendance_v"

        raw_ir["joins"] = []
        raw_ir["safety"] = {
            "read_only": True,
            "allow_subquery": False,
            "no_sql": True,
            "no_ddl": True,
        }

        if "select" not in raw_ir or not raw_ir["select"]:
            raw_ir["select"] = []
        if "filters" not in raw_ir:
            raw_ir["filters"] = []
        if "order_by" not in raw_ir:
            raw_ir["order_by"] = []
        if "limit" not in raw_ir:
            raw_ir["limit"] = 50

        # Harmonise legacy group_by_field/group_by.
        if raw_ir.get("group_by_field") and not raw_ir.get("group_by"):
            raw_ir["group_by"] = [raw_ir["group_by_field"]]

        ir = StructuredIR.model_validate(raw_ir)

        if not ir.safety.read_only or ir.safety.allow_subquery:
            raise ValueError("IR safety invariant violated by model output")
        if getattr(ir, "joins", None):
            raise ValueError("Joins are disabled")
        if getattr(ir, "table", None) not in ALLOWED_VIEWS:
            raise ValueError(f"Invalid view target: {getattr(ir, 'table', None)!r}")

        slots.update(ir)
        return ir


class TransformerIRPipeline(NLPIRPipeline):
    """
    Compatibility adapter expected by bhelviz_pipeline_v3.

    This thin wrapper deliberately stays lightweight:
    - it does not load torch models itself
    - it exposes the helper methods pipeline_v3 calls
    - local transformer inference is handled in bhelviz_pipeline_v3
    """

    def __init__(
        self,
        nlp_endpoint: str | None = None,
        api_key: str | None = None,
        schema_catalog: dict = SCHEMA_CATALOG,
        glossary: dict = BUSINESS_GLOSSARY,
        timeout: float = 10.0,
        confidence_threshold: float = 0.55,
        model_path: Optional[str] = None,
        pretrained_name: str = "distilbert-base-uncased",
    ) -> None:
        super().__init__(
            nlp_endpoint=nlp_endpoint or "",
            api_key=api_key or "",
            schema_catalog=schema_catalog,
            glossary=glossary,
            timeout=timeout,
        )
        self.confidence_threshold = confidence_threshold
        self.model_path = model_path
        self.pretrained_name = pretrained_name
        self._confidence = IRConfidenceScorer(threshold=confidence_threshold)


# ── RLHF REWARD SIGNAL ────────────────────────────────────────────────────────

@dataclass
class RLHFFeedback:
    """
    Non-sensitive feedback signals collected per query for RLHF training.
    SECURITY: Never contains decrypted row data, passwords, or PII.
    """

    session_id: str
    query_utterance: str
    ir_hash: str
    valid_ir: bool
    execution_success: bool
    semantic_match: float
    latency_ms: int
    user_rating: Optional[int] = None
    policy_violation: bool = False
    hallucination: bool = False
    unsafe_output: bool = False

    def reward(self) -> float:
        latency_bonus = max(0.0, 1.0 - self.latency_ms / 5000)
        return (
            0.35 * float(self.valid_ir)
            + 0.25 * float(self.execution_success)
            + 0.20 * self.semantic_match
            + 0.10 * latency_bonus
            - 0.30 * float(self.policy_violation)
            - 0.25 * float(self.hallucination)
            - 1.00 * float(self.unsafe_output)
        )


# ── GA / PSO HYPERPARAMETER SEARCH (offline only) ────────────────────────────

GENE_SPACE = [
    ("learning_rate", 1e-5, 5e-4, False),
    ("lora_rank", 4, 32, True),
    ("dropout", 0.0, 0.30, False),
    ("beam_width", 1, 8, True),
    ("top_k_retrieval", 1, 10, True),
    ("w_intent", 0.0, 1.0, False),
    ("w_schema", 0.0, 1.0, False),
    ("w_exec", 0.0, 1.0, False),
    ("w_safety", 0.0, 1.0, False),
    ("confidence_threshold", 0.30, 0.95, False),
]

GA_PSO_CHROMOSOME = [
    "learning_rate",
    "lora_rank",
    "dropout",
    "beam_width",
    "top_k_retrieval",
    "w_intent",
    "w_schema",
    "w_exec",
    "w_safety",
    "confidence_threshold",
]

GA_PSO_FITNESS_WEIGHTS = {
    "exact_match": 0.40,
    "exec_accuracy": 0.25,
    "schema_f1": 0.15,
    "latency_score": 0.10,
    "human_rating": 0.10,
    "unsafe_rate": -1.50,
}

GA_PSO_CONSTRAINTS = [
    "unsafe_rate == 0",
    "no unauthorized view references",
    "no raw SQL emission",
    "no result exposure in model path",
]


def _clip_value(name: str, value: Any, is_int: bool, lo: float, hi: float):
    if is_int:
        return int(max(lo, min(int(round(float(value))), hi)))
    return float(max(lo, min(float(value), hi)))


def _score_metrics(metrics: Dict[str, float]) -> float:
    return (
        GA_PSO_FITNESS_WEIGHTS["exact_match"] * float(metrics.get("exact_match", 0.0))
        + GA_PSO_FITNESS_WEIGHTS["exec_accuracy"] * float(metrics.get("exec_accuracy", 0.0))
        + GA_PSO_FITNESS_WEIGHTS["schema_f1"] * float(metrics.get("schema_f1", 0.0))
        + GA_PSO_FITNESS_WEIGHTS["latency_score"] * float(metrics.get("latency_score", 0.0))
        + GA_PSO_FITNESS_WEIGHTS["human_rating"] * float(metrics.get("human_rating", 0.0))
        + GA_PSO_FITNESS_WEIGHTS["unsafe_rate"] * float(metrics.get("unsafe_rate", 0.0))
    )


class GeneticAlgorithmOptimizer:
    """
    Small generic GA used by the trainer for hyperparameter search.
    fitness_fn(params) -> metrics dict
    """

    def __init__(
        self,
        fitness_fn,
        pop_size: int = 8,
        n_elite: int = 2,
        cx_prob: float = 0.8,
        mut_prob: float = 0.2,
        tournament_k: int = 3,
    ):
        self.fitness_fn = fitness_fn
        self.pop_size = pop_size
        self.n_elite = n_elite
        self.cx_prob = cx_prob
        self.mut_prob = mut_prob
        self.tournament_k = tournament_k

    def _sample_gene(self, name: str, lo: float, hi: float, is_int: bool):
        import random
        if is_int:
            return random.randint(int(lo), int(hi))
        return random.uniform(float(lo), float(hi))

    def _random_individual(self):
        return {name: self._sample_gene(name, lo, hi, is_int) for name, lo, hi, is_int in GENE_SPACE}

    def _evaluate(self, individual: Dict[str, Any]):
        metrics = self.fitness_fn(individual)
        return _score_metrics(metrics), metrics

    def _tournament(self, scored_population):
        import random
        contenders = random.sample(scored_population, k=min(self.tournament_k, len(scored_population)))
        contenders.sort(key=lambda x: x[0], reverse=True)
        return contenders[0][1]

    def _crossover(self, a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
        import random
        child = {}
        for key in a.keys():
            child[key] = a[key] if random.random() < 0.5 else b[key]
        return child

    def _mutate(self, individual: Dict[str, Any]) -> Dict[str, Any]:
        import random
        child = dict(individual)
        for name, lo, hi, is_int in GENE_SPACE:
            if random.random() < self.mut_prob:
                span = (hi - lo)
                if is_int:
                    child[name] = _clip_value(name, child[name] + random.randint(-2, 2), True, lo, hi)
                else:
                    child[name] = _clip_value(name, child[name] + random.uniform(-0.15, 0.15) * span, False, lo, hi)
        return child

    def run(self, generations: int = 10):
        import random

        population = [self._random_individual() for _ in range(self.pop_size)]
        history = []
        best_individual = None
        best_score = float("-inf")

        for gen in range(generations):
            scored = []
            for ind in population:
                score, metrics = self._evaluate(ind)
                scored.append((score, ind, metrics))
                if score > best_score:
                    best_score = score
                    best_individual = dict(ind)

            scored.sort(key=lambda x: x[0], reverse=True)
            history.append(
                {
                    "generation": gen,
                    "best_score": scored[0][0],
                    "avg_score": sum(x[0] for x in scored) / max(1, len(scored)),
                    "best": dict(scored[0][1]),
                }
            )

            elites = [dict(scored[i][1]) for i in range(min(self.n_elite, len(scored)))]
            next_pop = elites[:]

            while len(next_pop) < self.pop_size:
                parent_a = self._tournament([(s, i) for s, i, _ in scored])
                parent_b = self._tournament([(s, i) for s, i, _ in scored])
                if random.random() < self.cx_prob:
                    child = self._crossover(parent_a, parent_b)
                else:
                    child = dict(parent_a)
                child = self._mutate(child)
                next_pop.append(child)

            population = next_pop

        return best_individual or self._random_individual(), history


class ParticleSwarmOptimizer:
    """
    Small generic PSO used by the trainer for hyperparameter search.
    fitness_fn(params) -> metrics dict
    """

    def __init__(
        self,
        fitness_fn,
        n_particles: int = 8,
        inertia: float = 0.5,
        cognitive: float = 1.2,
        social: float = 1.2,
    ):
        self.fitness_fn = fitness_fn
        self.n_particles = n_particles
        self.inertia = inertia
        self.cognitive = cognitive
        self.social = social

    def _random_position(self):
        import random
        pos = {}
        for name, lo, hi, is_int in GENE_SPACE:
            pos[name] = int(random.randint(int(lo), int(hi))) if is_int else random.uniform(float(lo), float(hi))
        return pos

    def _evaluate(self, position: Dict[str, Any]):
        metrics = self.fitness_fn(position)
        return _score_metrics(metrics), metrics

    def run(self, iterations: int = 20):
        import random

        particles = []
        for _ in range(self.n_particles):
            pos = self._random_position()
            vel = {name: 0.0 for name, *_ in GENE_SPACE}
            score, metrics = self._evaluate(pos)
            particles.append(
                {
                    "pos": pos,
                    "vel": vel,
                    "best_pos": dict(pos),
                    "best_score": score,
                    "score": score,
                }
            )

        gbest = max(particles, key=lambda p: p["best_score"])
        global_best_pos = dict(gbest["best_pos"])
        global_best_score = gbest["best_score"]
        history = []

        for it in range(iterations):
            for p in particles:
                for name, lo, hi, is_int in GENE_SPACE:
                    r1 = random.random()
                    r2 = random.random()
                    personal = p["best_pos"][name]
                    global_target = global_best_pos[name]
                    current = p["pos"][name]
                    vel = (
                        self.inertia * p["vel"][name]
                        + self.cognitive * r1 * (personal - current)
                        + self.social * r2 * (global_target - current)
                    )
                    p["vel"][name] = vel
                    new_val = current + vel
                    p["pos"][name] = _clip_value(name, new_val, is_int, lo, hi)

                score, metrics = self._evaluate(p["pos"])
                p["score"] = score
                if score > p["best_score"]:
                    p["best_score"] = score
                    p["best_pos"] = dict(p["pos"])
                if score > global_best_score:
                    global_best_score = score
                    global_best_pos = dict(p["pos"])

            history.append(
                {
                    "iteration": it,
                    "best_score": global_best_score,
                    "avg_score": sum(p["score"] for p in particles) / max(1, len(particles)),
                    "best": dict(global_best_pos),
                }
            )

        return global_best_pos, history


# Backwards-compatible alias used by older code paths
SelectModeEnum = SelectMode

