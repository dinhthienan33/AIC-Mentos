"""Serve keyframe JPEGs from Hugging Face webdataset tars (`datav3/{shard}/shard_*.tar`)."""
from __future__ import annotations

import json
import logging
import re
import tarfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from huggingface_hub import HfApi, hf_hub_download

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

_VIDEO_RE = re.compile(r"^([A-Za-z]+)(\d+)_V(\d+)$", re.I)
_JPEG_CACHE_MAX = 4096
_PREFETCH_WORKERS = 8
_FRAME_CACHE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
}


def _sort_key(video_id: str, frame_idx: int) -> tuple:
    match = _VIDEO_RE.match(str(video_id or ""))
    if not match:
        return (str(video_id), int(frame_idx))
    return (match.group(1).upper(), int(match.group(2)), int(match.group(3)), int(frame_idx))


def _parse_meta(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    return {
        "global_id": int(data["global_id"]),
        "video_id": str(data["video_id"]),
        "frame_idx": int(data["frame_idx"]),
    }


class HfKeyframeStore:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._api = HfApi(token=self.settings.hf_token or None)
        self._lock = threading.RLock()
        self._shards: Optional[List[str]] = None
        self._tars: Dict[str, List[str]] = {}
        self._ranges: Dict[str, Tuple[dict, dict]] = {}
        self._lookup: Dict[Tuple[str, int], Tuple[str, str, str]] = {}
        self._indexed_tars: set[str] = set()
        self._jpeg_cache: OrderedDict[Tuple[str, int], bytes] = OrderedDict()
        ram_max = int(getattr(self.settings, "jpeg_ram_cache_max", _JPEG_CACHE_MAX) or _JPEG_CACHE_MAX)
        self._jpeg_cache_max = max(128, ram_max)
        self._disk_cache_enabled = bool(getattr(self.settings, "jpeg_disk_cache", True))
        self._cache_dir = Path(self.settings.model_cache_dir) / "hf_tar_index"
        local_dir = getattr(self.settings, "keyframe_local_dir", None)
        self._jpeg_dir = Path(local_dir) if local_dir else (Path(self.settings.model_cache_dir) / "hf_jpeg_cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._jpeg_dir.mkdir(parents=True, exist_ok=True)
        self._inflight: Dict[Tuple[str, int], threading.Event] = {}
        self._pool = ThreadPoolExecutor(max_workers=_PREFETCH_WORKERS, thread_name_prefix="hf-jpeg")
        self._load_range_cache()

    @property
    def repo_id(self) -> str:
        return self.settings.hf_repo_id

    def _token(self) -> Optional[str]:
        return self.settings.hf_token or None

    def _tar_key(self, shard: str, tar_name: str) -> str:
        return f"{shard}/{tar_name}"

    def list_shards(self) -> List[str]:
        with self._lock:
            if self._shards is None:
                prefix = self.settings.hf_keyframe_prefix.rstrip("/")
                items = self._api.list_repo_tree(
                    self.repo_id,
                    path_in_repo=prefix,
                    repo_type="dataset",
                    recursive=False,
                    token=self._token(),
                )
                names = []
                for item in items:
                    path = getattr(item, "path", "") or ""
                    name = path.rstrip("/").split("/")[-1]
                    if name:
                        names.append(name)
                self._shards = sorted(set(names))
            return list(self._shards)

    def shards_for_group(self, group_id: str) -> List[str]:
        prefix = f"{group_id}_"
        return [s for s in self.list_shards() if s.startswith(prefix)] or [f"{group_id}_a"]

    def public_url(self, video_id: str, frame_idx: int) -> str:
        base = (self.settings.public_base_url or "").rstrip("/")
        path = f"/frames/{video_id}/{int(frame_idx)}.jpg"
        return f"{base}{path}" if base else path

    def cache_headers(self, video_id: str, frame_idx: int) -> dict[str, str]:
        return {**_FRAME_CACHE_HEADERS, "ETag": self.etag(video_id, frame_idx)}

    @staticmethod
    def etag(video_id: str, frame_idx: int) -> str:
        return f'"{video_id}-{int(frame_idx)}"'

    def _disk_path(self, video_id: str, frame_idx: int) -> Path:
        return self._jpeg_dir / str(video_id) / f"{int(frame_idx):06d}.jpg"

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def _ram_get(self, key: Tuple[str, int]) -> Optional[bytes]:
        with self._lock:
            cached = self._jpeg_cache.get(key)
            if cached is not None:
                self._jpeg_cache.move_to_end(key)
            return cached

    def _ram_put(self, key: Tuple[str, int], data: bytes) -> None:
        with self._lock:
            self._jpeg_cache[key] = data
            self._jpeg_cache.move_to_end(key)
            while len(self._jpeg_cache) > self._jpeg_cache_max:
                self._jpeg_cache.popitem(last=False)

    def _local_complete(self) -> bool:
        return (self._jpeg_dir / ".extract_complete").is_file()

    def _local_exists(self, video_id: str, frame_idx: int) -> bool:
        path = self._disk_path(video_id, frame_idx)
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def _read_disk(self, video_id: str, frame_idx: int) -> Optional[bytes]:
        path = self._disk_path(video_id, frame_idx)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path.read_bytes()
        except OSError:
            return None
        return None

    def jpeg_path(self, video_id: str, frame_idx: int) -> Path:
        """Return the on-disk JPEG path, extracting from a tar only on a miss."""
        key = (str(video_id), int(frame_idx))
        path = self._disk_path(*key)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            pass
        if self._local_complete():
            raise FileNotFoundError(f"Keyframe not on disk: {video_id} frame={frame_idx}")
        self.get_jpeg(str(video_id), int(frame_idx))
        return path

    def get_jpeg(self, video_id: str, frame_idx: int) -> bytes:
        cache_key = (str(video_id), int(frame_idx))
        cached = self._ram_get(cache_key)
        if cached is not None:
            return cached

        disk = self._read_disk(*cache_key)
        if disk is not None:
            self._ram_put(cache_key, disk)
            return disk
        if self._local_complete():
            raise FileNotFoundError(f"Keyframe not on disk: {video_id} frame={frame_idx}")

        with self._lock:
            waiter = self._inflight.get(cache_key)
            owner = waiter is None
            if owner:
                waiter = threading.Event()
                self._inflight[cache_key] = waiter
        if not owner:
            waiter.wait(timeout=180)
            cached = self._ram_get(cache_key) or self._read_disk(*cache_key)
            if cached is not None:
                self._ram_put(cache_key, cached)
                return cached
            return self.get_jpeg(video_id, frame_idx)

        try:
            t0 = time.perf_counter()
            shard, tar_name, member = self.resolve(video_id, int(frame_idx))
            tar_path = self._download_tar(shard, tar_name)
            with tarfile.open(tar_path, "r") as tar:
                handle = tar.extractfile(member)
                if handle is None:
                    raise FileNotFoundError(member)
                data = handle.read()
            elapsed = time.perf_counter() - t0
            if elapsed > 0.4:
                logger.info(
                    "HF frame %s/%s from %s/%s in %.2fs",
                    video_id,
                    frame_idx,
                    shard,
                    tar_name,
                    elapsed,
                )
            try:
                self._atomic_write(self._disk_path(*cache_key), data)
            except OSError as exc:
                logger.warning("JPEG local write failed: %s", exc)
            self._ram_put(cache_key, data)
            return data
        finally:
            waiter.set()
            with self._lock:
                self._inflight.pop(cache_key, None)

    def prefetch(self, frames: List[Tuple[str, int]]) -> None:
        """Warm RAM/disk cache for search-result frames without blocking the API."""
        if self._local_complete():
            return
        seen: set[Tuple[str, int]] = set()
        for video_id, frame_idx in frames:
            key = (str(video_id), int(frame_idx))
            if key in seen:
                continue
            seen.add(key)
            if self._ram_get(key) is not None:
                continue
            if self._local_exists(*key):
                continue
            self._pool.submit(self._prefetch_one, key[0], key[1])

    def _prefetch_one(self, video_id: str, frame_idx: int) -> None:
        try:
            self.get_jpeg(video_id, frame_idx)
        except Exception as exc:
            logger.debug("Prefetch miss %s/%s: %s", video_id, frame_idx, exc)

    def resolve(self, video_id: str, frame_idx: int) -> Tuple[str, str, str]:
        key = (str(video_id), int(frame_idx))
        with self._lock:
            cached = self._lookup.get(key)
            if cached:
                return cached

        for shard in self.shards_for_group(str(video_id).split("_")[0]):
            found = self._resolve_in_shard(shard, str(video_id), int(frame_idx))
            if found:
                return found
        raise FileNotFoundError(f"Keyframe not in HF tars: {video_id} frame={frame_idx}")

    def _list_tars(self, shard: str) -> List[str]:
        with self._lock:
            if shard in self._tars:
                return self._tars[shard]
        prefix = f"{self.settings.hf_keyframe_prefix.rstrip('/')}/{shard}"
        items = self._api.list_repo_tree(
            self.repo_id,
            path_in_repo=prefix,
            repo_type="dataset",
            recursive=False,
            token=self._token(),
        )
        names = sorted(
            getattr(item, "path", "").split("/")[-1]
            for item in items
            if str(getattr(item, "path", "")).endswith(".tar")
        )
        with self._lock:
            self._tars[shard] = names
        return names

    def _download_tar(self, shard: str, tar_name: str) -> str:
        filename = f"{self.settings.hf_keyframe_prefix.rstrip('/')}/{shard}/{tar_name}"
        return hf_hub_download(
            repo_id=self.repo_id,
            repo_type="dataset",
            filename=filename,
            token=self._token(),
        )

    def _index_path(self, shard: str, tar_name: str) -> Path:
        safe = tar_name.replace("/", "_")
        return self._cache_dir / f"{shard}__{safe}.json"

    def _index_tar(self, shard: str, tar_name: str) -> None:
        tar_key = self._tar_key(shard, tar_name)
        with self._lock:
            if tar_key in self._indexed_tars:
                return
        index_path = self._index_path(shard, tar_name)
        if index_path.is_file():
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            self._ingest_index(shard, tar_name, payload)
            return

        t0 = time.perf_counter()
        tar_path = self._download_tar(shard, tar_name)
        rows: List[dict] = []
        with tarfile.open(tar_path, "r") as tar:
            pending_jpg = None
            for member in tar:
                name = member.name
                if name.endswith(".jpg"):
                    pending_jpg = name
                    continue
                if not name.endswith(".json"):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                meta = _parse_meta(handle.read())
                jpg = pending_jpg or (name[:-5] + ".jpg")
                rows.append(
                    {
                        "video_id": meta["video_id"],
                        "frame_idx": meta["frame_idx"],
                        "jpg": jpg,
                    }
                )
        index_path.write_text(json.dumps(rows), encoding="utf-8")
        self._ingest_index(shard, tar_name, rows)
        logger.info("Indexed %s (%s frames) in %.2fs", tar_key, len(rows), time.perf_counter() - t0)

    def _ingest_index(self, shard: str, tar_name: str, rows: List[dict]) -> None:
        if not rows:
            return
        tar_key = self._tar_key(shard, tar_name)
        with self._lock:
            for row in rows:
                key = (str(row["video_id"]), int(row["frame_idx"]))
                self._lookup[key] = (shard, tar_name, str(row["jpg"]))
            first = rows[0]
            last = rows[-1]
            self._ranges[tar_key] = (
                {"video_id": first["video_id"], "frame_idx": int(first["frame_idx"])},
                {"video_id": last["video_id"], "frame_idx": int(last["frame_idx"])},
            )
            self._indexed_tars.add(tar_key)
            self._save_range_cache()

    def _tar_range(self, shard: str, tar_name: str) -> Tuple[dict, dict]:
        tar_key = self._tar_key(shard, tar_name)
        with self._lock:
            cached = self._ranges.get(tar_key)
            if cached:
                return cached
        self._index_tar(shard, tar_name)
        with self._lock:
            return self._ranges[tar_key]

    def _resolve_in_shard(self, shard: str, video_id: str, frame_idx: int) -> Optional[Tuple[str, str, str]]:
        tars = self._list_tars(shard)
        if not tars:
            return None
        target = _sort_key(video_id, frame_idx)
        lo, hi = 0, len(tars) - 1
        candidate = None
        while lo <= hi:
            mid = (lo + hi) // 2
            first, last = self._tar_range(shard, tars[mid])
            first_k = _sort_key(first["video_id"], first["frame_idx"])
            last_k = _sort_key(last["video_id"], last["frame_idx"])
            if target < first_k:
                hi = mid - 1
            elif target > last_k:
                lo = mid + 1
            else:
                candidate = tars[mid]
                break
        if candidate is None:
            return None
        self._index_tar(shard, candidate)
        with self._lock:
            return self._lookup.get((video_id, int(frame_idx)))

    def _load_range_cache(self) -> None:
        path = self._cache_dir / "ranges.json"
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key, value in (payload or {}).items():
                self._ranges[key] = (value["first"], value["last"])
        except Exception as exc:
            logger.warning("Failed reading HF tar range cache: %s", exc)

    def _save_range_cache(self) -> None:
        try:
            payload = {k: {"first": a, "last": b} for k, (a, b) in self._ranges.items()}
            (self._cache_dir / "ranges.json").write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed writing HF tar range cache: %s", exc)


@lru_cache(maxsize=1)
def get_hf_frame_store() -> HfKeyframeStore:
    return HfKeyframeStore()
