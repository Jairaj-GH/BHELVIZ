"""Simple rule-based router to classify user questions into structured/document/hybrid.

This module is intentionally lightweight and deterministic. It can be replaced
later with an ML or LLM based classifier. The function `route_question` is
safe to call and returns a serialisable dict used by the `/query` endpoint.
"""
from __future__ import annotations
from typing import Dict

DOCUMENT_KEYWORDS = {
    "policy",
    "procedure",
    "manual",
    "policy",
    "leave policy",
    "entitled",
    "hr",
    "benefits",
}

STRUCTURED_KEYWORDS = {
    "attendance",
    "absent",
    "absences",
    "employee",
    "department",
    "count",
    "how many",
    "today",
    "present",
    "late",
    "salary",
}


def _contains_any(text: str, keywords: set[str]) -> bool:
    t = text.lower()
    for k in keywords:
        if k in t:
            return True
    return False


def route_question(question: str) -> Dict:
    """Return a routing decision for the given question.

    Returns:
      {"intent": "structured"|"document"|"hybrid", "confidence": float, "sql_query": None, "rag_query": None}
    """
    doc = _contains_any(question, DOCUMENT_KEYWORDS)
    structured = _contains_any(question, STRUCTURED_KEYWORDS)

    # heuristic: if both detected, it's hybrid
    if doc and structured:
        intent = "hybrid"
        confidence = 0.85
    elif structured:
        intent = "structured"
        confidence = 0.9
    elif doc:
        intent = "document"
        confidence = 0.9
    else:
        # fallback to structured by default for dashboard queries
        intent = "structured"
        confidence = 0.5

    return {
        "intent": intent,
        "confidence": confidence,
        "sql_query": None,
        "rag_query": question,
    }
