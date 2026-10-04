"""Object-detection / entity search over enrichment.entities_concat using Elasticsearch."""
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
                "od_analyzer": {
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
            "od_text": {
                "type": "text",
                "analyzer": "od_analyzer",
                "fields": {
                    "raw": {"type": "keyword", "ignore_above": 512},
                },
            },
        }
    },
}


def _normalize_entities(text: str) -> str:
    """Pipe-separated entities → spaces so the standard tokenizer splits cleanly."""
    return " ".join(part.strip() for part in str(text or "").replace("|", " ").split() if part.strip())


def _client(settings: Optional[Settings] = None):
    from elasticsearch import Elasticsearch

    settings = settings or get_settings()
    return Elasticsearch(settings.elasticsearch_url, request_timeout=120)


class OdElasticsearchStore:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.index = self.settings.elasticsearch_od_index
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
        logger.info("Indexed %s OD documents into %s", indexed, self.index)
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
            logger.warning("OD bulk index reported %s errors (showing 3): %s", len(errors), errors[:3])
        return int(success)

    def _iter_actions(self, mount: Path) -> Iterable[dict]:
        dbs = sorted(mount.glob("*.db"))
        if not dbs:
            logger.warning("No sqlite shards in %s to index OD from", mount)
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
                           e.entities_concat AS od_text
                    FROM enrichment e
                    JOIN frames f ON f.global_id = e.global_id
                    WHERE e.entities_concat IS NOT NULL
                      AND trim(e.entities_concat) != ''
                    """
                )
                for row in rows:
                    video_id = str(row["video_id"])
                    frame_idx = int(row["frame_idx"])
                    od_text = _normalize_entities(row["od_text"])
                    if not od_text:
                        continue
                    yield {
                        "_index": self.index,
                        "_id": f"{video_id}_{frame_idx}",
                        "_source": {
                            "global_id": int(row["global_id"]),
                            "video_id": video_id,
                            "group_id": video_id.split("_")[0] if "_" in video_id else video_id,
                            "frame_idx": frame_idx,
                            "shard": shard,
                            "od_text": od_text,
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
                {"match_phrase": {"od_text": {"query": q, "boost": 4}}},
                {"match": {"od_text": {"query": q, "operator": "and", "boost": 2}}},
            ]
        else:
            should = [
                {"match_phrase": {"od_text": {"query": q, "boost": 4}}},
                {"match": {"od_text": {"query": q, "operator": "and", "boost": 2}}},
                {"match": {"od_text": {"query": q, "operator": "or", "fuzziness": "AUTO"}}},
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
        return set(
            self.video_scores(
                query,
                include_groups=include_groups,
                include_videos=include_videos,
                limit=limit,
                strict=strict,
            ).keys()
        )

    def video_scores(
        self,
        query: str,
        *,
        include_groups: Optional[List[str]] = None,
        include_videos: Optional[List[str]] = None,
        limit: int = 10_000,
        strict: bool = True,
    ) -> Dict[str, float]:
        """Map video_id -> max ES score for frames matching the entity query."""
        if not (query or "").strip():
            return {}
        self._ready()
        q = query.strip()
        if strict:
            scores = self._video_score_agg(
                {"match_phrase": {"od_text": {"query": q}}},
                include_groups=include_groups,
                include_videos=include_videos,
                limit=limit,
            )
            if scores:
                return scores
            return self._video_score_agg(
                {"match": {"od_text": {"query": q, "operator": "and"}}},
                include_groups=include_groups,
                include_videos=include_videos,
                limit=limit,
            )
        return self._video_score_agg(
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

    def _video_score_agg(
        self,
        query: dict,
        *,
        include_groups: Optional[List[str]] = None,
        include_videos: Optional[List[str]] = None,
        limit: int = 10_000,
    ) -> Dict[str, float]:
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
                        "order": {"max_score": "desc"},
                    },
                    "aggs": {"max_score": {"max": {"script": "_score"}}},
                }
            },
        )
        buckets = ((resp.get("aggregations") or {}).get("videos") or {}).get("buckets") or []
        out: Dict[str, float] = {}
        for b in buckets:
            key = b.get("key")
            if not key:
                continue
            max_score = ((b.get("max_score") or {}).get("value"))
            out[str(key)] = float(max_score if max_score is not None else b.get("doc_count") or 1.0)
        return out

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        include_groups: Optional[List[str]] = None,
        include_videos: Optional[List[str]] = None,
        strict: bool = True,
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
                "fields": {"od_text": {"number_of_fragments": 1, "fragment_size": 180}},
            },
        )
        hits = []
        for hit in (resp.get("hits") or {}).get("hits") or []:
            src = hit.get("_source") or {}
            hl = None
            fragments = ((hit.get("highlight") or {}).get("od_text") or [])
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
                    "od_text": src.get("od_text") or "",
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
def get_od_es_store() -> OdElasticsearchStore:
    return OdElasticsearchStore()
