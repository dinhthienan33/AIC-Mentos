"""Utility helpers for path parsing and result formatting."""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_FRAME_REPO = None
_S3 = None


def _default_fps() -> float:
    try:
        from .config import get_settings

        return float(getattr(get_settings(), "default_fps", 25.0) or 25.0)
    except Exception:
        return 25.0


DEFAULT_FPS = _default_fps()


def set_runtime_deps(frame_repo=None, s3=None) -> None:
    global _FRAME_REPO, _S3
    _FRAME_REPO = frame_repo
    _S3 = s3


def video_ids_from_index_files(index_files: Optional[List[str]] = None) -> List[str]:
    """Map legacy FAISS shard names (L30_V001.bin) to video ids (L30_V001)."""
    videos: List[str] = []
    seen = set()
    for raw in index_files or []:
        name = str(raw).strip().replace("\\", "/").rsplit("/", 1)[-1]
        if not name:
            continue
        lower = name.lower()
        for suffix in (".bin", ".faiss", ".idx"):
            if lower.endswith(suffix):
                name = name[: -len(suffix)]
                break
        if name and name not in seen:
            seen.add(name)
            videos.append(name)
    return videos


def merge_include_videos(
    include_videos: Optional[List[str]] = None,
    index_files: Optional[List[str]] = None,
) -> Optional[List[str]]:
    merged: List[str] = []
    seen = set()
    for vid in list(include_videos or []) + video_ids_from_index_files(index_files):
        if vid and vid not in seen:
            seen.add(vid)
            merged.append(vid)
    return merged or None


def get_frame_repo():
    global _FRAME_REPO
    if _FRAME_REPO is None:
        from .db_repository import FrameDatabaseRepository

        _FRAME_REPO = FrameDatabaseRepository()
    return _FRAME_REPO


def get_s3():
    global _S3
    if _S3 is None:
        from .s3_client import get_s3_storage

        _S3 = get_s3_storage()
    return _S3


def extract_video_info(path: str) -> Tuple[str, str, int]:
    group_match = re.search(r"L(\d+)", path)
    video_match = re.search(r"V(\d+)", path)
    keyframe_match = re.search(r"/(\d+)\.webp", path)

    group_num = group_match.group(1) if group_match else "0"
    video_num = video_match.group(1) if video_match else "0"
    keyframe_num = int(keyframe_match.group(1)) if keyframe_match else 0

    group_id = f"L{group_num}"
    video_id = f"L{group_num}_V{video_num}"
    return group_id, video_id, keyframe_num


def extract_video_info_v2(path: str) -> Tuple[str, str, str]:
    """Extract group_id, video_id, keyframe_num from path-like identifiers."""
    name = os.path.basename(str(path))
    stem = name.rsplit(".", 1)[0] if "." in name else name
    # M01_V012_017125.jpg and hyphen ids: S01-V007_006123.jpg, N001-V002_000037.jpg
    match = re.match(r"^(?P<group>.+?)(?P<sep>[-_])(?P<vid>V\d+)_(?P<frame>\d+)$", stem)
    if match:
        group_id = match.group("group")
        video_id = f"{group_id}{match.group('sep')}{match.group('vid')}"
        return group_id, video_id, match.group("frame")
    match = re.match(r"^(?P<group>.+?)(?P<sep>[-_])(?P<vid>V\d+)$", stem)
    if match:
        group_id = match.group("group")
        video_id = f"{group_id}{match.group('sep')}{match.group('vid')}"
        return group_id, video_id, "0"
    # Support synthetic ids: L21_V001_000037.jpg / L21_V001_000037
    parts = name.split("_")
    if len(parts) >= 3 and parts[1].startswith("V"):
        group_id = parts[0]
        vid_id = f"{group_id}_{parts[1]}"
        keyframe_num = parts[2].split(".")[0]
        return group_id, vid_id, keyframe_num
    if len(parts) == 5:
        group_id = parts[1]
        vid_id = f"{group_id}_{parts[3]}"
        keyframe_num = parts[4].split(".")[0]
        return group_id, vid_id, keyframe_num
    if len(parts) == 2 and parts[1].startswith("V"):
        group_id = parts[0]
        vid_id = f"{group_id}_{parts[1].split('.')[0]}"
        return group_id, vid_id, "0"
    raise ValueError(f"Cannot parse path: {path}")


