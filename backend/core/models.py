"""Embedding model service (SigLIP2)."""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoProcessor

from .config import get_settings

# Keep legacy names as aliases pointing to the active SigLIP2 backend so old
# clients sending model_name=jinav2 continue to work.
MODEL_CONFIG: Dict[str, str] = {
    "siglip2": "google/siglip2-so400m-patch14-384",
    "jinav2": "google/siglip2-so400m-patch14-384",
    "jinav1": "google/siglip2-so400m-patch14-384",
    "blip2": "google/siglip2-so400m-patch14-384",
    "clip": "google/siglip2-so400m-patch14-384",
}

DEFAULT_MODEL = "siglip2"


class ModelService:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = None):
        settings = get_settings()
        alias = (model_name or DEFAULT_MODEL).lower()
        if alias not in MODEL_CONFIG:
            raise ValueError(f"Unsupported model: {model_name}")
        self.model_name = alias
        self.model_id = MODEL_CONFIG[alias]
        if self.model_id != settings.model_id and alias == "siglip2":
            self.model_id = settings.model_id
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.processor = None
        self.cache_dir = str(settings.model_cache_dir)

    def load(self):
        if self.model is not None:
            return

        os.makedirs(self.cache_dir, exist_ok=True)
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        print(f"Loading {self.model_id} on {self.device}...")

        self.processor = AutoProcessor.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            trust_remote_code=True,
        )
        self.model = AutoModel.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            trust_remote_code=True,
            torch_dtype=dtype,
        ).to(self.device)
        self.model.eval()
        # Optional torch.compile for faster text encode (set TORCH_COMPILE=1).
        if self.device == "cuda" and os.getenv("TORCH_COMPILE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            try:
                self.model.text_model = torch.compile(self.model.text_model, mode="reduce-overhead")
                print("torch.compile enabled for text_model")
            except Exception as exc:
                print(f"torch.compile skipped: {exc}")
        print(f"Model {self.model_name} loaded from {self.model_id}")

    @torch.inference_mode()
    def embedding_tensor(self, text: str) -> torch.Tensor:
        """Return L2-normalized float32 embedding on the model device (no CPU sync)."""
        self.load()
        # SigLIP2 is trained with pad-to-max_length (64). padding=True yields a
        # short sequence and near-random retrieval against the indexed images.
        inputs = self.processor(
            text=[text],
            padding="max_length",
            truncation=True,
            max_length=64,
            return_tensors="pt",
        )
        inputs = {
            k: v.to(self.device, non_blocking=True)
            for k, v in inputs.items()
            if k in {"input_ids", "attention_mask", "position_ids"}
        }
        outputs = self.model.text_model(**inputs)
        feats = F.normalize(outputs.pooler_output.float(), p=2, dim=-1)
        return feats[0]

    @torch.inference_mode()
    def embedding(self, text: str) -> np.ndarray:
        feats = self.embedding_tensor(text)
        return feats.detach().cpu().numpy().astype(np.float32)

    def embeddings(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors = [self.embedding(text) for text in texts]
        return np.stack(vectors, axis=0)

    def embed_text_list(self, text: str) -> List[float]:
        return self.embedding(text).tolist()
