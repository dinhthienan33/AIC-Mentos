"""Group/video catalog from the live Qdrant collection.

Groups are the payload `batch` values (L21_a, M01, N001, S01).
Videos are payload `video_id` values inside each group.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

import requests

from core.config import get_settings

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"data": None, "at": 0.0}
_CACHE_TTL = 300.0


def _sort_key(value: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value or "")]


def _facet(session: requests.Session, key: str, group: str | None = None, limit: int = 2000) -> List[dict]:
    settings = get_settings()
    body: Dict[str, Any] = {"key": key, "limit": limit}
    if group is not None:
        body["filter"] = {"must": [{"key": "batch", "match": {"value": group}}]}
    url = f"{settings.qdrant_url.rstrip('/')}/collections/{settings.qdrant_collection}/facet"
    headers = {"Content-Type": "application/json"}
    if settings.qdrant_api_key:
        headers["api-key"] = settings.qdrant_api_key
    response = session.post(url, json=body, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json().get("result", {}).get("hits") or []


def build_catalog() -> dict:
    session = requests.Session()
    groups = _facet(session, "batch", limit=500)
    group_ids = sorted((hit["value"] for hit in groups if hit.get("value")), key=_sort_key)
    frame_by_group = {hit["value"]: int(hit.get("count") or 0) for hit in groups}

    videos_by_group: Dict[str, List[dict]] = {}

    def load_group(group_id: str) -> tuple[str, List[dict]]:
        hits = _facet(session, "video_id", group=group_id, limit=2000)
        videos = [
            {"id": hit["value"], "frames": int(hit.get("count") or 0)}
            for hit in hits
            if hit.get("value")
        ]
        videos.sort(key=lambda item: _sort_key(item["id"]))
        return group_id, videos

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(load_group, group_id) for group_id in group_ids]
        for future in as_completed(futures):
            group_id, videos = future.result()
            videos_by_group[group_id] = videos

    catalog_groups = []
    for group_id in group_ids:
        videos = videos_by_group.get(group_id, [])
        catalog_groups.append(
            {
                "id": group_id,
                "frames": frame_by_group.get(group_id, 0),
                "video_count": len(videos),
                "videos": videos,
            }
        )
    return {
        "groups": catalog_groups,
        "group_count": len(catalog_groups),
        "video_count": sum(len(group["videos"]) for group in catalog_groups),
    }


def get_catalog(force: bool = False) -> dict:
    now = time.time()
    with _cache_lock:
        cached = _cache["data"]
        if cached is not None and not force and now - _cache["at"] < _CACHE_TTL:
            return cached
    data = build_catalog()
    with _cache_lock:
        _cache["data"] = data
        _cache["at"] = time.time()
    return data
