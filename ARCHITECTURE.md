# BHELVIZ — Architecture Overview

This repository implements a sandboxed conversational SQL assistant with RAG.

Components:
- `backend/` — FastAPI application (dev entrypoint `main.py`) with modules:
  - `nlp_engine.py` — NLP / IR pipeline
  - `retriever.py` / `ingestion.py` / `rag_engine.py` — RAG stack
  - `metrics.py` — Prometheus instrumentation
- `frontend/` — Vite + React UI (chatbot planned)
- `monitoring/` — Prometheus configuration

Run (development):
1. Copy `.env.example` to `.env` and fill secrets.
2. From repo root: `docker-compose up --build api nlp prometheus`

Notes:
- Dev mode uses SQLite for admin/audit and a readonly dev executor.
- RAG uses `sentence-transformers` and `chromadb` (not installed by default).
