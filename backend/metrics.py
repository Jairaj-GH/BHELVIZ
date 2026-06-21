try:
    from prometheus_client import Counter, Histogram
    from prometheus_fastapi_instrumentator import Instrumentator

    # Counters and histograms for RAG and NLP
    RAG_HITS = Counter("bhelviz_rag_hits_total", "Total RAG hits")
    RAG_MISSES = Counter("bhelviz_rag_misses_total", "Total RAG misses")
    RAG_LATENCY = Histogram("bhelviz_rag_latency_seconds", "RAG latency in seconds")


    def instrument_app(app):
        """Instrument the FastAPI app and expose /metrics."""
        Instrumentator().instrument(app).expose(app)

except Exception:
    # Fallback no-op implementations for environments without Prometheus libs
    class _NoopMetric:
        def inc(self, *a, **k):
            return None

        def observe(self, *a, **k):
            return None

    RAG_HITS = _NoopMetric()
    RAG_MISSES = _NoopMetric()
    RAG_LATENCY = _NoopMetric()


    def instrument_app(app):
        return None
