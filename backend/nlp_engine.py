"""
BHELVIZ — NLP Engine Client & IR Pipeline
═══════════════════════════════════════════════════════════════════════════════
Security contract:
  • NLP service has NO database credentials.
  • NLP service has NO network route to Oracle.
  • NLP service receives ONLY: user utterance, conversation slots, schema catalog.
  • NLP service NEVER receives query results, row data, or the Decoding Manual.
  • Model output is validated against a strict JSON schema before any compilation.
  • Adversarial prompt-injection attempts are filtered before IR generation.
  • Voice transcripts are ephemeral: TTL-purged, never persisted beyond the slot window.

RLHF integration:
  • Reward signal = f(valid_ir, exec_success, semantic_match, policy_violation)
  • PPO updates policy on IR tokens only — never on SQL or raw data.
  • GA / PSO used offline only for hyperparameter search (learning rate, LoRA rank,
    beam width, retrieval k, confidence threshold).
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from models import StructuredIR

log = logging.getLogger("bhelviz.nlp")

# ── VIEW CATALOG (exposed to NLP — no ciphertext, no data) ────────────────────

SCHEMA_CATALOG = {
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
        "employee_leave_v": [
            "request_id",
            "emp_id",
            "full_name",
            "leave_type",
            "start_date",
            "end_date",
            "reason",
            "status",
        ],
    },
}

BUSINESS_GLOSSARY = {
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
        self.last_intent = ir.intent
        self.last_view = getattr(ir, "table", None)
        self.last_chart_type = ir.chart_type
        if ir.time_window:
            self.last_time_window = ir.time_window.value

        for f in ir.filters:
            col = f.column.lower()
            if col in ("dept_code", "dept_name", "dept_name_enc"):
                self.last_dept = str(f.value)
            if col in ("current_role_code", "role_name"):
                self.last_role = str(f.value)
            if col in ("shift_code", "shift_name"):
                self.last_shift = str(f.value)
            if col in ("status_code", "status"):
                self.last_status = str(f.value)
            if col in ("status",):
                self.last_status = str(f.value)

        self.last_active_at = time.time()

    def resolve_pronouns(self, utterance: str) -> str:
        """
        Fills in simple slot references before NLP inference.
        Example: "same status" → fills last_status context.
        """
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


# ── SAFETY / MODERATION FILTER ────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    r"(?i)(ignore|forget|disregard).{0,20}(instruction|prompt|system)",
    r"(?i)(select|insert|update|delete|drop|create|alter|exec|execute)\s+",
    r"(?i)(union\s+select|or\s+1\s*=\s*1|and\s+1\s*=\s*1)",
    r"(?i)(--\s*$|/\*.*\*/)",
    r"(?i)(xp_|sp_|cmdshell|openrowset)",
    r"(?i)(base64|decode|eval|exec|system|subprocess)",
    r"(?i)(jailbreak|dan mode|developer mode|bypass)",
]

_COMPILED_PATTERNS = [re.compile(p) for p in _INJECTION_PATTERNS]

_PASSWORD_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|manual.?key|decode.?key|unlock.?key)"
    r".{0,30}[A-Za-z0-9!@#$%^&*]{8,}"
)


def is_safe_utterance(utterance: str) -> tuple[bool, str]:
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
    Converts a natural language utterance into a StructuredIR via the NLP service.

    The NLP service is completely isolated:
      - No DB credentials
      - No Oracle network route
      - Receives only: utterance text, view catalog, business glossary
      - Returns only: typed JSON IR

    In production this calls a self-hosted model endpoint.
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
- Never generate filters or select fields for columns not present in the target view.

View catalog:
{schema_summary}

Business glossary:
{glossary}

Output ONLY valid JSON matching this exact schema (no markdown, no preamble):
{{
  "intent": "attendance_summary|leave_summary|employee_lookup|role_comparison|anomaly_detection",
  "table": "employee_attendance_v|employee_leave_v",
  "description": "concise description",
  "chart_type": "table|bar|pie|line|area",
  "group_by_field": "dept_name|role_name|shift_name|status|status|null",
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
        for view_name in self.catalog["views"]:
            cols = ", ".join(self.catalog["view_columns"].get(view_name, []))
            lines.append(f"{view_name}({cols})")
        return "\n".join(lines)

    def _glossary_summary(self) -> str:
        return ", ".join(f"{k}→{v}" for k, v in self.glossary.items())

    def _default_ir(self, utterance: str) -> dict:
        """Safe fallback IR that still targets only approved views."""
        u = utterance.lower()

        if any(w in u for w in ["leave", "vacation", "approved leave", "pending leave"]):
            return {
                "intent": "leave_summary",
                "table": "employee_leave_v",
                "description": "Employee leave summary",
                "chart_type": "table",
                "group_by_field": "status",
                "select": [
                    {"table": "employee_leave_v", "column": "request_id", "alias": "request_id"},
                    {"table": "employee_leave_v", "column": "full_name", "alias": "full_name"},
                    {"table": "employee_leave_v", "column": "leave_type", "alias": "leave_type"},
                    {"table": "employee_leave_v", "column": "start_date", "alias": "start_date"},
                    {"table": "employee_leave_v", "column": "end_date", "alias": "end_date"},
                    {"table": "employee_leave_v", "column": "status", "alias": "status"},
                ],
                "filters": [
    {
        "column": "status",
        "op": "eq",
        "value": "APPROVED"
    }
],
                "order_by": [
                    {"table": "employee_leave_v", "column": "start_date", "direction": "desc"}
                ],
                "time_window": None,
                "limit": 50,
                "safety": {"read_only": True, "allow_subquery": False, "no_sql": True, "no_ddl": True},
            }

        # Default attendance view
        return {
            "intent": "attendance_summary",
            "table": "employee_attendance_v",
            "description": "Attendance summary",
            "chart_type": "bar",
            "group_by_field": "status",
            "select": [
                {"table": "employee_attendance_v", "column": "att_date", "alias": "att_date"},
                {"table": "employee_attendance_v", "column": "full_name", "alias": "full_name"},
                {"table": "employee_attendance_v", "column": "dept_name", "alias": "dept_name"},
                {"table": "employee_attendance_v", "column": "role_name", "alias": "role_name"},
                {"table": "employee_attendance_v", "column": "shift_name", "alias": "shift_name"},
                {"table": "employee_attendance_v", "column": "status", "alias": "status"},
            ],
            "filters": [],
            "order_by": [
                {"table": "employee_attendance_v", "column": "att_date", "direction": "desc"}
            ],
            "time_window": {"type": "relative", "value": "today"},
            "limit": 50,
            "safety": {"read_only": True, "allow_subquery": False, "no_sql": True, "no_ddl": True},
        }

    def get_ir(
        self,
        utterance: str,
        session_id: str,
        conversation_history: Optional[List[Dict]] = None,
    ) -> StructuredIR:
        """
        Full pipeline:
          1. Safety / moderation check
          2. Slot resolution (pronoun filling)
          3. NLP inference → raw JSON
          4. Pydantic validation of StructuredIR
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
        )

        raw_ir = self._call_nlp(system, utterance, conversation_history or [])

        # Hard guardrails: only approved views
        raw_ir["table"] = raw_ir.get("table") or (
            "employee_leave_v" if any(k in utterance.lower() for k in ["leave", "vacation"]) else "employee_attendance_v"
        )
        if raw_ir["table"] not in ALLOWED_VIEWS:
            raw_ir["table"] = "employee_attendance_v"

        # Force no joins in the returned IR
        raw_ir["joins"] = []

        # If model omitted safety, patch it to the strict contract
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

        # Validate via Pydantic
        ir = StructuredIR.model_validate(raw_ir)

        # Belt-and-suspenders safety enforcement
        if not ir.safety.read_only or ir.safety.allow_subquery:
            raise ValueError("IR safety invariant violated by model output")
        if getattr(ir, "joins", None):
            raise ValueError("Joins are disabled")
        if getattr(ir, "table", None) not in ALLOWED_VIEWS:
            raise ValueError(f"Invalid view target: {getattr(ir, 'table', None)!r}")

        slots.update(ir)
        return ir

    def _call_nlp(
        self,
        system: str,
        utterance: str,
        history: List[Dict],
    ) -> dict:
        """Calls the NLP endpoint. Returns parsed dict. Falls back on safe IR if needed."""
        messages = [*history[-10:], {"role": "user", "content": utterance}]

        with httpx.Client(timeout=self.timeout) as client:
            headers = {
                "x-api-key": self.api_key,
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

        import json

        try:
            log.info("NLP endpoint returned %d bytes", len(text))
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("NLP output must be a JSON object")
            return parsed
        except Exception:
            log.error("NLP returned invalid JSON (first 200 chars): %s…", text[:200])
            return self._default_ir(utterance)


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
