"""
BHELVIZ — Heavy PyTorch Model Implementation
(bhelviz_model_impl.py)

This file contains:
  • Label maps for intent, select mode, slots, aggregation, groupby, ranking, trend
  • LoRA adapter (LoRALinear)
  • Task-specific heads (intent, mode, slot, aggregation, groupby, ranking, trend)
  • Multi-task model (BhelvizNLPModel) with forward, predict, save/load
  • Tokenizer wrapper (BhelvizTokenizer)
  • build_model() factory
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import DistilBertModel, DistilBertTokenizerFast

# ══════════════════════════════════════════════════════════════════════════════
# LABEL MAPS
# ══════════════════════════════════════════════════════════════════════════════

INTENT_LABELS: List[str] = [
    "attendance_summary",
    "leave_summary",
    "employee_lookup",
    "role_comparison",
    "anomaly_detection",
    "trend_analysis",
    "department_summary",
    "shift_summary",
    "count_query",
    "names_query",
]

SELECT_MODE_LABELS: List[str] = [
    "COUNT",
    "LIST",
    "AGGREGATE",
    "DETAIL",
    "TREND",
    "NAMES_ONLY",
]

SLOT_LABELS: List[str] = [
    "O",
    "B-DEPT",
    "I-DEPT",
    "B-STATUS",
    "I-STATUS",
    "B-TIME",
    "I-TIME",
    "B-ROLE",
    "I-ROLE",
    "B-PERSON",
    "I-PERSON",
    "B-RANKING",
    "I-RANKING",
    "B-SHIFT",
    "I-SHIFT",
]

# ── NEW ANALYTICAL LABELS ────────────────────────────────────────────────────
AGGREGATION_LABELS: List[str] = [
    "NONE",
    "COUNT",
    "SUM",
    "AVG",
]

GROUPBY_LABELS: List[str] = [
    "NONE",
    "dept_name",
    "role",
    "shift",
    "status",
    "employee",
]

RANKING_LABELS: List[str] = [
    "NONE",
    "TOP",
    "BOTTOM",
]

TREND_LABELS: List[str] = [
    "NONE",
    "TIME_SERIES",
]

# ── ID <-> LABEL MAPPINGS ────────────────────────────────────────────────────
INTENT2ID  = {l: i for i, l in enumerate(INTENT_LABELS)}
ID2INTENT  = {i: l for l, i in INTENT2ID.items()}

MODE2ID    = {l: i for i, l in enumerate(SELECT_MODE_LABELS)}
ID2MODE    = {i: l for l, i in MODE2ID.items()}

SLOT2ID    = {l: i for i, l in enumerate(SLOT_LABELS)}
ID2SLOT    = {i: l for l, i in SLOT2ID.items()}

AGGREGATION2ID = {l: i for i, l in enumerate(AGGREGATION_LABELS)}
ID2AGGREGATION = {i: l for l, i in AGGREGATION2ID.items()}

GROUPBY2ID     = {l: i for i, l in enumerate(GROUPBY_LABELS)}
ID2GROUPBY     = {i: l for l, i in GROUPBY2ID.items()}

RANKING2ID     = {l: i for i, l in enumerate(RANKING_LABELS)}
ID2RANKING     = {i: l for l, i in RANKING2ID.items()}

TREND2ID       = {l: i for i, l in enumerate(TREND_LABELS)}
ID2TREND       = {i: l for l, i in TREND2ID.items()}

NUM_INTENTS    = len(INTENT_LABELS)       # 10
NUM_MODES      = len(SELECT_MODE_LABELS)  # 6
NUM_SLOT_TYPES = len(SLOT_LABELS)         # 9
HIDDEN_SIZE    = 768                      # DistilBERT hidden dim


# ══════════════════════════════════════════════════════════════════════════════
# LORA ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class LoRALinear(nn.Module):
    """
    Low-Rank Adaptation (LoRA) wrapper around a *frozen* nn.Linear.
    """

    def __init__(self, frozen_linear: nn.Linear, rank: int = 16, alpha: float = 32.0):
        super().__init__()
        d_out, d_in = frozen_linear.weight.shape
        self.frozen  = frozen_linear
        self.frozen.weight.requires_grad_(False)
        if self.frozen.bias is not None:
            self.frozen.bias.requires_grad_(False)

        self.scale  = alpha / rank
        self.lora_A = nn.Parameter(torch.empty(rank, d_in))
        self.lora_B = nn.Parameter(torch.zeros(d_out, rank))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.frozen(x) + F.linear(F.linear(x, self.lora_A), self.lora_B) * self.scale


# ══════════════════════════════════════════════════════════════════════════════
# TASK HEADS
# ══════════════════════════════════════════════════════════════════════════════

class IntentHead(nn.Module):
    def __init__(self, hidden: int = HIDDEN_SIZE, num_classes: int = NUM_INTENTS, dropout: float = 0.1):
        super().__init__()
        self.drop = nn.Dropout(dropout)
        self.fc1  = nn.Linear(hidden, 256)
        self.fc2  = nn.Linear(256, num_classes)

    def forward(self, cls: torch.Tensor) -> torch.Tensor:
        return self.fc2(F.relu(self.fc1(self.drop(cls))))


class SelectModeHead(nn.Module):
    def __init__(self, hidden: int = HIDDEN_SIZE, num_classes: int = NUM_MODES, dropout: float = 0.1):
        super().__init__()
        self.drop = nn.Dropout(dropout)
        self.fc1  = nn.Linear(hidden, 128)
        self.fc2  = nn.Linear(128, num_classes)

    def forward(self, cls: torch.Tensor) -> torch.Tensor:
        return self.fc2(F.relu(self.fc1(self.drop(cls))))


class SlotTaggerHead(nn.Module):
    def __init__(self, hidden: int = HIDDEN_SIZE, num_tags: int = NUM_SLOT_TYPES, dropout: float = 0.1):
        super().__init__()
        self.drop = nn.Dropout(dropout)
        self.fc1  = nn.Linear(hidden, 256)
        self.fc2  = nn.Linear(256, num_tags)

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        return self.fc2(F.relu(self.fc1(self.drop(hidden))))


# Simple linear heads for the new analytical tasks
class AggregationHead(nn.Linear):
    def __init__(self, hidden: int = HIDDEN_SIZE, num_classes: int = len(AGGREGATION_LABELS)):
        super().__init__(hidden, num_classes)

class GroupByHead(nn.Linear):
    def __init__(self, hidden: int = HIDDEN_SIZE, num_classes: int = len(GROUPBY_LABELS)):
        super().__init__(hidden, num_classes)

class RankingHead(nn.Linear):
    def __init__(self, hidden: int = HIDDEN_SIZE, num_classes: int = len(RANKING_LABELS)):
        super().__init__(hidden, num_classes)

class TrendHead(nn.Linear):
    def __init__(self, hidden: int = HIDDEN_SIZE, num_classes: int = len(TREND_LABELS)):
        super().__init__(hidden, num_classes)


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-TASK MODEL
# ══════════════════════════════════════════════════════════════════════════════

class BhelvizNLPModel(nn.Module):
    """
    Multi‑task NLP model for attendance‑domain understanding.
    Produces:
      - intent
      - select mode
      - slot tags (BIO)
      - aggregation type
      - group‑by field
      - ranking order
      - trend flag
    """

    # Loss weights for original tasks (new heads will be added later)
    LOSS_W = {"intent": 0.40, "mode": 0.35, "slot": 0.25}

    def __init__(
        self,
        pretrained_name: str = "distilbert-base-uncased",
        lora_rank: int       = 16,
        lora_alpha: float    = 32.0,
        dropout: float       = 0.1,
    ):
        super().__init__()

        # Frozen DistilBERT backbone
        self.encoder = DistilBertModel.from_pretrained(pretrained_name)
        for p in self.encoder.parameters():
            p.requires_grad_(False)

        # Inject LoRA into every attention Q and V linear in all 6 layers
        for layer in self.encoder.transformer.layer:
            a = layer.attention
            a.q_lin = LoRALinear(a.q_lin, rank=lora_rank, alpha=lora_alpha)
            a.v_lin = LoRALinear(a.v_lin, rank=lora_rank, alpha=lora_alpha)

        # Original task heads
        self.intent_head = IntentHead(dropout=dropout)
        self.mode_head   = SelectModeHead(dropout=dropout)
        self.slot_head   = SlotTaggerHead(dropout=dropout)

        # NEW analytical heads (all trainable from scratch)
        self.aggregation_head = AggregationHead()
        self.groupby_head     = GroupByHead()
        self.ranking_head     = RankingHead()
        self.trend_head       = TrendHead()

        self._ce = nn.CrossEntropyLoss(ignore_index=-100)

    def trainable_params(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def total_params(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def forward(
        self,
        input_ids,
        attention_mask,

        intent_labels=None,
        mode_labels=None,
        slot_labels=None,
        aggregation_labels=None,
        groupby_labels=None,
        ranking_labels=None,
        trend_labels=None,
    
) -> Dict[str, torch.Tensor]:
        enc   = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        h     = enc.last_hidden_state          # (B, L, H)
        cls_h = h[:, 0, :]                    # (B, H) – [CLS] token

        intent_logits = self.intent_head(cls_h)
        mode_logits   = self.mode_head(cls_h)
        slot_logits   = self.slot_head(h)     # (B, L, num_slot_types)

        # Forward through the new heads
        aggregation_logits = self.aggregation_head(cls_h)
        groupby_logits     = self.groupby_head(cls_h)
        ranking_logits     = self.ranking_head(cls_h)
        trend_logits       = self.trend_head(cls_h)

        out: Dict[str, torch.Tensor] = {
            "intent_logits":       intent_logits,
            "mode_logits":         mode_logits,
            "slot_logits":         slot_logits,
            "aggregation_logits":  aggregation_logits,
            "groupby_logits":      groupby_logits,
            "ranking_logits":      ranking_logits,
            "trend_logits":        trend_logits,
        }

        # Loss only for the three existing tasks (new heads will be trained later)
        if (
            intent_labels is not None
            and mode_labels is not None
            and slot_labels is not None
            and aggregation_labels is not None
            and groupby_labels is not None
            and ranking_labels is not None
            and trend_labels is not None
        ):
            l_intent = self._ce(intent_logits, intent_labels)
            l_mode   = self._ce(mode_logits,   mode_labels)
            B, L, C  = slot_logits.shape
            l_slot   = self._ce(slot_logits.view(B * L, C), slot_labels.view(B * L))
            l_agg = self._ce(
                aggregation_logits,
                aggregation_labels
            )

            l_group = self._ce(
                groupby_logits,
                groupby_labels
            )

            l_rank = self._ce(
                ranking_logits,
                ranking_labels
            )

            l_trend = self._ce(
                trend_logits,
                trend_labels
            )
            out["loss"] = (
                0.25 * l_intent
                + 0.20 * l_mode
                + 0.15 * l_slot

                + 0.15 * l_agg
                + 0.10 * l_group
                + 0.10 * l_rank
                + 0.05 * l_trend
            )
            out["loss_intent"] = l_intent
            out["loss_mode"]   = l_mode
            out["loss_slot"]   = l_slot
            out["loss_aggregation"] = l_agg
            out["loss_groupby"] = l_group
            out["loss_ranking"] = l_rank
            out["loss_trend"] = l_trend

        return out

    @torch.no_grad()
    def predict(
        self,
        input_ids:      torch.Tensor,
        attention_mask: torch.Tensor,
    ) -> Dict[str, object]:
        self.eval()
        out = self.forward(input_ids, attention_mask)

        ip = F.softmax(out["intent_logits"], dim=-1)
        mp = F.softmax(out["mode_logits"],   dim=-1)
        ap = F.softmax(out["aggregation_logits"], dim=-1)
        gp = F.softmax(out["groupby_logits"],     dim=-1)
        rp = F.softmax(out["ranking_logits"],     dim=-1)
        tp = F.softmax(out["trend_logits"],       dim=-1)

        ii = int(ip.argmax(-1)[0])
        mi = int(mp.argmax(-1)[0])
        st = out["slot_logits"][0].argmax(-1).tolist()
        ai = int(ap.argmax(-1)[0])
        gi = int(gp.argmax(-1)[0])
        ri = int(rp.argmax(-1)[0])
        ti = int(tp.argmax(-1)[0])

        return {
            "intent":      ID2INTENT[ii],
            "select_mode": ID2MODE[mi],

            "aggregation": ID2AGGREGATION[ai],
            "groupby":     ID2GROUPBY[gi],
            "ranking":     ID2RANKING[ri],
            "trend":       ID2TREND[ti],

            "slot_tags": [ID2SLOT[s] for s in st],

            "intent_conf": float(ip[0, ii]),
            "mode_conf":   float(mp[0, mi]),

            "aggregation_conf": float(ap[0, ai]),
            "groupby_conf":     float(gp[0, gi]),
            "ranking_conf":     float(rp[0, ri]),
            "trend_conf":       float(tp[0, ti]),
        }

    def save(self, path: str) -> None:
        trainable_state = {
            k: v for k, v in self.state_dict().items()
            if any(t in k for t in ("lora_A", "lora_B",
                                    "intent_head", "mode_head", "slot_head",
                                    "aggregation_head", "groupby_head",
                                    "ranking_head", "trend_head"))
        }
        torch.save({"state": trainable_state}, path)
        size_mb = Path(path).stat().st_size / 1e6
        print(f"[save] {path}  ({size_mb:.1f} MB,  {len(trainable_state)} tensors)")

    def load(self, path: str) -> None:
        ckpt = torch.load(path, map_location="cpu")
        miss, unexp = self.load_state_dict(ckpt["state"], strict=False)
        print("\n[Checkpoint Load Diagnostics]")
        print("Missing keys:")
        for k in miss[:20]:
            print("  ", k)

        print("\nUnexpected keys:")
        for k in unexp[:20]:
            print("  ", k)

        print(
            f"\nSummary: missing={len(miss)} unexpected={len(unexp)}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TOKENIZER
# ══════════════════════════════════════════════════════════════════════════════

class BhelvizTokenizer:
    MAX_LEN = 64

    def __init__(self, pretrained_name: str = "distilbert-base-uncased"):
        self.tok = DistilBertTokenizerFast.from_pretrained(pretrained_name)

    def encode(self, text: str) -> Dict[str, torch.Tensor]:
        enc = self.tok(
            text,
            max_length=self.MAX_LEN,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
            return_offsets_mapping=True,
        )
        return {
            "input_ids":      enc["input_ids"],
            "attention_mask": enc["attention_mask"],
            "offset_mapping": enc["offset_mapping"],
        }

    def encode_batch(self, texts: List[str]) -> Dict[str, torch.Tensor]:
        enc = self.tok(
            texts,
            max_length=self.MAX_LEN,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )
        return {"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]}

    def tokens(self, text: str) -> List[str]:
        return self.tok.convert_ids_to_tokens(self.tok.encode(text))


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def build_model(
    pretrained_name: str      = "distilbert-base-uncased",
    lora_rank: int            = 16,
    lora_alpha: float         = 32.0,
    dropout: float            = 0.1,
    checkpoint: Optional[str] = None,
) -> Tuple[BhelvizNLPModel, BhelvizTokenizer]:
    model     = BhelvizNLPModel(pretrained_name, lora_rank, lora_alpha, dropout)
    tokenizer = BhelvizTokenizer(pretrained_name)
    if checkpoint and Path(checkpoint).exists():
        model.load(checkpoint)
    total, train = model.total_params(), model.trainable_params()
    print(
        f"[BhelvizNLPModel] total={total:,}  trainable={train:,}  "
        f"({100*train/total:.2f}% receive gradients)"
    )
    return model, tokenizer