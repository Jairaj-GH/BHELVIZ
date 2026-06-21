import importlib
import sys
import types
import runpy


def test_retrieve_raises_when_no_backends():
    # Import the real module and then monkeypatch to simulate missing backends
    repo = __import__('os').path.join(__import__('os').getcwd(), 'BHELVIZ_FULL')
    backend_dir = __import__('os').path.join(repo, 'backend')
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    import retriever

    retriever.SentenceTransformer = None
    retriever.chromadb = None
    retriever._embed_model = None

    try:
        try:
            retriever.retrieve_context('query', top_k=3)
            raised = False
        except RuntimeError:
            raised = True
        assert raised, "Expected RuntimeError when heavy libs are missing"
    finally:
        # cleanup: remove module so other tests can import fresh
        if 'retriever' in sys.modules:
            del sys.modules['retriever']


def test_retrieve_returns_expected_structure_with_fakes():
    # Import and monkeypatch the retriever module to use fake backends
    repo = __import__('os').path.join(__import__('os').getcwd(), 'BHELVIZ_FULL')
    backend_dir = __import__('os').path.join(repo, 'backend')
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    import retriever

    # Provide fake embed model and fake collection
    class FakeEmbed:
        def encode(self, arr):
            return [[0.1, 0.2]]

    class FakeCollection:
        def query(self, query_embeddings=None, n_results=5, include=None):
            return {
                'documents': [["doc text"]],
                'metadatas': [[{"filename": "f.pdf", "page": 1}]],
                'distances': [[0.123]],
                'ids': [["id1"]],
            }

    retriever.SentenceTransformer = lambda *a, **k: FakeEmbed()
    retriever.chromadb = types.SimpleNamespace(Client=lambda settings: types.SimpleNamespace(get_or_create_collection=lambda name: FakeCollection()))
    retriever.Settings = lambda **k: None

    out = retriever.retrieve_context('find this', top_k=1)
    assert isinstance(out, list) and len(out) == 1
    item = out[0]
    assert item['text'] == 'doc text'
    assert item['metadata']['filename'] == 'f.pdf'
