from typing import List, Optional, Sequence, Set, Union

import faiss
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, PayloadSchemaType

BATCH_1_GROUPS = [f"L{i:02d}" for i in range(21, 31)]
BATCH_2_GROUPS = [f"K{i:02d}" for i in range(1, 21)]

PAYLOAD_INDEX_FIELDS = ("batch_id", "video_id", "frame_id")


def create_qdrant_client(url: str, api_key: str = "", timeout: int = 120) -> QdrantClient:
    return QdrantClient(url=url, api_key=api_key or None, timeout=timeout)


def ensure_payload_indexes(client: QdrantClient, collection_name: str) -> None:
    """Create keyword indexes required for Qdrant payload filters."""
    if not client.collection_exists(collection_name):
        return
    for field_name in PAYLOAD_INDEX_FIELDS:
        try:
            client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass


def _batch_groups(batch: int) -> Optional[List[str]]:
    if batch == 1:
        return BATCH_1_GROUPS
    if batch == 2:
        return BATCH_2_GROUPS
    return None


def _resolve_batch_groups(batch: int, include_groups: Optional[List[str]]) -> Optional[List[str]]:
    batch_groups = _batch_groups(batch)
    if include_groups and batch_groups:
        allowed = sorted(set(batch_groups) & set(include_groups))
        return allowed or None
    if include_groups:
        return include_groups
    return batch_groups


def _parse_seen_key(key: str) -> Optional[tuple[str, str]]:
    if not key or "_" not in key:
        return None
    video_id, _, frame_id = key.rpartition("_")
    if not video_id or not frame_id:
        return None
    return video_id, frame_id


def build_search_filter(
    *,
    batch: int = 1,
    include_groups: Optional[List[str]] = None,
    exclude_groups: Optional[List[str]] = None,
    include_videos: Optional[List[str]] = None,
    exclude_vid_id: Optional[List[str]] = None,
    exclude_keys: Optional[Set[str]] = None,
) -> Optional[Filter]:
    """Build a Qdrant filter for pre-search payload filtering and dedupe."""
    must: List[FieldCondition] = []
    must_not: List[Union[FieldCondition, Filter]] = []

    allowed_groups = _resolve_batch_groups(batch, include_groups)
    if allowed_groups:
        must.append(FieldCondition(key="batch_id", match=MatchAny(any=allowed_groups)))
    elif include_groups is not None or _batch_groups(batch) is not None:
        must.append(FieldCondition(key="batch_id", match=MatchAny(any=["__NO_MATCH__"])))

    if include_videos:
        must.append(FieldCondition(key="video_id", match=MatchAny(any=include_videos)))
    if exclude_groups:
        must_not.append(FieldCondition(key="batch_id", match=MatchAny(any=exclude_groups)))
    if exclude_vid_id:
        must_not.append(FieldCondition(key="video_id", match=MatchAny(any=exclude_vid_id)))

    if exclude_keys:
        for key in exclude_keys:
            parsed = _parse_seen_key(key)
            if not parsed:
                continue
            video_id, frame_id = parsed
            must_not.append(
                Filter(
                    must=[
                        FieldCondition(key="video_id", match=MatchValue(value=video_id)),
                        FieldCondition(key="frame_id", match=MatchValue(value=frame_id)),
                    ]
                )
            )

    if not must and not must_not:
        return None
    return Filter(must=must or None, must_not=must_not or None)


def normalize_vector(vector: Union[np.ndarray, Sequence[float]]) -> List[float]:
    arr = np.ascontiguousarray(np.asarray(vector, dtype=np.float32).reshape(1, -1))
    faiss.normalize_L2(arr)
    return arr[0].tolist()


def search_similar(
    client: QdrantClient,
    collection_name: str,
    query_vector: Union[np.ndarray, Sequence[float]],
    limit: int = 10,
    query_filter: Optional[Filter] = None,
    score_threshold: Optional[float] = None,
):
    if not client.collection_exists(collection_name):
        raise ValueError(f"Collection '{collection_name}' does not exist on Qdrant")

    response = client.query_points(
        collection_name=collection_name,
        query=normalize_vector(query_vector),
        limit=limit,
        query_filter=query_filter,
        score_threshold=score_threshold,
        with_payload=True,
    )
    return response.points
