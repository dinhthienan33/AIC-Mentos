import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def create_s3_client(region_name: Optional[str] = None):
    region_name = region_name or os.getenv("AWS_REGION", "ap-southeast-2")
    s3_client = boto3.client(
        "s3",
        region_name=region_name,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        config=Config(
            retries={"max_attempts": 10, "mode": "standard"},
            max_pool_connections=64,
        ),
    )
    print(f"✅ S3 client created successfully (region={region_name})")
    return s3_client


def fetch_s3_object_bytes(s3_client, bucket_name: str, key: str) -> bytes:
    obj = s3_client.get_object(Bucket=bucket_name, Key=key)
    return obj["Body"].read()


def build_snapshot_url(
    bucket_name: str,
    key: str,
    region_name: Optional[str] = None,
) -> str:
    """Public S3 URL, e.g. https://bucket.s3.region.amazonaws.com/path/to/104.jpg"""
    region_name = region_name or os.getenv("AWS_REGION", "ap-southeast-2")
    key = key.lstrip("/")
    template = os.getenv(
        "S3_PUBLIC_URL_TEMPLATE",
        "https://{bucket}.s3.{region}.amazonaws.com/{key}",
    )
    return template.format(bucket=bucket_name, region=region_name, key=key)


def fetch_snapshot_bytes(snapshot_url: str, timeout: int = 30) -> bytes:
    import urllib.request

    with urllib.request.urlopen(snapshot_url, timeout=timeout) as resp:
        return resp.read()


def parse_s3_location(s3_uri: str = "", bucket_name: str = "", prefix: str = "") -> Tuple[str, str]:
    s3_uri = (s3_uri or "").strip()

    if s3_uri.startswith("s3://"):
        parsed = urlparse(s3_uri)
        out_bucket = parsed.netloc.strip()
        out_prefix = parsed.path.lstrip("/")
    else:
        out_bucket = (bucket_name or "").replace("s3://", "").strip().strip("/")
        out_prefix = (prefix or "").strip().lstrip("/")

    if out_prefix and not out_prefix.endswith("/"):
        out_prefix += "/"

    return out_bucket, out_prefix


def ensure_bucket_exists(bucket_name: str, s3_client) -> bool:
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"✅ Bucket '{bucket_name}' is accessible")
        return True
    except ClientError as e:
        print(f"❌ Cannot access bucket '{bucket_name}': {e}")
        return False


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")


def _normalize_s3_etag(etag: str) -> str:
    return (etag or "").strip().strip('"')


def iter_s3_image_objects(
    s3_client,
    bucket_name: str,
    prefix: str = "",
    start_image: int = 0,
    max_images: Optional[int] = None,
) -> Iterator[Tuple[str, str, str]]:
    """Yield (key, etag, last_modified_iso) cho từng ảnh trong S3."""
    seen_images = 0
    taken = 0

    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(IMAGE_EXTENSIONS):
                continue

            if seen_images < start_image:
                seen_images += 1
                continue

            etag = _normalize_s3_etag(obj.get("ETag", ""))
            last_modified = obj["LastModified"].isoformat()
            yield key, etag, last_modified
            seen_images += 1
            taken += 1

            if max_images is not None and taken >= max_images:
                return


def _list_common_prefixes(s3_client, bucket_name: str, prefix: str) -> List[str]:
    """List immediate sub-prefixes (folders) under the given prefix."""
    prefixes: List[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            prefixes.append(cp["Prefix"])
    return prefixes


def _list_images_under_prefix(
    s3_client,
    bucket_name: str,
    prefix: str,
    limit: Optional[int] = None,
) -> List[Tuple[str, str, str]]:
    """List image objects under a single prefix, optionally stopping early."""
    results: List[Tuple[str, str, str]] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(IMAGE_EXTENSIONS):
                continue
            etag = _normalize_s3_etag(obj.get("ETag", ""))
            last_modified = obj["LastModified"].isoformat()
            results.append((key, etag, last_modified))
            if limit is not None and len(results) >= limit:
                return results
    return results


def list_s3_image_objects_parallel(
    s3_client,
    bucket_name: str,
    prefix: str = "",
    start_image: int = 0,
    max_images: Optional[int] = None,
    max_workers: int = 16,
) -> List[Tuple[str, str, str]]:
    """List S3 image objects by listing sub-prefixes in parallel.

    When max_images is set, each sub-prefix thread stops early once enough
    images have been collected globally, avoiding a full listing of all objects.
    """
    need = start_image + max_images if max_images is not None else None

    sub_prefixes = _list_common_prefixes(s3_client, bucket_name, prefix)

    if not sub_prefixes:
        results = _list_images_under_prefix(s3_client, bucket_name, prefix, limit=need)
    else:
        sub_prefixes.sort()
        print(f"⚡ Listing {len(sub_prefixes)} sub-prefixes in parallel (workers={max_workers})")

        if need is not None:
            per_prefix_limit = need
        else:
            per_prefix_limit = None

        results: List[Tuple[str, str, str]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _list_images_under_prefix, s3_client, bucket_name, sp, per_prefix_limit,
                ): sp
                for sp in sub_prefixes
            }
            for future in as_completed(futures):
                results.extend(future.result())

        results.sort(key=lambda x: x[0])

    if start_image > 0:
        results = results[start_image:]
    if max_images is not None:
        results = results[:max_images]

    return results


def iter_selected_images_s3(
    s3_client,
    bucket_name: str,
    prefix: str = "",
    start_image: int = 0,
    max_images: Optional[int] = None,
) -> Iterator[str]:
    for key, _, _ in iter_s3_image_objects(
        s3_client, bucket_name, prefix, start_image, max_images
    ):
        yield key
