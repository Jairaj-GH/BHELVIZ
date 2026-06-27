"""RAG orchestrator: retrieve context, call an LLM, return structured response.

Function: generate_rag_response(user_query, user_id)
 - retrieves top-5 chunks
 - builds extended system prompt
 - calls Gemini (free tier) or Claude (anthropic) to get StructuredIR + conversational answer
 - returns a dict matching `models.RAGResponse`
"""
from __future__ import annotations

import os
import logging
import uuid
from typing import List, Dict, Any

from core.models import RAGResponse, DocumentChunk, StructuredIR
from RAG.retriever import retrieve_context
from NLP.nlp_engine import NLPIRPipeline

log = logging.getLogger("bhelviz.rag")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

try:
    import anthropic
except Exception:
    anthropic = None

try:
    from google import genai
except Exception:
    genai = None

RAG_SYSTEM_PROMPT = (
    "You are the BHELVIZ document assistant. Answer the user's question in clear, "
    "plain prose using only the context provided below. Do not output JSON, code, "
    "or structured query objects — write a normal conversational answer. If the "
    "context doesn't contain the answer, say so directly."
)

def _build_system_prompt(base_prompt: str, chunks: List[Dict[str, Any]]) -> str:
    ctx_parts = []
    for i, c in enumerate(chunks):
        meta = c.get("metadata", {})
        src = f"{meta.get('filename','unknown')}#p{meta.get('page','?')}"
        ctx_parts.append(f"[{i+1}] SOURCE={src}\n{c.get('text','')}")
    ctx = "\n\n".join(ctx_parts)
    return f"{base_prompt}\n\nContext:\n{ctx}"


def generate_rag_response(user_query: str, user_id: int) -> RAGResponse:
    # Step 1: retrieve
    chunks = retrieve_context(user_query, top_k=5)
    texts = [c["text"] for c in chunks]

    system = _build_system_prompt(RAG_SYSTEM_PROMPT, chunks)

    # Step 3: call an LLM if available — Gemini first (free tier), Anthropic as fallback
    answer_text = ""
    structured_ir = None
    model_meta: Dict[str, Any] = {}

    if genai and GEMINI_API_KEY:
        client = genai.Client(api_key=GEMINI_API_KEY)
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_query,
                config={"system_instruction": system},
            )
            answer_text = resp.text
            model_meta = {"model": "gemini-2.5-flash"}
        except Exception as exc:
            log.exception("Gemini call failed: %s", exc)
            answer_text = "(RAG attempt failed: fallback answer)"
            model_meta = {"model": "fallback"}
    elif anthropic and ANTHROPIC_API_KEY:
        client = anthropic.Client(api_key=ANTHROPIC_API_KEY)
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=800,
                system=system,
                messages=[{"role": "user", "content": user_query}],
            )
            answer_text = "".join(
                block.text for block in resp.content if block.type == "text"
            )
            model_meta = {"model": resp.model}
        except Exception as exc:
            log.exception("Anthropic call failed: %s", exc)
            answer_text = "(RAG attempt failed: fallback answer)"
            model_meta = {"model": "fallback"}
    else:
        # Fallback: simple assembly of context and user query
        answer_text = "".join([t[:1000] + "\n\n" for t in texts]) + "\nAnswer: Based on the documents above..."
        model_meta = {"model": "none"}

    # Step 4: attempt to extract StructuredIR from the model answer using nlp pipeline
    try:
        nlp = NLPIRPipeline(nlp_endpoint="", api_key="")
        ir_obj = nlp._default_ir(user_query)
        structured_ir = StructuredIR.model_validate(ir_obj)
    except Exception:
        structured_ir = None

    # Build RAGResponse
    sources = [DocumentChunk(
        id=str(c.get('id')),
        text=c.get('text'),
        filename=c.get('metadata', {}).get('filename'),
        page=c.get('metadata', {}).get('page'),
        chunk_id=str(c.get('metadata', {}).get('chunk_id')),
        score=c.get('score')
    ) for c in chunks]

    return RAGResponse(
        answer=answer_text,
        structured_ir=structured_ir,
        sources=sources,
        model_meta=model_meta,
        message_id=str(uuid.uuid4()),
        conversation_id=str(user_id),
    )