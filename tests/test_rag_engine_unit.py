import importlib.util
import sys
import types
import os
from dataclasses import dataclass


def _load_rag_engine_with_fakes():
    # Prepare fake modules to satisfy imports without heavy deps
    fake_models = types.ModuleType("models")

    @dataclass
    class FakeDocumentChunk:
        id: str = None
        text: str = None
        filename: str = None
        page: int = None
        chunk_id: str = None
        score: float = 0.0

    @dataclass
    class FakeRAGResponse:
        answer: str
        structured_ir: object
        sources: list
        model_meta: dict
        message_id: str
        conversation_id: str

        def model_dump(self):
            return {"answer": self.answer, "sources": [s.filename for s in self.sources if hasattr(s, "filename")]}

    fake_models.DocumentChunk = FakeDocumentChunk
    fake_models.RAGResponse = FakeRAGResponse
    fake_models.StructuredIR = dict

    fake_retriever = types.ModuleType("retriever")
    fake_retriever.retrieve_context = lambda q, top_k=5: [{
        "id": "c1",
        "text": "This is a document chunk about company policy.",
        "metadata": {"filename": "policy.pdf", "page": 3, "chunk_id": "c1"},
        "score": 0.05,
    }]

    class DummyNLP:
        NLP_SYSTEM_PROMPT = "You are an assistant."

        def __init__(self, nlp_endpoint="", api_key=""):
            pass

        def _default_ir(self, user_query: str):
            return {"intent": "info", "table": "policy", "select": [], "filters": [], "safety": {"read_only": True}}

    fake_nlp_engine = types.ModuleType("nlp_engine")
    fake_nlp_engine.NLPIRPipeline = DummyNLP

    # Inject fakes into sys.modules
    sys.modules["models"] = fake_models
    sys.modules["retriever"] = fake_retriever
    sys.modules["nlp_engine"] = fake_nlp_engine

    # Load rag_engine from file
    repo = os.path.join(os.getcwd(), "BHELVIZ_FULL")
    path = os.path.join(repo, "backend", "rag_engine.py")
    spec = importlib.util.spec_from_file_location("rag_engine", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generate_rag_response_fallback_returns_rag_response():
    rag_engine = _load_rag_engine_with_fakes()
    # Ensure anthropic is not present so fallback path is used
    rag_engine.anthropic = None

    resp = rag_engine.generate_rag_response("Summarize policy", user_id=123)

    # Should be our fake RAGResponse dataclass
    assert hasattr(resp, "answer")
    assert resp.answer is not None and len(resp.answer) > 0
    assert resp.sources and len(resp.sources) > 0