def analyze_groups(paths: List[str], scores: List[float]) -> Dict:
    groups = defaultdict(lambda: {"videos": set(), "keyframes": [], "scores": []})
    for path, score in zip(paths, scores):
        if not path:
            continue
        group_id, video_id, keyframe_num = extract_video_info_v2(path)
        groups[group_id]["videos"].add(video_id)
        groups[group_id]["keyframes"].append(
            {"path": path, "video_id": video_id, "keyframe_num": keyframe_num, "score": score}
        )
        groups[group_id]["scores"].append(score)
    return dict(groups)


def filter_by_groups(
    paths: List[str],
    scores: List[float],
    include_groups: List[str] = None,
    exclude_groups: List[str] = None,
) -> Tuple[List[str], List[float]]:
    if not include_groups and not exclude_groups:
        return paths, scores

    filtered_paths, filtered_scores = [], []
    for path, score in zip(paths, scores):
        if not path:
            continue
        group_id, _, _ = extract_video_info_v2(path)
        if exclude_groups and group_id in exclude_groups:
            continue
        if include_groups and group_id not in include_groups:
            continue
        filtered_paths.append(path)
        filtered_scores.append(score)
    return filtered_paths, filtered_scores


def filter_by_vid_id(
    paths: List[str],
    scores: List[float],
    include_videos: List[str] = None,
    exclude_videos: List[str] = None,
) -> Tuple[List[str], List[float]]:
    if not include_videos and not exclude_videos:
        return paths, scores

    filtered_paths, filtered_scores = [], []
    for path, score in zip(paths, scores):
        if not path:
            continue
        _, video_id, _ = extract_video_info_v2(path)
        if exclude_videos and video_id in exclude_videos:
            continue
        if include_videos and video_id not in include_videos:
            continue
        filtered_paths.append(path)
        filtered_scores.append(score)
    return filtered_paths, filtered_scores


def synthetic_image_path(video_id: str, frame_idx: int, ext: str = "jpg") -> str:
    return f"{video_id}_{int(frame_idx):06d}.{ext}"


def format_results(query: str, paths: List[str], scores: List[float]) -> List[Dict]:
    results = []
    seen = set()
    repo = get_frame_repo()
    skip_db = bool(getattr(repo.settings, "skip_db_mount", False))

    for path, score in zip(paths, scores):
        if not path:
            continue
        try:
            group_id, video_id, keyframe_num = extract_video_info_v2(path)
        except ValueError:
            continue

        full_name = f"{video_id}_{keyframe_num}"
        if full_name in seen:
            continue
        seen.add(full_name)

        frame = None if skip_db else repo.get_frame(video_id, int(keyframe_num))
        if frame is not None:
            timestamp = float(frame.pts_time)
            fps = float(frame.fps or get_video_fps(video_id))
            image_path = frame.image_path
            shard = frame.shard
        else:
            fps = get_video_fps(video_id)
            timestamp = frame_to_time(keyframe_num, fps)
            image_path = f"keyframes/{video_id}/{int(keyframe_num):06d}.jpg"
            guessed = repo.guess_shards_for_video(video_id) if skip_db else []
            # Only pin a shard when unique; otherwise presign HEADs candidates.
            shard = guessed[0] if len(guessed) == 1 else None

        results.append(
            {
                "full_name": full_name,
                "keyframe_id": f"{video_id}_{keyframe_num}",
                "video_id": video_id,
                "group_id": group_id,
                "query": query,
                "keyframe_num": int(keyframe_num),
                "fps": float(fps),
                "timestamp": float(timestamp),
                "confidence_score": float(score),
                "image_path": image_path,
                "image_url": None,
                "video_url": get_video_url_with_start_time(
                    get_video_url(video_id),
                    keyframe_num=str(keyframe_num),
                    start_seconds=float(timestamp),
                ),
                "thumbnail_url": get_video_thumbnail(video_id),
                "_shard": shard,
            }
        )
    return results


