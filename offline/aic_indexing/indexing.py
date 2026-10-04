from concurrent.futures import ThreadPoolExecutor
from threading import Thread
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from tqdm import tqdm

from .jina_clip import embed_images_bytes, get_native_dim, load_model, normalize_l2
from .metadata import build_keyframe_metadata, stable_point_id
from .s3_utils import ensure_bucket_exists, list_s3_image_objects_parallel, parse_s3_location

S3Object = Tuple[str, str, str]

S3_DOWNLOAD_WORKERS = 48
CHUNK_SIZE = 1000


def load_qdrant_index_state(
    qdrant_client: QdrantClient,
    collection_name: str,
) -> Dict[str, Dict[str, Any]]:
    """Map s3_uri -> {point_id, etag, model, embedding_dim} từ Qdrant."""
    if not qdrant_client.collection_exists(collection_name):
        return {}

    state: Dict[str, Dict[str, Any]] = {}
    offset = None

    while True:
        records, offset = qdrant_client.scroll(
            collection_name=collection_name,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for record in records:
            payload = record.payload or {}
            s3_uri = payload.get("s3_uri")
            if not s3_uri:
                continue
            state[s3_uri] = {
                "point_id": record.id,
                "etag": payload.get("s3_etag", ""),
                "model": payload.get("model", ""),
                "embedding_dim": payload.get("embedding_dim"),
            }

        if offset is None:
            break

    return state


def select_objects_to_index(
    s3_objects: List[S3Object],
    bucket_name: str,
    index_state: Dict[str, Dict[str, Any]],
    model_name: str,
    effective_dim: int,
    full_reindex: bool,
    resume_reindex: bool = False,
) -> Tuple[List[S3Object], int, int]:
    """Chọn ảnh cần embed: mới, đổi etag, hoặc đổi model/dim.

    Modes:
      - incremental (default): skip ảnh đã có + cùng etag/model/dim.
      - full_reindex: embed lại toàn bộ, kể cả đã có trên Qdrant.
      - resume_reindex: tiếp tục full-reindex bị fail — chỉ embed những
        ảnh Qdrant **chưa có** (bỏ qua etag/model/dim check).
    """
    to_index: List[S3Object] = []
    skipped_unchanged = 0
    skipped_unrecognized = 0

    for key, etag, last_modified in s3_objects:
        metadata = build_keyframe_metadata(bucket_name, key)
        if metadata is None:
            skipped_unrecognized += 1
            continue

        s3_uri = metadata["s3_uri"]

        if full_reindex:
            to_index.append((key, etag, last_modified))
            continue

        existing = index_state.get(s3_uri)

        if resume_reindex:
            if existing is not None:
                skipped_unchanged += 1
                continue
            to_index.append((key, etag, last_modified))
            continue

        if existing is None:
            to_index.append((key, etag, last_modified))
            continue

        if (
            existing.get("etag") == etag
            and existing.get("model") == model_name
            and existing.get("embedding_dim") == effective_dim
        ):
            skipped_unchanged += 1
            continue

        to_index.append((key, etag, last_modified))

    return to_index, skipped_unchanged, skipped_unrecognized


def _ensure_qdrant_collection(
    qdrant_client: QdrantClient,
    collection_name: str,
    vector_size: int,
) -> None:
    if not qdrant_client.collection_exists(collection_name):
        qdrant_client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        print(f"🧱 Created Qdrant collection '{collection_name}' (dim={vector_size})")
        return

    info = qdrant_client.get_collection(collection_name)
    existing_size = info.config.params.vectors.size
    if existing_size != vector_size:
        raise ValueError(
            f"Collection '{collection_name}' has dim={existing_size}, "
            f"but current embedding dim={vector_size}. "
            "Tạo collection mới hoặc xóa collection cũ thủ công trước khi chạy lại."
        )

    print(f"📚 Using existing Qdrant collection '{collection_name}' (dim={vector_size})")


# ---------------------------------------------------------------------------
# Prefetch: download a full chunk of images in parallel (background thread)
# ---------------------------------------------------------------------------

def _prefetch_chunk(
    s3_client,
    bucket_name: str,
    chunk_items: List[S3Object],
    max_workers: int,
) -> List[Tuple[S3Object, Optional[bytes]]]:
    """Download all images in a chunk using a thread pool. Returns ordered list."""

    def _download_one(item: S3Object) -> Tuple[S3Object, Optional[bytes]]:
        key, etag, last_modified = item
        try:
            obj = s3_client.get_object(Bucket=bucket_name, Key=key)
            return item, obj["Body"].read()
        except Exception as e:
            print(f"⚠️ Failed to download {key}: {e}")
            return item, None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_download_one, item) for item in chunk_items]
        return [f.result() for f in futures]


