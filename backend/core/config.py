"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)


def _strip_quotes(value: Optional[str]) -> str:
    if value is None:
        return ""
    return value.strip().strip('"').strip("'")


def normalize_qdrant_url(url: str) -> str:
    """Ensure HTTPS Qdrant Cloud URLs use port 443.

    qdrant_client defaults to :6333 when no port is set; many networks block
    that port while HTTPS :443 works.
    """
    raw = _strip_quotes(url).rstrip("/")
    if not raw:
        return raw
    parsed = urlparse(raw)
    if parsed.scheme == "https" and not parsed.port:
        # urlparse leaves hostname without port; rebuild with :443
        netloc = parsed.hostname or ""
        if parsed.username or parsed.password:
            auth = parsed.username or ""
            if parsed.password:
                auth = f"{auth}:{parsed.password}"
            netloc = f"{auth}@{netloc}"
        netloc = f"{netloc}:443"
        return parsed._replace(netloc=netloc).geturl().rstrip("/")
    return raw


def parse_s3_uri(value: str, default_bucket: Optional[str] = None) -> Tuple[str, str]:
    """Parse `s3://bucket/prefix/` or bare bucket/prefix into (bucket, prefix)."""
    raw = _strip_quotes(value)
    if not raw:
        if default_bucket:
            return default_bucket, ""
        raise ValueError("Empty S3 URI/bucket value")

    if raw.startswith("s3://"):
        parsed = urlparse(raw)
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")
    elif "://" in raw and "console.aws.amazon.com" in raw:
        # Console URL like .../s3/buckets/aic2026?region=...
        match = re.search(r"/buckets/([^/?#]+)", raw)
        bucket = match.group(1) if match else (default_bucket or "")
        prefix = ""
        if not bucket:
            raise ValueError(f"Cannot parse bucket from console URL: {raw}")
    else:
        # Treat as bucket or bucket/prefix
        parts = raw.split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


