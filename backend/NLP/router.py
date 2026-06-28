"""Intelligent router: classify user questions into structured/document/hybrid.

Uses scored keyword matching across broad intent categories.
Unknown queries now default to 'document' (RAG) rather than structured,
since ambiguous questions are more likely to be policy/context questions.
"""
from __future__ import annotations
from typing import Dict

# ── DOCUMENT / POLICY intent keywords ────────────────────────────────────────
# Covers: policy questions, rules, entitlements, procedures, HR guidelines,
# benefits, compliance, leave rules, conduct, eligibility, definitions
DOCUMENT_KEYWORDS = {
    # Direct policy words
    "policy", "policies", "rule", "rules", "regulation", "regulations",
    "guideline", "guidelines", "procedure", "procedures", "manual",
    # HR-specific
    "leave", "entitled", "entitlement", "allowance", "benefits", "benefit",
    "eligibility", "eligible", "maternity", "paternity", "casual leave",
    "sick leave", "earned leave", "privilege leave",
    # Conduct & compliance
    "code of conduct", "compliance", "disciplinary", "grievance", "misconduct",
    "termination", "notice period", "probation", "appraisal process",
    "working hours", "shift timing", "overtime", "compensatory",
    # Travel & reimbursement
    "travel reimbursement", "travel allowance", "hra", "da", "lta",
    "medical reimbursement", "conveyance",
    # General "what does it say" triggers
    "what is", "what are", "how many days", "how much", "am i entitled",
    "can i", "is it allowed", "what happens if", "define", "explain",
    "tell me about",
}

# ── STRUCTURED / DATA intent keywords ────────────────────────────────────────
# Covers: queries about employee records, counts, attendance data, reports
STRUCTURED_KEYWORDS = {
    "attendance", "absent", "absences", "present", "late",
    "employee", "employees", "department", "departments",
    "count", "how many employees", "how many people",
    "list", "show", "display", "get", "fetch",
    "today", "yesterday", "this week", "this month",
    "salary", "payroll",
    "report", "summary", "compare", "breakdown",
    "shift", "night shift", "morning shift",
    "false attendance", "anomaly", "penalty",
    "hired", "joined", "designation", "role",
    "supervisors", "executives", "workmen",
    "power systems", "transmission", "r&d", "boiler",
}


def _score(text: str, keywords: set) -> int:
    """Count how many keywords from the set appear in the text."""
    t = text.lower()
    return sum(1 for k in keywords if k in t)


def route_question(question: str) -> Dict:
    """Return a routing decision for the given question.

    Returns:
      {"intent": "structured"|"document"|"hybrid", "confidence": float,
       "sql_query": None, "rag_query": None}
    """
    doc_score        = _score(question, DOCUMENT_KEYWORDS)
    structured_score = _score(question, STRUCTURED_KEYWORDS)

    if doc_score > 0 and structured_score > 0:
        # Both types of keywords — hybrid
        intent     = "hybrid"
        confidence = round(0.7 + min(0.2, (doc_score + structured_score) * 0.02), 2)

    elif structured_score > doc_score:
        # Clear data/SQL query
        intent     = "structured"
        confidence = round(min(0.95, 0.7 + structured_score * 0.05), 2)

    elif doc_score > 0:
        # Policy/document question
        intent     = "document"
        confidence = round(min(0.95, 0.7 + doc_score * 0.05), 2)

    else:
        # Unknown — default to RAG (more useful than empty SQL results)
        intent     = "document"
        confidence = 0.5

    return {
        "intent":     intent,
        "confidence": confidence,
        "sql_query":  None,
        "rag_query":  question,
    }