def format_qdrant_hits(query: str, hits: List[Dict]) -> List[Dict]:
    """Format Qdrant payload hits into KeyframeResult-compatible dicts."""
    paths = []
    scores = []
    for hit in hits:
        video_id = hit.get("video_id")
        frame_idx = hit.get("frame_idx")
        if video_id is None or frame_idx is None:
            continue
        paths.append(synthetic_image_path(video_id, int(frame_idx)))
        scores.append(float(hit.get("score", 0.0)))
    return format_results(query, paths, scores)


def attach_presigned_urls(results: List[Dict]) -> List[Dict]:
    if not results:
        return results
    repo = get_frame_repo()
    settings = getattr(repo, "settings", None)
    use_hf = bool(settings and getattr(settings, "keyframe_source", "hf") == "hf")
    if use_hf:
        from .hf_frames import get_hf_frame_store

        store = get_hf_frame_store()
        for result in results:
            result.pop("_shard", None)
            video_id = result.get("video_id")
            frame_idx = result.get("keyframe_num")
            if video_id is None or frame_idx is None:
                result["image_url"] = None
                continue
            result["image_url"] = store.public_url(str(video_id), int(frame_idx))
            if not result.get("image_path"):
                result["image_path"] = f"keyframes/{video_id}/{int(frame_idx):06d}.jpg"
        store.prefetch(
            [
                (str(item["video_id"]), int(item["keyframe_num"]))
                for item in results
                if item.get("video_id") is not None and item.get("keyframe_num") is not None
            ]
        )
        return results

    s3 = get_s3()
    for result in results:
        video_id = result.get("video_id")
        frame_idx = result.get("keyframe_num")
        image_path = result.get("image_path")
        shard = result.pop("_shard", None)
        skip_db = bool(getattr(repo.settings, "skip_db_mount", False))
        if not shard and video_id and not skip_db:
            shard = repo.resolve_shard(video_id)
        try:
            if shard and image_path and str(image_path).startswith("keyframes/"):
                key = s3.keyframe_object_key_from_image_path(shard, image_path)
            elif shard and video_id is not None and frame_idx is not None:
                key = s3.keyframe_object_key(shard, video_id, int(frame_idx))
            else:
                if skip_db:
                    candidates = [shard] if shard else (repo.guess_shards_for_video(video_id) if video_id else [])
                else:
                    candidates = [shard] if shard else (repo.shards_for_video(video_id) if video_id else [])
                key = s3.resolve_keyframe_key(
                    video_id=video_id,
                    frame_idx=int(frame_idx or 0),
                    shard_candidates=candidates,
                    image_path=image_path if image_path and str(image_path).startswith("keyframes/") else None,
                )
            result["image_url"] = s3.generate_presigned_url(key) if key else None
            if key and not result.get("image_path"):
                result["image_path"] = key
        except Exception:
            result["image_url"] = None
    return results


def frame_to_time(frame_id, fps: float) -> float:
    return int(frame_id) / float(fps or 25.0)


_VIDEO_URL_MAP: Optional[Dict[str, str]] = None
_VIDEO_FPS_MAP: Optional[Dict[str, float]] = None


def _default_urls_csv_path() -> str:
    env_path = os.getenv("VIDEO_URLS_CSV")
    if env_path:
        return env_path
    # AIC2025_Mentos-v2/urls.csv relative to this file: core/utils.py -> ../urls.csv
    return str(Path(__file__).resolve().parents[1] / "urls.csv")