# ---------------------------------------------------------------------------
# Process one chunk: embed (GPU) + upsert (Qdrant) in streaming fashion
# ---------------------------------------------------------------------------

def _process_chunk(
    downloaded: List[Tuple[S3Object, Optional[bytes]]],
    model,
    processor,
    device: torch.device,
    bucket_name: str,
    model_name: str,
    effective_dim: int,
    embedding_dim: Optional[int],
    keyframe_fps: float,
    index_state: Dict[str, Dict[str, Any]],
    embed_batch_size: int,
    qdrant_client: QdrantClient,
    qdrant_collection: str,
    upsert_batch_size: int,
    pbar: tqdm,
) -> Tuple[int, int]:
    """Embed + upsert one chunk. Returns (upserted_count, skipped_keys)."""

    ok_items: List[S3Object] = []
    ok_data: List[bytes] = []
    for item, data in downloaded:
        if data is not None:
            ok_items.append(item)
            ok_data.append(data)

    if not ok_items:
        return 0, 0

    upserted_total = 0
    skipped_keys = 0

    pending_points: List[PointStruct] = []

    for batch_start in range(0, len(ok_items), embed_batch_size):
        batch_items = ok_items[batch_start : batch_start + embed_batch_size]
        batch_data = ok_data[batch_start : batch_start + embed_batch_size]

        try:
            batch_embeddings = embed_images_bytes(
                model, processor, batch_data, device,
                embedding_dim=embedding_dim, keep_on_device=False,
            )
        except Exception as e:
            print(f"⚠️ Batch embed failed at {batch_items[0][0]}: {e}")
            batch_embeddings = _embed_one_by_one(
                model, processor, batch_data, batch_items, device, embedding_dim,
            )
            if batch_embeddings is None:
                pbar.update(len(batch_items))
                continue

        emb_np = batch_embeddings.numpy().astype("float32")
        norms = np.linalg.norm(emb_np, axis=1, keepdims=True)
        emb_np = emb_np / np.clip(norms, 1e-12, None)

        for row_idx, (key, etag, last_modified) in enumerate(batch_items):
            if row_idx >= emb_np.shape[0]:
                break
            metadata = build_keyframe_metadata(bucket_name, key, fps=keyframe_fps)
            if metadata is None:
                skipped_keys += 1
                continue

            s3_uri = metadata["s3_uri"]
            metadata["model"] = model_name
            metadata["embedding_dim"] = effective_dim
            metadata["s3_etag"] = etag
            metadata["s3_last_modified"] = last_modified

            existing = index_state.get(s3_uri)
            point_id = existing["point_id"] if existing else stable_point_id(s3_uri)

            pending_points.append(PointStruct(
                id=int(point_id),
                vector=emb_np[row_idx].tolist(),
                payload=metadata,
            ))

        if len(pending_points) >= upsert_batch_size:
            _flush_upsert(qdrant_client, qdrant_collection, pending_points, upsert_batch_size)
            upserted_total += len(pending_points)
            pending_points = []

        pbar.update(len(batch_items))

    if pending_points:
        _flush_upsert(qdrant_client, qdrant_collection, pending_points, upsert_batch_size)
        upserted_total += len(pending_points)

    return upserted_total, skipped_keys


