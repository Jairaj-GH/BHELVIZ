"""RAG orchestrator: retrieve context, call Claude, return structured response.

Function: generate_rag_response(user_query, user_id)
 - retrieves top-5 chunks
 - builds extended system prompt
 - calls Claude (anthropic) to get StructuredIR + conversational answer
 - returns a dict matching `models.RAGResponse`
"""
from __future__ import annotations

import os
import logging
import uuid
from typing import List, Dict, Any

from models import RAGResponse, DocumentChunk, StructuredIR
from retriever import retrieve_context
from nlp_engine import NLPIRPipeline

log = logging.getLogger("bhelviz.rag")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

try:
    import anthropic
except Exception:
    anthropic = None


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

    # Step 2: build system prompt
    base = NLPIRPipeline.NLP_SYSTEM_PROMPT if hasattr(NLPIRPipeline, 'NLP_SYSTEM_PROMPT') else "You are an assistant."
    system = _build_system_prompt(base, chunks)

    # Step 3: call Claude (anthropic) if available
    answer_text = ""
    structured_ir = None
    model_meta: Dict[str, Any] = {}

    if anthropic and ANTHROPIC_API_KEY:
        client = anthropic.Client(api_key=ANTHROPIC_API_KEY)
        try:
            resp = client.completions.create(
                model="claude-2.1",  # conservative choice
                prompt=system + f"\nUser: {user_query}\nAssistant:",
                max_tokens_to_sample=800,
            )
            answer_text = resp.completion
            model_meta = {"model": resp.model}
        except Exception as exc:
            log.exception("Anthropic call failed: %s", exc)
            answer_text = "(RAG attempt failed: fallback answer)"
    else:
        # Fallback: simple assembly of context and user query
        answer_text = "".join([t[:1000] + "\n\n" for t in texts]) + "\nAnswer: Based on the documents above..."

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
