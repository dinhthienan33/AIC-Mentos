import hashlib
import re
from typing import Any, Dict, Optional

KEYFRAME_PATH_RE = re.compile(
    r"keyframes/(?P<batch_folder>[^/]+)/keyframes/(?P<video_id>[^/]+)/(?P<frame_file>[^/]+\.(?:jpg|jpeg|png|webp|bmp|gif))$",
    re.IGNORECASE,
)


def stable_point_id(s3_uri: str) -> int:
    """Deterministic Qdrant point id from s3_uri (dùng cho keyframe mới)."""
    digest = hashlib.sha256(s3_uri.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big") & 0x7FFFFFFFFFFFFFFF


def batch_id_from_folder(batch_folder: str) -> str:
    """L21_a / L21_b -> L21"""
    head, sep, tail = batch_folder.rpartition("_")
    if sep and len(tail) == 1 and tail.isalpha():
        return head
    return batch_folder


def parse_frame_index(frame_file: str) -> int:
    stem = frame_file.rsplit(".", 1)[0]
    if not stem.isdigit():
        raise ValueError(f"Frame filename is not numeric: {frame_file}")
    return int(stem)


def build_keyframe_metadata(
    bucket_name: str,
    key: str,
    fps: float = 25.0,
    frame_id_width: int = 6,
) -> Optional[Dict[str, Any]]:
    """
    Parse S3 key like:
      <optional_prefix>/keyframes/<batch_folder>/keyframes/<video_id>/<frame>.jpg
      e.g. prefix/keyframes/L21_a/keyframes/L21_V001/123.jpg
    """
    normalized_key = key.lstrip("/")
    match = KEYFRAME_PATH_RE.search(normalized_key)
    if not match:
        return None

    batch_folder = match.group("batch_folder")
    video_id = match.group("video_id")
    frame_file = match.group("frame_file")
    frame_index = parse_frame_index(frame_file)
    frame_id = str(frame_index).zfill(frame_id_width)
    s3_uri = f"s3://{bucket_name}/{normalized_key}"
    timestamp = round(frame_index / fps, 3)

    return {
        "batch_id": batch_id_from_folder(batch_folder),
        "video_id": video_id,
        "frame_id": frame_id,
        "s3_uri": s3_uri,
        "timestamp": timestamp,
        "frame_index": frame_index,
    }


def s3_uri_to_bucket_key(s3_uri: str) -> tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid s3_uri: {s3_uri}")
    without_scheme = s3_uri[5:]
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


def snapshot_url_from_payload(payload: dict, region: Optional[str] = None) -> Optional[str]:
    from .s3_utils import build_snapshot_url

    s3_uri = payload.get("s3_uri")
    if s3_uri:
        bucket, key = s3_uri_to_bucket_key(s3_uri)
        return build_snapshot_url(bucket, key, region)

    bucket = payload.get("bucket")
    path = payload.get("path")
    if bucket and path:
        return build_snapshot_url(bucket, path, region)
    return None
