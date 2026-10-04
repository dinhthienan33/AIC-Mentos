"""OCR search over keyframe text using Elasticsearch."""
from __future__ import annotations

import logging
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .config import Settings, get_settings
from .utils import (
    frame_to_time,
    get_video_fps,
    get_video_thumbnail,
    get_video_url,
    get_video_url_with_start_time,
)

logger = logging.getLogger(__name__)

INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "ocr_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "global_id": {"type": "long"},
            "video_id": {"type": "keyword"},
            "group_id": {"type": "keyword"},
            "frame_idx": {"type": "integer"},
            "shard": {"type": "keyword"},
            "ocr_text": {
                "type": "text",
                "analyzer": "ocr_analyzer",
                "fields": {
                    "raw": {"type": "keyword", "ignore_above": 512},
                },
            },
        }
    },
}


def _client(settings: Optional[Settings] = None):
    from elasticsearch import Elasticsearch

    settings = settings or get_settings()
    return Elasticsearch(settings.elasticsearch_url, request_timeout=120)


class OcrElasticsearchStore:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.index = self.settings.elasticsearch_index
        self._es = None

    @property
    def es(self):
        if self._es is None:
            self._es = _client(self.settings)
        return self._es

    def ping(self) -> bool:
        try:
            return bool(self.es.ping())
        except Exception as exc:
            logger.warning("Elasticsearch ping failed: %s", exc)
            return False

    def doc_count(self) -> int:
        if not self.ping():
            return 0
        if not self.es.indices.exists(index=self.index):
            return 0
        return int(self.es.count(index=self.index).get("count") or 0)

    def ensure_index(self, *, reindex: bool = False) -> int:
        if not self.ping():
            raise RuntimeError(
                f"Elasticsearch is not reachable at {self.settings.elasticsearch_url}"
            )
        exists = self.es.indices.exists(index=self.index)
        count = self.doc_count() if exists else 0
        if exists and count > 0 and not reindex:
            return count
        if exists and reindex:
            self.es.indices.delete(index=self.index)
            exists = False
        if not exists:
            self.es.indices.create(
                index=self.index,
                settings=INDEX_BODY["settings"],
                mappings=INDEX_BODY["mappings"],
            )
        indexed = self._bulk_from_sqlite()
        self.es.indices.refresh(index=self.index)
        logger.info("Indexed %s OCR documents into %s", indexed, self.index)
        return indexed

    def _bulk_from_sqlite(self) -> int:
        from elasticsearch.helpers import bulk

        mount = Path(self.settings.db_mount_dir)
        actions = self._iter_actions(mount)
        success, errors = bulk(
            self.es,
            actions,
            chunk_size=1000,
            request_timeout=60,
            raise_on_error=False,
        )
        if errors:
            logger.warning("OCR bulk index reported %s errors (showing 3): %s", len(errors), errors[:3])
        return int(success)

    def _iter_actions(self, mount: Path) -> Iterable[dict]:
        dbs = sorted(mount.glob("*.db"))
        if not dbs:
            logger.warning("No sqlite shards in %s to index OCR from", mount)
            return
        for db_path in dbs:
            shard = db_path.stem
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT e.global_id AS global_id,
                           f.video_id AS video_id,
                           f.frame_idx AS frame_idx,
                           e.ocr_quotes_concat AS ocr_text
                    FROM enrichment e
                    JOIN frames f ON f.global_id = e.global_id
                    WHERE e.ocr_quotes_concat IS NOT NULL
                      AND trim(e.ocr_quotes_concat) != ''
                    """
                )
                for row in rows:
                    video_id = str(row["video_id"])
                    frame_idx = int(row["frame_idx"])
                    yield {
                        "_index": self.index,
                        "_id": f"{video_id}_{frame_idx}",
                        "_source": {
                            "global_id": int(row["global_id"]),
                            "video_id": video_id,
                            "group_id": video_id.split("_")[0] if "_" in video_id else video_id,
                            "frame_idx": frame_idx,
                            "shard": shard,
                            "ocr_text": str(row["ocr_text"]).strip(),
                        },
                    }
            finally:
                conn.close()

    def _text_query(
        self,
        query: str,
        *,
        include_groups: Optional[List[str]] = None,
        include_videos: Optional[List[str]] = None,
        strict: bool = False,
    ) -> dict:
        filters = []
        if include_groups:
            filters.append({"terms": {"group_id": [str(g).strip() for g in include_groups if g]}})
        if include_videos:
            filters.append({"terms": {"video_id": [str(v).strip() for v in include_videos if v]}})
        q = query.strip()
        if strict:
            should = [
                {"match_phrase": {"ocr_text": {"query": q, "boost": 4}}},
                {"match": {"ocr_text": {"query": q, "operator": "and", "boost": 2}}},
            ]
        else:
            should = [
                {"match_phrase": {"ocr_text": {"query": q, "boost": 4}}},
                {"match": {"ocr_text": {"query": q, "operator": "and", "boost": 2}}},
                {"match": {"ocr_text": {"query": q, "operator": "or", "fuzziness": "AUTO"}}},
            ]
        return {
            "bool": {
                "filter": filters,
                "should": should,
                "minimum_should_match": 1,
            }
        }

    def _ready(self) -> None:
        if not self.ping():
            raise RuntimeError(
                f"Elasticsearch is not reachable at {self.settings.elasticsearch_url}"
            )
        if not self.es.indices.exists(index=self.index) or self.doc_count() == 0:
            self.ensure_index()

    def matching_video_ids(
        self,
        query: str,
        *,
        include_groups: Optional[List[str]] = None,
        include_videos: Optional[List[str]] = None,
        limit: int = 10_000,
        strict: bool = True,
    ) -> Set[str]:
        if not (query or "").strip():
            return set()
        self._ready()
        q = query.strip()
        if strict:
            phrase = self._video_id_agg(
                {"match_phrase": {"ocr_text": {"query": q}}},
                include_groups=include_groups,
                include_videos=include_videos,
                limit=limit,
            )
            if phrase:
                return phrase
            return self._video_id_agg(
                {"match": {"ocr_text": {"query": q, "operator": "and"}}},
                include_groups=include_groups,
                include_videos=include_videos,
                limit=limit,
            )
        return self._video_id_agg(
            self._text_query(
                query,
                include_groups=include_groups,
                include_videos=include_videos,
                strict=False,
            ),
            include_groups=None,
            include_videos=None,
            limit=limit,
        )

    def _video_id_agg(
        self,
        query: dict,
        *,
        include_groups: Optional[List[str]] = None,
        include_videos: Optional[List[str]] = None,
        limit: int = 10_000,
    ) -> Set[str]:
        filters = []
        if include_groups:
            filters.append({"terms": {"group_id": [str(g).strip() for g in include_groups if g]}})
        if include_videos:
            filters.append({"terms": {"video_id": [str(v).strip() for v in include_videos if v]}})
        body = query
        if filters:
            body = {"bool": {"filter": filters, "must": [query]}}
        resp = self.es.search(
            index=self.index,
            size=0,
            query=body,
            aggs={
                "videos": {
                    "terms": {
                        "field": "video_id",
                        "size": max(1, int(limit)),
                    }
                }
            },
        )
        buckets = ((resp.get("aggregations") or {}).get("videos") or {}).get("buckets") or []
        return {str(b.get("key")) for b in buckets if b.get("key")}

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        include_groups: Optional[List[str]] = None,
        include_videos: Optional[List[str]] = None,
        strict: bool = False,
    ) -> List[Dict]:
        if not (query or "").strip():
            return []
        self._ready()
        resp = self.es.search(
            index=self.index,
            size=max(1, int(top_k)),
            query=self._text_query(
                query,
                include_groups=include_groups,
                include_videos=include_videos,
                strict=strict,
            ),
            highlight={
                "fields": {"ocr_text": {"number_of_fragments": 1, "fragment_size": 180}},
            },
        )
        hits = []
        for hit in (resp.get("hits") or {}).get("hits") or []:
            src = hit.get("_source") or {}
            hl = None
            fragments = ((hit.get("highlight") or {}).get("ocr_text") or [])
            if fragments:
                hl = fragments[0]
            video_id = str(src.get("video_id") or "")
            frame_idx = int(src.get("frame_idx") or 0)
            fps = float(get_video_fps(video_id))
            timestamp = frame_to_time(frame_idx, fps)
            image_url = None
            try:
                from .hf_frames import get_hf_frame_store

                if self.settings.keyframe_source == "hf":
                    image_url = get_hf_frame_store().public_url(video_id, frame_idx)
            except Exception:
                image_url = None
            hits.append(
                {
                    "video_id": video_id,
                    "group_id": src.get("group_id") or (video_id.split("_")[0] if video_id else ""),
                    "keyframe_num": frame_idx,
                    "fps": fps,
                    "timestamp": timestamp,
                    "ocr_text": src.get("ocr_text") or "",
                    "confidence_score": float(hit.get("_score") or 0.0),
                    "image_path": f"keyframes/{video_id}/{frame_idx:06d}.jpg",
                    "image_url": image_url,
                    "video_url": get_video_url_with_start_time(
                        get_video_url(video_id),
                        start_seconds=timestamp,
                    ),
                    "thumbnail_url": get_video_thumbnail(video_id),
                    "highlight": hl,
                }
            )
        return hits


@lru_cache(maxsize=1)
def get_ocr_es_store() -> OcrElasticsearchStore:
    return OcrElasticsearchStore()