def load_video_urls(csv_path: Optional[str] = None, *, force_reload: bool = False) -> Dict[str, str]:
    """Load ``video_name,url`` (and fps) mapping from urls.csv (cached)."""
    global _VIDEO_URL_MAP, _VIDEO_FPS_MAP
    if _VIDEO_URL_MAP is not None and not force_reload:
        return _VIDEO_URL_MAP

    path = Path(csv_path or _default_urls_csv_path())
    mapping: Dict[str, str] = {}
    fps_map: Dict[str, float] = {}
    if not path.is_file():
        _VIDEO_URL_MAP = mapping
        _VIDEO_FPS_MAP = fps_map
        return mapping

    import csv

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            name = (row.get("video_name") or row.get("video_id") or "").strip()
            url = (row.get("url") or "").strip()
            if name and url:
                mapping[name] = url
            if not name:
                continue
            fps_raw = (row.get("fps") or "").strip()
            if not fps_raw:
                continue
            try:
                fps_val = float(fps_raw)
            except ValueError:
                continue
            if fps_val > 0:
                fps_map[name] = fps_val
    _VIDEO_URL_MAP = mapping
    _VIDEO_FPS_MAP = fps_map
    return mapping


def load_video_fps(csv_path: Optional[str] = None, *, force_reload: bool = False) -> Dict[str, float]:
    """Load ``video_name -> fps`` from urls.csv (cached with URLs)."""
    load_video_urls(csv_path, force_reload=force_reload)
    return dict(_VIDEO_FPS_MAP or {})


def _youtube_video_id(url: str) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "youtu.be" in host:
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid or None
    if "youtube.com" in host:
        q = dict(parse_qsl(parsed.query))
        if q.get("v"):
            return q["v"]
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0] in {"embed", "shorts", "v"} and len(parts) > 1:
            return parts[1]
    return None


def get_video_url(video_id: str) -> Optional[str]:
    """YouTube URL from urls.csv, otherwise a Bunny embed for M/N/S."""
    if not video_id:
        return None
    vid = str(video_id).strip()
    youtube = load_video_urls().get(vid)
    if youtube:
        return youtube
    from bunny_client import lookup_video

    hit = lookup_video(vid)
    if not hit:
        return None
    return hit.get("video_url")


def get_video_thumbnail(video_id: str) -> Optional[str]:
    """Derive a YouTube thumbnail URL from the mapped watch URL."""
    url = get_video_url(video_id)
    yt_id = _youtube_video_id(url or "")
    if not yt_id:
        return None
    return f"https://i.ytimg.com/vi/{yt_id}/sddefault.jpg"


def _add_query_param(url: str, new_params: dict) -> str:
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q.update(new_params)
    return urlunparse(parts._replace(query=urlencode(q, doseq=True)))


def get_video_url_with_start_time(
    video_url: str,
    keyframe_num: str = None,
    start_seconds: float = None,
) -> Optional[str]:
    if not video_url:
        return None
    if start_seconds is None:
        return video_url
    seconds = int(float(start_seconds))
    lower = video_url.lower()
    if "youtube.com" in lower or "youtu.be" in lower:
        return _add_query_param(video_url, {"t": f"{seconds}s"})
    if "mediadelivery.net" in lower or "bunnycdn.com" in lower:
        return _add_query_param(video_url, {"t": str(seconds), "autoplay": "true"})
    return _add_query_param(video_url, {"start": str(seconds)})


# Backward-compatible CSV loader call sites.
def load_metadata(csv_path: str = None):
    return load_video_urls(csv_path)


def get_video_fps(video_id: str, metadata_df=None) -> float:
    vid = str(video_id or "").strip()
    csv_fps = load_video_fps().get(vid)
    if csv_fps:
        return float(csv_fps)
    try:
        repo = get_frame_repo()
        if not getattr(repo.settings, "skip_db_mount", False):
            return float(repo.get_video_fps(vid) or DEFAULT_FPS)
    except Exception:
        pass
    return float(DEFAULT_FPS)
