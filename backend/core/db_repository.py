"""SQLite repositories backed by S3 frame_db shards."""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .config import Settings, get_settings
from .s3_client import S3Storage, get_s3_storage

logger = logging.getLogger(__name__)

_ENRICHMENT_STOPWORDS = {
    # Vietnamese function words add noise to long natural-language queries.
    "ai", "bị", "bởi", "các", "cái", "cho", "chỉ", "chiếc", "có", "của",
    "cùng", "đang", "đây", "để", "đến", "đó", "được", "giữa", "gì", "gần",
    "hai", "hoặc", "khi", "không", "kia", "là", "lại", "lúc", "mà", "một",
    "này", "những", "như", "nhiều", "phía", "qua", "ra", "rồi", "sau", "sẽ",
    "theo", "thì", "trên", "trong", "trước", "từ", "và", "vào", "về", "với",
    # English equivalents, because translated queries are searched as well.
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "for",
    "from", "has", "have", "in", "into", "is", "it", "of", "on", "or", "that",
    "the", "then", "there", "this", "to", "was", "were", "with",
}


def normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


@dataclass
class FrameRecord:
    global_id: int
    video_id: str
    frame_idx: int
    pts_time: float
    fps: float
    image_path: str
    shard: str
    shot_id: Optional[int] = None
    asr_context: str = ""


@dataclass
class ASRSegmentRecord:
    video_id: str
    start_time: float
    end_time: float
    text: str
    score: float = 1.0