@dataclass(frozen=True)
class Settings:
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_endpoint_url: Optional[str]
    s3_bucket: str
    database_prefix: str
    keyframe_prefix: str
    qdrant_url: str
    qdrant_api_key: str
    qdrant_collection: str
    hf_repo_id: str
    hf_token: str
    snapshot_filename: str
    snapshot_dir: Path
    model_id: str
    model_cache_dir: Path
    db_mount_dir: Path
    host: str
    port: int
    groq_api_key: str
    openai_api_key: str
    openai_model: str
    openai_reasoning_effort: str
    presign_expires_seconds: int
    auto_restore_snapshot: bool
    frame_db_shards: tuple
    skip_db_mount: bool
    keyframe_source: str
    hf_keyframe_prefix: str
    hf_db_prefix: str
    public_base_url: str
    default_fps: float
    jpeg_ram_cache_max: int
    jpeg_disk_cache: bool
    keyframe_local_dir: Path
    elasticsearch_url: str
    elasticsearch_index: str
    elasticsearch_asr_index: str
    elasticsearch_od_index: str
    elasticsearch_enrichment_index: str

    @property
    def llm_api_key(self) -> str:
        """Preferred LLM key for translate / temporal extraction."""
        return self.openai_api_key or self.groq_api_key

    @property
    def has_llm(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def database_bucket(self) -> str:
        # Logical name kept for env compatibility; physical bucket is s3_bucket.
        return self.database_prefix.rstrip("/") or "frame_db"

    @property
    def keyframe_bucket_uri(self) -> str:
        return f"s3://{self.s3_bucket}/{self.keyframe_prefix}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    aws_access_key_id = _strip_quotes(os.getenv("AWS_ACCESS_KEY_ID"))
    aws_secret_access_key = _strip_quotes(os.getenv("AWS_SECRET_ACCESS_KEY"))
    aws_region = _strip_quotes(os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-southeast-1")

    s3_url_raw = _strip_quotes(os.getenv("S3_URL"))
    endpoint_url = None
    default_bucket = "aic2026"
    if s3_url_raw:
        if "console.aws.amazon.com" in s3_url_raw:
            _, _ = parse_s3_uri(s3_url_raw, default_bucket=default_bucket)
            match = re.search(r"/buckets/([^/?#]+)", s3_url_raw)
            if match:
                default_bucket = match.group(1)
            region_match = re.search(r"[?&]region=([^&]+)", s3_url_raw)
            if region_match:
                aws_region = region_match.group(1)
        elif s3_url_raw.startswith("http://") or s3_url_raw.startswith("https://"):
            # Custom S3-compatible endpoint
            if "amazonaws.com" not in s3_url_raw:
                endpoint_url = s3_url_raw.rstrip("/")

    keyframe_bucket_env = _strip_quotes(os.getenv("KEYFRAME_BUCKET") or f"s3://{default_bucket}/aic2026/keyframes/")
    s3_bucket, keyframe_prefix = parse_s3_uri(keyframe_bucket_env, default_bucket=default_bucket)

    database_bucket_env = _strip_quotes(os.getenv("DATABASE_BUCKET") or "frame_db")
    if database_bucket_env.startswith("s3://"):
        db_bucket, database_prefix = parse_s3_uri(database_bucket_env, default_bucket=s3_bucket)
        if db_bucket != s3_bucket:
            # Databases live in the same physical bucket as keyframes in this project.
            s3_bucket = db_bucket
    else:
        database_prefix = database_bucket_env
        if not database_prefix.endswith("/"):
            database_prefix += "/"

    snapshot_dir = Path(_strip_quotes(os.getenv("SNAPSHOT_DIR") or "./datav3/qdrant_snapshots"))
    model_cache_dir = Path(_strip_quotes(os.getenv("MODEL_CACHE_DIR") or "./datav3/model_cache"))
    db_mount_dir = Path(_strip_quotes(os.getenv("DB_MOUNT_DIR") or "./datav3/db_mounts"))

    hf_token = _strip_quotes(os.getenv("HF_TOKEN") or os.getenv("HF_TOKEN_WRITE"))

    shards_raw = _strip_quotes(os.getenv("FRAME_DB_SHARDS"))
    frame_db_shards = tuple(s.strip() for s in shards_raw.split(",") if s.strip()) if shards_raw else tuple()

    return Settings(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_region=aws_region,
        s3_endpoint_url=endpoint_url,
        s3_bucket=s3_bucket,
        database_prefix=database_prefix,
        keyframe_prefix=keyframe_prefix,
        qdrant_url=normalize_qdrant_url(os.getenv("QDRANT_URL") or "http://localhost:6333"),
        qdrant_api_key=_strip_quotes(os.getenv("QDRANT_API_KEY")),
        qdrant_collection=_strip_quotes(os.getenv("QDRANT_COLLECTION") or "siglip2_keyframes_final"),
        hf_repo_id=_strip_quotes(os.getenv("HF_REPO_ID") or "htNghiaaa/aic26-lowres-keyframes"),
        hf_token=hf_token,
        snapshot_filename=_strip_quotes(
            os.getenv("QDRANT_SNAPSHOT_FILENAME") or "qdrant_vectorstore_siglip2_v3/qdrant.snapshot"
        ),
        snapshot_dir=snapshot_dir,
        model_id=_strip_quotes(os.getenv("MODEL_ID") or "google/siglip2-so400m-patch14-384"),
        model_cache_dir=model_cache_dir,
        db_mount_dir=db_mount_dir,
        host=_strip_quotes(os.getenv("HOST") or "0.0.0.0"),
        port=int(_strip_quotes(os.getenv("PORT") or "8000") or 8000),
        groq_api_key=_strip_quotes(os.getenv("GROQ_API_KEY")),
        openai_api_key=_strip_quotes(os.getenv("OPEN_AI_API_KEY") or os.getenv("OPENAI_API_KEY")),
        openai_model=_strip_quotes(os.getenv("OPENAI_MODEL") or "gpt-5.6-luna") or "gpt-5.6-luna",
        openai_reasoning_effort=(
            _strip_quotes(os.getenv("OPENAI_REASONING_EFFORT") or "none").lower() or "none"
        ),
        presign_expires_seconds=int(_strip_quotes(os.getenv("PRESIGN_EXPIRES_SECONDS") or "3600") or 3600),
        auto_restore_snapshot=_strip_quotes(os.getenv("AUTO_RESTORE_SNAPSHOT") or "true").lower()
        in {"1", "true", "yes", "on"},
        frame_db_shards=frame_db_shards,
        # Image search does not need SQLite; default skip S3 DB downloads.
        skip_db_mount=_strip_quotes(os.getenv("SKIP_DB_MOUNT") or "true").lower()
        in {"1", "true", "yes", "on"},
        keyframe_source=_strip_quotes(os.getenv("KEYFRAME_SOURCE") or "hf").lower() or "hf",
        hf_keyframe_prefix=_strip_quotes(os.getenv("HF_KEYFRAME_PREFIX") or "datav3") or "datav3",
        # Dataset has datav3 frames but no db_v3 folder; db_v2 is the current sqlite dump.
        hf_db_prefix=_strip_quotes(os.getenv("HF_DB_PREFIX") or "db_v2") or "db_v2",
        public_base_url=_strip_quotes(os.getenv("PUBLIC_BASE_URL")),
        default_fps=float(_strip_quotes(os.getenv("DEFAULT_FPS") or "25") or 25.0),
        jpeg_ram_cache_max=int(_strip_quotes(os.getenv("FRAME_JPEG_RAM_MAX") or "4096") or 4096),
        jpeg_disk_cache=_strip_quotes(os.getenv("FRAME_JPEG_DISK_CACHE") or "true").lower()
        in {"1", "true", "yes", "on"},
        keyframe_local_dir=Path(_strip_quotes(os.getenv("KEYFRAME_LOCAL_DIR") or "./datav3/keyframes")),
        elasticsearch_url=_strip_quotes(os.getenv("ELASTICSEARCH_URL") or "http://127.0.0.1:9200")
        or "http://127.0.0.1:9200",
        elasticsearch_index=_strip_quotes(os.getenv("ELASTICSEARCH_INDEX") or "ocr_keyframes")
        or "ocr_keyframes",
        elasticsearch_asr_index=_strip_quotes(os.getenv("ELASTICSEARCH_ASR_INDEX") or "asr_segments")
        or "asr_segments",
        elasticsearch_od_index=_strip_quotes(os.getenv("ELASTICSEARCH_OD_INDEX") or "od_entities")
        or "od_entities",
        elasticsearch_enrichment_index=_strip_quotes(
            os.getenv("ELASTICSEARCH_ENRICHMENT_INDEX") or "enrichment_keyframes"
        )
        or "enrichment_keyframes",
    )


def require_aws_credentials(settings: Optional[Settings] = None) -> Settings:
    settings = settings or get_settings()
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        raise RuntimeError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set")
    return settings
