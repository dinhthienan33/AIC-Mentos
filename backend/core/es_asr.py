"""ASR search over transcript segments using Elasticsearch."""
from __future__ import annotations

import logging
import sqlite3
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "asr_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "seg_id": {"type": "integer"},
            "video_id": {"type": "keyword"},
            "group_id": {"type": "keyword"},
            "shard": {"type": "keyword"},
            "start_time": {"type": "float"},
            "end_time": {"type": "float"},
            "duration": {"type": "float"},
            "asr_text": {
                "type": "text",
                "analyzer": "asr_analyzer",
                "fields": {
                    "raw": {"type": "keyword", "ignore_above": 512},
                },
            },
            "asr_context": {
                "type": "text",
                "analyzer": "asr_analyzer",
            },
        }
    },
}


def _client(settings: Optional[Settings] = None):
    from elasticsearch import Elasticsearch

    settings = settings or get_settings()
    return Elasticsearch(settings.elasticsearch_url, request_timeout=120)


class AsrElasticsearchStore:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.index = self.settings.elasticsearch_asr_index
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
        logger.info("Indexed %s ASR documents into %s", indexed, self.index)
        return indexed

    def _bulk_from_sqlite(self) -> int:
        from elasticsearch.helpers import bulk

        mount = Path(self.settings.db_mount_dir)
        actions = list(self._iter_actions(mount))
        if not actions:
            logger.warning("No ASR documents to index from %s", mount)
            return 0
        logger.info("Indexing %s ASR documents from sqlite", len(actions))
        success, errors = bulk(
            self.es,
            actions,
            chunk_size=1000,
            request_timeout=120,
            raise_on_error=False,
            raise_on_exception=False,
        )
        if errors:
            logger.warning("ASR bulk index reported %s errors (showing 3): %s", len(errors), errors[:3])
        logger.info("ASR bulk success=%s prepared=%s", success, len(actions))
        return int(success)

    def _iter_actions(self, mount: Path) -> Iterable[dict]:
        dbs = sorted(mount.glob("*.db"))
        if not dbs:
            logger.warning("No sqlite shards in %s to index ASR from", mount)
            return
        for db_path in dbs:
            shard = db_path.stem
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                tables = {
                    str(r[0])
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='asr_results'"
                    )
                }
                if "asr_results" not in tables:
                    logger.warning("Skip %s: no asr_results table", db_path.name)
                    continue
                cols = {row[1] for row in conn.execute("PRAGMA table_info(asr_results)")}
                if "text" not in cols or "video_id" not in cols:
                    logger.warning("Skip %s: asr_results missing video_id/text", db_path.name)
                    continue
                select_parts = ["video_id", "text AS asr_text"]
                select_parts.append("id" if "id" in cols else "NULL AS id")
                select_parts.append("start_time" if "start_time" in cols else "0 AS start_time")
                select_parts.append("end_time" if "end_time" in cols else "0 AS end_time")
                rows = list(conn.execute(
                    f"""
                    SELECT {", ".join(select_parts)}
                    FROM asr_results
                    WHERE text IS NOT NULL
                      AND trim(text) != ''
                    ORDER BY video_id, start_time
                    """
                ))
                for idx, row in enumerate(rows):
                    video_id = str(row["video_id"] or "").strip()
                    if not video_id:
                        continue
                    context_parts = []
                    for neighbor_idx in range(max(0, idx - 1), min(len(rows), idx + 2)):
                        neighbor = rows[neighbor_idx]
                        if str(neighbor["video_id"] or "").strip() == video_id:
                            context_parts.append(str(neighbor["asr_text"] or "").strip())
                    start = float(row["start_time"] or 0.0)
                    end = float(row["end_time"] or start)
                    seg_id = row["id"]
                    if seg_id is None:
                        doc_id = f"{shard}_{video_id}_{int(round(start * 1000))}_{int(round(end * 1000))}"
                        seg_id_int = 0
                    else:
                        doc_id = f"{shard}_{int(seg_id)}"
                        seg_id_int = int(seg_id)
                    yield {
                        "_index": self.index,
                        "_id": doc_id,
                        "_source": {
                            "seg_id": seg_id_int,
                            "video_id": video_id,
                            "group_id": video_id.split("_")[0] if "_" in video_id else video_id,
                            "shard": shard,
                            "start_time": start,
                            "end_time": end,
                            "duration": max(0.0, end - start),
                            "asr_text": str(row["asr_text"]).strip(),
                            "asr_context": " ".join(part for part in context_parts if part),
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
    ) -> dict:
        filters = []
        if include_groups:
            filters.append({"terms": {"group_id": [str(g).strip() for g in include_groups if g]}})
        if include_videos:
            filters.append({"terms": {"video_id": [str(v).strip() for v in include_videos if v]}})
        q = query.strip().strip("\"'")
        analyzed = self.es.indices.analyze(
            index=self.index,
            analyzer="asr_analyzer",
            text=q,
        )
        tokens = [
            str(item.get("token") or "")
            for item in analyzed.get("tokens", [])
            if item.get("token")
        ][:12]
        phrase_slop = max(2, min(12, len(tokens) * 2))
        should = [
            {"match_phrase": {"asr_text": {"query": q, "boost": 10}}},
            {
                "match_phrase": {
                    "asr_context": {
                        "query": q,
                        "slop": phrase_slop,
                        "boost": 6,
                    }
                }
            },
        ]
        if len(tokens) >= 2:
            # Build ordered fuzzy phrases from the analyzer output. For longer
            # queries, permit up to 30% missing ASR tokens while preserving
            # order and proximity. This is query-length driven, not phrase-specific.
            max_missing = min(2, int(len(tokens) * 0.3))
            min_size = max(2, len(tokens) - max_missing)
            token_sequences = []
            for size in range(len(tokens), min_size - 1, -1):
                token_sequences.extend(combinations(tokens, size))
            for sequence in token_sequences[:32]:
                should.append({
                    "span_near": {
                        "clauses": [
                            (
                                {
                                    "span_multi": {
                                        "match": {
                                            "fuzzy": {
                                                "asr_context": {
                                                    "value": token,
                                                    "fuzziness": "AUTO",
                                                    "prefix_length": 1,
                                                    "max_expansions": 20,
                                                }
                                            }
                                        }
                                    }
                                }
                                if len(token) >= 5
                                else {"span_term": {"asr_context": token}}
                            )
                            for token in sequence
                        ],
                        "slop": phrase_slop,
                        "in_order": True,
                    }
                })
        elif tokens:
            should.append({
                "match": {
                    "asr_text": {
                        "query": q,
                        "fuzziness": "AUTO",
                        "prefix_length": 1,
                    }
                }
            })
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

    def search_grouped(
        self,
        query: str,
        *,
        top_k: int = 10,
        include_groups: Optional[List[str]] = None,
        include_videos: Optional[List[str]] = None,
        segments_per_video: int = 20,
    ) -> List[Dict]:
        """Return the top_k ASR segments, grouped by video in the response.

        Multiple matching segments may belong to the same video, so the
        number of returned video groups can be smaller than top_k.
        """
        if not (query or "").strip():
            return []
        self._ready()

        size = max(1, int(top_k))
        _ = segments_per_video  # legacy kw; segment budget is top_k
        highlight = {
            "fields": {"asr_text": {"number_of_fragments": 1, "fragment_size": 180}},
        }
        resp = self.es.search(
            index=self.index,
            size=size,
            query=self._text_query(
                query,
                include_groups=include_groups,
                include_videos=include_videos,
            ),
            highlight=highlight,
            sort=["_score"],
        )
        grouped: Dict[str, Dict] = {}
        order: List[str] = []
        for hit in (resp.get("hits") or {}).get("hits") or []:
            src = hit.get("_source") or {}
            video_id = str(src.get("video_id") or "")
            if not video_id:
                continue
            score = float(hit.get("_score") or 0.0)
            fragments = ((hit.get("highlight") or {}).get("asr_text") or [])
            start = float(src.get("start_time") or 0.0)
            end = float(src.get("end_time") or start)
            segment = {
                "start_time": start,
                "end_time": end,
                "duration": float(src.get("duration") or max(0.0, end - start)),
                "text": src.get("asr_text") or "",
                "highlight": fragments[0] if fragments else None,
                "score": score,
            }
            bucket = grouped.get(video_id)
            if bucket is None:
                grouped[video_id] = {
                    "video_name": video_id,
                    "score": score,
                    "segments": [segment],
                }
                order.append(video_id)
            else:
                bucket["score"] = max(float(bucket["score"]), score)
                bucket["segments"].append(segment)

        # Keep segments chronological within each video for playback UX.
        out: List[Dict] = []
        for video_id in order:
            bucket = grouped[video_id]
            bucket["segments"].sort(key=lambda s: float(s.get("start_time") or 0.0))
            out.append(bucket)
        return out

    def matching_video_ids(
        self,
        query: str,
        *,
        include_groups: Optional[List[str]] = None,
        include_videos: Optional[List[str]] = None,
        limit: int = 10_000,
    ) -> Set[str]:
        if not (query or "").strip():
            return set()
        self._ready()
        resp = self.es.search(
            index=self.index,
            size=0,
            query=self._text_query(
                query,
                include_groups=include_groups,
                include_videos=include_videos,
            ),
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


@lru_cache(maxsize=1)
def get_asr_es_store() -> AsrElasticsearchStore:
    return AsrElasticsearchStore()
