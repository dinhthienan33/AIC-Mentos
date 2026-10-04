"""Qdrant snapshot download policy: only the vector snapshot may be fetched."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests
from huggingface_hub import HfApi, hf_hub_download

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

ALLOWED_SNAPSHOT_SUFFIXES = (".snapshot",)
FORBIDDEN_DOWNLOAD_SUFFIXES = (".tar", ".bin", ".faiss", ".csv", ".json", ".jpg", ".jpeg", ".png", ".webp")


class SnapshotPolicyError(ValueError):
    pass


def assert_allowed_snapshot_filename(filename: str) -> str:
    name = (filename or "").replace("\\", "/")
    if not name.endswith(ALLOWED_SNAPSHOT_SUFFIXES):
        raise SnapshotPolicyError(f"Only .snapshot files may be downloaded, got: {filename}")
    lower = name.lower()
    for bad in FORBIDDEN_DOWNLOAD_SUFFIXES:
        if lower.endswith(bad) and not lower.endswith(".snapshot"):
            raise SnapshotPolicyError(f"Forbidden download type: {filename}")
    # Extra guard: never allow tar shards / keyframe archives.
    if "/data/" in lower and lower.endswith(".tar"):
        raise SnapshotPolicyError(f"Keyframe tar download is forbidden: {filename}")
    return name


def resolve_snapshot_filename(settings: Optional[Settings] = None) -> str:
    settings = settings or get_settings()
    preferred = assert_allowed_snapshot_filename(settings.snapshot_filename)

    api = HfApi(token=settings.hf_token or None)
    files = api.list_repo_files(
        repo_id=settings.hf_repo_id,
        repo_type="dataset",
        token=settings.hf_token or None,
    )
    if preferred in files:
        return preferred

    snapshots_v2 = [
        f
        for f in files
        if f.startswith("snapshot_vectorstore_v2/snapshot_") and f.endswith(".snapshot")
    ]
    snapshots_v1 = [
        f
        for f in files
        if f.startswith("snapshot_vectorstore/snapshot_") and f.endswith(".snapshot")
    ]
    snapshots = snapshots_v2 or snapshots_v1
    if not snapshots:
        raise FileNotFoundError(f"No Qdrant snapshot found in {settings.hf_repo_id}")
    return assert_allowed_snapshot_filename(sorted(snapshots)[-1])


def download_qdrant_snapshot(settings: Optional[Settings] = None, force: bool = False) -> Path:
    """Download the single allowed Qdrant snapshot to local snapshot_dir."""
    settings = settings or get_settings()
    settings.snapshot_dir.mkdir(parents=True, exist_ok=True)

    filename = resolve_snapshot_filename(settings)
    local_name = Path(filename).name
    local_path = settings.snapshot_dir / local_name
    if local_path.exists() and not force:
        logger.info("Using cached Qdrant snapshot at %s", local_path)
        return local_path

    logger.info("Downloading Qdrant snapshot %s from %s", filename, settings.hf_repo_id)
    downloaded = hf_hub_download(
        repo_id=settings.hf_repo_id,
        repo_type="dataset",
        filename=filename,
        token=settings.hf_token or None,
        local_dir=str(settings.snapshot_dir),
    )
    downloaded_path = Path(downloaded)
    if downloaded_path.resolve() != local_path.resolve():
        # hf_hub_download keeps the repo-relative path
        # (e.g. qdrant_vectorstore_siglip2_v3/qdrant.snapshot). Flatten to
        # snapshot_dir / basename and drop the nested copy.
        if local_path.exists():
            local_path.unlink()
        downloaded_path.replace(local_path)
        nested_dir = downloaded_path.parent
        if nested_dir.resolve() != settings.snapshot_dir.resolve():
            try:
                nested_dir.rmdir()
            except OSError:
                pass
    return local_path


def _qdrant_headers(settings: Optional[Settings] = None) -> dict:
    settings = settings or get_settings()
    headers = {}
    if settings.qdrant_api_key:
        headers["api-key"] = settings.qdrant_api_key
    return headers


def collection_ready(
    qdrant_url: str,
    collection: str,
    min_points: int = 1,
    api_key: Optional[str] = None,
) -> bool:
    try:
        headers = {"api-key": api_key} if api_key else {}
        resp = requests.get(
            f"{qdrant_url.rstrip('/')}/collections/{collection}",
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return False
        payload = resp.json()
        result = payload.get("result") or {}
        points = (
            result.get("points_count")
            or result.get("indexed_vectors_count")
            or (result.get("status") == "green" and 1)
            or 0
        )
        return int(points) >= min_points
    except Exception as exc:
        logger.warning("Qdrant collection check failed: %s", exc)
        return False


def upload_snapshot_file(
    snapshot_path: Path,
    settings: Optional[Settings] = None,
    restore_timeout: int = 3600,
    collection: Optional[str] = None,
) -> dict:
    """Upload a local .snapshot file to Qdrant and restore into the configured collection."""
    settings = settings or get_settings()
    collection = collection or settings.qdrant_collection
    path = Path(snapshot_path)
    if not path.exists():
        raise FileNotFoundError(f"Snapshot not found: {path}")
    if not path.name.endswith(".snapshot"):
        raise SnapshotPolicyError(f"Only .snapshot files may be uploaded, got: {path.name}")

    url = (
        f"{settings.qdrant_url.rstrip('/')}/collections/"
        f"{collection}/snapshots/upload?priority=snapshot&wait=false"
    )
    logger.info("Uploading snapshot %s -> %s (%s)", path, settings.qdrant_url, collection)
    with open(path, "rb") as handle:
        resp = requests.post(
            url,
            headers=_qdrant_headers(settings),
            files={"snapshot": (path.name, handle, "application/octet-stream")},
            timeout=restore_timeout,
        )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"Qdrant snapshot restore failed: {resp.status_code} {resp.text[:500]}")
    return {
        "restored": True,
        "collection": collection,
        "qdrant_url": settings.qdrant_url,
        "snapshot_path": str(path),
        "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
        "response": resp.json() if resp.content else {},
    }


def restore_snapshot_if_needed(
    settings: Optional[Settings] = None,
    force: bool = False,
    restore_timeout: int = 3600,
    local_snapshot_path: Optional[Path] = None,
    collection: Optional[str] = None,
) -> dict:
    settings = settings or get_settings()
    collection = collection or settings.qdrant_collection
    status = {
        "restored": False,
        "skipped": False,
        "collection": collection,
        "qdrant_url": settings.qdrant_url,
        "snapshot_path": None,
    }

    if not force and collection_ready(
        settings.qdrant_url,
        collection,
        api_key=settings.qdrant_api_key or None,
    ):
        status["skipped"] = True
        return status

    if local_snapshot_path:
        snapshot_path = Path(local_snapshot_path)
    else:
        snapshot_path = download_qdrant_snapshot(settings=settings)
    status["snapshot_path"] = str(snapshot_path)
    uploaded = upload_snapshot_file(
        snapshot_path,
        settings=settings,
        restore_timeout=restore_timeout,
        collection=collection,
    )
    status.update(uploaded)
    return status
