"""Frame-level enrichment search (hybrid rerank) using Elasticsearch."""
from __future__ import annotations

import logging
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

TEXT_FIELDS: Tuple[Tuple[str, float], ...] = (
    ("description_vi", 2.0),
    ("description_en", 2.0),
    ("entities_concat", 1.2),
    ("ocr_quotes_concat", 1.7),
    ("scene_type", 1.2),
    ("frame_position", 0.1),
    ("action_verbs_concat", 1.4),
    ("query_suggestions", 1.6),
)

_TEXT_MAPPING = {
    "type": "text",
    "analyzer": "enrichment_analyzer",
}

INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "enrichment_analyzer": {
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
            **{name: _TEXT_MAPPING for name, _ in TEXT_FIELDS},
        }
    },
}


def _client(settings: Optional[Settings] = None):
    from elasticsearch import Elasticsearch

    settings = settings or get_settings()
    return Elasticsearch(settings.elasticsearch_url, request_timeout=120)


class EnrichmentElasticsearchStore:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.index = self.settings.elasticsearch_enrichment_index
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
        logger.info("Indexed %s enrichment documents into %s", indexed, self.index)
        return indexed

    def _bulk_from_sqlite(self) -> int:
        from elasticsearch.helpers import bulk

        mount = Path(self.settings.db_mount_dir)
        success, errors = bulk(
            self.es,
            self._iter_actions(mount),
            chunk_size=1000,
            request_timeout=120,
            raise_on_error=False,
        )
        if errors:
            logger.warning(
                "Enrichment bulk index reported %s errors (showing 3): %s",
                len(errors),
                errors[:3],
            )
        return int(success)

    def _iter_actions(self, mount: Path) -> Iterable[dict]:
        dbs = sorted(mount.glob("*.db"))
        if not dbs:
            logger.warning("No sqlite shards in %s to index enrichment from", mount)
            return
        wanted = [name for name, _ in TEXT_FIELDS]
        for db_path in dbs:
            shard = db_path.stem
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                tables = {
                    str(r[0])
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if "enrichment" not in tables or "frames" not in tables:
                    continue
                en_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(enrichment)")}
                select_fields = ["e.global_id AS global_id", "f.video_id AS video_id", "f.frame_idx AS frame_idx"]
                present = [name for name in wanted if name in en_cols]
                for name in present:
                    select_fields.append(f"e.{name} AS {name}")
                sql = f"""
                    SELECT {", ".join(select_fields)}
                    FROM enrichment e
                    JOIN frames f ON f.global_id = e.global_id
                    WHERE f.video_id IS NOT NULL AND f.frame_idx IS NOT NULL
                """
                for row in conn.execute(sql):
                    video_id = str(row["video_id"])
                    frame_idx = int(row["frame_idx"])
                    source = {
                        "global_id": int(row["global_id"]) if row["global_id"] is not None else None,
                        "video_id": video_id,
                        "group_id": video_id.split("_")[0] if "_" in video_id else video_id,
                        "frame_idx": frame_idx,
                        "shard": shard,
                    }
                    has_text = False
                    for name in present:
                        value = str(row[name] or "").strip()
                        source[name] = value
                        if value:
                            has_text = True
                    if not has_text:
                        continue
                    yield {
                        "_index": self.index,
                        "_id": f"{video_id}_{frame_idx}",
                        "_source": source,
                    }
            finally:
                conn.close()

    def search_paths(
        self,
        query: str,
        *,
        limit: int = 500,
        include_groups: Optional[Sequence[str]] = None,
        include_videos: Optional[Sequence[str]] = None,
        batch: Optional[int] = None,
    ) -> List[Tuple[float, str]]:
        cleaned = str(query or "").strip()
        if not cleaned:
            return []
        if not self.ping():
            return []
        if not self.es.indices.exists(index=self.index) or self.doc_count() == 0:
            return []

        filters = []
        groups = [str(g).strip() for g in (include_groups or []) if str(g).strip()]
        videos = [str(v).strip() for v in (include_videos or []) if str(v).strip()]
        if videos:
            filters.append({"terms": {"video_id": videos}})
        elif groups:
            filters.append({"terms": {"group_id": groups}})
        if batch in (1, 2):
            filters.append({"prefix": {"video_id": "L" if int(batch) == 1 else "K"}})

        fields = [f"{name}^{boost}" for name, boost in TEXT_FIELDS]
        body = {
            "bool": {
                "filter": filters,
                "must": [
                    {
                        "multi_match": {
                            "query": cleaned,
                            "fields": fields,
                            "type": "best_fields",
                            "operator": "or",
                            "tie_breaker": 0.2,
                        }
                    }
                ],
            }
        }
        cap = max(1, int(limit or 500))
        resp = self.es.search(
            index=self.index,
            size=cap,
            query=body,
            _source=["video_id", "frame_idx"],
        )
        out: List[Tuple[float, str]] = []
        seen = set()
        for hit in (resp.get("hits") or {}).get("hits") or []:
            src = hit.get("_source") or {}
            video_id = src.get("video_id")
            frame_idx = src.get("frame_idx")
            if video_id is None or frame_idx is None:
                continue
            key = (str(video_id), int(frame_idx))
            if key in seen:
                continue
            seen.add(key)
            out.append((float(hit.get("_score") or 0.0), f"{video_id}_{int(frame_idx):06d}.jpg"))
        return out


@lru_cache(maxsize=1)
def get_enrichment_es_store() -> EnrichmentElasticsearchStore:
    return EnrichmentElasticsearchStore()
