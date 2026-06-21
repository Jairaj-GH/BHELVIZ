import asyncio
from types import SimpleNamespace

import importlib, os, sys

# ensure package import works when running from repo root
repo = os.path.join(os.getcwd(), "BHELVIZ_FULL")
backend_dir = os.path.join(repo, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import main as main
from auth import TokenPayload


def _make_token_payload():
    return TokenPayload(sub="42", email="tester@bhel.in", role="user", session_id="sess1", exp=9999999999)


def test_rag_hit_returns_rag_response():
    # Mock retriever to return a single chunk
    main.retrieve_context = lambda q, top_k=5: [{"id": "c1", "text": "hello from doc", "metadata": {"filename": "doc.pdf", "page": 1}, "score": 0.1}]

    # Mock generate_rag_response to return object with model_dump
    class DummyRag:
        def __init__(self):
            self.sources = [1]

        def model_dump(self):
            return {"answer": "doc answer", "sources": ["doc.pdf"]}

    main.generate_rag_response = lambda q, uid: DummyRag()

    req = main.QueryRequest(utterance="Who is absent?", session_id="s1", history=[])
    user = _make_token_payload()

    result = asyncio.run(main.query(req, current_user=user, request=None))
    assert isinstance(result, dict)
    assert result.get("answer") == "doc answer"


def test_rag_miss_falls_back_to_executor():
    # No chunks
    main.retrieve_context = lambda q, top_k=5: []

    # Provide a simple semantic_pipeline returning a dict IR
    class DummyPipeline:
        def get_ir(self, utterance, session_id, conversation_history):
            return {"intent": "attendance_summary", "table": "employee_attendance_v", "select": [], "filters": [], "safety": {"read_only": True}}

    main.semantic_pipeline = DummyPipeline()

    # Mock validate_ir and compile_and_execute
    main.validate_ir = lambda ir: True

    class DummyResult:
        def model_dump(self):
            return {"row_count": 0, "rows": []}

    def compile_and_execute(ir, conn, metadata):
        return DummyResult()

    main.compile_and_execute = compile_and_execute

    # Mock dev session with connection() and close()
    class DummyDB:
        def connection(self):
            return None

        def close(self):
            return None

    main.get_dev_session = lambda: DummyDB()

    req = main.QueryRequest(utterance="Who is absent?", session_id="s2", history=[])
    user = _make_token_payload()

    result = asyncio.run(main.query(req, current_user=user, request=None))
    assert isinstance(result, dict)
    assert result.get("row_count") == 0 or "rows" in result
