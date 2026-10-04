"""Image quality preprocessing: reject blurry / too-dark frames.

Uses only Pillow + NumPy (no OpenCV dependency).

Typical usage::

    from core.preprocessing import ImageQualityFilter, QualityConfig

    filt = ImageQualityFilter(QualityConfig(min_brightness=40.0, min_laplacian_var=50.0))
    kept, rejected = filt.filter_paths(["a.jpg", "b.jpg"])
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union
import os

import numpy as np
from PIL import Image

ImageLike = Union[str, Path, Image.Image, np.ndarray, bytes]


class RejectReason(str, Enum):
    OK = "ok"
    TOO_DARK = "too_dark"
    TOO_BLURRY = "too_blurry"
    TOO_SMALL = "too_small"
    LOAD_FAILED = "load_failed"


@dataclass(frozen=True)
class QualityConfig:
    """Thresholds for accepting a keyframe.

    Attributes:
        min_brightness: Mean grayscale luminance in [0, 255]. Frames darker than
            this are rejected (``độ sáng < x``).
        min_laplacian_var: Variance of Laplacian; lower = more blurry.
        min_width / min_height: Reject tiny / broken frames.
        max_side: Longest side used when scoring (downscale for speed).
    """

    min_brightness: float = 40.0
    min_laplacian_var: float = 50.0
    min_width: int = 64
    min_height: int = 64
    max_side: int = 256


@dataclass
class QualityResult:
    path: Optional[str] = None
    accepted: bool = False
    reason: RejectReason = RejectReason.LOAD_FAILED
    brightness: Optional[float] = None
    laplacian_var: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "accepted": self.accepted,
            "reason": self.reason.value,
            "brightness": self.brightness,
            "laplacian_var": self.laplacian_var,
            "width": self.width,
            "height": self.height,
            "error": self.error,
        }


def load_image(source: ImageLike) -> Image.Image:
    """Load RGB PIL image from path / PIL / ndarray / raw bytes."""
    if isinstance(source, Image.Image):
        return source.convert("RGB")
    if isinstance(source, (str, Path)):
        with Image.open(source) as img:
            return img.convert("RGB")
    if isinstance(source, (bytes, bytearray)):
        from io import BytesIO

        with Image.open(BytesIO(source)) as img:
            return img.convert("RGB")
    if isinstance(source, np.ndarray):
        arr = source
        if arr.ndim == 2:
            return Image.fromarray(arr.astype(np.uint8), mode="L").convert("RGB")
        if arr.ndim == 3 and arr.shape[2] >= 3:
            return Image.fromarray(arr[:, :, :3].astype(np.uint8), mode="RGB")
        raise ValueError(f"Unsupported ndarray shape: {arr.shape}")
    raise TypeError(f"Unsupported image type: {type(source)!r}")


def _to_gray_uint8(img: Image.Image, max_side: int) -> Tuple[np.ndarray, int, int]:
    width, height = img.size
    if max(width, height) > max_side:
        scale = max_side / float(max(width, height))
        img = img.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.BILINEAR,
        )
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    return gray, width, height


def compute_brightness(gray: np.ndarray) -> float:
    """Mean luminance in [0, 255]."""
    return float(np.mean(gray))


def compute_laplacian_variance(gray: np.ndarray) -> float:
    """Blur proxy: variance of a discrete Laplacian kernel.

    Sharp images → high variance; blurry / flat images → low variance.
    """
    # 3x3 Laplacian kernel
    kernel = np.array([[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    # Manual valid convolution (no scipy dependency)
    padded = np.pad(gray, 1, mode="edge")
    lap = (
        kernel[0, 1] * padded[0:-2, 1:-1]
        + kernel[1, 0] * padded[1:-1, 0:-2]
        + kernel[1, 1] * padded[1:-1, 1:-1]
        + kernel[1, 2] * padded[1:-1, 2:]
        + kernel[2, 1] * padded[2:, 1:-1]
    )
    return float(np.var(lap))


def assess_image(source: ImageLike, config: Optional[QualityConfig] = None) -> QualityResult:
    """Score one image and decide accept / reject."""
    cfg = config or QualityConfig()
    path = str(source) if isinstance(source, (str, Path)) else None
    try:
        img = load_image(source)
    except Exception as exc:  # noqa: BLE001 - surface load failures as reject
        return QualityResult(path=path, accepted=False, reason=RejectReason.LOAD_FAILED, error=str(exc))

    width, height = img.size
    if width < cfg.min_width or height < cfg.min_height:
        return QualityResult(
            path=path,
            accepted=False,
            reason=RejectReason.TOO_SMALL,
            width=width,
            height=height,
        )

    gray, orig_w, orig_h = _to_gray_uint8(img, cfg.max_side)
    brightness = compute_brightness(gray)
    lap_var = compute_laplacian_variance(gray)

    if brightness < cfg.min_brightness:
        reason = RejectReason.TOO_DARK
        accepted = False
    elif lap_var < cfg.min_laplacian_var:
        reason = RejectReason.TOO_BLURRY
        accepted = False
    else:
        reason = RejectReason.OK
        accepted = True

    return QualityResult(
        path=path,
        accepted=accepted,
        reason=reason,
        brightness=brightness,
        laplacian_var=lap_var,
        width=orig_w,
        height=orig_h,
    )


@dataclass
class ImageQualityFilter:
    """Batch filter over paths / in-memory images."""

    config: QualityConfig = field(default_factory=QualityConfig)

    def assess(self, source: ImageLike) -> QualityResult:
        return assess_image(source, self.config)

    def is_good(self, source: ImageLike) -> bool:
        return self.assess(source).accepted

    def filter_paths(
        self,
        paths: Sequence[Union[str, Path]],
    ) -> Tuple[List[str], List[QualityResult]]:
        """Return (kept_paths, rejected_results)."""
        kept: List[str] = []
        rejected: List[QualityResult] = []
        for path in paths:
            result = self.assess(path)
            if result.accepted:
                kept.append(str(path))
            else:
                rejected.append(result)
        return kept, rejected

    def filter_images(
        self,
        images: Sequence[ImageLike],
        ids: Optional[Sequence[str]] = None,
    ) -> Tuple[List[int], List[QualityResult]]:
        """Return (kept_indices, all_results)."""
        kept_idx: List[int] = []
        results: List[QualityResult] = []
        for i, image in enumerate(images):
            result = self.assess(image)
            if ids is not None and i < len(ids) and result.path is None:
                result.path = ids[i]
            results.append(result)
            if result.accepted:
                kept_idx.append(i)
        return kept_idx, results


def iter_image_files(
    root: Union[str, Path],
    suffixes: Iterable[str] = (".jpg", ".jpeg", ".png", ".webp", ".bmp"),
) -> List[Path]:
    """Collect image files under a directory (recursive)."""
    root_path = Path(root)
    allowed = {s.lower() if s.startswith(".") else f".{s.lower()}" for s in suffixes}
    return sorted(p for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() in allowed)


def filter_directory(
    root: Union[str, Path],
    config: Optional[QualityConfig] = None,
) -> Tuple[List[str], List[QualityResult]]:
    """Convenience: scan a folder and keep only good frames."""
    filt = ImageQualityFilter(config or QualityConfig())
    return filt.filter_paths(iter_image_files(root))


def _resolve_result_key(result: dict, s3, frame_repo=None) -> Optional[str]:
    """Resolve S3 object key for a formatted search result dict."""
    video_id = result.get("video_id")
    frame_idx = result.get("keyframe_num")
    image_path = result.get("image_path")
    shard = result.get("_shard")
    skip_db = bool(getattr(getattr(frame_repo, "settings", None), "skip_db_mount", False))
    if not shard and frame_repo is not None and video_id:
        try:
            if skip_db:
                guessed = frame_repo.guess_shards_for_video(video_id)
                shard = guessed[0] if len(guessed) == 1 else None
            else:
                shard = frame_repo.resolve_shard(video_id)
        except Exception:  # noqa: BLE001
            shard = None

    if shard and image_path and str(image_path).startswith("keyframes/"):
        return s3.keyframe_object_key_from_image_path(shard, image_path)
    if shard and video_id is not None and frame_idx is not None:
        return s3.keyframe_object_key(shard, video_id, int(frame_idx))

    candidates = [shard] if shard else []
    if not candidates and frame_repo is not None and video_id:
        try:
            if skip_db:
                candidates = frame_repo.guess_shards_for_video(video_id) or []
            else:
                candidates = frame_repo.shards_for_video(video_id) or []
        except Exception:  # noqa: BLE001
            candidates = []
    return s3.resolve_keyframe_key(
        video_id=video_id,
        frame_idx=int(frame_idx or 0),
        shard_candidates=candidates,
        image_path=image_path if image_path and str(image_path).startswith("keyframes/") else None,
    )


def _evaluate_search_result(
    result: dict,
    *,
    s3,
    frame_repo=None,
    filt: ImageQualityFilter,
    attach_urls: bool,
) -> Tuple[str, dict]:
    """Return (\"kept\"|\"denied\", payload_dict)."""
    item = dict(result)
    shard = item.pop("_shard", None)
    if shard is not None:
        item["_shard"] = shard

    try:
        from .config import get_settings
        from .hf_frames import get_hf_frame_store

        settings = get_settings()
        if getattr(settings, "keyframe_source", "hf") == "hf":
            video_id = item.get("video_id")
            frame_idx = item.get("keyframe_num")
            if video_id is None or frame_idx is None:
                raise RuntimeError("Missing video_id/frame_idx")
            store = get_hf_frame_store()
            raw = store.get_jpeg(str(video_id), int(frame_idx))
            quality = filt.assess(raw)
            if attach_urls:
                item["image_url"] = store.public_url(str(video_id), int(frame_idx))
            item.pop("_shard", None)
        else:
            key = _resolve_result_key(item, s3, frame_repo=frame_repo)
            if not key:
                raise RuntimeError("Unable to resolve S3 key")
            raw = s3.get_bytes(key)
            quality = filt.assess(raw)
            if attach_urls:
                item["image_url"] = s3.generate_presigned_url(key)
            item.pop("_shard", None)
    except Exception as exc:  # noqa: BLE001
        item.pop("_shard", None)
        return (
            "denied",
            {
                "keyframe_id": item.get("keyframe_id"),
                "video_id": item.get("video_id"),
                "group_id": item.get("group_id"),
                "keyframe_num": item.get("keyframe_num"),
                "image_path": item.get("image_path"),
                "image_url": None,
                "confidence_score": item.get("confidence_score"),
                "reason": RejectReason.LOAD_FAILED.value,
                "brightness": None,
                "laplacian_var": None,
                "error": str(exc),
            },
        )

    if quality.accepted:
        return "kept", item

    return (
        "denied",
        {
            "keyframe_id": item.get("keyframe_id"),
            "video_id": item.get("video_id"),
            "group_id": item.get("group_id"),
            "keyframe_num": item.get("keyframe_num"),
            "image_path": item.get("image_path"),
            "image_url": item.get("image_url"),
            "confidence_score": item.get("confidence_score"),
            "reason": quality.reason.value,
            "brightness": quality.brightness,
            "laplacian_var": quality.laplacian_var,
        },
    )


def partition_search_results_by_quality(
    results: Sequence[dict],
    *,
    s3,
    frame_repo=None,
    config: Optional[QualityConfig] = None,
    limit: Optional[int] = None,
    attach_urls: bool = True,
    max_workers: Optional[int] = None,
) -> Tuple[List[dict], List[dict]]:
    """Download/score images in parallel waves; stop early once ``limit`` kept.

    Denied items are plain dicts with search metadata + ``reason`` /
    ``brightness`` / ``laplacian_var``. Kept items keep the original result
    shape (and get ``image_url`` when ``attach_urls`` is true).
    """
    cfg = config or QualityConfig()
    filt = ImageQualityFilter(cfg)
    workers = max(1, int(max_workers or os.getenv("QUALITY_FILTER_WORKERS") or 8))
    pool = list(results)
    kept: List[dict] = []
    denied: List[dict] = []
    cursor = 0

    while cursor < len(pool):
        if limit is not None and len(kept) >= int(limit):
            break

        remaining_keep = (int(limit) - len(kept)) if limit is not None else len(pool) - cursor
        # Over-sample a bit in each wave to absorb rejects, but don't pull everything.
        wave_size = min(len(pool) - cursor, max(workers, remaining_keep * 2))
        wave = pool[cursor : cursor + wave_size]
        cursor += wave_size

        outcomes: List[Tuple[int, str, dict]] = []
        with ThreadPoolExecutor(max_workers=min(workers, len(wave))) as executor:
            future_map = {
                executor.submit(
                    _evaluate_search_result,
                    result,
                    s3=s3,
                    frame_repo=frame_repo,
                    filt=filt,
                    attach_urls=attach_urls,
                ): idx
                for idx, result in enumerate(wave)
            }
            for fut in as_completed(future_map):
                idx = future_map[fut]
                status, payload = fut.result()
                outcomes.append((idx, status, payload))

        for _, status, payload in sorted(outcomes, key=lambda row: row[0]):
            if status == "kept":
                if limit is None or len(kept) < int(limit):
                    kept.append(payload)
                # Extra accepted frames beyond limit are ignored (not denied).
            else:
                denied.append(payload)

    return kept, denied


def build_denied_images_payload(denied: Sequence[dict]) -> dict:
    """Wrap denied rows into the API ``denied_images`` object."""
    items = []
    for row in denied:
        items.append(
            {
                "keyframe_id": row.get("keyframe_id"),
                "video_id": row.get("video_id"),
                "group_id": row.get("group_id"),
                "keyframe_num": row.get("keyframe_num"),
                "image_path": row.get("image_path"),
                "image_url": row.get("image_url"),
                "confidence_score": row.get("confidence_score"),
                "reason": row.get("reason") or RejectReason.LOAD_FAILED.value,
                "brightness": row.get("brightness"),
                "laplacian_var": row.get("laplacian_var"),
            }
        )
    return {"total": len(items), "items": items}
