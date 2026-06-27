"""Document ingestion: PDF/DOCX/TXT -> chunks -> embeddings -> ChromaDB

Endpoints:
 - POST /admin/documents (multipart) : upload files (admin only)

This module loads the sentence-transformers embedding model once and uses
ChromaDB for persistent vector storage in `enterprise_docs` collection.
"""
from __future__ import annotations

import os
import logging
import uuid
import tempfile
from typing import List, Optional, Tuple

try:
    from fastapi import APIRouter, Depends, UploadFile, HTTPException
    from core.auth import require_admin
    router = APIRouter()
except Exception:  # allow importing this module in environments without FastAPI
    APIRouter = None
    Depends = lambda x=None: None
    UploadFile = object
    require_admin = lambda: None
    class HTTPException(Exception):
        pass
    # simple dummy router so decorators are no-ops when FastAPI isn't installed
    class _DummyRouter:
        def post(self, *a, **k):
            def dec(fn):
                return fn
            return dec

    router = _DummyRouter()

log = logging.getLogger("bhelviz.ingestion")

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - runtime dependency
    SentenceTransformer = None

try:
    import chromadb
    from chromadb.config import Settings
except Exception:
    chromadb = None

try:
    import PyPDF2
except Exception:
    PyPDF2 = None

try:
    import docx
except Exception:
    docx = None

# Globals initialised lazily
_embed_model: Optional[object] = None
_chroma_client = None
_collection = None

CHROMA_DIR = os.environ.get("CHROMA_PERSIST_DIR", os.path.join(os.getcwd(), "chroma_db"))
COLLECTION_NAME = os.environ.get("CHROMA_COLLECTION", "enterprise_docs")


def _init_resources():
    global _embed_model, _chroma_client, _collection

    if _embed_model is None:
        if SentenceTransformer is None:
            raise RuntimeError("sentence-transformers is required for embeddings")
        log.info("Loading embedding model: all-MiniLM-L6-v2")
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")

    if _chroma_client is None:
        if chromadb is None:
            raise RuntimeError("chromadb is required for vector storage")
        _chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME
        )

def _read_pdf_text(path: str) -> List[Tuple[int, str]]:
    if PyPDF2 is None:
        raise RuntimeError("PyPDF2 not installed")
    texts = []
    with open(path, "rb") as fh:
        reader = PyPDF2.PdfReader(fh)
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            texts.append((i, text))
    return texts


def _read_docx_text(path: str) -> List[Tuple[int, str]]:
    if docx is None:
        raise RuntimeError("python-docx not installed")
    doc = docx.Document(path)
    # python-docx does not provide page numbers; treat whole doc as page 1
    text = "\n".join(p.text for p in doc.paragraphs)
    return [(1, text)]


def _read_txt_text(path: str) -> List[Tuple[int, str]]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return [(1, f.read())]


def _chunk_text(text: str, target_tokens: int = 500, overlap_tokens: int = 50) -> List[str]:
    # Heuristic: approx 4 chars per token
    chars_per_token = 4
    chunk_size = target_tokens * chars_per_token
    overlap = overlap_tokens * chars_per_token

    if not text:
        return []

    # Split into sentences roughly for better boundaries
    import re

    sentences = re.split(r"(?<=[\.\?!])\s+", text)
    chunks: List[str] = []
    current = ""

    for s in sentences:
        if len(current) + len(s) + 1 <= chunk_size:
            current = (current + " " + s).strip()
        else:
            if current:
                chunks.append(current.strip())
                # start new current with overlap from tail of current
                if overlap > 0:
                    tail = current[-overlap:]
                    current = (tail + " " + s).strip()
                else:
                    current = s.strip()
            else:
                # sentence longer than chunk, hard-split
                for i in range(0, len(s), chunk_size - 50):
                    chunks.append(s[i : i + chunk_size].strip())
                current = ""

    if current:
        chunks.append(current.strip())

    return chunks


def _embed_texts(texts: List[str]) -> List[List[float]]:
    _init_resources()
    # model.encode returns numpy array
    embs = _embed_model.encode(texts, show_progress_bar=False)
    # Convert to native Python lists
    return [e.tolist() if hasattr(e, "tolist") else list(e) for e in embs]


@router.post("/admin/documents")
async def upload_documents(files: List[UploadFile], admin=Depends(require_admin)):
    """Upload one or more documents; only admin may call this endpoint."""
    _init_resources()

    results = []
    total_chunks = 0

    for up in files:
        filename = up.filename or f"upload-{uuid.uuid4().hex[:8]}"
        suffix = os.path.splitext(filename)[1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            data = await up.read()
            tmp.write(data)
            tmp_path = tmp.name

        try:
            if suffix in (".pdf",):
                pages = _read_pdf_text(tmp_path)
            elif suffix in (".docx", ".doc"):
                pages = _read_docx_text(tmp_path)
            else:
                pages = _read_txt_text(tmp_path)

            file_chunks = []
            for page_no, text in pages:
                chunks = _chunk_text(text)
                for idx, chunk in enumerate(chunks):
                    chunk_id = f"{uuid.uuid4().hex}"
                    doc_id = f"{filename}::{page_no}::{idx}::{chunk_id}"
                    file_chunks.append((doc_id, chunk, {"filename": filename, "page": page_no, "chunk_id": idx}))

            if not file_chunks:
                results.append({"filename": filename, "inserted": 0})
                continue

            ids = [c[0] for c in file_chunks]
            texts = [c[1] for c in file_chunks]
            metadatas = [c[2] for c in file_chunks]

            embeddings = _embed_texts(texts)

            # Upsert into ChromaDB
            col = _chroma_client.get_collection(COLLECTION_NAME)
            col.add(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)

            total_chunks += len(ids)
            results.append({"filename": filename, "inserted": len(ids)})

        except Exception as exc:
            log.exception("Failed to ingest %s: %s", filename, exc)
            raise HTTPException(status_code=500, detail=f"Ingestion failed for {filename}: {exc}")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # Persist ChromaDB to disk (if client supports persist)
    try:
        if hasattr(_chroma_client, "persist"):
            _chroma_client.persist()
    except Exception:
        log.warning("ChromaDB persist failed", exc_info=True)

    return {"status": "ok", "total_chunks": total_chunks, "files": results}