def _embed_one_by_one(model, processor, batch_data, batch_items, device, embedding_dim):
    """Fallback: embed images one-by-one when a batch fails."""
    rows = []
    for (key, _, _), img_data in zip(batch_items, batch_data):
        try:
            emb = embed_images_bytes(
                model, processor, [img_data], device,
                embedding_dim=embedding_dim, keep_on_device=False,
            )
            rows.append(emb)
        except Exception as inner_e:
            print(f"⚠️ Failed to process {key}: {inner_e}")
            rows.append(None)

    valid = [r for r in rows if r is not None]
    if not valid:
        return None
    return torch.cat(valid, dim=0)


def _flush_upsert(
    qdrant_client: QdrantClient,
    collection_name: str,
    points: List[PointStruct],
    batch_size: int,
) -> None:
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        qdrant_client.upsert(collection_name=collection_name, points=batch, wait=True)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def indexing(
    s3_client,
    bucket_name: str,
    qdrant_url: str,
    qdrant_api_key: str,
    qdrant_collection: str,
    model_name: str,
    start_image: int,
    prefix: str = "K",
    max_images: Optional[int] = None,
    upsert_batch_size: int = 512,
    embed_batch_size: int = 512,
    model_id: str = "ViT-L-14",
    pretrained: str = "datacomp_xl_s13b_b90k",
    keyframe_fps: float = 25.0,
    embedding_dim: Optional[int] = None,
    full_reindex: bool = False,
    resume_reindex: bool = False,
    s3_download_workers: int = S3_DOWNLOAD_WORKERS,
    chunk_size: int = CHUNK_SIZE,
) -> Tuple[QdrantClient, dict, np.ndarray]:
    """
    Pipeline:  S3 list → [chunk of N images] → prefetch (threads) → embed (GPU) → upsert (Qdrant)
                                                 ↑ next chunk downloads in background while GPU works

    Download + embed + upsert are pipelined per chunk for maximum throughput.
    """
    if isinstance(bucket_name, str) and bucket_name.startswith("s3://"):
        bucket_name, parsed_prefix = parse_s3_location(s3_uri=bucket_name)
        if prefix in (None, "", "K"):
            prefix = parsed_prefix

    if full_reindex and resume_reindex:
        raise ValueError("Không thể dùng cả --full-reindex và --resume-reindex cùng lúc.")

    is_jina = model_id.startswith("jinaai/")
    native_dim = 1024 if is_jina else 768
    effective_dim = embedding_dim or native_dim
    if resume_reindex:
        mode_label = "resume reindex (chỉ index ảnh chưa có trên Qdrant)"
    elif full_reindex:
        mode_label = "force re-embed all"
    else:
        mode_label = "incremental upsert"

    print(f"🔍 Starting AWS S3 -> Embedding -> Qdrant indexing ({mode_label})...")
    print(f"📦 Source bucket: {bucket_name}")
    print(f"🧭 Prefix filter: {prefix}")

    if not ensure_bucket_exists(bucket_name, s3_client):
        raise ValueError(f"Bucket '{bucket_name}' is not accessible")

    s3_objects = list_s3_image_objects_parallel(
        s3_client, bucket_name, prefix, start_image, max_images,
    )
    if not s3_objects:
        raise ValueError("No images found from S3 with current filters")

    print(f"📥 Total S3 images under prefix: {len(s3_objects)}")
    print(f"📐 Embedding dim: {effective_dim}")

    qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=120)
    index_state = load_qdrant_index_state(qdrant_client, qdrant_collection)
    if index_state:
        print(f"📚 Loaded {len(index_state)} existing vectors from Qdrant")

    objects_to_index, skipped_unchanged, skipped_unrecognized = select_objects_to_index(
        s3_objects, bucket_name, index_state, model_name,
        effective_dim, full_reindex, resume_reindex=resume_reindex,
    )

    if skipped_unchanged:
        print(f"⏭️ Skipped {skipped_unchanged} unchanged images")
    if skipped_unrecognized:
        print(f"⚠️ Skipped {skipped_unrecognized} keys with unrecognized path format")

    if not objects_to_index:
        print("✅ Index is up to date. Nothing to embed or upsert.")
        if qdrant_client.collection_exists(qdrant_collection):
            count_info = qdrant_client.count(collection_name=qdrant_collection)
            print(f"📊 Collection '{qdrant_collection}' has {count_info.count} vectors")
        return qdrant_client, {}, np.empty((0, effective_dim), dtype=np.float32)

    total_to_index = len(objects_to_index)
    num_chunks = (total_to_index + chunk_size - 1) // chunk_size
    print(f"🆕 Images to index/update: {total_to_index}")
    print(f"📦 Chunk size: {chunk_size} | Total chunks: {num_chunks}")
    print(f"⚡ Embed batch: {embed_batch_size} | Upsert batch: {upsert_batch_size}")
    print(f"🔗 S3 download workers: {s3_download_workers}")

    model, processor, device = load_model(model_id=model_id, pretrained=pretrained)

    _ensure_qdrant_collection(qdrant_client, qdrant_collection, effective_dim)

    # --- Pipelined loop: prefetch chunk N+1 while processing chunk N ---
    chunks = [
        objects_to_index[i : i + chunk_size]
        for i in range(0, total_to_index, chunk_size)
    ]

    total_upserted = 0
    total_skipped_keys = 0

    prefetch_result: Optional[List] = None
    prefetch_thread: Optional[Thread] = None

    def _do_prefetch(chunk_items):
        nonlocal prefetch_result
        prefetch_result = _prefetch_chunk(
            s3_client, bucket_name, chunk_items, s3_download_workers,
        )

    # Kick off prefetch for the first chunk
    prefetch_thread = Thread(target=_do_prefetch, args=(chunks[0],))
    prefetch_thread.start()

    pbar = tqdm(total=total_to_index, desc="Processing images", unit="img")

    for chunk_idx, chunk_items in enumerate(chunks):
        # Wait for this chunk's prefetch to finish
        prefetch_thread.join()
        downloaded = prefetch_result

        # Start prefetching the NEXT chunk in background
        if chunk_idx + 1 < len(chunks):
            prefetch_thread = Thread(target=_do_prefetch, args=(chunks[chunk_idx + 1],))
            prefetch_thread.start()

        chunk_label = f"[chunk {chunk_idx + 1}/{num_chunks}]"
        ok_count = sum(1 for _, d in downloaded if d is not None)
        pbar.set_postfix_str(f"{chunk_label} downloaded={ok_count}/{len(chunk_items)}")

        upserted, skipped = _process_chunk(
            downloaded=downloaded,
            model=model,
            processor=processor,
            device=device,
            bucket_name=bucket_name,
            model_name=model_name,
            effective_dim=effective_dim,
            embedding_dim=embedding_dim,
            keyframe_fps=keyframe_fps,
            index_state=index_state,
            embed_batch_size=embed_batch_size,
            qdrant_client=qdrant_client,
            qdrant_collection=qdrant_collection,
            upsert_batch_size=upsert_batch_size,
            pbar=pbar,
        )

        total_upserted += upserted
        total_skipped_keys += skipped

        tqdm.write(
            f"✅ {chunk_label} Upserted {upserted} vectors "
            f"(total so far: {total_upserted})"
        )

    pbar.close()

    if total_skipped_keys:
        print(f"⚠️ Skipped {total_skipped_keys} keys with unrecognized path format during embed")

    count_info = qdrant_client.count(collection_name=qdrant_collection)
    print(
        f"✅ Done! Upserted {total_upserted} vectors this run; "
        f"collection '{qdrant_collection}' now has {count_info.count} vectors"
    )

    id2metadata: Dict[int, Dict[str, Any]] = {}
    return qdrant_client, id2metadata, np.empty((0, effective_dim), dtype=np.float32)
