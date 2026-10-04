"""Local GPU vector index — avoids remote Qdrant RTT for search latency.

Vectors live on CUDA as fp16; query embed + matmul top-k stays on GPU.
Enable with ``USE_LOCAL_INDEX=true`` (default true when index files exist).
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch

logger = logging.getLogger(__name__)

DEFAULT_INDEX_DIR = Path("data/local_index")


def index_dir() -> Path:
    return Path(os.getenv("LOCAL_INDEX_DIR") or DEFAULT_INDEX_DIR)


def index_ready(directory: Optional[Path] = None) -> bool:
    d = directory or index_dir()
    return (d / "vectors_fp16.npy").exists() and (d / "meta.jsonl").exists()


def use_local_index() -> bool:
    """Prefer local GPU index when files exist, unless explicitly disabled.

    ``USE_LOCAL_INDEX=false`` forces remote Qdrant.
    Unset / ``true`` / ``auto`` → use local index when ``data/local_index`` is ready.
    """
    raw = (os.getenv("USE_LOCAL_INDEX") or "auto").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return index_ready()
    return index_ready()

class LocalGpuIndex:
    """Exact cosine top-k over all keyframe vectors on GPU."""

    def __init__(self, directory: Optional[Path] = None, device: Optional[str] = None):
        self.directory = Path(directory or index_dir())
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.vectors: Optional[torch.Tensor] = None  # [N, D] fp16, L2-normalized
        self.meta: List[Dict[str, Any]] = []
        self._video_to_idx: Dict[str, torch.Tensor] = {}
        self._batch_to_idx: Dict[str, torch.Tensor] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        vec_path = self.directory / "vectors_fp16.npy"
        meta_path = self.directory / "meta.jsonl"
        if not vec_path.exists() or not meta_path.exists():
            raise FileNotFoundError(f"Local index missing under {self.directory}")

        logger.info("Loading local GPU index from %s ...", self.directory)
        arr = np.load(vec_path)  # float16 [N, D]
        if arr.dtype != np.float16:
            arr = arr.astype(np.float16)
        # Qdrant cosine assumes unit vectors; normalize once on load.
        t = torch.from_numpy(arr)
        if self.device == "cuda":
            t = t.to(device="cuda", dtype=torch.float16, non_blocking=True)
        else:
            t = t.to(dtype=torch.float32)
        # normalize in fp32 for stability then cast back
        t32 = t.float()
        t32 = torch.nn.functional.normalize(t32, p=2, dim=-1)
        self.vectors = t32.to(dtype=t.dtype) if self.device == "cuda" else t32

        metas: List[Dict[str, Any]] = []
        with open(meta_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    metas.append(json.loads(line))
        if len(metas) != self.vectors.shape[0]:
            raise RuntimeError(
                f"meta/vector length mismatch: {len(metas)} vs {self.vectors.shape[0]}"
            )
        self.meta = metas
        from collections import defaultdict

        buckets: Dict[str, List[int]] = defaultdict(list)
        batch_buckets: Dict[str, List[int]] = defaultdict(list)
        for i, m in enumerate(metas):
            vid = str(m.get("video_id") or "")
            if vid:
                buckets[vid].append(i)
            batch_id = str(m.get("batch") or "")
            if batch_id:
                batch_buckets[batch_id].append(i)
        self._video_to_idx = {
            vid: torch.tensor(idxs, dtype=torch.long) for vid, idxs in buckets.items()
        }
        self._batch_to_idx = {
            batch_id: torch.tensor(idxs, dtype=torch.long)
            for batch_id, idxs in batch_buckets.items()
        }
        self._loaded = True
        logger.info(
            "Local GPU index ready: n=%s dim=%s device=%s dtype=%s",
            self.vectors.shape[0],
            self.vectors.shape[1],
            self.device,
            self.vectors.dtype,
        )

    @torch.inference_mode()
    def search(
        self,
        query_vec: Sequence[float] | np.ndarray | torch.Tensor,
        top_k: int = 10,
        *,
        batch: Optional[int] = None,
        exclude_groups: Optional[Sequence[str]] = None,
        exclude_vid_id: Optional[Sequence[str]] = None,
        include_groups: Optional[Sequence[str]] = None,
        include_videos: Optional[Sequence[str]] = None,
        score_threshold: float = 0.0,
        overfetch_factor: int = 4,
    ) -> List[Dict[str, Any]]:
        self.load()
        assert self.vectors is not None

        excl_g = set(exclude_groups or [])
        incl_g = set(include_groups or [])
        excl_v = set(exclude_vid_id or [])
        incl_v = set(include_videos or [])
        prefix = None
        if not incl_g and not incl_v:
            if batch == 1:
                prefix = "L"
            elif batch == 2:
                prefix = "K"

        if isinstance(query_vec, torch.Tensor):
            q = query_vec.detach()
        else:
            q = torch.as_tensor(np.asarray(query_vec, dtype=np.float32))
        q = q.float().view(-1)
        q = torch.nn.functional.normalize(q, p=2, dim=-1)
        if self.device == "cuda":
            q = q.to(device="cuda", dtype=self.vectors.dtype, non_blocking=True)
        else:
            q = q.to(dtype=self.vectors.dtype)

        row_idx = None
        if incl_v:
            parts = [self._video_to_idx[v] for v in incl_v if v in self._video_to_idx]
            if not parts:
                return []
            row_idx = torch.cat(parts)
            if self.device == "cuda":
                row_idx = row_idx.to(device="cuda", non_blocking=True)
            scores = self.vectors.index_select(0, row_idx) @ q
            fetch_k = min(int(top_k), scores.numel())
        elif incl_g:
            parts = [self._batch_to_idx[g] for g in incl_g if g in self._batch_to_idx]
            if not parts:
                return []
            row_idx = torch.cat(parts)
            if self.device == "cuda":
                row_idx = row_idx.to(device="cuda", non_blocking=True)
            scores = self.vectors.index_select(0, row_idx) @ q
            fetch_k = min(int(top_k), scores.numel())
        else:
            scores = self.vectors @ q
            need_filter = bool(prefix or excl_g or excl_v or incl_g)
            fetch_k = int(top_k)
            if need_filter:
                fetch_k = max(int(top_k) * max(1, int(overfetch_factor)), int(top_k))
            fetch_k = min(fetch_k, scores.numel())

        vals, idxs = torch.topk(scores, k=max(1, fetch_k), largest=True, sorted=True)
        vals_cpu = vals.float().cpu().tolist()
        idxs_cpu = idxs.cpu().tolist()

        hits: List[Dict[str, Any]] = []
        for score, loc in zip(vals_cpu, idxs_cpu):
            if score_threshold and float(score) < score_threshold:
                continue
            idx = int(row_idx[loc].item()) if row_idx is not None else int(loc)
            m = self.meta[idx]
            video_id = str(m.get("video_id") or "")
            frame_idx = m.get("frame_idx")
            if not video_id or frame_idx is None:
                continue
            group_id = str(m.get("batch") or "") or video_id.split("_", 1)[0]
            if prefix and not video_id.startswith(prefix):
                continue
            if excl_g and group_id in excl_g:
                continue
            if incl_g and group_id not in incl_g:
                continue
            if excl_v and video_id in excl_v:
                continue
            from .utils import synthetic_image_path

            hits.append(
                {
                    "id": m.get("id", idx),
                    "score": float(score),
                    "video_id": video_id,
                    "frame_idx": int(frame_idx),
                    "batch": m.get("batch"),
                    "global_id": m.get("global_id"),
                    "path": synthetic_image_path(video_id, int(frame_idx)),
                    "payload": m,
                }
            )
            if len(hits) >= int(top_k):
                break
        return hits


@lru_cache(maxsize=1)
def get_local_index() -> LocalGpuIndex:
    return LocalGpuIndex()
