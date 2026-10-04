"""Qdrant + SigLIP2 retrieval engine."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from qdrant_client import QdrantClient

from .config import Settings, get_settings
from .db_repository import FrameDatabaseRepository
from .models import ModelService
from .snapshot import restore_snapshot_if_needed
from .utils import (
    filter_by_groups,
    filter_by_vid_id,
    synthetic_image_path,
)

logger = logging.getLogger(__name__)


class QdrantSearchEngine:
    def __init__(
        self,
        model_name: str = "siglip2",
        settings: Optional[Settings] = None,
        frame_repo: Optional[FrameDatabaseRepository] = None,
        model_service: Optional[ModelService] = None,
        qdrant_client: Optional[QdrantClient] = None,
    ):
        self.settings = settings or get_settings()
        self.model_name = model_name
        self.model_service = model_service or ModelService(model_name=model_name)
        self.frame_repo = frame_repo or FrameDatabaseRepository(settings=self.settings)
        self._client = qdrant_client
        self.collection = self.settings.qdrant_collection

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            self._client = self._connect_with_retry()
        return self._client

    def _connect_with_retry(self, attempts: int = 15, delay: float = 1.0) -> QdrantClient:
        last_error = None
        for idx in range(attempts):
            try:
                kwargs = {
                    "url": self.settings.qdrant_url,
                    "timeout": 60,
                    "prefer_grpc": False,
                    "check_compatibility": False,
                }
                if self.settings.qdrant_api_key:
                    kwargs["api_key"] = self.settings.qdrant_api_key
                client = QdrantClient(**kwargs)
                client.get_collections()
                return client
            except Exception as exc:
                last_error = exc
                if idx < attempts - 1:
                    time.sleep(delay)
        raise RuntimeError(f"Cannot connect to Qdrant at {self.settings.qdrant_url}: {last_error}")

    def ensure_ready(self, restore: Optional[bool] = None) -> dict:
        should_restore = self.settings.auto_restore_snapshot if restore is None else restore
        if should_restore:
            return restore_snapshot_if_needed(settings=self.settings)
        return {"skipped": True, "restored": False}

    def ensure_payload_indexes(self) -> None:
        """Cloud Qdrant requires a keyword payload index before filtering on video_id."""
        from qdrant_client.http import models as rest

        try:
            info = self.client.get_collection(self.collection)
            schema = info.payload_schema or {}
            if "video_id" in schema:
                return
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="video_id",
                field_schema=rest.PayloadSchemaType.KEYWORD,
                wait=True,
            )
            logger.info("Created Qdrant payload index video_id on %s", self.collection)
        except Exception as exc:
            logger.warning("Could not ensure video_id payload index: %s", exc)

    def search_hits(
        self,
        query: str,
        top_k: int = 10,
        exclude_groups: Optional[Sequence[str]] = None,
        exclude_vid_id: Optional[Sequence[str]] = None,
        include_groups: Optional[Sequence[str]] = None,
        include_videos: Optional[Sequence[str]] = None,
        score_threshold: float = 0.0,
        batch: Optional[int] = None,
        overfetch_factor: int = 4,
    ) -> List[Dict[str, Any]]:
        if not query or not str(query).strip():
            return []

        # Local GPU index path: embed + matmul on CUDA (no remote Qdrant RTT).
        from .local_index import get_local_index, use_local_index

        if use_local_index():
            qvec = self.model_service.embedding_tensor(query)
            return get_local_index().search(
                qvec,
                top_k=top_k,
                batch=batch,
                exclude_groups=exclude_groups,
                exclude_vid_id=exclude_vid_id,
                include_groups=include_groups,
                include_videos=include_videos,
                score_threshold=score_threshold,
                overfetch_factor=overfetch_factor,
            )

        need_post_filter = bool(
            exclude_groups or exclude_vid_id or include_groups or include_videos or batch in (1, 2)
        )
        fetch_k = int(top_k)
        if need_post_filter:
            fetch_k = max(int(top_k) * max(1, int(overfetch_factor)), int(top_k))
        qvec = self.model_service.embed_text_list(query)
        query_filter = None
        vids = [str(v).strip() for v in (include_videos or []) if str(v).strip()]
        groups = [str(g).strip() for g in (include_groups or []) if str(g).strip()]
        # Groups are payload `batch` values (L21_a, M01, N001, S01), not video prefixes.
        filtered_groups_in_qdrant = False
        if vids or groups:
            from qdrant_client.http import models as rest

            self.ensure_payload_indexes()
            must = []
            if groups:
                must.append(
                    rest.FieldCondition(
                        key="batch",
                        match=rest.MatchAny(any=groups),
                    )
                )
            if vids:
                must.append(
                    rest.FieldCondition(
                        key="video_id",
                        match=rest.MatchAny(any=vids),
                    )
                )
            query_filter = rest.Filter(must=must)
            filtered_groups_in_qdrant = bool(groups)
            fetch_k = int(top_k)
        try:
            response = self.client.query_points(
                collection_name=self.collection,
                query=qvec,
                limit=fetch_k,
                query_filter=query_filter,
                with_payload=["video_id", "frame_idx", "batch", "global_id"],
            )
        except Exception as exc:
            # Fallback: overfetch + post-filter if payload index missing/unavailable.
            if query_filter is None:
                raise
            logger.warning("Qdrant video_id filter failed, post-filtering: %s", exc)
            filtered_groups_in_qdrant = False
            response = self.client.query_points(
                collection_name=self.collection,
                query=qvec,
                limit=max(fetch_k * max(1, int(overfetch_factor)), fetch_k),
                query_filter=None,
                with_payload=["video_id", "frame_idx", "batch", "global_id"],
            )
        points = getattr(response, "points", response)

        hits: List[Dict[str, Any]] = []
        paths: List[str] = []
        scores: List[float] = []
        for point in points:
            payload = point.payload or {}
            video_id = payload.get("video_id")
            frame_idx = payload.get("frame_idx")
            if video_id is None or frame_idx is None:
                continue
            score = float(point.score)
            if score_threshold and score < score_threshold:
                continue
            path = synthetic_image_path(str(video_id), int(frame_idx))
            hits.append(
                {
                    "id": point.id,
                    "score": score,
                    "video_id": str(video_id),
                    "frame_idx": int(frame_idx),
                    "batch": payload.get("batch"),
                    "global_id": payload.get("global_id"),
                    "path": path,
                    "payload": payload,
                }
            )
            paths.append(path)
            scores.append(score)

        if groups and not filtered_groups_in_qdrant:
            allowed = set(groups)
            hits = [h for h in hits if str(h.get("batch") or "") in allowed]
            paths = [h["path"] for h in hits]
            scores = [h["score"] for h in hits]

        if exclude_groups:
            paths, scores = filter_by_groups(
                paths,
                scores,
                include_groups=None,
                exclude_groups=list(exclude_groups),
            )
            keep = set(paths)
            hits = [h for h in hits if h["path"] in keep]

        if exclude_vid_id or include_videos:
            paths = [h["path"] for h in hits]
            scores = [h["score"] for h in hits]
            paths, scores = filter_by_vid_id(
                paths,
                scores,
                include_videos=list(include_videos) if include_videos else None,
                exclude_videos=list(exclude_vid_id) if exclude_vid_id else None,
            )
            keep = set(paths)
            hits = [h for h in hits if h["path"] in keep]

        # Legacy AIC2025 scope: 1 => L*, 2 => K*. Skip it when the request
        # already scoped by group or video, otherwise M/N/S selections return nothing.
        if batch in (1, 2) and not groups and not vids:
            prefix = "L" if int(batch) == 1 else "K"
            hits = [h for h in hits if str(h.get("video_id", "")).startswith(prefix)]

        return hits[: max(1, int(top_k))]

    def search(
        self,
        exclude_groups: list = None,
        exclude_vid_id: list = None,
        query: str = None,
        candidate_k: int = 100,
        batch: Optional[int] = None,
        include_groups: list = None,
        include_videos: list = None,
        score_threshold: float = 0.0,
    ) -> Tuple[List[float], List[str]]:
        hits = self.search_hits(
            query=query,
            top_k=candidate_k,
            exclude_groups=exclude_groups,
            exclude_vid_id=exclude_vid_id,
            include_groups=include_groups,
            include_videos=include_videos,
            score_threshold=score_threshold,
            batch=batch,
        )
        scores = [float(h["score"]) for h in hits]
        paths = [h["path"] for h in hits]
        return scores, paths

    def hybrid_search(
        self,
        *,
        query: str,
        candidate_k: int,
        enrichment_queries: Optional[Sequence[str]] = None,
        exclude_groups: Optional[Sequence[str]] = None,
        exclude_vid_id: Optional[Sequence[str]] = None,
        include_groups: Optional[Sequence[str]] = None,
        include_videos: Optional[Sequence[str]] = None,
        score_threshold: float = 0.0,
        batch: Optional[int] = None,
    ) -> Tuple[List[float], List[str]]:
        """Fuse visual rank with frame-level enrichment rank.

        Long, detailed KIS queries often retrieve the correct frame at visual
        ranks 30-200. Enrichment descriptions capture the distinguishing
        objects/actions/OCR, so weighted reciprocal-rank fusion promotes those
        candidates without turning noisy text into a hard filter.
        """
        output_k = max(1, int(candidate_k))
        # 250 candidates covers the observed rescue range (visual ranks up to
        # ~200). Pulling 1000 for a normal top-20/100 request added noisy
        # enrichment matches and made ranking both slower and less stable.
        retrieval_k = min(1000, max(250, output_k))
        visual_scores, visual_paths = self.search(
            query=query,
            candidate_k=retrieval_k,
            exclude_groups=list(exclude_groups) if exclude_groups else None,
            exclude_vid_id=list(exclude_vid_id) if exclude_vid_id else None,
            include_groups=list(include_groups) if include_groups else None,
            include_videos=list(include_videos) if include_videos else None,
            score_threshold=score_threshold,
            batch=batch,
        )
        if not visual_paths:
            return [], []

        text_queries = []
        seen_queries = set()
        for value in enrichment_queries or (query,):
            cleaned = str(value or "").strip()
            key = normalize_rank_query(cleaned)
            if not key or key in seen_queries:
                continue
            seen_queries.add(key)
            text_queries.append(cleaned)

        text_ranks: List[Dict[str, int]] = []
        for text_query in text_queries[:2]:
            ranked = self.frame_repo.search_enrichment_paths(
                text_query,
                limit=retrieval_k,
                include_groups=include_groups,
                include_videos=include_videos,
                batch=batch,
            )
            text_ranks.append({path: rank for rank, (_, path) in enumerate(ranked, 1)})

        if not text_ranks:
            return visual_scores[:output_k], visual_paths[:output_k]

        video_text_rank: Dict[str, int] = {}
        from .utils import extract_video_info_v2

        for ranks in text_ranks:
            for path, rank in ranks.items():
                if rank > 100:
                    continue
                try:
                    _, video_id, _ = extract_video_info_v2(path)
                except Exception:
                    continue
                video_text_rank[video_id] = min(
                    rank, video_text_rank.get(video_id, rank)
                )

        rrf_k = 60.0
        fused: List[Tuple[float, int, float, str]] = []
        for visual_rank, (path, visual_score) in enumerate(
            zip(visual_paths, visual_scores), 1
        ):
            score = 1.0 / (rrf_k + visual_rank)
            matched_text_ranks = [
                ranks[path]
                for ranks in text_ranks
                if path in ranks and ranks[path] <= 100
            ]
            if matched_text_ranks:
                best_rank = min(matched_text_ranks)
                score += 0.60 / (rrf_k + best_rank)
                try:
                    _, video_id, _ = extract_video_info_v2(path)
                except Exception:
                    video_id = ""
                if video_id in video_text_rank:
                    score += 0.30 / (rrf_k + video_text_rank[video_id])
            elif visual_rank <= 20:
                # Some shards have incomplete enrichment. A strong visual
                # top-20 candidate must not regress merely because its text
                # row is absent.
                score += 0.010
            fused.append((score, visual_rank, float(visual_score), path))

        # Keep the strongest visual anchors stable. This bounds the downside
        # of noisy generated text while still allowing enrichment to improve
        # the uncertain tail of the top 20.
        protected_count = min(15, output_k, len(fused))
        protected = fused[:protected_count]
        tail = sorted(
            fused[protected_count:],
            key=lambda item: (-item[0], item[1]),
        )
        chosen = (protected + tail)[:output_k]
        return [item[0] for item in chosen], [item[3] for item in chosen]

    def asr_search_atlas(self, keywords: str, limit=None):
        top_k = limit or 10
        try:
            from .es_asr import get_asr_es_store

            store = get_asr_es_store()
            if store.ping():
                return store.search_grouped(keywords, top_k=top_k)
        except Exception as exc:
            logger.warning("ASR Elasticsearch search failed, sqlite fallback: %s", exc)
        return self.frame_repo.search_asr(keywords, limit=top_k)

    def od_search_atlas(self, keywords_list: List[str], all_path: List[str] = None, top_k=None, index_name: str = "od_index"):
        scores = self.frame_repo.od_scores(keywords_list, limit=top_k)
        items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        if all_path:
            allowed = set()
            for path in all_path:
                # path may be video id or synthetic keyframe path
                from .utils import extract_video_info_v2

                try:
                    _, video_id, _ = extract_video_info_v2(path)
                except Exception:
                    video_id = str(path)
                allowed.add(video_id)
            items = [(vid, score) for vid, score in items if vid in allowed]
        if isinstance(top_k, int):
            items = items[: max(1, top_k)]
        return items

    def final_search(
        self,
        query: str,
        top_k: int,
        score_threshold: float = 0.0,
        od_list: list = None,
        asr_list: list = None,
        oc_list: list = None,
        ocr_list: list = None,
        exclude_groups: list = None,
        exclude_vid_id: list = None,
        include_groups: list = None,
        include_videos: list = None,
        batch: Optional[int] = None,
    ) -> Tuple[List[float], List[str]]:
        scoped = set(include_videos) if include_videos else None

        vid_to_od_score: Dict[str, float] = {}
        if od_list:
            vid_to_od_score = dict(self.od_search_atlas(od_list, top_k=None))

        ocr_videos = set()
        if ocr_list:
            ocr_videos = self.frame_repo.matching_videos_ocr(ocr_list)
            scoped = ocr_videos if scoped is None else scoped & ocr_videos

        asr_videos = set()
        if asr_list:
            joined = " ".join(asr_list) if len(asr_list) > 1 else asr_list[0]
            asr_results = self.asr_search_atlas(joined, limit=10_000)
            asr_videos = {item["video_name"] for item in asr_results}
            scoped = asr_videos if scoped is None else scoped & asr_videos

        caption_videos = set()
        if oc_list:
            ids = self.frame_repo.matching_global_ids_caption(oc_list)
            caption_videos = self.frame_repo._videos_matching_enrichment_field("description_en", oc_list)
            caption_videos |= self.frame_repo._videos_matching_enrichment_field("description_vi", oc_list)
            _ = ids
            scoped = caption_videos if scoped is None else scoped & caption_videos

        if (ocr_list or asr_list or oc_list) and not scoped:
            return [], []

        base_scores, base_paths = self.search(
            exclude_groups=exclude_groups,
            exclude_vid_id=exclude_vid_id,
            query=query,
            candidate_k=max(int(top_k) * 5, int(top_k)),
            batch=batch,
            include_groups=include_groups,
            include_videos=list(scoped) if scoped is not None else include_videos,
            score_threshold=0.0,
        )

        from .utils import extract_video_info_v2

        scored: List[Tuple[float, str]] = []
        for path, base_score in zip(base_paths, base_scores):
            try:
                _, video_id, _ = extract_video_info_v2(path)
            except Exception:
                video_id = str(path)
            final_score = float(base_score) + 0.5 * float(vid_to_od_score.get(video_id, 0.0))
            if asr_list and video_id not in asr_videos:
                continue
            if ocr_list and video_id not in ocr_videos:
                continue
            if oc_list and video_id not in caption_videos:
                continue
            if score_threshold and final_score < score_threshold:
                continue
            scored.append((final_score, path))

        scored.sort(key=lambda x: x[0], reverse=True)
        scored = scored[: max(1, int(top_k))]
        return [s for s, _ in scored], [p for _, p in scored]


# Backwards-compatible alias used by api.py
SearchEngine = QdrantSearchEngine


def normalize_rank_query(value: str) -> str:
    """Cheap equality normalization used to avoid duplicate FTS queries."""
    return " ".join(str(value or "").lower().split())