class FrameDatabaseRepository:
    """Lazy-mounted SQLite DBs from S3 for ASR/OCR/OD/caption/frame metadata."""

    def __init__(
        self,
        storage: Optional[S3Storage] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        if storage is not None:
            self.storage = storage
        elif self.settings.skip_db_mount or self.settings.keyframe_source == "hf":
            self.storage = None
        else:
            self.storage = get_s3_storage()
        self.mount_dir = Path(self.settings.db_mount_dir)
        self.mount_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._local_paths: Dict[str, Path] = {}
        self._video_to_shard: Dict[str, str] = {}
        self._shards: Optional[List[str]] = None

    def list_shards(self) -> List[str]:
        with self._lock:
            if self._shards is None:
                local = sorted(
                    p.stem
                    for p in self.mount_dir.glob("*.db")
                    if p.is_file() and p.stat().st_size > 0
                )
                if local:
                    discovered = local
                elif self.storage is None or self.settings.keyframe_source == "hf":
                    from .hf_frames import get_hf_frame_store

                    discovered = get_hf_frame_store().list_shards()
                else:
                    discovered = self.storage.list_database_shards()
                allowed = tuple(getattr(self.settings, "frame_db_shards", ()) or ())
                if allowed:
                    discovered = [s for s in discovered if s in allowed]
                self._shards = discovered
            return list(self._shards)

    def shards_for_group(self, group_id: str) -> List[str]:
        prefix = f"{group_id}_"
        return [s for s in self.list_shards() if s.startswith(prefix)]

    def shards_for_video(self, video_id: str) -> List[str]:
        with self._lock:
            if video_id in self._video_to_shard:
                return [self._video_to_shard[video_id]]
        return self.guess_shards_for_video(video_id)

    def guess_shards_for_video(self, video_id: str) -> List[str]:
        """Infer shard names from video_id without opening SQLite."""
        group = (video_id or "").split("_")[0]
        if not group:
            return []
        fallback = [f"{group}_a"]
        try:
            matched = self.shards_for_group(group)
        except Exception as exc:
            logger.warning("Shard list failed for %s: %s", group, exc)
            return fallback
        return matched or fallback

    def ensure_db(self, shard: str) -> Path:
        with self._lock:
            cached = self._local_paths.get(shard)
            if cached and cached.exists():
                return cached

            local_path = self.mount_dir / f"{shard}.db"
            if not local_path.exists():
                if self.settings.skip_db_mount:
                    raise RuntimeError(
                        f"skip_db_mount=true; refusing to download SQLite shard {shard} from S3"
                    )
                key = self.storage.database_key(shard)
                logger.info("Mounting SQLite shard from S3: %s -> %s", key, local_path)
                self.storage.download_file(key, str(local_path))
            self._local_paths[shard] = local_path
            return local_path

    @contextmanager
    def connect(self, shard: str):
        path = self.ensure_db(shard)
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def build_video_shard_index(self, shards: Optional[Sequence[str]] = None) -> Dict[str, str]:
        """Populate video_id -> shard map. Expensive; call lazily or at warmup."""
        target_shards = list(shards or self.list_shards())
        mapping: Dict[str, str] = {}
        for shard in target_shards:
            try:
                with self.connect(shard) as conn:
                    rows = conn.execute("SELECT DISTINCT video_id FROM frames").fetchall()
                    for row in rows:
                        video_id = row["video_id"]
                        mapping[video_id] = shard
                        self._video_to_shard[video_id] = shard
            except Exception as exc:
                logger.warning("Failed indexing shard %s: %s", shard, exc)
        return mapping

    def resolve_shard(self, video_id: str) -> Optional[str]:
        with self._lock:
            if video_id in self._video_to_shard:
                return self._video_to_shard[video_id]

        for shard in self.shards_for_video(video_id):
            try:
                with self.connect(shard) as conn:
                    row = conn.execute(
                        "SELECT 1 AS ok FROM frames WHERE video_id = ? LIMIT 1",
                        (video_id,),
                    ).fetchone()
                    if row:
                        with self._lock:
                            self._video_to_shard[video_id] = shard
                        return shard
            except Exception as exc:
                logger.warning("Shard probe failed for %s/%s: %s", shard, video_id, exc)
        return None

    def get_frame(self, video_id: str, frame_idx: int) -> Optional[FrameRecord]:
        shard = self.resolve_shard(video_id)
        candidates = [shard] if shard else self.shards_for_video(video_id)
        for candidate in candidates:
            if not candidate:
                continue
            try:
                with self.connect(candidate) as conn:
                    row = conn.execute(
                        """
                        SELECT global_id, video_id, frame_idx, pts_time, fps, image_path, shot_id, asr_context
                        FROM frames
                        WHERE video_id = ? AND frame_idx = ?
                        LIMIT 1
                        """,
                        (video_id, int(frame_idx)),
                    ).fetchone()
                    if row:
                        with self._lock:
                            self._video_to_shard[video_id] = candidate
                        return FrameRecord(
                            global_id=int(row["global_id"]),
                            video_id=row["video_id"],
                            frame_idx=int(row["frame_idx"]),
                            pts_time=float(row["pts_time"]),
                            fps=float(row["fps"] or self.settings.default_fps),
                            image_path=row["image_path"],
                            shard=candidate,
                            shot_id=row["shot_id"],
                            asr_context=row["asr_context"] or "",
                        )
            except Exception as exc:
                logger.warning("get_frame failed %s %s in %s: %s", video_id, frame_idx, candidate, exc)
        return None

    def get_video_fps(self, video_id: str) -> float:
        shard = self.resolve_shard(video_id)
        candidates = [shard] if shard else self.shards_for_video(video_id)
        for candidate in candidates:
            if not candidate:
                continue
            try:
                with self.connect(candidate) as conn:
                    row = conn.execute(
                        "SELECT fps FROM frames WHERE video_id = ? LIMIT 1",
                        (video_id,),
                    ).fetchone()
                    if row and row["fps"]:
                        return float(row["fps"])
            except Exception:
                continue
        return float(self.settings.default_fps or 25.0)

    def search_asr(self, query: str, limit: int = 10) -> List[Dict]:
        """Return [{video_name, score, segments:[{start_time,end_time,duration,text}]}]"""
        if not query or not query.strip():
            return []

        keywords = [normalize_text(k) for k in re.split(r"\s+", query.strip()) if k]
        aggregated: Dict[str, Dict] = {}

        for shard in self.list_shards():
            try:
                with self.connect(shard) as conn:
                    rows = conn.execute(
                        """
                        SELECT video_id, start_time, end_time, text
                        FROM asr_results
                        ORDER BY start_time
                        """
                    ).fetchall()
                    for row in rows:
                        text = row["text"] or ""
                        score = _keyword_score(text, keywords)
                        if score <= 0:
                            # Also allow substring match on raw text for accented queries.
                            raw_q = query.strip().lower()
                            if raw_q and raw_q in text.lower():
                                score = 1.0
                            else:
                                continue
                        video_id = row["video_id"]
                        bucket = aggregated.setdefault(
                            video_id,
                            {"video_name": video_id, "score": 0.0, "segments": []},
                        )
                        bucket["score"] = max(bucket["score"], score)
                        start = float(row["start_time"])
                        end = float(row["end_time"])
                        bucket["segments"].append(
                            {
                                "start_time": start,
                                "end_time": end,
                                "duration": max(0.0, end - start),
                                "text": text,
                            }
                        )
            except Exception as exc:
                logger.warning("ASR search failed on %s: %s", shard, exc)

        results = sorted(aggregated.values(), key=lambda x: x["score"], reverse=True)
        return results[: max(1, int(limit))]

    def matching_videos_asr(self, keywords: Sequence[str]) -> Set[str]:
        if not keywords:
            return set()
        query = " ".join(str(k) for k in keywords if k and str(k).strip())
        if not query:
            return set()
        try:
            from .es_asr import get_asr_es_store

            store = get_asr_es_store()
            if store.ping():
                return store.matching_video_ids(query)
        except Exception as exc:
            logger.warning("ASR Elasticsearch matching failed, sqlite fallback: %s", exc)
        hits = self.search_asr(query, limit=10_000)
        return {h["video_name"] for h in hits}

    def matching_videos_ocr(self, keywords: Sequence[str]) -> Set[str]:
        cleaned = [str(k).strip() for k in keywords if k and str(k).strip()]
        if not cleaned:
            return set()
        try:
            from .es_ocr import get_ocr_es_store

            store = get_ocr_es_store()
            if store.ping():
                videos: Set[str] = set()
                for keyword in cleaned:
                    videos |= store.matching_video_ids(keyword, strict=True)
                return videos
        except Exception as exc:
            logger.warning("OCR Elasticsearch matching failed, sqlite fallback: %s", exc)
        return self._videos_matching_enrichment_field("ocr_quotes_concat", cleaned)

    def od_scores(self, keywords: Sequence[str], limit: Optional[int] = None) -> Dict[str, float]:
        """Map video_id -> score using ES od_entities (entities_concat), sqlite FTS fallback."""
        cleaned = [str(k).strip() for k in keywords if k and str(k).strip()]
        if not cleaned:
            return {}

        try:
            from .es_od import get_od_es_store

            store = get_od_es_store()
            if store.ping():
                scores: Dict[str, float] = {}
                per = max(1, int(limit)) if isinstance(limit, int) else 10_000
                for keyword in cleaned:
                    for video_id, es_score in store.video_scores(
                        keyword, limit=per, strict=True
                    ).items():
                        # Count keyword hits (parity with sqlite _keyword_score) while
                        # keeping ES relevance as a small tie-breaker.
                        prev = scores.get(video_id, 0.0)
                        hit_count = int(prev) + 1
                        frac = min(0.99, float(es_score) / (float(es_score) + 10.0))
                        scores[video_id] = float(hit_count) + frac
                if scores:
                    if isinstance(limit, int):
                        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                        return dict(ranked[: max(1, int(limit))])
                    return scores
        except Exception as exc:
            logger.warning("OD Elasticsearch scoring failed, sqlite fallback: %s", exc)

        return self._od_scores_sqlite(cleaned, limit=limit)

    def _od_scores_sqlite(
        self, cleaned: Sequence[str], limit: Optional[int] = None
    ) -> Dict[str, float]:
        normed = [normalize_text(k) for k in cleaned if k]
        if not normed:
            return {}
        scores: Dict[str, float] = {}
        match_expr = " OR ".join(_fts_quote(k) for k in normed)

        for shard in self.list_shards():
            try:
                with self.connect(shard) as conn:
                    rows = []
                    try:
                        sql = """
                            SELECT fr.video_id AS video_id, e.entities_concat AS entities
                            FROM enrichment_fts
                            JOIN enrichment e ON e.global_id = enrichment_fts.global_id
                            JOIN frames fr ON fr.global_id = enrichment_fts.global_id
                            WHERE enrichment_fts MATCH ?
                        """
                        if isinstance(limit, int):
                            sql += f" LIMIT {max(1, int(limit))}"
                        rows = conn.execute(sql, (match_expr,)).fetchall()
                    except sqlite3.Error:
                        where = " OR ".join(["lower(entities_concat) LIKE ?" for _ in normed])
                        params = [f"%{k}%" for k in normed]
                        sql = f"""
                            SELECT fr.video_id AS video_id, e.entities_concat AS entities
                            FROM enrichment e
                            JOIN frames fr ON fr.global_id = e.global_id
                            WHERE {where}
                        """
                        rows = conn.execute(sql, params).fetchall()

                    for row in rows:
                        video_id = row["video_id"]
                        entities = row["entities"] or ""
                        score = _keyword_score(entities, normed)
                        if score <= 0:
                            score = 1.0
                        scores[video_id] = max(scores.get(video_id, 0.0), score)
            except Exception as exc:
                logger.warning("OD search failed on %s: %s", shard, exc)
        return scores

    def search_od_paths(self, keywords: Sequence[str], limit: int = 10) -> List[str]:
        """Standalone OD search: keyframe paths whose entities_concat match keywords."""
        cleaned = [str(k).strip() for k in keywords if k and str(k).strip()]
        if cleaned:
            try:
                from .es_od import get_od_es_store

                store = get_od_es_store()
                if store.ping():
                    paths: List[str] = []
                    seen = set()
                    per = max(1, int(limit or 10))
                    for keyword in cleaned:
                        for hit in store.search(keyword, top_k=per, strict=True):
                            path = hit.get("image_path")
                            if path and path not in seen:
                                seen.add(path)
                                paths.append(path)
                            if len(paths) >= per:
                                return paths[:per]
                    if paths:
                        return paths[:per]
            except Exception as exc:
                logger.warning("OD Elasticsearch path search failed, sqlite fallback: %s", exc)
        return self._search_enrichment_paths(
            "entities_concat", keywords, limit=limit, fts_column="entities_concat"
        )
    def search_ocr_paths(self, keywords: Sequence[str], limit: int = 10) -> List[str]:
        cleaned = [str(k).strip() for k in keywords if k and str(k).strip()]
        if cleaned:
            try:
                from .es_ocr import get_ocr_es_store

                store = get_ocr_es_store()
                if store.ping():
                    paths: List[str] = []
                    seen = set()
                    per = max(1, int(limit or 10))
                    for keyword in cleaned:
                        for hit in store.search(keyword, top_k=per, strict=True):
                            path = hit.get("image_path")
                            if path and path not in seen:
                                seen.add(path)
                                paths.append(path)
                            if len(paths) >= per:
                                return paths[:per]
                    if paths:
                        return paths[:per]
            except Exception as exc:
                logger.warning("OCR Elasticsearch path search failed, sqlite fallback: %s", exc)
        return self._search_enrichment_paths(
            "ocr_quotes_concat", keywords, limit=limit, fts_column="ocr_quotes_concat"
        )

    def search_enrichment_paths(
        self,
        query: str,
        *,
        limit: int = 500,
        include_groups: Optional[Sequence[str]] = None,
        include_videos: Optional[Sequence[str]] = None,
        batch: Optional[int] = None,
    ) -> List[Tuple[float, str]]:
        """Rank frames by all searchable enrichment fields.

        Elasticsearch is used when the enrichment index is populated; sqlite FTS
        is the fallback. The caller fuses this rank with visual retrieval.
        """
        tokens = _enrichment_query_tokens(query)
        if not tokens:
            return []

        cap = max(1, int(limit or 500))
        try:
            from .es_enrichment import get_enrichment_es_store

            store = get_enrichment_es_store()
            if store.ping() and store.doc_count() > 0:
                return store.search_paths(
                    query,
                    limit=cap,
                    include_groups=include_groups,
                    include_videos=include_videos,
                    batch=batch,
                )
        except Exception as exc:
            logger.warning("Enrichment Elasticsearch search failed, sqlite fallback: %s", exc)

        match_expr = " OR ".join(_fts_quote(token) for token in tokens)
        groups = {str(group).strip() for group in (include_groups or []) if str(group).strip()}
        videos = {str(video).strip() for video in (include_videos or []) if str(video).strip()}

        if videos:
            shards = sorted(
                {
                    shard
                    for video_id in videos
                    for shard in self.guess_shards_for_video(video_id)
                }
            )
        elif groups:
            shards = sorted(
                {
                    shard
                    for group_id in groups
                    for shard in self.shards_for_group(group_id)
                }
            )
        else:
            shards = self.list_shards()

        ranked: List[Tuple[float, str, int]] = []
        for shard in shards:
            try:
                with self.connect(shard) as conn:
                    where = ["enrichment_fts MATCH ?"]
                    params: List[object] = [match_expr]
                    if videos:
                        placeholders = ",".join("?" for _ in videos)
                        where.append(f"fr.video_id IN ({placeholders})")
                        params.extend(sorted(videos))
                    elif groups:
                        group_clauses = []
                        for group_id in sorted(groups):
                            group_clauses.append("fr.video_id LIKE ?")
                            params.append(f"{group_id}_%")
                        where.append(f"({' OR '.join(group_clauses)})")
                    if batch in (1, 2):
                        where.append("fr.video_id LIKE ?")
                        params.append("L%" if int(batch) == 1 else "K%")
                    params.append(cap)
                    rows = conn.execute(
                        f"""
                        SELECT fr.video_id AS video_id,
                               fr.frame_idx AS frame_idx,
                               bm25(
                                   enrichment_fts,
                                   0.0, 2.0, 2.0, 1.2, 1.7,
                                   1.2, 0.1, 1.4, 1.6
                               ) AS fts_rank
                        FROM enrichment_fts
                        JOIN frames fr ON fr.global_id = enrichment_fts.global_id
                        WHERE {' AND '.join(where)}
                        ORDER BY fts_rank
                        LIMIT ?
                        """,
                        params,
                    ).fetchall()
                    for row in rows:
                        ranked.append(
                            (
                                float(row["fts_rank"]),
                                str(row["video_id"]),
                                int(row["frame_idx"]),
                            )
                        )
            except sqlite3.Error as exc:
                logger.warning("Enrichment FTS search failed on %s: %s", shard, exc)

        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        out: List[Tuple[float, str]] = []
        seen = set()
        for fts_rank, video_id, frame_idx in ranked:
            key = (video_id, frame_idx)
            if key in seen:
                continue
            seen.add(key)
            # FTS5 BM25 is lower-is-better and normally negative.
            relevance = max(0.0, -float(fts_rank))
            out.append((relevance, f"{video_id}_{frame_idx:06d}.jpg"))
            if len(out) >= cap:
                break
        return out

    def _search_enrichment_paths(
        self,
        field: str,
        keywords: Sequence[str],
        *,
        limit: int = 10,
        fts_column: Optional[str] = None,
    ) -> List[str]:
        cleaned = [normalize_text(k) for k in keywords if k and str(k).strip()]
        if not cleaned:
            return []
        scored: List[Tuple[float, str, int]] = []
        col = fts_column or field
        match_expr = " OR ".join(f"{col}:{_fts_quote(k)}" for k in cleaned)
        like_params = [f"%{k}%" for k in cleaned]
        like_where = " OR ".join([f"lower(e.{field}) LIKE ?" for _ in cleaned])
        for shard in self.list_shards():
            try:
                with self.connect(shard) as conn:
                    rows = []
                    try:
                        rows = conn.execute(
                            """
                            SELECT fr.video_id AS video_id, fr.frame_idx AS frame_idx, e.{field} AS text
                            FROM enrichment_fts
                            JOIN enrichment e ON e.global_id = enrichment_fts.global_id
                            JOIN frames fr ON fr.global_id = enrichment_fts.global_id
                            WHERE enrichment_fts MATCH ?
                            """.format(field=field),
                            (match_expr,),
                        ).fetchall()
                    except sqlite3.Error:
                        rows = conn.execute(
                            f"""
                            SELECT fr.video_id AS video_id, fr.frame_idx AS frame_idx, e.{field} AS text
                            FROM enrichment e
                            JOIN frames fr ON fr.global_id = e.global_id
                            WHERE {like_where}
                            """,
                            like_params,
                        ).fetchall()
                    for row in rows:
                        text = row["text"] or ""
                        score = _keyword_score(text, cleaned) or 1.0
                        scored.append((score, str(row["video_id"]), int(row["frame_idx"])))
            except Exception as exc:
                logger.warning("Enrichment path search failed on %s.%s: %s", shard, field, exc)
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        out: List[str] = []
        seen = set()
        cap = max(1, int(limit or 10))
        for _, video_id, frame_idx in scored:
            key = (video_id, frame_idx)
            if key in seen:
                continue
            seen.add(key)
            out.append(f"keyframes/{video_id}/{frame_idx:06d}.jpg")
            if len(out) >= cap:
                break
        return out

    def filter_paths_by_od(self, keywords: Sequence[str], paths: Sequence[str], limit: Optional[int] = None) -> List[str]:
        """Filter origin paths by OD, or search globally when no paths are given."""
        if not paths:
            return self.search_od_paths(keywords, limit=limit or 10)
        cleaned = [normalize_text(k) for k in keywords if k and str(k).strip()]
        if not cleaned:
            return list(paths)[: max(1, int(limit or len(paths)))]

        video_ids = [_path_to_video_id(path) for path in paths]
        matched_videos = set(self.od_scores(cleaned).keys())
        matched, unmatched = [], []
        for path, video_id in zip(paths, video_ids):
            if video_id in matched_videos:
                matched.append(path)
            else:
                unmatched.append(path)
        merged = matched + unmatched
        if limit:
            return merged[: max(1, int(limit))]
        return merged

    def filter_paths_by_ocr(self, keywords: Sequence[str], paths: Sequence[str], limit: Optional[int] = None) -> List[str]:
        if not paths:
            return self.search_ocr_paths(keywords, limit=limit or 10)
        cleaned = [normalize_text(k) for k in keywords if k and str(k).strip()]
        if not cleaned:
            return list(paths)

        matched_videos = self.matching_videos_ocr(keywords)
        matched, unmatched = [], []
        for path in paths:
            video_id = _path_to_video_id(path)
            if video_id in matched_videos:
                matched.append(path)
            else:
                unmatched.append(path)
        merged = matched + unmatched
        if limit:
            return merged[: max(1, int(limit))]
        return merged

    def filter_paths_by_asr(self, keywords: Sequence[str], paths: Sequence[str]) -> List[str]:
        if not paths:
            return []
        if not keywords:
            return list(paths)
        matched_videos = self.matching_videos_asr(keywords)
        matched, unmatched = [], []
        for path in paths:
            video_id = _path_to_video_id(path)
            if video_id in matched_videos:
                matched.append(path)
            else:
                unmatched.append(path)
        return matched + unmatched

    def matching_global_ids_ocr(self, keywords: Sequence[str]) -> Set[int]:
        return self._global_ids_matching_enrichment_field("ocr_quotes_concat", keywords)

    def matching_global_ids_caption(self, keywords: Sequence[str]) -> Set[int]:
        cleaned = [normalize_text(k) for k in keywords if k and str(k).strip()]
        if not cleaned:
            return set()
        ids: Set[int] = set()
        match_expr = " OR ".join(_fts_quote(k) for k in cleaned)
        for shard in self.list_shards():
            try:
                with self.connect(shard) as conn:
                    try:
                        rows = conn.execute(
                            """
                            SELECT global_id FROM enrichment_fts
                            WHERE enrichment_fts MATCH ?
                            """,
                            (match_expr,),
                        ).fetchall()
                        ids.update(int(r["global_id"]) for r in rows)
                    except sqlite3.Error:
                        where = " OR ".join(
                            [
                                "lower(description_en) LIKE ? OR lower(description_vi) LIKE ?"
                                for _ in cleaned
                            ]
                        )
                        params: List[str] = []
                        for k in cleaned:
                            params.extend([f"%{k}%", f"%{k}%"])
                        rows = conn.execute(
                            f"SELECT global_id FROM enrichment WHERE {where}",
                            params,
                        ).fetchall()
                        ids.update(int(r["global_id"]) for r in rows)
            except Exception as exc:
                logger.warning("Caption search failed on %s: %s", shard, exc)
        return ids

    def _videos_matching_enrichment_field(self, field: str, keywords: Sequence[str]) -> Set[str]:
        cleaned = [normalize_text(k) for k in keywords if k and str(k).strip()]
        if not cleaned:
            return set()
        videos: Set[str] = set()
        for shard in self.list_shards():
            try:
                with self.connect(shard) as conn:
                    where = " OR ".join([f"lower(e.{field}) LIKE ?" for _ in cleaned])
                    params = [f"%{k}%" for k in cleaned]
                    rows = conn.execute(
                        f"""
                        SELECT DISTINCT fr.video_id AS video_id
                        FROM enrichment e
                        JOIN frames fr ON fr.global_id = e.global_id
                        WHERE {where}
                        """,
                        params,
                    ).fetchall()
                    videos.update(r["video_id"] for r in rows)
            except Exception as exc:
                logger.warning("Enrichment field search failed on %s.%s: %s", shard, field, exc)
        return videos

    def _global_ids_matching_enrichment_field(self, field: str, keywords: Sequence[str]) -> Set[int]:
        cleaned = [normalize_text(k) for k in keywords if k and str(k).strip()]
        if not cleaned:
            return set()
        ids: Set[int] = set()
        for shard in self.list_shards():
            try:
                with self.connect(shard) as conn:
                    where = " OR ".join([f"lower({field}) LIKE ?" for _ in cleaned])
                    params = [f"%{k}%" for k in cleaned]
                    rows = conn.execute(
                        f"SELECT global_id FROM enrichment WHERE {where}",
                        params,
                    ).fetchall()
                    ids.update(int(r["global_id"]) for r in rows)
            except Exception as exc:
                logger.warning("Enrichment id search failed on %s.%s: %s", shard, field, exc)
        return ids

    def cleanup_mounts(self) -> None:
        with self._lock:
            for path in list(self._local_paths.values()):
                try:
                    if path.exists():
                        path.unlink()
                except OSError as exc:
                    logger.warning("Failed deleting mount %s: %s", path, exc)
            self._local_paths.clear()


def _fts_quote(term: str) -> str:
    safe = term.replace('"', " ")
    return f'"{safe}"'


def _enrichment_query_tokens(query: str, max_tokens: int = 64) -> List[str]:
    """Extract stable FTS terms while preserving Vietnamese diacritics."""
    tokens: List[str] = []
    seen = set()
    for token in re.findall(r"[^\W_]+", str(query or "").lower(), flags=re.UNICODE):
        if len(token) < 2 or token.isdigit() or token in _ENRICHMENT_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= max_tokens:
            break
    return tokens


def _keyword_score(text: str, keywords: Sequence[str]) -> float:
    hay = normalize_text(text)
    if not hay or not keywords:
        return 0.0
    hits = sum(1 for k in keywords if k in hay)
    return float(hits)


def _path_to_video_id(path: str) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    p = Path(raw)
    parent = p.parent.name
    if re.match(r"^[A-Za-z]+\d+_V\d+$", parent, re.I):
        return parent
    name = p.name
    parts = name.split("_")
    if len(parts) >= 2 and parts[1].startswith("V"):
        return f"{parts[0]}_{parts[1].split('.')[0]}"
    stem = name.split(".")[0]
    if re.match(r"^[A-Za-z]+\d+_V\d+$", stem, re.I):
        return stem
    return stem or name
