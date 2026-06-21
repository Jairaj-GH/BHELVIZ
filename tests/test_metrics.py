from backend import metrics


def test_metrics_noop_inc_and_observe():
    # Should not raise even if prometheus libs are absent
    metrics.RAG_HITS.inc()
    metrics.RAG_MISSES.inc()
    metrics.RAG_LATENCY.observe(0.123)


def test_instrument_app_noop():
    class DummyApp:
        pass

    # Should be callable and not raise
    metrics.instrument_app(DummyApp())
