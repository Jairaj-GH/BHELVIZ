"""Semantic retriever using SentenceTransformers + ChromaDB.

Provides `retrieve_context(query: str, top_k=5) -> List[str]` which returns
the top-k chunk texts with source metadata.
"""
from __future__ import annotations

import os
import logging
from typing import List, Dict, Any

log = logging.getLogger("bhelviz.retriever")

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

try:
    import chromadb
    from chromadb.config import Settings
except Exception:
    chromadb = None

CHROMA_DIR = os.environ.get("CHROMA_PERSIST_DIR", os.path.join(os.getcwd(), "chroma_db"))
COLLECTION_NAME = os.environ.get("CHROMA_COLLECTION", "enterprise_docs")

_embed_model = None
_chroma_client = None
_collection = None


def _init():
    global _embed_model, _chroma_client, _collection
    if _embed_model is None:
        if SentenceTransformer is None:
            raise RuntimeError("sentence-transformers is required for retriever")
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
    if _chroma_client is None:
        if chromadb is None:
            raise RuntimeError("chromadb is required for retriever")
        _chroma_client = chromadb.PersistentClient(
            path=CHROMA_DIR
        )

        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME
        )


def retrieve_context(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Return list of dicts: {"id","text","metadata","score"}
    Empty list if ChromaDB not populated.
    """
    _init()
    # embed
    emb = _embed_model.encode([query])[0]
    # query chroma
    results = _collection.query(query_embeddings=[emb], n_results=top_k, include=['metadatas','documents','distances'])# ['ids']
    out: List[Dict[str, Any]] = []
    # results fields are lists per query
    docs = results.get('documents', [[]])[0]
    metadatas = results.get('metadatas', [[]])[0]
    distances = results.get('distances', [[]])[0]
    ids = results.get('ids', [[]])[0]

    for i, doc in enumerate(docs):
        meta = metadatas[i] if i < len(metadatas) else {}
        score = float(distances[i]) if i < len(distances) else 0.0
        ident = ids[i] if i < len(ids) else None
        out.append({"id": ident, "text": doc, "metadata": meta or {}, "score": score})

    return out
