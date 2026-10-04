"""S3 storage adapter replacing Azure blob helpers."""
from __future__ import annotations

import io
import logging
import time
from collections import OrderedDict
from functools import lru_cache
from typing import Iterable, List, Optional, Tuple

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError
from PIL import Image

from .config import Settings, get_settings, require_aws_credentials

logger = logging.getLogger(__name__)


class S3Storage:
    def __init__(self, settings: Optional[Settings] = None, client: Optional[BaseClient] = None):
        self.settings = require_aws_credentials(settings or get_settings())
        if client is not None:
            self.client = client
        else:
            session = boto3.Session(
                aws_access_key_id=self.settings.aws_access_key_id,
                aws_secret_access_key=self.settings.aws_secret_access_key,
                region_name=self.settings.aws_region,
            )
            kwargs = {
                "config": Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "virtual"},
                    retries={"max_attempts": 5},
                )
            }
            # Force regional host (bucket.s3.<region>.amazonaws.com). Without an
            # explicit endpoint, boto3 often signs against s3.amazonaws.com.
            if self.settings.s3_endpoint_url:
                kwargs["endpoint_url"] = self.settings.s3_endpoint_url
            else:
                kwargs["endpoint_url"] = f"https://s3.{self.settings.aws_region}.amazonaws.com"
            self.client = session.client("s3", **kwargs)

        self.bucket = self.settings.s3_bucket
        self.database_prefix = self.settings.database_prefix
        self.keyframe_prefix = self.settings.keyframe_prefix
        self._presign_cache: OrderedDict[str, Tuple[str, float]] = OrderedDict()

    def list_keys(self, prefix: str, suffix: Optional[str] = None, max_keys: Optional[int] = None) -> List[str]:
        keys: List[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if suffix and not key.endswith(suffix):
                    continue
                keys.append(key)
                if max_keys is not None and len(keys) >= max_keys:
                    return keys
        return keys

    def list_prefixes(self, prefix: str) -> List[str]:
        prefixes: List[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, Delimiter="/"):
            for entry in page.get("CommonPrefixes", []):
                prefixes.append(entry["Prefix"])
        return prefixes

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def download_file(self, key: str, local_path: str) -> str:
        self.client.download_file(self.bucket, key, local_path)
        return local_path

    def get_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def put_bytes(self, key: str, data: bytes, content_type: Optional[str] = None) -> None:
        kwargs = {"Bucket": self.bucket, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        self.client.put_object(**kwargs)

    def generate_presigned_url(self, key: str, expires_in: Optional[int] = None) -> str:
        ttl = int(expires_in or self.settings.presign_expires_seconds)
        cached = self._presign_cache.get(key)
        now = time.time()
        if cached is not None:
            url, expires_at = cached
            if expires_at - now > 60:
                self._presign_cache.move_to_end(key)
                return url
        url = self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": "inline",
                "ResponseCacheControl": f"public, max-age={ttl}, immutable",
            },
            ExpiresIn=ttl,
        )
        self._presign_cache[key] = (url, now + ttl)
        self._presign_cache.move_to_end(key)
        while len(self._presign_cache) > 8192:
            self._presign_cache.popitem(last=False)
        return url

    def list_database_shards(self) -> List[str]:
        """Return shard names like L21_a from frame_db/L21_a/frame_db.db."""
        shards: List[str] = []
        for key in self.list_keys(self.database_prefix, suffix="/frame_db.db"):
            # frame_db/L21_a/frame_db.db -> L21_a
            relative = key[len(self.database_prefix) :] if key.startswith(self.database_prefix) else key
            parts = relative.split("/")
            if parts:
                shards.append(parts[0])
        return sorted(set(shards))

    def database_key(self, shard: str) -> str:
        return f"{self.database_prefix}{shard}/frame_db.db"

    def keyframe_object_key(self, shard: str, video_id: str, frame_idx: int) -> str:
        return f"{self.keyframe_prefix}{shard}/keyframes/{video_id}/{int(frame_idx):06d}.jpg"

    def keyframe_object_key_from_image_path(self, shard: str, image_path: str) -> str:
        image_path = image_path.lstrip("/")
        return f"{self.keyframe_prefix}{shard}/{image_path}"

    def resolve_keyframe_key(
        self,
        video_id: str,
        frame_idx: int,
        shard_candidates: Optional[Iterable[str]] = None,
        image_path: Optional[str] = None,
    ) -> Optional[str]:
        candidates = list(shard_candidates or [])
        if not candidates:
            group = video_id.split("_")[0]
            candidates = [s for s in self.list_database_shards() if s.startswith(f"{group}_")]
            if not candidates:
                candidates = [f"{group}_a"]

        for shard in candidates:
            if image_path:
                key = self.keyframe_object_key_from_image_path(shard, image_path)
            else:
                key = self.keyframe_object_key(shard, video_id, frame_idx)
            if self.exists(key):
                return key

        # Fallback to first candidate even if HEAD failed (may still be valid soon).
        shard = candidates[0]
        if image_path:
            return self.keyframe_object_key_from_image_path(shard, image_path)
        return self.keyframe_object_key(shard, video_id, frame_idx)

    def create_thumbnail_if_needed(
        self,
        source_key: str,
        size: Tuple[int, int] = (200, 200),
        quality: int = 85,
    ) -> bool:
        """Create a sidecar thumbnail next to the source object if missing."""
        thumb_key = _thumbnail_key(source_key, size)
        if self.exists(thumb_key):
            return False

        raw = self.get_bytes(source_key)
        with Image.open(io.BytesIO(raw)) as img:
            img = img.convert("RGB")
            img.thumbnail(size, Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            self.put_bytes(thumb_key, buf.getvalue(), content_type="image/jpeg")
        return True


def _thumbnail_key(source_key: str, size: Tuple[int, int]) -> str:
    if "." in source_key:
        stem, _ext = source_key.rsplit(".", 1)
    else:
        stem = source_key
    return f"{stem}_thumb_{size[0]}x{size[1]}.jpg"


@lru_cache(maxsize=1)
def get_s3_storage() -> S3Storage:
    return S3Storage()


# Backwards-compatible aliases used while migrating call sites.
def create_s3_client() -> S3Storage:
    return get_s3_storage()
