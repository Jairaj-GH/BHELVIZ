"""
Wrapper shim for `bhelviz_model_impl.py` that provides a lightweight
pure-Python fallback when PyTorch / transformers are not installed.

This file exposes: `BhelvizNLPModel`, `BhelvizTokenizer`, `build_model`.
If the full implementation fails to import, a minimal deterministic
fallback is provided so the rest of the pipeline can run in dev mode.
"""

from __future__ import annotations

from typing import Tuple, List, Dict, Optional
try:
    # Prefer the full implementation (may require torch + transformers)
    from bhelviz_model_impl import BhelvizNLPModel, BhelvizTokenizer, build_model  # type: ignore
    FULL_IMPL_AVAILABLE = True
    print("FULL_IMPL_AVAILABLE =", FULL_IMPL_AVAILABLE)
except Exception as e:  # pragma: no cover - fallback path
    FULL_IMPL_AVAILABLE = False
    print("MODEL IMPORT FAILED:", repr(e))
    
    class BhelvizNLPModel:
        """Minimal fallback model with the same `predict`/stat API used by pipeline."""

        def __init__(self, *args, **kwargs):
            pass

        def eval(self):
            return None

        def trainable_params(self) -> int:
            return 0

        def total_params(self) -> int:
            return 0

        def predict(self, input_ids, attention_mask) -> Dict[str, object]:
            # Deterministic simplistic prediction useful for dev and smoke tests
            # input_ids/attention_mask may be None in the fallback tokenizer
            return {
                "intent": "attendance_summary",
                "select_mode": "LIST",
                "slot_tags": ["O"],
                "intent_conf": 0.75,
                "mode_conf": 0.75,
            }

        def save(self, path: str) -> None:  # noop
            return None

        def load(self, path: str) -> None:  # noop
            return None

    class BhelvizTokenizer:
        """Simple whitespace tokenizer fallback used when transformers are absent."""

        MAX_LEN = 64

        def __init__(self, pretrained_name: str = "distilbert-base-uncased"):
            self.pretrained_name = pretrained_name

        def encode(self, text: str) -> Dict[str, Optional[object]]:
            # Return placeholders compatible with pipeline.predict() calls
            toks = self.tokens(text)
            return {"input_ids": None, "attention_mask": None, "offset_mapping": None}

        def encode_batch(self, texts: List[str]) -> Dict[str, Optional[object]]:
            return {"input_ids": None, "attention_mask": None}

        def tokens(self, text: str) -> List[str]:
            return [t for t in text.strip().split() if t]

    def build_model(
        pretrained_name: str = "distilbert-base-uncased",
        lora_rank: int = 16,
        lora_alpha: float = 32.0,
        dropout: float = 0.1,
        checkpoint: Optional[str] = None,
    ) -> Tuple[BhelvizNLPModel, BhelvizTokenizer]:
        """Return fallback model + tokenizer."""
        return BhelvizNLPModel(), BhelvizTokenizer(pretrained_name)
